"""Parser tests for web/summary-markdown.js, run through Node (no DOM).

The renderer splits into a DOM-free parser (``parseSummaryMarkdown``) and a
thin DOM layer, so the parsing rules are asserted here deterministically via
``node`` when it is available. Skipped otherwise so the hermetic suite stays
green on machines without Node.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(ROOT, "web", "summary-markdown.js")

NODE_SCRIPT = r"""
"use strict";
const fs = require("fs");
global.window = {};
new Function(fs.readFileSync(process.argv[2], "utf8"))();
const parse = global.window.parseSummaryMarkdown;

let failures = 0;
function check(name, cond, detail) {
  if (!cond) {
    failures += 1;
    console.error("FAIL: " + name + (detail ? " :: " + detail : ""));
  }
}

// Headings keep level and text; no raw # survives in text.
const h = parse("## Puntos clave\n#cabeza sin espacio");
check("heading level", h[0].type === "heading" && h[0].level === 2, JSON.stringify(h[0]));
check("heading text", h[0].text === "Puntos clave", h[0].text);

// Tables: separator row dropped, cells trimmed, pipes never leak.
const t = parse("| Que | Quien |\n|---|---|\n| A | B |\n| C | D |");
check("table block", t.length === 1 && t[0].type === "table", JSON.stringify(t));
check("table rows", t[0].rows.length === 3, JSON.stringify(t[0].rows));
check("table header", t[0].rows[0][0] === "Que" && t[0].rows[0][1] === "Quien");
check("table cell", t[0].rows[2][1] === "D");

// Consecutive bullets group into one list; inline bold/code tokenize.
const l = parse("- **Fecha**: hoy\n- uso de `pi` CLI\n- texto solo");
check("list grouped", l.length === 1 && l[0].type === "list" && l[0].items.length === 3,
  JSON.stringify(l));
const inline = parse("- **Fecha**: hoy")[0].items[0];
check("bullet bold token", JSON.stringify(inline) ===
  JSON.stringify([{kind:"bold",s:"Fecha"},{kind:"text",s:": hoy"}]),
  JSON.stringify(inline));
const inline2 = parse("- uso de `pi` CLI")[0].items[0];
check("bullet code token", inline2.some(function (p) {
  return p.kind === "code" && p.s === "pi";
}), JSON.stringify(inline2));

// hr and paragraph passthrough; CR normalized.
const m = parse("---\r\nlinea sola");
check("hr", m[0].type === "hr", JSON.stringify(m[0]));
check("paragraph", m[1].type === "paragraph" && m[1].text === "linea sola",
  JSON.stringify(m[1]));

// Blank lines never become blocks.
check("blank lines dropped", parse("\n\n\n").length === 0);

if (failures) {
  console.error(failures + " parser check(s) failed");
  process.exit(1);
}
console.log("parser checks OK");
"""


@unittest.skipUnless(shutil.which("node"), "node is not available")
class TestSummaryMarkdownParser(unittest.TestCase):

    def test_parser_rules_match_summary_contract(self) -> None:
        self.assertTrue(os.path.exists(RENDERER),
                        "web/summary-markdown.js is missing")
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False,
                encoding="utf-8", newline="\n") as fh:
            fh.write(NODE_SCRIPT)
            script = fh.name
        try:
            proc = subprocess.run(
                ["node", script, RENDERER],
                capture_output=True, text=True, timeout=60,
                cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        finally:
            os.unlink(script)
        self.assertEqual(
            proc.returncode, 0,
            f"node parser test failed:\nstdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("parser checks OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
