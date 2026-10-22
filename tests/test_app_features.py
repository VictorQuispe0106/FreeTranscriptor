"""Feature tests for the usability slice (feature S1, S2, S3).

S1: the GUI history lists *session* transcripts (raw *_live-captions.md),
newest first, from any transcript directory, without pulling derived files
(.limpio.md, .resumen.md) or the .snapshots/ subfolder.

S2: the decision "did the user already read the end?" is the only input the
autoplay logic needs; it must be true exactly at the bottom.
"""

from __future__ import annotations

import os
import tempfile
import unittest


class ListTranscriptsTests(unittest.TestCase):
    """S1: session transcript discovery for the history row."""

    def _touch(self, path: str, mtime: float) -> None:
        with open(path, "w", encoding="utf-8"):
            pass
        os.utime(path, (mtime, mtime))

    def test_lists_only_session_md_newest_first(self):
        from transcriptor_app import list_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self._touch(os.path.join(tmp, "a_live-captions.md"), 1000)
            self._touch(os.path.join(tmp, "b_live-captions.md"), 2000)
            self._touch(os.path.join(tmp, "b_live-captions.limpio.md"), 3000)
            self._touch(os.path.join(tmp, "b_live-captions.resumen.md"), 3100)
            self._touch(os.path.join(tmp, "notes.txt"), 3200)
            os.makedirs(os.path.join(tmp, ".snapshots"))
            self._touch(os.path.join(tmp, ".snapshots", "deep.md"), 4000)
            os.makedirs(os.path.join(tmp, "subdir"))
            self._touch(os.path.join(tmp, "subdir", "c_live-captions.md"), 5000)

            found = list_transcripts(tmp)

        self.assertEqual([name for name, _ in found],
                         ["b_live-captions.md", "a_live-captions.md"])

    def test_empty_and_missing_directory_yield_nothing(self):
        from transcriptor_app import list_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_transcripts(tmp), [])
        self.assertEqual(list_transcripts(os.path.join(tmp, "nope")), [])


class AtEndTests(unittest.TestCase):
    """S2: scroll-down summary: the tail is only followed when already at the end."""

    def test_at_bottom_is_true(self):
        from transcriptor_app import at_end

        self.assertTrue(at_end(0.99, 1.0))
        self.assertTrue(at_end(1.0, 1.0))

    def test_scrolled_up_is_false(self):
        from transcriptor_app import at_end

        self.assertFalse(at_end(0.4, 0.8))
        self.assertFalse(at_end(0.0, 0.0))


class TranscriptCopyTests(unittest.TestCase):
    """S3: copy puts exactly the displayed meeting text on the clipboard."""

    def test_copy_strips_virtual_marker_and_trailing_newline(self):
        from transcriptor_app import copyable_line

        self.assertEqual(copyable_line("[12:00:01] hola\n"), "[12:00:01] hola")
        self.assertEqual(copyable_line("[12:00:01] \u25b8 aho\n"),
                         "[12:00:01] \u25b8 aho")

    def test_copy_accepts_blank_widget(self):
        from transcriptor_app import copyable_line

        self.assertEqual(copyable_line(""), "")
        self.assertEqual(copyable_line("\n"), "")


if __name__ == "__main__":
    unittest.main()
