"""Shared core of the Transcriptor en vivo web app (no GUI code here).

Live Captions pipeline, transcript files and history/search helpers:

  launch_livecaptions() -> WatcherSession (captions_watch.ps1, UI Automation)
  -> transcript md files -> list_transcripts / search_transcripts

The web GUI (web_gui.py) is the only front; the tkinter app was removed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fluidez import clean
from transcript_lines import parse_markdown, read_markdown, to_txt, write_utf8

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHER = os.path.join(PROJECT_DIR, "captions_watch.ps1")
TRANSCRIPTS_DIR = os.path.join(PROJECT_DIR, "transcripts")
SNAPSHOTS_DIR = os.path.join(TRANSCRIPTS_DIR, ".snapshots")
LIVE_CAPTURES = os.path.join(os.environ.get("WINDIR", r"C:\\Windows"),
                             "System32", "LiveCaptions.exe")


def auto_summary_due(enabled: bool, transcript: str, pi_ok: bool) -> bool:
    """S2: the only decision to auto-summarize on stop (shared with web)."""
    return bool(enabled and pi_ok and transcript
                and os.path.exists(transcript))

# PowerShell 5.1 with a redirected stdout emits the OEM codepage; decode
# defensively. The .md file is always written by the watcher as UTF-8.
_DECODE = dict(encoding="utf-8", errors="replace")
_HISTORY_SUFFIX = "_live-captions.md"

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


def search_transcripts(directory: str, needle: str, limit: int = 200) -> list[dict]:
    """S3: global full-text search across session transcripts.

    Scans the same sessions ``list_transcripts`` returns (newest first, raw
    ``*_live-captions.md`` only) and matches ``needle`` case-insensitively
    against every utterance. Returns ``[{name, matches: [{stamp, text}]}]``
    with at most ``limit`` matches in total, newest session first.
    """
    needle = (needle or "").strip().lower()
    if not needle:
        return []
    results: list[dict] = []
    total = 0
    for name, path in list_transcripts(directory):
        matches: list[dict] = []
        try:
            utterances = read_markdown(path)
        except OSError:
            continue
        for utterance in utterances:
            if needle not in utterance.text.lower():
                continue
            matches.append({"stamp": utterance.t, "text": utterance.text})
            total += 1
            if total >= limit:
                break
        if matches:
            results.append({"name": name, "matches": matches})
        if total >= limit:
            break
    return results


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
            # '- growing tail' -> the caption that is still being typed.
            if line.startswith("> "):
                if self.on_tail:
                    self.on_tail(line[2:])
                continue
            # Watcher console noise (never transcript content): the run's
            # own bookkeeping lines must not become transcript rows.
            if (line.startswith(("source :", "output :", "reading -",
                                 "resume  :"))
                    or re.match(r"^\d+ line\(s\) -> ", line)):
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
