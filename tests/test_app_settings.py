"""Tests for the persisted app settings store (S1) and the auto-summary
decision helper shared by the tkinter app and the web GUI (S2).

The store is one JSON file with atomic writes; a missing or corrupt file
falls back to defaults so neither front ever crashes on startup.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

app_settings = importlib.import_module("app_settings")


class _TempConfig(unittest.TestCase):
    """Point app_settings.CONFIG_PATH at a temp file for every test."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="transcriptor-settings-")
        self.config_path = os.path.join(self.tmp_dir, "transcriptor_config.json")
        self._old_path = app_settings.CONFIG_PATH
        app_settings.CONFIG_PATH = self.config_path

    def tearDown(self) -> None:
        app_settings.CONFIG_PATH = self._old_path
        shutil.rmtree(self.tmp_dir, ignore_errors=True)


class SettingsStoreTests(_TempConfig):

    def test_default_auto_summary_is_off(self) -> None:
        self.assertFalse(app_settings.get_bool("auto_summary"))

    def test_set_bool_persists_across_reads(self) -> None:
        app_settings.set_bool("auto_summary", True)
        self.assertTrue(app_settings.get_bool("auto_summary"))
        # A fresh load from disk must agree (real persistence, not memory).
        on_disk = json.loads(
            open(self.config_path, encoding="utf-8").read())
        self.assertIs(on_disk["auto_summary"], True)

    def test_corrupt_config_falls_back_to_default(self) -> None:
        with open(self.config_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertFalse(app_settings.get_bool("auto_summary"))
        # And the store recovers: a write after corruption still works.
        app_settings.set_bool("auto_summary", True)
        self.assertTrue(app_settings.get_bool("auto_summary"))

    def test_atomic_write_leaves_no_temp_files(self) -> None:
        app_settings.set_bool("auto_summary", True)
        leftovers = [f for f in os.listdir(self.tmp_dir)
                     if f != "transcriptor_config.json"]
        self.assertEqual(leftovers, [])


class AutoSummaryDueTests(unittest.TestCase):
    """S2: the only decision inputs are toggle, transcript and Pi."""

    def test_true_only_with_all_conditions(self) -> None:
        from transcriptor_app import auto_summary_due

        existing = os.path.join(self._tmp(), "x.md")
        with open(existing, "w", encoding="utf-8"):
            pass
        self.assertTrue(auto_summary_due(True, existing, True))
        self.assertFalse(auto_summary_due(False, existing, True))
        self.assertFalse(auto_summary_due(True, "", True))
        self.assertFalse(auto_summary_due(True, os.path.join(self._tmp(),
                                                             "nope.md"), True))
        self.assertFalse(auto_summary_due(True, existing, False))

    def _tmp(self) -> str:
        if not hasattr(self, "_dir"):
            self._dir = tempfile.mkdtemp(prefix="transcriptor-due-")
        return self._dir


if __name__ == "__main__":
    unittest.main()
