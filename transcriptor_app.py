"""Transcriptor en vivo - app.

One-click desktop app for live transcription on this Windows machine.

Flow (all handled by the app, the user never opens Live Captions by hand):
  1. If Windows Live Captions is not running, the app starts it
     (%WINDIR%\\System32\\LiveCaptions.exe).
  2. The app launches captions_watch.ps1, which reads the caption window
     through UI Automation and appends every line to
     transcripts\\<timestamp>_live-captions.md (UTF-8 no BOM).
  3. Stop: the app ends the watcher. Live Captions stays open for reuse.

Requires only the standard Python install (tkinter, datetime, subprocess).
No audio capture is used, so it works even with the broken WASAPI driver.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import pi_bridge
from fluidez import clean
from transcript_lines import parse_markdown, read_markdown, to_txt, write_utf8

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(PROJECT_DIR, "captions_watch.ps1")
TRANSCRIPTS_DIR = os.path.join(PROJECT_DIR, "transcripts")
SNAPSHOTS_DIR = os.path.join(TRANSCRIPTS_DIR, ".snapshots")
LIVE_CAPTURES = os.path.join(os.environ.get("WINDIR", r"C:\\Windows"),
                             "System32", "LiveCaptions.exe")

# PowerShell 5.1 with a redirected stdout emits the OEM codepage; decode
# defensively. The .md file is always written by the watcher as UTF-8.
_DECODE = dict(encoding="utf-8", errors="replace")


def livecaptions_running() -> int | None:
    """Return the pid of a running LiveCaptions.exe, or None."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name LiveCaptions -ErrorAction SilentlyContinue | "
             "Select-Object -First 1).Id"],
            capture_output=True, text=True, timeout=15, **_DECODE)
    except (subprocess.TimeoutExpired, OSError):
        return None
    match = re.search(r"\b(\d+)\b", out.stdout or "")
    return int(match.group(1)) if match else None


def launch_livecaptions(timeout_s: float = 15.0) -> int | None:
    """Start Live Captions if absent; wait until its process is up."""
    pid = livecaptions_running()
    if pid:
        return pid
    if not os.path.exists(LIVE_CAPTURES):
        return None
    creationflags = 0x08000000  # CREATE_NO_WINDOW
    subprocess.Popen([LIVE_CAPTURES], close_fds=True, creationflags=creationflags)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pid = livecaptions_running()
        if pid:
            return pid
        time.sleep(0.5)
    return None


