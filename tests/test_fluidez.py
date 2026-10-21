"""Tests for the deterministic fluency pass (feature S2).

`fluidez.clean` is layer 1: local, offline, no IA. Every rule in the feature
document's `## Interfaces` section gets its own test, including the guardrail
that the pass may never invent words.
"""

from __future__ import annotations

import re
import unittest
from collections import Counter
from difflib import SequenceMatcher

from fluidez import clean, render_markdown
from transcript_lines import Utterance, parse_markdown


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def near(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio()


def filler(count: int) -> str:
    """`count` distinct four-character tokens joined by single spaces."""
    return " ".join(f"p{index:03d}" for index in range(count))


class NormalizationTests(unittest.TestCase):
    def test_collapses_internal_whitespace(self):
        got = clean([Utterance("00:00:01", "Buen  d\u00eda,   arrancamos")])
        self.assertEqual(got, [Utterance("00:00:01", "Buen d\u00eda, arrancamos")])

    def test_drops_empty_and_whitespace_only_texts(self):
        self.assertEqual(clean([Utterance("00:00:01", "   "), Utterance("00:00:02", "")]), [])


class DuplicateTests(unittest.TestCase):
    def test_collapses_exact_consecutive_duplicate_and_keeps_the_first_timestamp(self):
        got = clean([Utterance("00:00:01", "est\u00e1 listo"), Utterance("00:00:02", "Est\u00e1 listo")])
        self.assertEqual(got, [Utterance("00:00:01", "est\u00e1 listo")])

    def test_keeps_equal_text_that_is_not_consecutive(self):
        # Closed sentences, so rule 4 (open fragments) cannot merge them.
        got = clean(
            [
                Utterance("00:00:01", "Est\u00e1 listo."),
                Utterance("00:00:02", "Otra cosa distinta ac\u00e1."),
                Utterance("00:00:03", "Est\u00e1 listo."),
            ]
        )
        self.assertEqual(len(got), 3)

    def test_collapses_near_duplicate_consecutive_and_keeps_the_longer_text(self):
        short = "El proveedor confirm\u00f3 la entrega para el jueves"
        long = "El proveedor confirm\u00f3 la entrega para el jueves."
        self.assertGreaterEqual(near(short, long), 0.92)
        got = clean([Utterance("00:00:03", short), Utterance("00:00:04", long)])
        self.assertEqual(got, [Utterance("00:00:03", long)])

    def test_keeps_the_earlier_timestamp_even_when_the_later_text_is_longer(self):
        got = clean([Utterance("09:00:00", "texto corto"), Utterance("09:00:05", "texto corto.")])
        self.assertEqual(got[0].t, "09:00:00")

    def test_keeps_consecutive_texts_below_the_ratio_threshold(self):
        a = "El proveedor confirm\u00f3 la entrega"
        b = "Ma\u00f1ana revisamos el presupuesto"
        self.assertLess(near(a, b), 0.92)
        got = clean([Utterance("00:00:01", a), Utterance("00:00:02", b)])
        self.assertEqual(got, [Utterance("00:00:01", a), Utterance("00:00:02", b)])


class OrphanFragmentTests(unittest.TestCase):
    def test_merges_an_open_fragment_with_a_lowercase_continuation(self):
        got = clean(
            [
                Utterance("00:00:05", "El tema del presupuesto lo vemos la semana que"),
                Utterance("00:00:06", "viene con m\u00e1s detalle"),
            ]
        )
        self.assertEqual(
            got,
            [Utterance("00:00:05", "El tema del presupuesto lo vemos la semana que viene con m\u00e1s detalle")],
        )

    def test_keeps_the_first_timestamp_when_merging(self):
        got = clean(
            [
                Utterance("00:01:00", "arranca la frase que"),
                Utterance("00:01:07", "sigue ac\u00e1"),
            ]
        )
        self.assertEqual(got[0].t, "00:01:00")

    def test_does_not_merge_when_the_fragment_is_already_closed(self):
        got = clean([Utterance("00:00:01", "Est\u00e1 listo."), Utterance("00:00:02", "y arrancamos")])
        self.assertEqual(len(got), 2)

    def test_does_not_merge_when_the_next_fragment_starts_uppercase(self):
        got = clean([Utterance("00:00:01", "seguimos con el tema"), Utterance("00:00:02", "El informe est\u00e1 listo")])
        self.assertEqual(len(got), 2)

    def test_merge_stops_at_the_400_character_cap(self):
        # Distinct tokens so rule 5 (repeated words) cannot interfere.
        self.assertEqual(len(filler(64)), 319)
        self.assertEqual(len(filler(80)), 399)
        within = filler(64)  # 319 chars
        over = filler(80)    # 399 chars
        merged = clean([Utterance("00:00:01", within), Utterance("00:00:02", "hola que tal")])
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].text.endswith("hola que tal"))
        split = clean([Utterance("00:00:03", over), Utterance("00:00:04", "hola")])
        self.assertEqual(len(split), 2)


