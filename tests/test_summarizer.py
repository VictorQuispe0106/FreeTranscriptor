"""Tests for the pluggable summary engine (summarizer.py, S1-S4).

Detection order and the two stream parsers are asserted deterministically
with synthetic streams; no Ollama, no network, no Pi.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

summarizer = importlib.import_module("summarizer")
app_settings = importlib.import_module("app_settings")
pi_bridge = importlib.import_module("pi_bridge")


def _settings(**overrides):
    """Isolated config + no Pi: the machine's real engines must not leak in.

    Returns (stop_callable, tmp_dir); the caller MUST stop() in cleanup,
    otherwise the global pi_bridge.available mock leaks into other test
    modules during discovery.
    """
    tmp = tempfile.mkdtemp(prefix="transcriptor-engines-")
    patchers = [
        unittest.mock.patch.object(
            app_settings, "CONFIG_PATH", os.path.join(tmp, "config.json")),
        unittest.mock.patch.object(
            pi_bridge, "available", return_value=False),
    ]
    for patcher in patchers:
        patcher.start()

    def stop() -> None:
        for patcher in reversed(patchers):
            patcher.stop()

    for key, value in overrides.items():
        app_settings.set(key, value)
    return stop, tmp


class OllamaStreamTests(unittest.TestCase):

    def test_parses_deltas_and_done(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Hola "}}),
            json.dumps({"message": {"role": "assistant", "content": "mundo."}}),
            json.dumps({"done": True}),
        ]
        deltas: list[str] = []
        text = summarizer._consume_ollama_stream(
            [l.encode() + b"\n" for l in lines],
            on_delta=deltas.append)
        self.assertEqual(text, "Hola mundo.")
        self.assertEqual(deltas, ["Hola ", "mundo."])

    def test_error_record_raises(self):
        lines = [json.dumps({"error": "model 'x' not found"}).encode() + b"\n"]
        with self.assertRaises(pi_bridge.SummaryError):
            summarizer._consume_ollama_stream(lines)

    def test_empty_stream_is_an_error(self):
        with self.assertRaises(pi_bridge.SummaryError):
            summarizer._consume_ollama_stream([])

    def test_cancel_between_lines(self):
        lines = [json.dumps({"message": {"content": "x"}}).encode() + b"\n"]
        cancel = __import__("threading").Event()
        cancel.set()
        with self.assertRaises(pi_bridge.SummaryError):
            summarizer._consume_ollama_stream(lines, cancel_event=cancel)


class CloudStreamTests(unittest.TestCase):

    def test_parses_sse_deltas_and_done(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"Resumen"}}]}',
            b'data: {"choices":[{"delta":{"content":" corto"}}]}',
            b"data: [DONE]",
        ]
        text = summarizer._consume_cloud_stream(lines)
        self.assertEqual(text, "Resumen corto")

    def test_ignores_garbage_and_keepalives(self):
        lines = [b": keepalive", b"data: not-json",
                 b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                 b"data: [DONE]"]
        self.assertEqual(summarizer._consume_cloud_stream(lines), "ok")

    def test_empty_stream_is_an_error(self):
        with self.assertRaises(pi_bridge.SummaryError):
            summarizer._consume_cloud_stream([])


class DetectionTests(unittest.TestCase):

    def setUp(self):
        self._stop, self._tmp = _settings()
        self.addCleanup(self._stop)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def test_no_engine_detects_none(self):
        self.assertEqual(summarizer.detect_backend(), "none")

    def test_cloud_key_enables_cloud(self):
        app_settings.set("cloud_base_url", "https://api.example.com/v1")
        app_settings.set("cloud_api_key", "sk-test")
        engines = summarizer.available_engines()
        self.assertTrue(engines["cloud"])
        self.assertEqual(summarizer.detect_backend(),
                         "pi" if engines["pi"] else "cloud")

    def test_cloud_needs_both_url_and_key(self):
        app_settings.set("cloud_base_url", "https://api.example.com/v1")
        self.assertFalse(summarizer.available_engines()["cloud"])

    def test_ollama_detection_needs_the_model_installed(self):
        with unittest.mock.patch.object(
                summarizer, "OLLAMA_HOST", "http://127.0.0.1:1"):
            self.assertIsNone(summarizer.ollama_ready())

    def test_summarize_without_engine_fails_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.md")
            open(path, "w", encoding="utf-8").write("- **[00:00:01]** hola\n")
            with self.assertRaises(pi_bridge.SummaryError):
                summarizer.summarize(path)


if __name__ == "__main__":
    unittest.main()