class WatcherSession:
    """One transcription run: a hidden PowerShell watcher + its transcript path."""

    def __init__(self, on_line, on_status, on_error) -> None:
        self.on_line = on_line
        self.on_status = on_status
        self.on_error = on_error
        self.proc: subprocess.Popen | None = None
        self.out_path = ""
        self.started_at = 0.0
        self.lines_since_stop = 0
        self.stopped = False

    def start(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        self.out_path = os.path.join(TRANSCRIPTS_DIR, f"{stamp}_live-captions.md")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", WATCHER, "-Out", self.out_path]
        self.proc = subprocess.Popen(
            cmd, cwd=PROJECT_DIR, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, close_fds=True, creationflags=0x08000000,
            **_DECODE)
        self.started_at = time.monotonic()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        proc = self.proc
        for raw in proc.stdout:  # type: ignore[union-attr]
            line = raw.rstrip("\r\n")
            if not line:
                continue
            # '- **[hh:mm:ss]** text' -> show the text
            self.lines_since_stop += 1
            match = re.match(r"- \*\*\[[0-9:.]+]\*\* (.*)", line)
            text = match.group(1) if match else line
            self.on_line(text)
            self.on_status("Transcribiendo - %d linea(s)" % self.lines_since_stop)
        code = proc.wait()
        elapsed = time.monotonic() - self.started_at
        self.on_status("Listo: %d linea(s) en %0.0f s" % (self.lines_since_stop, elapsed))
        if code not in (0, None) and not self.stopped:
            self.on_error("El watcher termino con codigo %s" % code)

    def stop(self, timeout_s: float = 5.0) -> None:
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None


class App(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root)
        self.root = root
        root.title("Transcriptor en vivo")
        root.minsize(560, 380)

        self.session: WatcherSession | None = None
        self.last_transcript = ""
        self.summary_cancel: threading.Event | None = None
        self.status_var = tk.StringVar(value="Listo.")
        self._build()
        self.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(400, self._keep_status_time)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(
            self, text="Transcribe lo que se escucha por los parlantes "
                       "(Windows Live Captions). Abrilo antes de la reunion.",
            wraplength=540).grid(column=0, row=0, sticky="ew", padx=12, pady=(12, 4))

        buttons = ttk.Frame(self)
        buttons.grid(column=0, row=1, sticky="ew", padx=12, pady=6)
        self.btn_start = ttk.Button(buttons, text="Transcribir", command=self.start)
        self.btn_stop = ttk.Button(buttons, text="Parar", command=self.stop,
                                   state="disabled")
        self.btn_open = ttk.Button(buttons, text="Abrir transcript",
                                   command=self.open_transcript, state="disabled")
        self.btn_export = ttk.Button(buttons, text="Exportar TXT",
                                     command=self.export_txt, state="disabled")
        self.btn_summary = ttk.Button(buttons, text="Resumir con Pi",
                                      command=self.run_summary, state="disabled")
        self.btn_cancel = ttk.Button(buttons, text="Cancelar resumen",
                                     command=self.cancel_summary, state="disabled")
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_open.pack(side="left", padx=(0, 8))
        self.btn_export.pack(side="left", padx=(0, 8))
        self.btn_summary.pack(side="left", padx=(0, 8))
        self.btn_cancel.pack(side="left")
        threading.Thread(target=self._probe_pi, daemon=True).start()

        self.lines = tk.Text(self, height=14, wrap="word", state="disabled")
        self.lines.grid(column=0, row=2, sticky="nsew", padx=12, pady=(4, 4))
        scroll = ttk.Scrollbar(self, command=self.lines.yview)
        scroll.grid(column=0, row=2, sticky="ens", padx=(0, 2))
        self.lines.configure(yscrollcommand=scroll.set)

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(column=0, row=3, sticky="ew", padx=12, pady=(0, 10))

    # --- pi probe --------------------------------------------------------- #

    def _probe_pi(self) -> None:
        ok = pi_bridge.available()
        self.root.after(0, lambda: self.btn_summary.configure(
            state="normal" if ok else "disabled"))
        if not ok:
            self.root.after(0, lambda: self.status_var.set(
                "Pi no esta disponible: no se podra resumir."))

    # --- actions -------------------------------------------------------- #

    def _remember_transcript(self, path: str) -> None:
        self.last_transcript = path
        self.btn_export.configure(state="normal")
        if pi_bridge.available():
            self.btn_summary.configure(state="normal")

    def start(self) -> None:
        if self.session:
            return
        self.btn_start.configure(state="disabled")
        threading.Thread(target=self._start_worker, daemon=True).start()

    def _start_worker(self) -> None:
        self.status_var.set("Abriendo Subtitulos en vivo...")
        pid = launch_livecaptions()
        if not pid:
            self.root.after(0, messagebox.showerror, "Transcriptor en vivo",
                            "No se pudo abrir Live Captions. Verifica que "
                            "'Subtitulos en directo' esté disponible en esta "
                            "PC y vuelve a intentarlo.")
            self.root.after(0, self._reset_ui)
            return

        def do_start() -> None:
            session = WatcherSession(self._on_line, lambda s: self.status_var.set(s),
                                     self._on_error)
            session.start()
            self.session = session
            self.btn_stop.configure(state="normal")
            self.btn_open.configure(state="normal")
            self.status_var.set("Transcribiendo...")
            self._remember_transcript(session.out_path)

        self.root.after(0, do_start)

    def stop(self) -> None:
        if self.session:
            self.session.stop()
            self.session = None
        self.btn_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")

    def open_transcript(self) -> None:
        if not self.session or not self.session.out_path:
            return
        os.startfile(self.session.out_path)  # noqa: S606 (local trusted path)

    def export_txt(self) -> None:
        source = self.last_transcript
        if not source or not os.path.exists(source):
            messagebox.showinfo("Transcriptor en vivo",
                                "Todavia no hay transcript para exportar.")
            return
        target = Path(source).with_suffix(".txt")
        try:
            write_utf8(target, to_txt(read_markdown(source)))
        except OSError as exc:
            messagebox.showerror("Transcriptor en vivo",
                                 f"No se pudo escribir el TXT: {exc}")
            return
        os.startfile(str(target))  # noqa: S606 (local trusted path)

    def run_summary(self) -> None:
        if self.summary_cancel:
            return
        source = self.last_transcript
        if not source or not os.path.exists(source):
            messagebox.showinfo("Transcriptor en vivo",
                                "Todavia no hay transcript para resumir.")
            return
        self.summary_cancel = threading.Event()
        self.btn_summary.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self._append_line("Resumiendo con Pi... (Cancelable)")
        threading.Thread(target=self._summary_worker, args=(source,),
                         daemon=True).start()

    def cancel_summary(self) -> None:
        if self.summary_cancel:
            self.summary_cancel.set()
            self.status_var.set("Cancelando resumen...")

    def _summary_worker(self, source: str) -> None:
        cancel = self.summary_cancel
        assert cancel is not None
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
            self.root.after(0, self._append_line,
                            f"Texto limpio: {len(utterances)} -> {len(polished)} lineas. "
                            "Pi esta resumiendo...")
            summary = pi_bridge.summarize(
                derived["limpio"], on_delta=self._on_summary_delta,
                cancel_event=cancel)
            if cancel.is_set():
                raise pi_bridge.SummaryError("Resumen cancelado por el usuario.")
            write_utf8(derived["resumen"], render_resumen(stem, derived, summary,
                                                          len(polished)))
        except (pi_bridge.SummaryError, pi_bridge.PiUnavailable, OSError) as exc:
            self.root.after(0, self._append_line, f"El resumen fallo: {exc}")
        finally:
            done = self.summary_cancel is not None and cancel is self.summary_cancel
            self.summary_cancel = None
            self.root.after(0, self._summary_done, derived, done)

    def _summary_done(self, derived: dict[str, str], ok: bool) -> None:
        self.btn_cancel.configure(state="disabled")
        self.btn_start.configure(state="normal")
        if pi_bridge.available():
            self.btn_summary.configure(state="normal")
        if not ok:
            self.status_var.set("Resumen cancelado o con error.")
            return
        target = derived["resumen"]
        if os.path.exists(target):
            self.status_var.set("Resumen listo.")
            self._append_line(f"Resumen escrito en {target}")
            os.startfile(target)  # noqa: S606 (local trusted path)
        else:
            self.status_var.set("Resumen no generado (ver el log en la ventana).")

    def _on_summary_delta(self, delta: str) -> None:
        self.root.after(0, self._append_line, delta.rstrip())

    # --- callbacks ------------------------------------------------------ #

    def _on_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.lines.configure(state="normal")
        self.lines.insert("end", f"[{stamp}] {text}\n")
        self.lines.see("end")
        self.lines.configure(state="disabled")

    def _append_line(self, text: str) -> None:
        if not text:
            return
        self._on_line(text)

    def _on_error(self, message: str) -> None:
        self.status_var.set(f"Atencion: {message}")

    def _reset_ui(self) -> None:
        self.session = None
        self.btn_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")

    def _keep_status_time(self) -> None:
        if self.session:
            elapsed = int(time.monotonic() - self.session.started_at)
            self.status_var.set(
                f"Transcribiendo - {elapsed//60}:{elapsed%60:02d} - "
                f"{self.session.lines_since_stop} linea(s)")
        self.root.after(1000, self._keep_status_time)

    def _on_close(self) -> None:
        if self.session:
            self.session.stop()
        self.root.destroy()


def render_limpio(stem: str, raw_count: int, polished_count: int,
                  utterances) -> str:
    """Header + body of the derived «limpio» markdown file."""
    from fluidez import render_markdown

    title = f"Transcript limpio \u2014 {stem} ({polished_count} de {raw_count} lineas)"
    meta = [
        ("Fuente", "Windows Live Captions (UI Automation)"),
        ("Limpieza", "local, sin IA: duplicados, fragmentos y repeticiones"),
    ]
    return render_markdown(title, meta, utterances)


def render_resumen(stem: str, derived: dict[str, str], summary: str,
                   polished_count: int) -> str:
    """Header + the Pi summary, written to the derived «resumen» file."""
    header = [
        f"# Resumen con Pi \u2014 {stem}",
        "",
        f"- **Fecha**: {datetime.now():%Y-%m-%d %H:%M}",
        f"- **Resumido hasta**: el final del archivo ({polished_count} lineas limpias)",
        f"- **Texto limpio**: {derived['limpio']}",
        "",
        "---",
        "",
    ]
    return "\n".join(header) + summary + ("\n" if not summary.endswith("\n") else "")


def main() -> int:
    app = App(tk.Tk())
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
