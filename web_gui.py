"""Web GUI for the live transcriptor: local server + SSE, Stitch design.

Browser front reproducing the Stitch screen as-is; the watcher pipeline is
shared verbatim with the tkinter app:

  captions_watch.ps1 (unchanged) -> WatcherSession (unchanged) -> this server
  -> SSE push -> browser rendering of the Stitch HTML.

No new dependencies: http.server + queues + browser EventSource. Binds to
127.0.0.1 only. The tkinter app keeps working as the alternative front.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pi_bridge
import app_settings
from fluidez import clean
from transcript_lines import parse_markdown, read_markdown, to_txt, write_utf8

import transcriptor_app
from transcriptor_app import (PROJECT_DIR, SNAPSHOTS_DIR, TRANSCRIPTS_DIR,
                              WatcherSession, auto_summary_due,
                              launch_livecaptions, list_transcripts,
                              render_limpio, render_resumen)

WEB_DIR = os.path.join(PROJECT_DIR, "web")
PORT = int(os.environ.get("TRANSCRIPTOR_PORT", "8737"))
CREATE_NO_WINDOW = 0x08000000


class Hub:
    """Fan-out of server events to every SSE client (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[int, queue.Queue] = {}
        self._next = 0

    def register(self) -> tuple[int, queue.Queue]:
        with self._lock:
            key = self._next
            self._next += 1
            self._clients[key] = queue.Queue(maxsize=500)
            return key, self._clients[key]

    def unregister(self, key: int) -> None:
        with self._lock:
            self._clients.pop(key, None)

    def publish(self, event: dict) -> None:
        with self._lock:
            targets = list(self._clients.values())
        payload = json.dumps(event, ensure_ascii=False)
        for box in targets:
            try:
                box.put_nowait(payload)
            except queue.Full:  # slow client: drop rather than stall the pipe
                pass


class Orchestrator:
    """Session + summary state, shared by every connected browser tab."""

    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.lock = threading.Lock()
        self.session: WatcherSession | None = None
        self.last_transcript = ""
        self.summary_cancel: threading.Event | None = None
        self.status = "Listo."
        self.starting = False
        self.history: list[tuple[str, str]] = list_transcripts(TRANSCRIPTS_DIR)

    # --- events ---------------------------------------------------------- #

    def publish_status(self, text: str) -> None:
        self.status = text
        self.hub.publish({"type": "status", "text": text})

    def publish_state(self) -> None:
        self.hub.publish({
            "type": "state",
            "transcribing": self.session is not None,
            "summaryRunning": self.summary_cancel is not None,
            "piAvailable": pi_bridge.available(),
        })

    def refresh_history(self) -> None:
        self.history = list_transcripts(TRANSCRIPTS_DIR)
        self.hub.publish({
            "type": "history",
            "items": [{"name": name, "mtime": os.path.getmtime(path)}
                      for name, path in self.history],
        })

    # --- transcription ---------------------------------------------------- #

    def _make_session(self, out_path: str = "", captions_pid: int = 0) -> WatcherSession:
        session = WatcherSession(
            on_line=lambda text: self.hub.publish(
                {"type": "line",
                 "stamp": datetime.now().strftime("%H:%M:%S"), "text": text}),
            on_status=lambda s: self.publish_status(s),
            on_tail=lambda text: self.hub.publish(
                {"type": "tail",
                 "stamp": datetime.now().strftime("%H:%M:%S"), "text": text}),
            on_error=self._on_error)
        session.start(out_path=out_path, captions_pid=captions_pid)
        return session

    def start(self, resume: bool = False) -> str:
        with self.lock:
            if self.session or self.starting:
                return "Ya hay una transcripción corriendo."
            self.starting = True
        self.hub.publish({"type": "state", "transcribing": False,
                          "summaryRunning": self.summary_cancel is not None,
                          "piAvailable": pi_bridge.available(),
                          "starting": True})
        self.hub.publish({"type": "status", "text": "Abriendo Subtitulos en vivo..."})
        threading.Thread(target=self._start_worker, args=(resume,),
                         daemon=True).start()
        return ""

    def _start_worker(self, resume: bool) -> None:
        try:
            # Launch and PIN the exact Live Captions instance, like the
            # tkinter app does; without this the watcher's startup check
            # fails with exit 1 whenever none was already running.
            pid = launch_livecaptions() or 0
            if not pid:
                self.hub.publish({
                    "type": "watcherLost",
                    "message": "No se pudo abrir Live Captions "
                               "(Subtitulos en directo)."})
                self.publish_state()
                return
            out_path = (self.last_transcript
                        if resume and self.last_transcript else "")
            session = self._make_session(out_path=out_path, captions_pid=pid)
            with self.lock:
                self.session = session
                self.last_transcript = session.out_path
            self.publish_status("Transcribiendo...")
            self.publish_state()
            self.refresh_history()
        finally:
            with self.lock:
                self.starting = False
            self.publish_state()

    def stop(self) -> str:
        with self.lock:
            session, self.session = self.session, None
        if session:
            session.stop()
            self.publish_state()
            # S2: with the persisted toggle on, the closing session gets its
            # Pi summary for free. The server shutdown path never routes here.
            if auto_summary_due(app_settings.get_bool("auto_summary"),
                                self.last_transcript,
                                pi_bridge.available()):
                self.run_summary()
            return ""
        self.publish_state()
        return ""

    def _on_error(self, message: str) -> None:
        with self.lock:
            dead, self.session = self.session, None
        if dead is not None:
            self.hub.publish({"type": "watcherLost", "message": str(message)})
        self.publish_state()

    # --- summary ----------------------------------------------------------- #

    def run_summary(self, source: str | None = None, live: bool = False) -> str:
        with self.lock:
            if self.summary_cancel:
                return "Ya hay un resumen corriendo."
            source = source or self.last_transcript
            if not source or not os.path.exists(source):
                return "Todavía no hay transcript para resumir."
            self.summary_cancel = threading.Event()
            cancel = self.summary_cancel
        self.publish_state()
        self.hub.publish({"type": "summaryStarted", "live": live})
        threading.Thread(target=self._summary_worker, args=(source, live, cancel),
                         daemon=True).start()
        return ""

    def cancel_summary(self) -> str:
        if self.summary_cancel:
            self.summary_cancel.set()
            self.publish_status("Cancelando resumen...")
            return ""
        return "No hay resumen en curso."

    def _summary_worker(self, source: str, live: bool,
                        cancel: threading.Event) -> None:
        stem = Path(source).stem
        stamp = datetime.now().strftime("%H%M%S")
        derived = {
            "snapshot": os.path.join(SNAPSHOTS_DIR, f"{stem}-{stamp}.md"),
            "limpio": os.path.join(TRANSCRIPTS_DIR, f"{stem}.limpio.md"),
            "resumen": os.path.join(TRANSCRIPTS_DIR, f"{stem}.resumen.md"),
        }
        try:
            raw = Path(source).read_bytes().decode("utf-8", errors="replace")
            utterances = parse_markdown(raw)
            write_utf8(derived["snapshot"], raw)
            polished = clean(utterances)
            write_utf8(
                derived["limpio"],
                render_limpio(stem, len(utterances), len(polished), polished))
            self.hub.publish({
                "type": "summaryProgress",
                "text": f"Texto limpio: {len(utterances)} -> {len(polished)} "
                        "lineas. Pi esta resumiendo..."})
            summary = pi_bridge.summarize(
                derived["limpio"], on_delta=lambda d: self.hub.publish(
                    {"type": "summaryDelta", "text": d.rstrip()}),
                cancel_event=cancel)
            if cancel.is_set():
                raise pi_bridge.SummaryError("Resumen cancelado por el usuario.")
            write_utf8(derived["resumen"], render_resumen(
                stem, derived, summary, len(polished)))
        except (pi_bridge.SummaryError, pi_bridge.PiUnavailable, OSError) as exc:
            self.hub.publish({"type": "summaryFailed", "text": f"El resumen fallo: {exc}"})
        finally:
            with self.lock:
                done = (self.summary_cancel is not None
                        and cancel is self.summary_cancel)
                self.summary_cancel = None
            self.hub.publish({"type": "summaryDone", "ok": done,
                              "resumen": derived["resumen"]})
            self.publish_state()


class Handler(BaseHTTPRequestHandler):
    orchestrator: Orchestrator = None  # type: ignore[assignment]
    hub: Hub = None  # type: ignore[assignment]
    server_version = "TranscriptorWeb/1.0"

    # --- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64_000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _safe_transcript(self, name: str) -> str:
        """Only names inside TRANSCRIPTS_DIR are ever accepted."""
        full = os.path.realpath(os.path.join(TRANSCRIPTS_DIR, name))
        root = os.path.realpath(TRANSCRIPTS_DIR)
        if os.path.dirname(full) != root or not name.endswith(".md"):
            return ""
        return full if os.path.isfile(full) else ""

    # --- routes ------------------------------------------------------------ #

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(WEB_DIR, "index.html"),
                          "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._json({"error": "index.html no encontrado"}, 500)
            return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        if path == "/summary-markdown.js":
            try:
                with open(os.path.join(WEB_DIR, "summary-markdown.js"),
                          "rb") as fh:
                    self._send(200, fh.read(),
                               "application/javascript; charset=utf-8")
            except OSError:
                self._json({"error": "summary-markdown.js no encontrado"},
                           404)
            return
        if path == "/api/state":
            orch = self.orchestrator
            self._json({
                "transcribing": orch.session is not None,
                "summaryRunning": orch.summary_cancel is not None,
                "piAvailable": pi_bridge.available(),
                "status": orch.status,
                "history": [name for name, _ in orch.history],
                "autoSummary": app_settings.get_bool("auto_summary"),
            })
            return
        if path == "/api/historial-search":
            query = parse_qs(urlsplit(self.path).query).get("q", [""])[0]
            self._json({"items": transcriptor_app.search_transcripts(
                TRANSCRIPTS_DIR, query)})
            return
        if path == "/api/resumen-text":
            orch = self.orchestrator
            if not orch.last_transcript:
                self._json({"text": ""})
                return
            resumen = Path(orch.last_transcript).with_suffix(".resumen.md")
            if not resumen.exists():
                self._json({"text": ""})
                return
            self._json({"text": resumen.read_text(encoding="utf-8",
                                                 errors="replace")})
            return
        if path == "/events":
            self._serve_events()
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/action":
            self._json({"error": "not found"}, 404)
            return
        body = self._read_body()
        action = body.get("action")
        orch = self.orchestrator
        error = ""
        if action == "start":
            error = orch.start(resume=bool(body.get("resume")))
        elif action == "stop":
            error = orch.stop()
        elif action == "summary":
            error = orch.run_summary()
        elif action == "summary-live":
            error = orch.run_summary(live=True)
        elif action == "cancel-summary":
            error = orch.cancel_summary()
        elif action == "set-auto-summary":
            try:
                app_settings.set_bool("auto_summary",
                                      bool(body.get("enabled")))
            except (OSError, KeyError) as exc:
                error = f"No se pudo guardar el ajuste: {exc}"
        elif action == "export-txt":
            error = self._export_txt()
        elif action == "export-history":
            error = self._export_history(body)
        elif action == "open":
            error = self._open_file(body)
        else:
            error = f"accion desconocida: {action}"
            self._json({"ok": False, "error": error}, 400)
            return
        self._json({"ok": not error, "error": error} if error else {"ok": True})

    def _export_txt(self) -> str:
        orch = self.orchestrator
        source = orch.last_transcript
        if not source or not os.path.exists(source):
            return "Todavía no hay transcript para exportar."
        return self._write_txt_export(source)

    def _export_history(self, body: dict) -> str:
        """Same TXT export as _export_txt, for a history transcript only."""
        full = self._safe_transcript(str(body.get("name", "")))
        if not full:
            return "Archivo no válido."
        return self._write_txt_export(full)

    def _write_txt_export(self, source: str) -> str:
        """Shared TXT export: write <source>.txt next to it and open it."""
        try:
            target = Path(source).with_suffix(".txt")
            write_utf8(target, to_txt(read_markdown(source)))
        except OSError as exc:
            return f"No se pudo escribir el TXT: {exc}"
        subprocess.Popen(["cmd", "/c", "start", "", str(target)],
                         creationflags=CREATE_NO_WINDOW)
        return ""

    def _open_file(self, body: dict) -> str:
        full = self._safe_transcript(str(body.get("name", "")))
        if not full:
            return "Archivo no válido."
        subprocess.Popen(["cmd", "/c", "start", "", full],
                         creationflags=CREATE_NO_WINDOW)
        return ""

    # --- SSE --------------------------------------------------------------- #

    def _serve_events(self) -> None:
        key, box = self.hub.register()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = box.get(timeout=15.0)
                except queue.Empty:
                    payload = json.dumps({"type": "ping"})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unregister(key)


def build_server(orchestrator: Orchestrator | None = None,
                 port: int = PORT) -> ThreadingHTTPServer:
    hub = Hub()
    orch = orchestrator or Orchestrator(hub)
    Handler.hub = hub
    Handler.orchestrator = orch
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return server


def main() -> int:
    server = build_server()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Transcriptor web en {url} (Ctrl+C para cortar)")
    subprocess.Popen(["cmd", "/c", "start", "", url],
                     creationflags=CREATE_NO_WINDOW)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if Handler.orchestrator.session:
            Handler.orchestrator.session.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
