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

/* For a one-line, non-braced statement like `const WATCHLIST_KEY = "...";` --
   extractBraced can't extract these since there's no `{` to balance against. */
function extractStatement(source, anchor) {
  const idx = source.indexOf(anchor);
  if (idx === -1) {
    throw new Error(`extractStatement: anchor not found in app.js: ${JSON.stringify(anchor)}`);
  }
  const end = source.indexOf(";", idx);
  if (end === -1) {
    throw new Error(`extractStatement: no terminating ';' found for: ${JSON.stringify(anchor)}`);
  }
  return source.slice(idx, end + 1);
}

/* Extracts `specs` (each a `function name(...)` / `const NAME = {` /
   `let name = {` signature as it literally appears in app.js, or `{stmt:
   "..."}` for a one-line non-braced statement) as a block of source text,
   evaluates them together in one fresh vm context, and hands back an object
   with one property per name in `exportNames` -- so callers never touch the
   context object directly and don't need to know whether a given binding
   happens to attach to it (function decls and `var` do; `let`/`const`
   don't, which is exactly what this sidesteps). `extraGlobals` seeds the vm
   context with anything the extracted code expects to find as a free
   global (e.g. a fake `localStorage`). */
function loadFromAppJs(specs, exportNames, extraGlobals) {
  const source = fs.readFileSync(APP_JS_PATH, "utf8");
  const blocks = specs.map((spec) =>
    typeof spec === "string" ? extractBraced(source, spec) : extractStatement(source, spec.stmt)
  );
  const combined =
    blocks.join("\n") + "\nthis.__exports__ = { " + exportNames.join(", ") + " };\n";
  const context = vm.createContext({ ...extraGlobals });
  vm.runInContext(combined, context, { filename: "static/app.js (extracted)" });
  return context.__exports__;
}

/* A minimal Web Storage stand-in -- not a DOM shim, just the four-method
   key/value interface localStorage exposes. Injected as a vm global so
   loadWatchlist/saveWatchlist/isWatched/toggleWatch/filtered() can run
   unmodified; tests get the same instance back so they can seed malformed
   data or inspect exactly what got persisted. */
class FakeStorage {
  constructor() {
    this._data = new Map();
  }
  getItem(key) {
    return this._data.has(key) ? this._data.get(key) : null;
  }
  setItem(key, value) {
    this._data.set(key, String(value));
  }
  removeItem(key) {
    this._data.delete(key);
  }
  clear() {
    this._data.clear();
  }
}

/* Every name here is a pure function/const in app.js's "format a raw value
   for display" section -- no DOM, no fetch, no localStorage -- which is why
   this narrow slice can be sandboxed without stubbing out the browser. */
const FORMAT_NAMES = [
  "esc", "fmtNum", "fmtInt", "fmtMoney", "fmtPrice", "fmtPct", "fmtSigned",
  "fmtText", "fmtUrl", "FORMATTERS", "cell", "ago",
];
const FORMAT_ANCHORS = [
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
  return loadFromAppJs(FORMAT_ANCHORS, FORMAT_NAMES);
}

/* sortPanelRows(rows, sort) is fully self-contained -- takes both its inputs
   as parameters, touches no global state -- so it needs no companion blocks
   at all. */
function loadSortPanelRows() {
  return loadFromAppJs(["function sortPanelRows(rows, sort)"], ["sortPanelRows"]).sortPanelRows;
}

/* buildTip(row) and fmtRaw(field, value) are DOM-free but do read the
   mutable `state` object (for state.meta's columns/signals/quality_signals/
   dampen) -- extracting the real `let state = {` initializer, rather than
   hand-writing a stand-in shape, means a future field rename in app.js
   breaks this extraction loudly instead of silently testing a stale shape.
   Tests mutate the returned `state.meta` directly between cases; since
   buildTip/fmtRaw only ever read state.meta's properties (never reassign
   `state` itself), mutating the shared object is exactly what the real
   render path does too. */
function loadPanelHelpers() {
  return loadFromAppJs(
    [...FORMAT_ANCHORS, "let state = {", "function fmtRaw(field, value)", "function buildTip(row)"],
    [...FORMAT_NAMES, "state", "fmtRaw", "buildTip"]
  );
}

const WATCHLIST_ANCHORS = [
  { stmt: "const WATCHLIST_KEY = " },
  "function loadWatchlist()",
  "function saveWatchlist(all)",
  "function isWatched(slug, key)",
  "function toggleWatch(slug, key)",
];
const WATCHLIST_NAMES = ["loadWatchlist", "saveWatchlist", "isWatched", "toggleWatch"];

/* localStorage is the only global these four touch -- no document, no
   `state`. Returns the storage instance alongside the functions so tests
   can seed malformed JSON or inspect exactly what got persisted. */
function loadWatchlistHelpers() {
  const storage = new FakeStorage();
  const helpers = loadFromAppJs(WATCHLIST_ANCHORS, WATCHLIST_NAMES, { localStorage: storage });
  return { ...helpers, storage };
}

/* filtered() is the main table's real filter+sort pipeline: reads state.rows
   plus every filter toggle in state, and calls isWatched() for the watched-
   only filter -- so it needs both the state initializer and the watchlist
   chain (hence localStorage) alongside itself. */
function loadFilteredHelper() {
  const storage = new FakeStorage();
  const helpers = loadFromAppJs(
    [...WATCHLIST_ANCHORS, "let state = {", "function filtered()"],
    [...WATCHLIST_NAMES, "state", "filtered"],
    { localStorage: storage }
  );
  return { ...helpers, storage };
}

module.exports = {
  loadFormatHelpers,
  loadSortPanelRows,
  loadPanelHelpers,
  loadWatchlistHelpers,
  loadFilteredHelper,
  extractBraced,
};
