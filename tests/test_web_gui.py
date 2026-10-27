"""Hermetic tests for the web GUI server (no real LiveCaptions, no network).

Every test builds an in-process server on an ephemeral port via
``web_gui.build_server(port=0)`` and speaks plain ``http.client`` to it.
``web_gui.TRANSCRIPTS_DIR`` is pointed at a temporary directory BEFORE the
server is built because ``Orchestrator`` and ``Handler`` both read that
module global at call time.
"""

from __future__ import annotations

import http.client
import importlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

web_gui = importlib.import_module("web_gui")
transcriptor_core = importlib.import_module("transcriptor_core")
app_settings = importlib.import_module("app_settings")
pi_bridge = importlib.import_module("pi_bridge")


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _Restore:
    """Context manager restoring module attributes even on test failure."""

    def __init__(self, module: object, **overrides: str) -> None:
        self._module = module
        self._new = overrides
        self._old: dict[str, object] = {}

    def __enter__(self):
        self._old = {k: getattr(self._module, k) for k in self._new}
        for k, v in self._new.items():
            setattr(self._module, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            setattr(self._module, k, v)
        return False


class WebGuiServerTestCase(unittest.TestCase):
    """Shared server fixtures: temp transcripts dir + live server."""

    def setUp(self) -> None:
        base = tempfile.mkdtemp(prefix="transcriptor-web-test-")
        self.tmp_dir = base
        self.transcripts_dir = os.path.join(base, "transcripts")
        os.makedirs(self.transcripts_dir, exist_ok=True)
        # Real history markdown file with a valid utterance line.
        self.history_md = "2026_01_28_test_live-captions.md"
        self._write_utf8(os.path.join(self.transcripts_dir, self.history_md),
                         "# Transcriptor\n\n- **[00:00:01]** hola\n")
        self._restore = _Restore(
            web_gui, TRANSCRIPTS_DIR=self.transcripts_dir)
        self._restore.__enter__()
        port = _free_port()
        self.server = web_gui.build_server(port=port)
        self.server.daemon_threads = True  # no hung SSE handler threads
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            daemon=True)
        self.thread.start()
        host, bound = self.server.server_address[:2]
        self.addr = (host, bound)

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            self.thread.join(timeout=5)
            self._restore.__exit__()
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # --- small helpers ----------------------------------------------------- #

    def _write_utf8(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def _get(self, path: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection(*self.addr, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def _post(self, path: str, body: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection(*self.addr, timeout=10)
        try:
            conn.request(
                "POST", path, body=body,
                headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()


class TestGetIndex(WebGuiServerTestCase):

    def test_get_index_serves_stitch_html_without_fakes(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Transcriptor", body)
        # Removed fictional widgets must never come back in the served HTML.
        self.assertNotIn("VU IN", body)
        self.assertNotIn("#1024-LIVE", body)


class TestGetState(WebGuiServerTestCase):

    def test_get_state_reports_history(self) -> None:
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertIn("history", state)
        self.assertIsInstance(state["history"], list)
        self.assertIn(self.history_md, state["history"])


class TestAutoSummarySetting(WebGuiServerTestCase):
    """S1: the toggle is visible in state and persists via one action."""

    def setUp(self) -> None:
        super().setUp()
        self._config = _Restore(
            app_settings,
            CONFIG_PATH=os.path.join(self.tmp_dir, "transcriptor_config.json"))
        self._config.__enter__()

    def tearDown(self) -> None:
        self._config.__exit__()
        super().tearDown()

    def test_state_exposes_auto_summary_default_off(self) -> None:
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["autoSummary"], False)

    def test_set_auto_summary_persists_and_reports(self) -> None:
        status, body = self._post(
            "/api/action", json.dumps({"action": "set-auto-summary",
                                        "enabled": True}))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertTrue(app_settings.get_bool("auto_summary"))
        status, body = self._get("/api/state")
        self.assertIs(json.loads(body)["autoSummary"], True)

    def test_set_auto_summary_accepts_false(self) -> None:
        app_settings.set_bool("auto_summary", True)
        status, body = self._post(
            "/api/action", json.dumps({"action": "set-auto-summary",
                                        "enabled": False}))
        self.assertTrue(json.loads(body)["ok"])
        self.assertFalse(app_settings.get_bool("auto_summary"))


class TestStopTriggersAutoSummary(WebGuiServerTestCase):
    """S2: stopping a session with the toggle on starts the Pi summary."""

    def setUp(self) -> None:
        super().setUp()
        self._config = _Restore(
            app_settings,
            CONFIG_PATH=os.path.join(self.tmp_dir, "transcriptor_config.json"))
        self._config.__enter__()

    def tearDown(self) -> None:
        self._config.__exit__()
        super().tearDown()

    def test_stop_with_toggle_on_runs_summary(self) -> None:
        orch = web_gui.Handler.orchestrator
        orch.last_transcript = os.path.join(self.transcripts_dir,
                                            self.history_md)
        orch.session = _FakeSession()
        calls: list[tuple] = []
        original = orch.run_summary
        orch.run_summary = (  # type: ignore[method-assign]
            lambda *a, **k: calls.append(a) or "")
        try:
            app_settings.set_bool("auto_summary", True)
            orch.stop()
        finally:
            orch.run_summary = original  # type: ignore[method-assign]
        self.assertEqual(calls, [()])
        self.assertIsNone(orch.session)

    def test_stop_with_toggle_off_does_not_summarize(self) -> None:
        orch = web_gui.Handler.orchestrator
        orch.last_transcript = os.path.join(self.transcripts_dir,
                                            self.history_md)
        orch.session = _FakeSession()
        calls: list[tuple] = []
        original = orch.run_summary
        orch.run_summary = (  # type: ignore[method-assign]
            lambda *a, **k: calls.append(a) or "")
        try:
            orch.stop()
        finally:
            orch.run_summary = original  # type: ignore[method-assign]
        self.assertEqual(calls, [])


class _FakeSession:
    """Minimal WatcherSession double: stop() must be callable exactly."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self, timeout_s: float = 5.0) -> None:
        self.stopped = True


class TestHistorySearch(WebGuiServerTestCase):
    """S3/S4: one GET returns every match, session newest first."""

    def _search(self, query: str) -> tuple[int, dict]:
        status, body = self._get(
            "/api/historial-search?q=" + urllib.parse.quote(query))
        return status, json.loads(body)

    def test_search_returns_matches_from_history(self) -> None:
        status, out = self._search("hola")
        self.assertEqual(status, 200)
        self.assertEqual(out["items"][0]["name"], self.history_md)
        self.assertEqual(out["items"][0]["matches"],
                         [{"stamp": "00:00:01", "text": "hola"}])

    def test_search_without_needle_is_empty(self) -> None:
        status, out = self._search("")
        self.assertEqual(status, 200)
        self.assertEqual(out["items"], [])

    def test_search_with_no_hits_is_empty(self) -> None:
        status, out = self._search("zzz-nada")
        self.assertEqual(status, 200)
        self.assertEqual(out["items"], [])


class TestServerEvents(WebGuiServerTestCase):

    def test_hub_publish_reaches_sse_client(self) -> None:
        """A hub.publish lands as a 'data:' line on a real SSE reader."""
        received: list[str] = []
        got_line = threading.Event()
        conn = http.client.HTTPConnection(*self.addr, timeout=10)

        def reader() -> None:
            try:
                conn.request("GET", "/events")
                response = conn.getresponse()
                if response.status != 200:
                    return
                while not got_line.is_set():
                    line = response.fp.readline().decode("utf-8")
                    if not line:
                        return
                    received.append(line)
                    if "Hub status x" in line:
                        got_line.set()
                        return
            except OSError:
                pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        # Wait for the SSE handler to register its queue on the hub.
        for _ in range(200):
            if getattr(web_gui.Handler, "hub") is not None and \
                    web_gui.Handler.hub._clients:
                break
            threading.Event().wait(0.05)
        web_gui.Handler.hub.publish(
            {"type": "status", "text": "Hub status x"})
        self.assertTrue(got_line.wait(timeout=5),
                        f"no SSE data received: {received!r}")
        got_line.set()
        try:
            conn.close()
        except OSError:
            pass
        self.assertTrue(
            any(line.startswith("data:") and "Hub status x" in line
                for line in received))


class TestStaticAssets(WebGuiServerTestCase):

    def test_summary_markdown_js_is_served(self) -> None:
        status, body = self._get("/summary-markdown.js")
        self.assertEqual(status, 200)
        self.assertIn("renderSummaryMarkdown", body)

    def test_index_references_summary_renderer(self) -> None:
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn('src="summary-markdown.js"', body)

    def test_resumen_text_without_session_returns_empty(self) -> None:
        """No crash (500) when no transcript was opened yet: 8fb68aa lesson."""
        status, body = self._get("/api/resumen-text")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"text": ""})

    def test_api_history_route_is_gone(self) -> None:
        """The dead /api/history endpoint was removed: nobody consumed it
        (the front reads history from /api/state)."""
        status, _ = self._get("/api/history")
        self.assertEqual(status, 404)


class TestPostActions(WebGuiServerTestCase):

    def test_post_export_txt_writes_and_opens(self) -> None:
        """Pre-existing behavior pinned while factoring the export helper."""
        orch = web_gui.Handler.orchestrator
        orch.last_transcript = os.path.join(self.transcripts_dir, self.history_md)
        with unittest.mock.patch.object(web_gui.subprocess, "Popen") as popen:
            status, body = self._post(
                "/api/action", json.dumps({"action": "export-txt"}))
            popen.assert_called_once()
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body).get("ok"))
        txt = os.path.join(self.transcripts_dir,
                           self.history_md.replace(".md", ".txt"))
        self.assertTrue(os.path.isfile(txt), "TXT not created")
        with open(txt, encoding="utf-8") as fh:
            self.assertIn("hola", fh.read())

    def test_post_unknown_action_returns_400(self) -> None:
        status, body = self._post(
            "/api/action", json.dumps({"action": "explode"}))
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_post_export_history_writes_txt_via_safe_transcript(self) -> None:
        # Stub the viewer launch: no real `cmd start` window during tests.
        with unittest.mock.patch.object(web_gui.subprocess, "Popen") as popen:
            status, body = self._post(
                "/api/action",
                json.dumps({"action": "export-history",
                            "name": self.history_md}))
            popen.assert_called_once()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body).get("ok"), True)
        txt = os.path.join(self.transcripts_dir,
                           self.history_md.replace(".md", ".txt"))
        self.assertTrue(os.path.isfile(txt), "TXT not created")
        with open(txt, encoding="utf-8") as fh:
            self.assertIn("hola", fh.read())

    def test_post_export_history_rejects_escape(self) -> None:
        status, body = self._post(
            "/api/action",
            json.dumps({"action": "export-history", "name": "../bad.md"}))
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload.get("ok"))
        self.assertIn("no", payload.get("error", "") + payload.get("error", ""))


class TestStartLaunchesCaptions(unittest.TestCase):
    """S2/web: clicking Transcribir MUST launch and pin Live Captions first.

    Regression for the user-reported watcher error: the web Orchestrator
    spawned the watcher without launching Live Captions, so the watcher's
    startup check failed with exit 1 whenever none was already running, and
    the Reanudar button re-failed forever.
    """

    def setUp(self) -> None:
        import web_gui

        self.web_gui = web_gui
        self.hub = web_gui.Hub()
        self.events: list[dict] = []

        def fake_publish(self_event, event: dict) -> None:
            self.events.append(event)

        self._hub_restore = _Restore(web_gui.Hub, publish=fake_publish)
        self._hub_restore.__enter__()
        self._pi_restore = _Restore(pi_bridge, available=lambda: True)
        self._pi_restore.__enter__()

    def tearDown(self) -> None:
        self._pi_restore.__exit__()
        self._hub_restore.__exit__()

    def test_start_launches_captions_and_pins_pid(self) -> None:
        recorded: dict[str, object] = {}

        class FakeProc:
            stdout = []
            stderr = None
            poll = lambda self: 0
            wait = lambda self, timeout=None: 0
            terminate = lambda self: None
            kill = lambda self: None

        def fake_popen(cmd, *args, **kwargs):
            recorded["cmd"] = cmd
            return FakeProc()

        with _Restore(transcriptor_core.subprocess, Popen=fake_popen):
            with _Restore(self.web_gui, launch_livecaptions=lambda: 4321):
                orch = self.web_gui.Orchestrator(self.hub)
                self.assertEqual(orch.start(), "")
                deadline = time.monotonic() + 5
                while orch.session is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                orch.session.stop()
                orch.session = None
        self.assertIsNotNone(recorded.get("cmd"))
        cmd = recorded["cmd"]
        self.assertIn("-CaptionsPid", cmd)
        self.assertEqual(cmd[cmd.index("-CaptionsPid") + 1], "4321")

    def test_start_without_captions_publishes_failure_and_no_session(self) -> None:
        with _Restore(self.web_gui, launch_livecaptions=lambda: None):
            orch = self.web_gui.Orchestrator(self.hub)
            self.assertEqual(orch.start(), "")
            deadline = time.monotonic() + 5
            while orch.session is not None and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertIsNone(orch.session)
        types = [e.get("type") for e in self.events]
        self.assertIn("watcherLost", types)


if __name__ == "__main__":
    unittest.main()
