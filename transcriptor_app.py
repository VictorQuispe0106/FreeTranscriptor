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
_HISTORY_SUFFIX = "_live-captions.md"

# Visual identity taken from the Stitch DESKTOP design (feature T10).
PALETTE = {
    "bg": "#0d1117",        # near-black app background
    "panel": "#131a21",     # cards, text areas, button faces
    "border": "#22303a",    # hairline borders
    "accent": "#2dd4bf",    # luminous teal (primary)
    "accent_text": "#06251f",  # text on accent fills
    "text": "#d7e2ea",      # main foreground
    "muted": "#7d8fa0",     # secondary text, inactive tabs
    "tab_active": "#1b2a30",
}


def list_transcripts(directory: str) -> list[tuple[str, str]]:
    """Session transcripts in a transcript directory, newest first.

    Returns (name, full path) pairs for raw *_live-captions.md files only:
    derived files (.limpio.md, .resumen.md), the .snapshots folder, other
    directories and non-markdown files never enter the history.
    """
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found: list[tuple[str, str]] = []
    for name in names:
        if not name.endswith(_HISTORY_SUFFIX):
            continue
        full = os.path.join(directory, name)
        if os.path.isfile(full):
            found.append((name, full))

    def mtime(pair: tuple[str, str]) -> float:
        try:
            return os.path.getmtime(pair[1])
        except OSError:
            return 0.0

    found.sort(key=mtime, reverse=True)
    return found


def at_end(first: float, last: float) -> bool:
    """True when a yview tuple says the widget bottom is on screen.

    The transcript view only auto-follows the newest line when the reader
    is already at the bottom; reading a previous line is never interrupted.
    """
    return last >= 0.999