class RepeatedWordTests(unittest.TestCase):
    def test_collapses_an_immediate_stopword_repeat(self):
        self.assertEqual(clean([Utterance("00:00:01", "que que ya lo hablamos")]), [Utterance("00:00:01", "que ya lo hablamos")])

    def test_collapses_an_immediate_stopword_repeat_three_times(self):
        self.assertEqual(clean([Utterance("00:00:01", "la la la semana")]), [Utterance("00:00:01", "la semana")])

    def test_collapses_a_triple_repeat_of_any_word(self):
        self.assertEqual(clean([Utterance("00:00:01", "hola hola hola a todos")]), [Utterance("00:00:01", "hola a todos")])

    def test_keeps_a_double_repeat_of_a_non_stopword(self):
        self.assertEqual(clean([Utterance("00:00:01", "muy muy bueno")]), [Utterance("00:00:01", "muy muy bueno")])


class GuardrailTests(unittest.TestCase):
    """`clean` may only remove duplicates and repeated stopwords: never invent."""

    def test_never_invents_words(self):
        source = [
            Utterance("00:00:01", "que que ya lo hablamos"),
            Utterance("00:00:02", "muy muy bueno el informe"),
            Utterance("00:00:03", "El proveedor confirm\u00f3 la entrega"),
        ]
        before = Counter(w for u in source for w in words(u.text))
        after = Counter(w for u in clean(source) for w in words(u.text))
        self.assertLessEqual(after, before)

    def test_only_removes_the_duplicated_stopword(self):
        source = [
            Utterance("00:00:01", "que que ya lo hablamos"),
            Utterance("00:00:02", "muy muy bueno el informe"),
            Utterance("00:00:03", "El proveedor confirm\u00f3 la entrega"),
        ]
        before = Counter(w for u in source for w in words(u.text))
        after = Counter(w for u in clean(source) for w in words(u.text))
        self.assertEqual(before - after, Counter({"que": 1}))

    def test_keeps_a_word_that_only_appears_once(self):
        source = [Utterance("00:00:01", "el acuerdo es definitivo"), Utterance("00:00:02", "cerramos la reuni\u00f3n")]
        after = [w for u in clean(source) for w in words(u.text)]
        self.assertIn("definitivo", after)
        self.assertIn("reuni\u00f3n", after)


class RenderMarkdownTests(unittest.TestCase):
    def test_renders_the_same_shape_the_producers_write(self):
        got = render_markdown(
            "Transcript limpio \u2014 2026-10-21 14:03",
            [("Fuente", "Windows Live Captions (UI Automation)")],
            [Utterance("00:00:04", "Buen d\u00eda.")],
        )
        self.assertIn("# Transcript limpio \u2014 2026-10-21 14:03", got)
        self.assertIn("- **Fuente**: Windows Live Captions (UI Automation)", got)
        self.assertIn("\n---\n", got)
        self.assertIn("- **[00:00:04]** Buen d\u00eda.\n", got)

    def test_round_trips_through_the_parser(self):
        source = [Utterance("00:00:04", "Buen d\u00eda."), Utterance("00:00:11", "El proveedor confirm\u00f3.")]
        rendered = render_markdown("t", [("Fuente", "x")], source)
        self.assertEqual(parse_markdown(rendered), source)

    def test_ends_with_a_single_trailing_newline(self):
        got = render_markdown("t", [], [Utterance("00:00:04", "Buen d\u00eda.")])
        self.assertTrue(got.endswith("Buen d\u00eda.\n"))
        self.assertFalse(got.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
