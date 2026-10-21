"""Shared parsing and rendering for transcript Markdown files (feature S1, S4).

Both producers write the same line shape::

    - **[hh:mm:ss]** texto

Windows Live Captions (``captions_watch.ps1``) and the faster-whisper engine
(``transcribe.py:TranscriptWriter.add``) agree on it, so one parser serves both.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]

# '- **[00:00:04]** texto' -- fractional seconds are accepted and dropped.
_BULLET = re.compile(r"^\s*-\s+\*\*\[([0-9:.,]+)\]\*\*\s*(.*)$")
# '- **Fuente**: valor' -- the header metadata block both producers write.
_META = re.compile(r"^\s*-\s+\*\*[^*\[\]]+\*\*\s*:")
# '---' separators between the header block and the utterances.
_RULE = re.compile(r"^-{3,}$")
# Leading hh:mm:ss of a stamp, so '00:01:02.500' normalizes to '00:01:02'.
_STAMP = re.compile(r"^\d{1,2}:\d{2}:\d{2}")


@dataclass(frozen=True)
class Utterance:
    """One spoken line: a normalized ``hh:mm:ss`` stamp plus its text."""

    t: str
    text: str


def _normalize_stamp(raw: str) -> str:
    match = _STAMP.match(raw)
    return match.group(0) if match else raw


def parse_markdown(text: str) -> list[Utterance]:
    """Parse transcript Markdown into utterances, in document order.

    Headers, metadata, ``---`` rules and blank lines are skipped. A line that
    carries text but no timestamp is kept as an utterance with ``t == ""``:
    dropping text is worse than losing its timestamp.
    """
    utterances: list[Utterance] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or _RULE.match(line):
            continue
        match = _BULLET.match(line)
        if match:
            body = match.group(2).strip()
            if body:
                utterances.append(Utterance(_normalize_stamp(match.group(1)), body))
            continue
        if _META.match(line):
            continue
        body = line[1:].strip() if line.startswith("-") else line
        if body:
            utterances.append(Utterance("", body))
    return utterances


def read_markdown(path: PathLike) -> list[Utterance]:
    """Read a transcript file as UTF-8 (invalid bytes replaced) and parse it."""
    return parse_markdown(Path(path).read_bytes().decode("utf-8", errors="replace"))


def to_txt(utterances: Sequence[Utterance]) -> str:
    """Render utterances as plain text: one ``[hh:mm:ss] texto`` line each.

    The Markdown markup is gone, so the result can be pasted anywhere. An
    utterance without a timestamp is written as bare text.
    """
    if not utterances:
        return ""
    lines = [f"[{item.t}] {item.text}" if item.t else item.text for item in utterances]
    return "\n".join(lines) + "\n"


def write_utf8(path: PathLike, text: str) -> None:
    """Write UTF-8 without BOM, LF endings, creating parent directories."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
