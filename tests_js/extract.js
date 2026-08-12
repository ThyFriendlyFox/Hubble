/* Pulls named functions/consts straight out of the real, shipped static/app.js
   source text and evaluates them in an isolated vm context -- so these tests
   can never drift from what actually ships (no hand-copied duplicate of the
   logic to fall out of sync), without needing app.js itself to become an ES
   module or gain any build step. Deliberately zero npm dependencies: only
   node:fs/node:path/node:vm, all built into Node itself. */
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const APP_JS_PATH = path.join(__dirname, "..", "static", "app.js");

function extractBraced(source, anchor) {
  const idx = source.indexOf(anchor);
  if (idx === -1) {
    throw new Error(`extractBraced: anchor not found in app.js: ${JSON.stringify(anchor)}`);
  }
  const braceStart = source.indexOf("{", idx);
  let depth = 0;
  for (let i = braceStart; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) {
        let end = i + 1;
        if (source[end] === ";") end++;
        return source.slice(idx, end);
      }
    }
  }
  throw new Error(`extractBraced: unbalanced braces extracting: ${JSON.stringify(anchor)}`);
}

/* Every name here is a pure function/const in app.js's "format a raw value
   for display" section -- no DOM, no fetch, no localStorage -- which is why
   this narrow slice can be sandboxed without stubbing out the browser. */
const ANCHORS = [
  "function esc(s)",
  "function fmtNum(n)",
  "function fmtInt(n)",
  "function fmtMoney(n)",
  "function fmtPrice(p)",
  "function fmtPct(n)",
  "function fmtSigned(n)",
  "function fmtText(s)",
  "function fmtUrl(u)",
  "const FORMATTERS = {",
  "function cell(row, col)",
  "function ago(s)",
];

function loadFormatHelpers() {
  const source = fs.readFileSync(APP_JS_PATH, "utf8");
  const blocks = ANCHORS.map((anchor) => extractBraced(source, anchor));
  const combined =
    blocks.join("\n") +
    "\nthis.__exports__ = " +
    "{ esc, fmtNum, fmtInt, fmtMoney, fmtPrice, fmtPct, fmtSigned, fmtText, fmtUrl, FORMATTERS, cell, ago };\n";
  const context = vm.createContext({});
  vm.runInContext(combined, context, { filename: "static/app.js (extracted)" });
  return context.__exports__;
}

module.exports = { loadFormatHelpers, extractBraced };