def copyable_line(block: str) -> str:
    """The exact displayed text, minus the widget's trailing newline."""
    return block.rstrip("\n")


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

    def __init__(self, on_line, on_status, on_error, on_tail=None) -> None:
        self.on_line = on_line
        self.on_status = on_status
        self.on_error = on_error
        self.on_tail = on_tail
        self.proc: subprocess.Popen | None = None
        self.out_path = ""
        self.started_at = 0.0
        self.lines_since_stop = 0
        self.stopped = False

    def start(self, out_path: str = "", captions_pid: int = 0) -> None:
        # CaptionsPid (S5): read only the exact LaunchCaptions.exe instance the
        # app launched, never leave the left-over instance of an old session.
        # out_path (S6): set when restarting after a dead watcher, so the run
        # continues appending to the file that was already being written.
        if out_path:
            self.out_path = out_path
        else:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
            self.out_path = os.path.join(TRANSCRIPTS_DIR, f"{stamp}_live-captions.md")
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", WATCHER, "-Out", self.out_path]
        if captions_pid > 0:
            cmd += ["-CaptionsPid", str(captions_pid)]
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
            # '> growing tail' -> the caption that is still being typed.
            if line.startswith("> "):
                if self.on_tail:
                    self.on_tail(line[2:])
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
        self._apply_theme(root)

        self.session: WatcherSession | None = None
        self.last_transcript = ""
        self.summary_cancel: threading.Event | None = None
        self.tail_active = False
        self.status_var = tk.StringVar(value="Listo.")
        self._history_map: dict[str, str] = {}
        self._build()
        self.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(400, self._keep_status_time)

    def _apply_theme(self, root: tk.Tk) -> None:
        """T10: stitch-derived dark identity; static config, zero per-line cost."""
        p = PALETTE
        root.configure(bg=p["bg"])
        root.option_add("*TCombobox*Listbox.background", p["panel"])
        root.option_add("*TCombobox*Listbox.foreground", p["text"])
        root.option_add("*TCombobox*Listbox.selectBackground", p["tab_active"])
        root.option_add("*TCombobox*Listbox.selectForeground", p["accent"])
        style = ttk.Style(root)
        try:
            style.theme_use("clam")  # honors border/background config on Windows
        except tk.TclError:
            pass
        style.configure(".", background=p["bg"], foreground=p["text"],
                        font=("Segoe UI", 9))
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["text"])
        style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        style.configure("TButton", background=p["panel"], foreground=p["text"],
                        bordercolor=p["border"], focuscolor=p["accent"],
                        padding=(8, 3))
        style.map("TButton",
                  background=[("active", p["tab_active"]), ("disabled", p["bg"])],
                  foreground=[("disabled", p["muted"])])
        style.configure("Accent.TButton", background=p["accent"],
                        foreground=p["accent_text"], bordercolor=p["accent"])
        style.map("Accent.TButton",
                  background=[("active", "#39e0cb"), ("disabled", p["panel"])],
                  foreground=[("disabled", p["muted"])])
        style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
        style.configure("TNotebook.Tab", background=p["panel"], foreground=p["muted"],
                        padding=(10, 3))
        style.map("TNotebook.Tab",
                  background=[("selected", p["tab_active"])],
                  foreground=[("selected", p["accent"])])
        style.configure("TCombobox", fieldbackground=p["panel"], background=p["panel"],
                        foreground=p["text"], arrowcolor=p["accent"],
                        bordercolor=p["border"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["panel"])],
                  foreground=[("readonly", p["text"])])

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        ttk.Label(
            self, text="Transcribe lo que se escucha por los parlantes "
                       "(Windows Live Captions). Abrilo antes de la reunion.",
            wraplength=540, style="Muted.TLabel").grid(
            column=0, row=0, sticky="ew", padx=12, pady=(12, 4))

        buttons = ttk.Frame(self)
        buttons.grid(column=0, row=1, sticky="ew", padx=12, pady=6)
        self.btn_start = ttk.Button(buttons, text="Transcribir", command=self.start,
                                    style="Accent.TButton")
        self.btn_stop = ttk.Button(buttons, text="Parar", command=self.stop,
                                   state="disabled")
        self.btn_open = ttk.Button(buttons, text="Abrir transcript",
                                   command=self.open_transcript, state="disabled")
        self.btn_export = ttk.Button(buttons, text="Exportar TXT",
                                     command=self.export_txt, state="disabled")
        self.btn_copy = ttk.Button(buttons, text="Copiar",
                                   command=self.copy_transcript, state="disabled")
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_open.pack(side="left", padx=(0, 8))
        self.btn_export.pack(side="left", padx=(0, 8))
        self.btn_copy.pack(side="left")
        threading.Thread(target=self._probe_pi, daemon=True).start()

        # S8: explicit partial summary, running while transcription continues.
        derived = ttk.Frame(self)
        derived.grid(column=0, row=2, sticky="ew", padx=12, pady=(0, 6))
        self.btn_summary = ttk.Button(derived, text="Resumir con Pi",
                                      command=self.run_summary,
                                      state="disabled")
        self.btn_live = ttk.Button(derived, text="Resumir hasta ahora",
                                   command=self.run_live_summary,
                                   state="disabled")
        self.btn_cancel = ttk.Button(derived, text="Cancelar resumen",
                                     command=self.cancel_summary,
                                     state="disabled")
        self.btn_copy_sum = ttk.Button(derived, text="Copiar resumen",
                                       command=self.copy_summary,
                                       state="disabled")
        self.btn_summary.pack(side="left", padx=(0, 8))
        self.btn_live.pack(side="left", padx=(0, 8))
        self.btn_cancel.pack(side="left", padx=(0, 8))
        self.btn_copy_sum.pack(side="left")

        # S1: session history row. Actions apply to the selected prior file,
        # never to the running session.
        history = ttk.Frame(self)
        history.grid(column=0, row=4, sticky="ew", padx=12, pady=(2, 2))
        ttk.Label(history, text="Historial:", style="Muted.TLabel").pack(
            side="left", padx=(0, 4))
        self.history_pick = ttk.Combobox(history, state="readonly", width=52)
        self.history_pick.pack(side="left", padx=(0, 8))
        self.history_pick.bind("<<ComboboxSelected>>", self._on_history_selected)
        self.btn_hist_open = ttk.Button(history, text="Abrir",
                                        command=self.open_history, state="disabled")
        self.btn_hist_txt = ttk.Button(history, text="TXT",
                                       command=self.export_history, state="disabled")
        self.btn_hist_summary = ttk.Button(
            history, text="Resumir",
            command=lambda: self.run_summary(self._selected_history_path()),
            state="disabled")
        self.btn_hist_open.pack(side="left", padx=(0, 4))
        self.btn_hist_txt.pack(side="left", padx=(0, 4))
        self.btn_hist_summary.pack(side="left")

        # S7: the transcript and the Pi summary never share one widget again.
        notebook = ttk.Notebook(self)
        notebook.grid(column=0, row=3, sticky="nsew", padx=12, pady=(4, 4))
        trans_tab = ttk.Frame(notebook)
        sum_tab = ttk.Frame(notebook)
        notebook.add(trans_tab, text="Transcripción")
        notebook.add(sum_tab, text="Resumen (Pi)")
        self.lines = tk.Text(
            trans_tab, height=14, wrap="word", state="disabled",
            bg=PALETTE["panel"], fg=PALETTE["text"], relief="flat", padx=8, pady=6,
            insertbackground=PALETTE["accent"],
            selectbackground=PALETTE["tab_active"], font=("Segoe UI", 10))
        self.lines.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(trans_tab, command=self.lines.yview)
        scroll.pack(side="right", fill="y")
        self.lines.configure(yscrollcommand=scroll.set)
        # Timestamp in accent, growing tail muted — the Stitch chip look.
        self.lines.tag_configure("stamp", foreground=PALETTE["accent"])
        self.lines.tag_configure("tail", foreground=PALETTE["muted"])
        self.summary_log = tk.Text(
            sum_tab, height=14, wrap="word", state="disabled",
            bg=PALETTE["panel"], fg=PALETTE["text"], relief="flat", padx=8, pady=6,
            insertbackground=PALETTE["accent"], font=("Segoe UI", 10))
        self.summary_log.pack(side="left", fill="both", expand=True)
        sum_scroll = ttk.Scrollbar(sum_tab, command=self.summary_log.yview)
        sum_scroll.pack(side="right", fill="y")
        self.summary_log.configure(yscrollcommand=sum_scroll.set)
        self._notebook = notebook

        status = ttk.Label(self, textvariable=self.status_var, anchor="w",
                           style="Muted.TLabel")
        status.grid(column=0, row=5, sticky="ew", padx=12, pady=(0, 10))
        self._refresh_history()

    # --- history ---------------------------------------------------------

    def _refresh_history(self) -> None:
        pairs = list_transcripts(TRANSCRIPTS_DIR)
        self._history_map = dict(pairs)
        self.history_pick.configure(values=[name for name, _ in pairs])
        current = self.history_pick.get()
        if current not in self._history_map:
            self.history_pick.set("")
            for button in (self.btn_hist_open, self.btn_hist_txt,
                           self.btn_hist_summary):
                button.configure(state="disabled")

    def _on_history_selected(self, _event: object = None) -> None:
        if not self.history_pick.get():
            return
        for button in (self.btn_hist_open, self.btn_hist_txt,
                       self.btn_hist_summary):
            button.configure(state="normal")

    def _selected_history_path(self) -> str:
        return self._history_map.get(self.history_pick.get(), "")

    def open_history(self) -> None:
        path = self._selected_history_path()
        if path:
            os.startfile(path)  # noqa: S606 (local trusted path)

    def export_history(self) -> None:
        source = self._selected_history_path()
        if not source:
            return
        try:
            target = Path(source).with_suffix(".txt")
            write_utf8(target, to_txt(read_markdown(source)))
        except OSError as exc:
            messagebox.showerror("Transcriptor en vivo",
                                 f"No se pudo escribir el TXT: {exc}")
            return
        os.startfile(str(target))  # noqa: S606 (local trusted path)

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
        self.btn_copy.configure(state="normal")
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
                                     self._on_error, self._on_tail)
            session.start(captions_pid=pid or 0)
            self.session = session
            self.btn_stop.configure(state="normal")
            self.btn_open.configure(state="normal")
            if pi_bridge.available():
                self.btn_live.configure(state="normal")
            self.status_var.set("Transcribiendo...")
            self._remember_transcript(session.out_path)
            self._refresh_history()

        self.root.after(0, do_start)

    def stop(self) -> None:
        if self.session:
            self.session.stop()
            self.session = None
        self.btn_stop.configure(state="disabled")
        self.btn_start.configure(state="normal")
        self.btn_live.configure(state="disabled")

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

    def run_summary(self, source: str | None = None, live: bool = False) -> None:
        if self.summary_cancel:
            return
        source = source or self.last_transcript
        if not source or not os.path.exists(source):
            messagebox.showinfo("Transcriptor en vivo",
                                "Todavia no hay transcript para resumir.")
            return
        self.summary_cancel = threading.Event()
        self.btn_summary.configure(state="disabled")
        self.btn_live.configure(state="disabled")
        if not live:
            # A final summary owns the app; live partial (S8) never touches the
            # transcription state: the session keeps running untouched.
            self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self._notebook.select(1)
        self._summary_line("Resumiendo con Pi... (Cancelable)")
        threading.Thread(target=self._summary_worker, args=(source, live),
                         daemon=True).start()

    def run_live_summary(self) -> None:
        """S8: summarize the growing transcript without stopping the session."""
        if self.session and self.session.out_path:
            self.run_summary(self.session.out_path, live=True)

    def copy_summary(self) -> None:
        resumen = Path(self.last_transcript).with_suffix(".resumen.md")
        if not self.last_transcript or not resumen.exists():
            messagebox.showinfo("Transcriptor en vivo",
                                "Todavia no hay resumen para copiar.")
            return
        try:
            text = resumen.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Transcriptor en vivo",
                                 f"No se pudo leer el resumen: {exc}")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Resumen copiado al portapapeles.")

    def copy_transcript(self) -> None:
        block = self.lines.get("1.0", "end")
        if not block.strip():
            messagebox.showinfo("Transcriptor en vivo",
                                "Todavia no hay texto que copiar.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(copyable_line(block))
        self.status_var.set("Transcript copiado al portapapeles.")

    def cancel_summary(self) -> None:
        if self.summary_cancel:
            self.summary_cancel.set()
            self.status_var.set("Cancelando resumen...")

    def _summary_worker(self, source: str, live: bool) -> None:
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
            self.root.after(0, self._summary_line, f"El resumen fallo: {exc}")
        finally:
            done = self.summary_cancel is not None and cancel is self.summary_cancel
            self.summary_cancel = None
            self.root.after(0, self._summary_done, derived, done, live)

    def _summary_done(self, derived: dict[str, str], ok: bool,
                      live: bool = False) -> None:
        self.btn_cancel.configure(state="disabled")
        if self.session is None:
            self.btn_start.configure(state="normal")
        if pi_bridge.available():
            self.btn_summary.configure(state="normal")
            if live:
                self.btn_live.configure(state="normal")
        if not ok:
            self.status_var.set("Resumen cancelado o con error.")
            return
        target = derived["resumen"]
        if os.path.exists(target):
            self.status_var.set("Resumen listo.")
            self._summary_line(f"Resumen escrito en {target}")
            os.startfile(target)  # noqa: S606 (local trusted path)
        else:
            self.status_var.set("Resumen no generado (ver el log en la ventana).")

    def _on_summary_delta(self, delta: str) -> None:
        self.root.after(0, self._summary_line, delta.rstrip())

    def _summary_line(self, text: str) -> None:
        if not text:
            return
        self.summary_log.configure(state="normal")
        self.summary_log.insert("end", f"{text}\n")
        self.summary_log.see("end")
        self.summary_log.configure(state="disabled")

    # --- callbacks ------------------------------------------------------ #

    def _remove_last_line(self) -> None:
        """Delete the last real text line, keeping the widget's trailing newline."""
        last = int(self.lines.index("end-1c").split(".")[0])
        self.lines.delete(f"{max(last - 1, 1)}.0", "end-1c")

    def _on_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        # S2: only follow the newest line when the reader is already at the
        # bottom; reading an earlier line is never interrupted by autoplay.
        follow = at_end(*self.lines.yview())
        self.lines.configure(state="normal")
        if self.tail_active:
            self._remove_last_line()
            self.tail_active = False
        self.lines.insert("end", f"[{stamp}] ", "stamp")
        self.lines.insert("end", f"{text}\n")
        if follow:
            self.lines.see("end")
        self.lines.configure(state="disabled")

    def _on_tail(self, text: str) -> None:
        """Replace the growing caption line in place, the moment it is heard."""
        stamp = datetime.now().strftime("%H:%M:%S")
        follow = at_end(*self.lines.yview())
        self.lines.configure(state="normal")
        if self.tail_active:
            self._remove_last_line()
        self.lines.insert("end", f"[{stamp}] ", "stamp")
        self.lines.insert("end", f"\u25b8 {text}\n", "tail")
        if follow:
            self.lines.see("end")
        self.lines.configure(state="disabled")
        self.tail_active = True

    def _append_line(self, text: str) -> None:
        if not text:
            return
        self._on_line(text)

    def _on_error(self, message: str) -> None:
        self.status_var.set(f"Atencion: {message}")
        # S6: the watcher died in the middle of a run (captions window closed,
        # UIA lost). Offer to continue the same file instead of losing the run.
        if self.session is not None and self.last_transcript and os.path.exists(self.last_transcript):
            self.session = None
            self.root.after(0, self._offer_restart, message)

    def _offer_restart(self, message: str) -> None:
        if messagebox.askyesno(
                "Transcriptor en vivo",
                f"La transcripcion se corto: {message}\n\n"
                "Reiniciarla en el mismo archivo de transcript?"):
            threading.Thread(target=self._restart_worker, daemon=True).start()
        else:
            self._reset_ui()

    def _restart_worker(self) -> None:
        self.status_var.set("Reabriendo Subtitulos en vivo...")
        pid = launch_livecaptions() or 0

        def do_restart() -> None:
            session = WatcherSession(self._on_line, lambda s: self.status_var.set(s),
                                     self._on_error, self._on_tail)
            session.start(out_path=self.last_transcript, captions_pid=pid)
            self.session = session
            self.btn_stop.configure(state="normal")
            self.btn_start.configure(state="disabled")
            if pi_bridge.available():
                self.btn_live.configure(state="normal")
            self.status_var.set("Transcribiendo (mismo archivo)...")

        self.root.after(0, do_restart)

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
