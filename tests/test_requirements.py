"""Replicability guard: requirements.txt must pin the verified set.

The launchers install exactly this file into .venv on a stranger's machine,
so every direct import of the project must be present with a == pin.
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REQUIREMENTS = os.path.join(ROOT, "requirements.txt")

EXPECTED = {
    "numpy",          # imported directly by transcribe.py
    "faster-whisper",
    "ctranslate2",
    "scipy",
    "av",
    "soundcard",
    "PyAudioWPatch",  # imported as pyaudiowpatch
}


def _pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    with open(REQUIREMENTS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, _, version = line.partition("==")
            pins[name.strip()] = version.strip()
    return pins


class RequirementsTests(unittest.TestCase):

    def test_every_requirement_is_pinned(self) -> None:
        pins = _pins()
        self.assertTrue(pins, "requirements.txt is empty")
        for name, version in pins.items():
            self.assertTrue(version, f"{name} has no == pin")

    def test_pinned_set_matches_the_direct_imports(self) -> None:
        self.assertEqual(set(_pins()), EXPECTED)


if __name__ == "__main__":
    unittest.main()
