"""Browser e2e for summary Markdown rendering (opt-in, GENTLE_E2E=1).

Mirrors the real UI flow end to end: a live server, a real browser over SSE,
and the summary event stream published through the hub. The saved lesson from
the watcher fix (8fb68aa) applies: hermetic tests cannot see front-end
rendering, so this guard exists. Playwright 1.62 is installed globally;
``channel="msedge"`` avoids downloading Chromium.

Run with:  set GENTLE_E2E=1 && py -m unittest tests.test_summary_render_e2e -v
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

web_gui = importlib.import_module("web_gui")
from tests.test_web_gui import WebGuiServerTestCase  # noqa: E402


SAMPLE_SUMMARY = (
    "## Resumen\n"
    "Reunión sobre alerta de Palo Alto.\n"
    "\n"
    "## Puntos clave\n"
    "- **Versión** actual: `11.h10`\n"
    "\n"
    "## Acciones\n"
    "\n"
    "| Que | Quien | Para cuando |\n"
    "|---|---|---|\n"
    "| Aplicar perfiles | Ernesto | Hoy |\n"
    "| Validar upgrade | Jerry | Viernes |\n"
)


@unittest.skipUnless(os.environ.get("GENTLE_E2E") == "1",
                     "e2e runs only with GENTLE_E2E=1")
class TestSummaryRenderE2E(WebGuiServerTestCase):

    def _wait_for_sse_client(self) -> None:
        hub = web_gui.Handler.hub
        for _ in range(200):
            if hub is not None and getattr(hub, "_clients", None):
                return
            time.sleep(0.05)
        self.fail("the browser never registered on the hub")

    def test_summary_stream_renders_markdown_not_raw_text(self) -> None:
        from playwright.sync_api import sync_playwright

        url = f"http://{self.addr[0]}:{self.addr[1]}/"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                self._wait_for_sse_client()
                hub = web_gui.Handler.hub
                hub.publish({"type": "summaryStarted", "live": False})
                hub.publish({"type": "summaryDelta", "text": SAMPLE_SUMMARY})
                hub.publish({"type": "summaryDone", "ok": True})
                try:
                    page.wait_for_selector("#summary-log table",
                                          timeout=10000)
                except Exception:
                    self.fail(
                        "table never rendered; panel html=\n"
                        + page.locator("#panel-resumen").inner_html()
                        + "\npageerrors=" + "\n".join(errors))

                headings = page.locator("#summary-log h3").all_inner_texts()
                self.assertIn("Puntos clave", headings)
                self.assertIn("Acciones", headings)
                headers = page.locator(
                    "#summary-log table th").all_inner_texts()
                self.assertEqual(
                    headers, ["Que", "Quien", "Para cuando"])
                rows = page.locator(
                    "#summary-log table tbody tr").count()
                self.assertEqual(rows, 2)
                codes = page.locator(
                    "#summary-log li code").all_inner_texts()
                self.assertEqual(codes, ["11.h10"])
                # Raw markdown must not survive anywhere in the panel.
                panel_text = page.locator(
                    "#panel-resumen").inner_text()
                self.assertNotIn("## ", panel_text)
                self.assertNotIn("|---|", panel_text)
            finally:
                browser.close()


@unittest.skipUnless(os.environ.get("GENTLE_E2E") == "1",
                     "e2e runs only with GENTLE_E2E=1")
class TestHistorySearchE2E(WebGuiServerTestCase):
    """S4: typing + Enter in the filter shows global results (no click, the
    row's open action would launch a real file on this machine)."""

    def _wait_for_sse_client(self) -> None:
        hub = web_gui.Handler.hub
        for _ in range(200):
            if hub is not None and getattr(hub, "_clients", None):
                return
            time.sleep(0.05)
        self.fail("the browser never registered on the hub")

    def test_enter_in_filter_shows_search_results(self) -> None:
        from playwright.sync_api import sync_playwright

        url = f"http://{self.addr[0]}:{self.addr[1]}/"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page()
                page.goto(url)
                self._wait_for_sse_client()
                page.fill("#filterInput", "hola")
                page.press("#filterInput", "Enter")
                page.wait_for_selector(
                    "#search-results:not(.hidden)", timeout=10000)
                text = page.locator("#search-results").inner_text()
                self.assertIn("2026_01_28_test", text)
                self.assertIn("[00:00:01]", text)
                self.assertIn("hola", text)
            finally:
                browser.close()


@unittest.skipUnless(os.environ.get("GENTLE_E2E") == "1",
                     "e2e runs only with GENTLE_E2E=1")
class TestReopenShowsLastResumenE2E(WebGuiServerTestCase):
    """User-reported bug: after a server restart the Resumen tab stayed
    empty even though a .resumen.md existed. The boot preload must render
    the newest saved resumen."""

    def test_reopen_renders_newest_saved_resumen(self) -> None:
        resumen = os.path.join(self.transcripts_dir,
                               self.history_md.replace(".md", ".resumen.md"))
        with open(resumen, "w", encoding="utf-8") as fh:
            fh.write("## Resumen\nReunion de prueba.\n\n"
                     "## Acciones\n\n| Que | Quien |\n|---|---|\n"
                     "| Aplicar perfiles | Ernesto |\n")
        from playwright.sync_api import sync_playwright

        url = f"http://{self.addr[0]}:{self.addr[1]}/"
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page()
                page.goto(url)
                page.click("#tab-resumen")
                page.wait_for_selector("#summary-log h3", timeout=10000)
                self.assertIn("Resumen", page.locator("#summary-log h3")
                              .all_inner_texts())
                self.assertEqual(
                    page.locator("#summary-log table th").all_inner_texts(),
                    ["Que", "Quien"])
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
