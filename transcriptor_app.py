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
from tkinter import messagebox, ttk

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(PROJECT_DIR, "captions_watch.ps1")
TRANSCRIPTS_DIR = os.path.join(PROJECT_DIR, "transcripts")
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
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.btn_open.pack(side="left")

        self.lines = tk.Text(self, height=14, wrap="word", state="disabled")
        self.lines.grid(column=0, row=2, sticky="nsew", padx=12, pady=(4, 4))
        scroll = ttk.Scrollbar(self, command=self.lines.yview)
        scroll.grid(column=0, row=2, sticky="ens", padx=(0, 2))
        self.lines.configure(yscrollcommand=scroll.set)

        status = ttk.Label(self, textvariable=self.status_var, anchor="w")
        status.grid(column=0, row=3, sticky="ew", padx=12, pady=(0, 10))

    # --- actions -------------------------------------------------------- #

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

    # --- callbacks ------------------------------------------------------ #

    def _on_line(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.lines.configure(state="normal")
        self.lines.insert("end", f"[{stamp}] {text}\n")
        self.lines.see("end")
        self.lines.configure(state="disabled")

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


def main() -> int:
    app = App(tk.Tk())
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
