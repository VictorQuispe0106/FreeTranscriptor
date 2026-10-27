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
        from transcriptor_core import list_transcripts

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
        from transcriptor_core import list_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list_transcripts(tmp), [])
        self.assertEqual(list_transcripts(os.path.join(tmp, "nope")), [])


class SearchTranscriptsTests(unittest.TestCase):
    """S3: global history search across session transcripts, newest first."""

    def _write(self, path: str, text: str, mtime: float) -> None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.utime(path, (mtime, mtime))

    def test_matches_across_sessions_newest_first_case_insensitive(self):
        from transcriptor_core import search_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self._write(os.path.join(tmp, "a_live-captions.md"),
                        "# T\n\n- **[00:00:01]** hola\n"
                        "- **[00:00:05]** palo alto firewall\n", 1000)
            self._write(os.path.join(tmp, "b_live-captions.md"),
                        "# T\n\n- **[00:01:00]** revisar PALO ALTO\n", 2000)
            self._write(os.path.join(tmp, "b_live-captions.limpio.md"),
                        "- **[00:00:01]** palo alto derivado\n", 3000)

            found = search_transcripts(tmp, "palo")

        self.assertEqual([item["name"] for item in found],
                         ["b_live-captions.md", "a_live-captions.md"])
        self.assertEqual(found[0]["matches"],
                         [{"stamp": "00:01:00", "text": "revisar PALO ALTO"}])
        self.assertEqual(found[1]["matches"],
                         [{"stamp": "00:00:05", "text": "palo alto firewall"}])

    def test_derived_files_and_snapshots_never_match(self):
        from transcriptor_core import search_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self._write(os.path.join(tmp, "a_live-captions.md"),
                        "- **[00:00:01]** hola\n", 1000)
            self._write(os.path.join(tmp, "a_live-captions.resumen.md"),
                        "palo alto en el resumen\n", 1100)
            self._write(os.path.join(tmp, "a_live-captions.limpio.md"),
                        "palo alto en el limpio\n", 1200)
            self._write(os.path.join(tmp, "notas.txt"), "palo alto txt\n", 1300)
            os.makedirs(os.path.join(tmp, ".snapshots"))
            self._write(os.path.join(tmp, ".snapshots", "deep.md"),
                        "palo alto snapshot\n", 1400)

            found = search_transcripts(tmp, "palo")

        self.assertEqual(found, [])

    def test_limit_caps_total_matches(self):
        from transcriptor_core import search_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self._write(os.path.join(tmp, "a_live-captions.md"),
                        "".join(f"- **[00:00:{i:02d}]** hola {i}\n"
                                for i in range(10)), 1000)
            self._write(os.path.join(tmp, "b_live-captions.md"),
                        "".join(f"- **[00:01:{i:02d}]** hola {i}\n"
                                for i in range(10)), 2000)

            found = search_transcripts(tmp, "hola", limit=12)

        total = sum(len(item["matches"]) for item in found)
        self.assertEqual(total, 12)
        # Newest session keeps its 10; the older one gets cut at the cap.
        self.assertEqual(len(found[0]["matches"]), 10)
        self.assertEqual(len(found[1]["matches"]), 2)

    def test_empty_needle_and_missing_directory_yield_nothing(self):
        from transcriptor_core import search_transcripts

        with tempfile.TemporaryDirectory() as tmp:
            self._write(os.path.join(tmp, "a_live-captions.md"),
                        "- **[00:00:01]** hola\n", 1000)
            self.assertEqual(search_transcripts(tmp, "   "), [])
            self.assertEqual(search_transcripts(tmp, ""), [])
        self.assertEqual(search_transcripts(os.path.join(tmp, "nope"), "h"), [])


class WatcherSessionStartTests(unittest.TestCase):
    """S5/S6: the watcher process is spawned with the exact pinned options."""

    def _spawn_capture(self):
        import transcriptor_core

        recorded = {}

        class FakeProc:
            stdout = []
            stderr = None
            poll = lambda self: 0
            wait = lambda self, timeout=None: 0
            terminate = lambda self: None
            kill = lambda self: None

        original = transcriptor_core.subprocess.Popen

        def fake(cmd, *args, **kwargs):
            recorded["cmd"] = cmd
            return FakeProc()

        transcriptor_core.subprocess.Popen = fake
        try:
            session = transcriptor_core.WatcherSession(
                lambda text: None, lambda status: None, lambda message: None)
            session.start(out_path="\\somewhere\\out.md", captions_pid=1234)
            session.stop()
        finally:
            transcriptor_core.subprocess.Popen = original
        return session, recorded["cmd"]

    def test_custom_out_path_is_used(self):
        session, cmd = self._spawn_capture()
        self.assertEqual(session.out_path, "\\somewhere\\out.md")
        self.assertIn("-Out", cmd)
        self.assertIn("\\somewhere\\out.md", cmd)

    def test_pinned_pid_is_forwarded(self):
        _session, cmd = self._spawn_capture()
        self.assertIn("-CaptionsPid", cmd)
        self.assertEqual(cmd[cmd.index("-CaptionsPid") + 1], "1234")

    def test_no_pin_when_pid_unknown(self):
        import transcriptor_core

        recorded = {}

        class FakeProc:
            stdout = []
            stderr = None
            poll = lambda self: 0
            wait = lambda self, timeout=None: 0
            terminate = lambda self: None
            kill = lambda self: None

        original = transcriptor_core.subprocess.Popen

        def fake(cmd, *args, **kwargs):
            recorded["cmd"] = cmd
            return FakeProc()

        transcriptor_core.subprocess.Popen = fake
        try:
            session = transcriptor_core.WatcherSession(
                lambda text: None, lambda status: None, lambda message: None)
            session.start(captions_pid=0)
            session.stop()
        finally:
            transcriptor_core.subprocess.Popen = original
        self.assertNotIn("-CaptionsPid", recorded["cmd"])


class WallClockStampTests(unittest.TestCase):
    """S4: the parser keeps lines stamped with real time, not elapsed time."""

    def test_wall_clock_stamp_survives_a_full_round_trip(self):
        from transcript_lines import parse_markdown

        lines = parse_markdown("- **[14:03:12]** La entrega es el jueves.\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].t, "14:03:12")
        self.assertEqual(lines[0].text, "La entrega es el jueves.")


if __name__ == "__main__":
    unittest.main()
