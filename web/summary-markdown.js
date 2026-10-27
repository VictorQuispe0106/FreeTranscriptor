/* Minimal, safe Markdown renderer for Pi summaries.
 *
 * Scope: exactly the constructs the summary contract produces (pi_bridge
 * `_contract`): ATX headings, pipe tables with separator row, bullet lists,
 * hr rules, inline **bold** and inline `code`. Everything else renders as
 * plain paragraphs.
 *
 * Safety: the DOM is built with createElement/textContent only; summary or
 * transcript text is never handed to innerHTML, so it cannot inject markup.
 * The DOM-free parser (parseSummaryMarkdown) is asserted by
 * tests/test_summary_markdown_parser.py through Node.
 */
(function () {
  "use strict";

  var HEADING_RE = /^(#{1,4})\s+(.*)$/;
  var BULLET_RE = /^[-*]\s+(.*)$/;
  var HR_RE = /^\s*(?:-{3,}|\*{3,})\s*$/;
  var INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`)/g;

  var STYLES = {
    heading1: "text-lg font-semibold text-primary-container pb-1",
    heading2: "text-base font-semibold text-primary-container pb-0.5",
    heading3: "text-sm font-semibold text-secondary-container pb-0.5",
    heading4: "text-sm font-semibold text-secondary-container pb-0.5",
    paragraph: "leading-relaxed",
    list: "list-disc pl-5 space-y-1 leading-relaxed",
    hr: "border-outline-variant my-2",
    table: "w-full border-collapse my-1 text-sm",
    th: "border border-outline-variant px-2 py-1 text-left font-semibold " +
      "text-secondary-container align-top",
    td: "border border-outline-variant px-2 py-1 align-top",
    code: "bg-surface-container px-1 rounded text-primary-fixed-dim"
  };

  function isTableRow(line) {
    var trimmed = line.trim();
    return trimmed.charAt(0) === "|" &&
      trimmed.indexOf("|", 1) !== -1;
  }

  function parseTableRow(line) {
    var trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split("|").map(function (cell) {
      return cell.trim();
    });
  }

  function isSeparatorRow(cells) {
    return cells.length > 0 && cells.every(function (cell) {
      return /^:?-{3,}:?$/.test(cell);
    });
  }

  function parseInline(text) {
    var parts = [];
    var last = 0;
    var match;
    INLINE_RE.lastIndex = 0;
    while ((match = INLINE_RE.exec(text)) !== null) {
      if (match.index > last) {
        parts.push({ kind: "text", s: text.slice(last, match.index) });
      }
      var token = match[0];
      if (token.charAt(0) === "*") {
        parts.push({ kind: "bold", s: token.slice(2, -2) });
      } else {
        parts.push({ kind: "code", s: token.slice(1, -1) });
      }
      last = match.index + token.length;
    }
    if (last < text.length) {
      parts.push({ kind: "text", s: text.slice(last) });
    }
    return parts;
  }

  function parseSummaryMarkdown(text) {
    var blocks = [];
    var lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var heading = line.match(HEADING_RE);
      if (heading) {
        blocks.push({
          type: "heading",
          level: heading[1].length,
          text: heading[2].trim()
        });
        continue;
      }
      if (HR_RE.test(line)) {
        blocks.push({ type: "hr" });
        continue;
      }
      if (isTableRow(line)) {
        var rows = [];
        while (i < lines.length && isTableRow(lines[i])) {
          var cells = parseTableRow(lines[i]);
          if (!isSeparatorRow(cells)) {
            rows.push(cells);
          }
          i++;
        }
        i--;
        blocks.push({ type: "table", rows: rows });
        continue;
      }
      var bullet = line.match(BULLET_RE);
      if (bullet) {
        var parsed = parseInline(bullet[1]);
        var lastBlock = blocks[blocks.length - 1];
        if (lastBlock && lastBlock.type === "list") {
          lastBlock.items.push(parsed);
        } else {
          blocks.push({ type: "list", items: [parsed] });
        }
        continue;
      }
      if (!line.trim()) {
        continue;
      }
      blocks.push({ type: "paragraph", text: line });
    }
    return blocks;
  }

  function appendInline(el, parts) {
    (parts || []).forEach(function (part) {
      if (part.kind === "bold") {
        var strong = document.createElement("strong");
        strong.textContent = part.s;
        el.appendChild(strong);
      } else if (part.kind === "code") {
        var code = document.createElement("code");
        code.className = STYLES.code;
        code.textContent = part.s;
        el.appendChild(code);
      } else if (part.s) {
        el.appendChild(document.createTextNode(part.s));
      }
    });
  }

  function renderSummaryMarkdown(root, text) {
    root.textContent = "";
    parseSummaryMarkdown(text).forEach(function (block) {
      var el;
      if (block.type === "heading") {
        var level = Math.min(block.level, 4);
        el = document.createElement("h" + Math.min(level + 1, 6));
        el.className = STYLES["heading" + level];
        el.textContent = block.text;
      } else if (block.type === "hr") {
        el = document.createElement("hr");
        el.className = STYLES.hr;
      } else if (block.type === "paragraph") {
        el = document.createElement("p");
        el.className = STYLES.paragraph;
        appendInline(el, parseInline(block.text));
      } else if (block.type === "list") {
        el = document.createElement("ul");
        el.className = STYLES.list;
        block.items.forEach(function (item) {
          var li = document.createElement("li");
          appendInline(li, item);
          el.appendChild(li);
        });
      } else if (block.type === "table" && block.rows.length) {
        el = document.createElement("table");
        el.className = STYLES.table;
        var head = document.createElement("thead");
        var headRow = document.createElement("tr");
        block.rows[0].forEach(function (cell) {
          var th = document.createElement("th");
          th.className = STYLES.th;
          th.textContent = cell;
          headRow.appendChild(th);
        });
        head.appendChild(headRow);
        el.appendChild(head);
        var body = document.createElement("tbody");
        block.rows.slice(1).forEach(function (row) {
          var tr = document.createElement("tr");
          row.forEach(function (cell) {
            var td = document.createElement("td");
            td.className = STYLES.td;
            td.textContent = cell;
            tr.appendChild(td);
          });
          body.appendChild(tr);
        });
        el.appendChild(body);
      } else {
        return;
      }
      root.appendChild(el);
    });
    return root.children.length;
  }

  window.renderSummaryMarkdown = renderSummaryMarkdown;
  window.parseSummaryMarkdown = parseSummaryMarkdown;
})();
