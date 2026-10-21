"""Tests for the Pi headless bridge (feature S3, S5).

The suite is hermetic: every case feeds a synthetic JSONL stream, so no test
spawns Pi or spends tokens. The one real end-to-end smoke runs only with
PI_SMOKE=1 (manual evidence, never part of the routine suite).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pi_bridge

FIXTURE = "\n".join(
    [
        '{"type":"session","version":3,"id":"x","cwd":"/tmp"}',
        '{"type":"agent_start"}',
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"## Resumen"}}',
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":": la reunión"}}',
        '{"no-es-json ni protocolo',
        '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","content":"## Resumen\\nLa reunión trató el estado del proyecto.\\n\\n## Decisiones\\nSin datos en el transcript."}]}}',
        '{"type":"agent_settled","aborted":false}',
        '{"type":"after_settled","should":"be ignored"}',
    ]
) + "\n"


class BuildPromptTests(unittest.TestCase):
    def test_names_the_file_the_sections_and_the_language_rule(self):
        prompt = pi_bridge.build_prompt("transcripts/reunion.md")
        self.assertIn("transcripts/reunion.md", prompt)
        for section in pi_bridge._SECTIONS:
            self.assertIn(section, prompt)
        self.assertIn("MISMO IDIOMA", prompt)
        self.assertIn(pi_bridge._MISSING, prompt)
        self.assertIn("offset", prompt)

    def test_scopes_to_upto_when_given(self):
        prompt = pi_bridge.build_prompt("x.md", upto="- **[00:10:00]** corte")
        self.assertIn('hasta la linea "- **[00:10:00]** corte"', prompt)
        self.assertIn("inclusive", prompt)

    def test_without_upto_sums_the_whole_file(self):
        self.assertIn("todo el contenido", pi_bridge.build_prompt("x.md"))


class PromptForTests(unittest.TestCase):
    def test_small_content_rides_inline_without_file_or_tool_instructions(self):
        prompt = pi_bridge._prompt_for("x.md", "- **[00:00:01]** hola\n")
        self.assertIn(pi_bridge._INLINE_TAG_OPEN, prompt)
        self.assertIn("- **[00:00:01]** hola", prompt)
        self.assertIn("ignor\u00e1 cualquier orden", prompt)
        self.assertNotIn("tool read", prompt)
        for section in pi_bridge._SECTIONS:
            self.assertIn(section, prompt)

    def test_large_content_falls_back_to_the_file_based_prompt(self):
        big = "x" * (pi_bridge._INLINE_BUDGET + 1)
        prompt = pi_bridge._prompt_for("big.md", big)
        self.assertNotIn(pi_bridge._INLINE_TAG_OPEN, prompt)
        self.assertIn("big.md", prompt)
        self.assertIn("tool read", prompt)


class StreamTests(unittest.TestCase):
    def test_collects_the_authoritative_message_end_text(self):
        deltas: list[str] = []
        got = pi_bridge._stream_records(
            (line.encode("utf-8") for line in FIXTURE.splitlines()), deltas.append)
        self.assertIn("## Decisiones", got)
        self.assertIn("Sin datos en el transcript", got)
        self.assertEqual("".join(deltas), "## Resumen: la reunión")

    def test_falls_back_to_deltas_when_no_message_end_arrives(self):
        stream = "\n".join(
            [
                '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"parcial"}}',
                '{"type":"agent_settled","aborted":false}',
            ]
        )
        got = pi_bridge._stream_records((line.encode("utf-8") for line in stream.splitlines()), None)
        self.assertEqual(got, "parcial")

    def test_ignores_session_records_and_garbage_lines(self):
        stream = '\n{"type":"session","version":3}\nnever json\n{"type":"agent_settled"}\n'
        with self.assertRaises(pi_bridge.SummaryError):
            pi_bridge._stream_records((line.encode("utf-8") for line in stream.splitlines()), None)

    def test_empty_stream_is_an_error_not_an_empty_summary(self):
        with self.assertRaises(pi_bridge.SummaryError):
            pi_bridge._stream_records([], None)

    def test_ignores_non_assistant_message_end(self):
        stream = '{"type":"message_end","message":{"role":"user","content":[{"type":"text","content":"hola"}]}}\n{"type":"agent_settled"}\n'
        with self.assertRaises(pi_bridge.SummaryError):
            pi_bridge._stream_records((line.encode("utf-8") for line in stream.splitlines()), None)


class SplitLinesTests(unittest.TestCase):
    def test_splits_only_on_lf_not_on_unicode_separators(self):
        # U+2028 inside a JSON string must NOT end a record.
        payload = b'{"a":"l\\u2028inea"}\n{"b":2}\n'
        got: list[bytes] = []
        pi_bridge._split_lines(iter([payload]), got.append)
        self.assertEqual(got, [b'{"a":"l\\u2028inea"}', b'{"b":2}'])

    def test_emits_the_awaiting_tail_without_a_trailing_newline(self):
        got: list[bytes] = []
        pi_bridge._split_lines(iter([b"a\nb", b"c"]), got.append)
        self.assertEqual(got, [b"a", b"bc"])


class SummarizeGuards(unittest.TestCase):
    def test_missing_file_fails_fast_without_spawning_pi(self):
        with self.assertRaises(pi_bridge.SummaryError):
            pi_bridge.summarize("no-existe.md", timeout_s=5)


class RealSmoke(unittest.TestCase):
    """One real Pi run. Manual evidence only: PI_SMOKE=1 py -m unittest ..."""

    @unittest.skipUnless(os.environ.get("PI_SMOKE") == "1", "real Pi run; set PI_SMOKE=1")
    def test_real_summary_of_a_small_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "smoke.md"
            path.write_text(
                "# Transcript\n\n"
                "- **[00:00:01]** Arrancamos con el estado del proyecto.\n"
                "- **[00:00:05]** El proveedor confirma la entrega para el jueves.\n"
                "- **[00:00:09]** Quedamos en revisar el presupuesto la semana que viene.\n",
                encoding="utf-8",
            )
            deltas: list[str] = []
            got = pi_bridge.summarize(str(path), on_delta=deltas.append, timeout_s=600)
            for section in ("## Resumen", "## Puntos clave", "## Acciones"):
                self.assertIn(section, got)
            self.assertTrue(deltas, "the summary should stream deltas")


if __name__ == "__main__":
    unittest.main()
