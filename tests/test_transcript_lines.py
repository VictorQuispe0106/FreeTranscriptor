"""Tests for the shared transcript parser and TXT renderer (feature S1, S4).

Covers `transcript_lines`: the parser must accept what both producers write
(`captions_watch.ps1` and `transcribe.py:TranscriptWriter.add`) and the TXT
export must drop the Markdown markup without losing a single utterance.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from transcript_lines import Utterance, parse_markdown, read_markdown, to_txt, write_utf8

# Real header + body shape of a Live Captions transcript.
SAMPLE = """# Transcript en vivo \u2014 2026-10-21 14:03

- **Fuente**: Windows Live Captions (UI Automation)
- **Archivo**: C:\\tmp\\x.md

---

- **[00:00:04]** Buen d\u00eda, arrancamos con el estado del proyecto.
- **[00:00:11]** El proveedor confirm\u00f3 la entrega para el jueves.
"""


class ParseMarkdownTests(unittest.TestCase):
    def test_skips_title_metadata_rules_and_blank_lines(self):
        got = parse_markdown(SAMPLE)
        self.assertEqual(
            got,
            [
                Utterance("00:00:04", "Buen d\u00eda, arrancamos con el estado del proyecto."),
                Utterance("00:00:11", "El proveedor confirm\u00f3 la entrega para el jueves."),
            ],
        )

    def test_keeps_timestamp_and_text_of_a_plain_utterance(self):
        got = parse_markdown("- **[00:01:02]** hola que tal\n")
        self.assertEqual(got, [Utterance("00:01:02", "hola que tal")])

    def test_accepts_fractional_seconds_and_normalizes_to_hh_mm_ss(self):
        got = parse_markdown("- **[00:01:02.500]** hola\n")
        self.assertEqual(got, [Utterance("00:01:02", "hola")])

    def test_keeps_text_without_a_timestamp_instead_of_dropping_it(self):
        got = parse_markdown("- texto suelto\nprosa sin bullet\n")
        self.assertEqual(got, [Utterance("", "texto suelto"), Utterance("", "prosa sin bullet")])

    def test_ignores_a_metadata_shaped_bullet(self):
        self.assertEqual(parse_markdown("- **Nota**: algo\n"), [])

    def test_empty_input_yields_no_utterances(self):
        self.assertEqual(parse_markdown(""), [])


class ToTxtTests(unittest.TestCase):
    def test_writes_one_line_per_utterance_with_bracketed_timestamp(self):
        got = to_txt(parse_markdown(SAMPLE))
        self.assertEqual(
            got,
            "[00:00:04] Buen d\u00eda, arrancamos con el estado del proyecto.\n"
            "[00:00:11] El proveedor confirm\u00f3 la entrega para el jueves.\n",
        )

    def test_hides_markdown_markup_entirely(self):
        got = to_txt(parse_markdown(SAMPLE))
        for marker in ("**", "## ", "# ", "---"):
            self.assertNotIn(marker, got)

    def test_utterance_without_timestamp_has_no_brackets(self):
        self.assertEqual(to_txt([Utterance("", "hola")]), "hola\n")

    def test_empty_input_is_an_empty_string(self):
        self.assertEqual(to_txt([]), "")


class ReadMarkdownTests(unittest.TestCase):
    def test_reads_utf8_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.md"
            path.write_text(SAMPLE, encoding="utf-8")
            self.assertEqual(read_markdown(path), parse_markdown(SAMPLE))

    def test_replaces_invalid_bytes_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.md"
            path.write_bytes(b"- **[00:00:01]** caf\xe9 con leche\n")
            got = read_markdown(path)
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].t, "00:00:01")
            self.assertIn("caf", got[0].text)


class WriteUtf8Tests(unittest.TestCase):
    def test_writes_utf8_without_bom_and_with_lf_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "t.txt"
            write_utf8(path, "hola\nchau\n")
            self.assertEqual(path.read_bytes(), "hola\nchau\n".encode("utf-8"))

    def test_creates_missing_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a" / "b" / "t.txt"
            write_utf8(path, "x")
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
