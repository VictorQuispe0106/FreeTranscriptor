"""Deterministic fluency pass for transcript text (feature S2).

Layer 1 of the fluency work: local, offline, no IA. It removes what the live
caption buffer duplicates and re-emits, joins phrases that were split mid
sentence, and collapses repeated stopwords. It never rewrites content: the
guardrail test asserts that no word is invented and that only duplicated
material is dropped.

The raw transcript stays untouched; this module produces a derived file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from transcript_lines import Utterance

#: Consecutive lines at or above this similarity are the same one, rewritten.
NEAR_DUPLICATE_RATIO = 0.92
#: A merged phrase is not allowed to grow past this length.
MERGE_CHAR_CAP = 400
#: Text ending in one of these already closes its sentence.
CLOSED_ENDINGS = (".", "?", "!", "\u2026", ":", '"', "\u00bb", ")")
#: Punctuation that may open a quoted sentence ('"¿Y ahora?').
LEADING_JUNK = "\"'\u00ab\u00bf\u00a1("
#: Words whose immediate repetition is a caption artifact, never speech.
STOPWORDS = frozenset(
    "de la que el y a en es un se no lo con por para una los las del".split()
)
#: The same word repeated immediately: '(\w+)(\s+\1)+'.
_REPEATED_WORD = re.compile(r"\b([^\W\d_]+)(?:\s+\1\b)+", re.IGNORECASE | re.UNICODE)


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left.casefold(), right.casefold()).ratio()


def _starts_lowercase(text: str) -> bool:
    stripped = text.lstrip(LEADING_JUNK)
    return bool(stripped) and stripped[0].islower()


def _collapse_duplicates(items: list[Utterance]) -> list[Utterance]:
    """Rules 2 and 3: drop the consecutive lines that repeat the previous one."""
    kept: list[Utterance] = []
    for item in items:
        if kept:
            previous = kept[-1]
            if previous.text.casefold() == item.text.casefold():
                continue
            if _ratio(previous.text, item.text) >= NEAR_DUPLICATE_RATIO:
                # Live Captions rewrites its growing tail: keep the fuller text
                # but never move the timestamp forward.
                longer = item.text if len(item.text) > len(previous.text) else previous.text
                kept[-1] = Utterance(previous.t, longer)
                continue
        kept.append(item)
    return kept


def _merge_fragments(items: list[Utterance]) -> list[Utterance]:
    """Rule 4: join a phrase split mid sentence with its continuation."""
    merged: list[Utterance] = []
    for item in items:
        if merged:
            previous = merged[-1]
            candidate = f"{previous.text} {item.text}"
            if (
                not previous.text.endswith(CLOSED_ENDINGS)
                and _starts_lowercase(item.text)
                and len(candidate) <= MERGE_CHAR_CAP
            ):
                merged[-1] = Utterance(previous.t, candidate)
                continue
        merged.append(item)
    return merged


def _collapse_repeats(text: str) -> str:
    """Rule 5: collapse an immediately repeated stopword, or any word 3+ times."""

    def replacement(match: re.Match[str]) -> str:
        word = match.group(1)
        occurrences = len(match.group(0).split())
        if word.casefold() in STOPWORDS or occurrences >= 3:
            return word
        return match.group(0)

    return _REPEATED_WORD.sub(replacement, text)


def clean(utterances: Sequence[Utterance]) -> list[Utterance]:
    """Apply the deterministic fluency rules, in the order the spec pins."""
    items = [Utterance(item.t, " ".join(item.text.split())) for item in utterances]
    items = [item for item in items if item.text]
    items = _collapse_duplicates(items)
    items = _merge_fragments(items)
    return [Utterance(item.t, _collapse_repeats(item.text)) for item in items]


def render_markdown(
    title: str,
    meta: Sequence[tuple[str, str]],
    utterances: Sequence[Utterance],
) -> str:
    """Render utterances with the same Markdown shape the producers write."""
    parts = [f"# {title}", ""]
    for key, value in meta:
        parts.append(f"- **{key}**: {value}")
    if meta:
        parts.append("")
    parts.append("---")
    parts.append("")
    for item in utterances:
        parts.append(f"- **[{item.t}]** {item.text}" if item.t else f"- {item.text}")
    return "\n".join(parts) + "\n"
