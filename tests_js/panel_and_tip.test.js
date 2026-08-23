const test = require("node:test");
const assert = require("node:assert/strict");
const { loadSortPanelRows, loadPanelHelpers } = require("./extract");

/* sortPanelRows runs inside a vm sandbox, so the array it returns is built
   with that context's own Array constructor -- structurally identical to a
   normal array but a different realm, which makes assert's strict deep-
   equal (rightly) refuse to treat it as `===`-comparable to a literal array
   from this file's realm. Array.from() here, called from our own realm,
   sidesteps that by producing a plain local array from the values. */
test("sortPanelRows: numeric sort, dir=1 ascending / dir=-1 descending, nullish sorts as -Infinity", () => {
  const sortPanelRows = loadSortPanelRows();
  const rows = [{ amount: 5 }, { amount: null }, { amount: 20 }, { amount: 5 }];

  const asc = sortPanelRows(rows, { key: "amount", dir: 1 });
  assert.deepEqual(Array.from(asc, (r) => r.amount), [null, 5, 5, 20]);

  const desc = sortPanelRows(rows, { key: "amount", dir: -1 });
  assert.deepEqual(Array.from(desc, (r) => r.amount), [20, 5, 5, null]);
});

test("sortPanelRows: string sort uses localeCompare, not lexicographic byte order", () => {
  const sortPanelRows = loadSortPanelRows();
  const rows = [{ name: "banana" }, { name: "Apple" }, { name: "cherry" }];
  const asc = sortPanelRows(rows, { key: "name", dir: 1 });
  assert.deepEqual(
    Array.from(asc, (r) => r.name),
    ["Apple", "banana", "cherry"]
  ); // a plain byte-order sort would put "Apple" (capital A) after "banana"
});

test("sortPanelRows: returns a new array and never mutates the input", () => {
  const sortPanelRows = loadSortPanelRows();
  const rows = [{ amount: 3 }, { amount: 1 }, { amount: 2 }];
  const original = rows.slice();
  const sorted = sortPanelRows(rows, { key: "amount", dir: 1 });
  assert.notEqual(sorted, rows);
  assert.deepEqual(rows, original);
});

test("sortPanelRows: no sort passed through returns rows as-is", () => {
  const sortPanelRows = loadSortPanelRows();
  const rows = [{ amount: 3 }, { amount: 1 }];
  assert.equal(sortPanelRows(rows, null), rows);
  assert.equal(sortPanelRows(rows, undefined), rows);
});

function baseMeta() {
  return {
    columns: [{ field: "amount", fmt: "money" }],
    signals: [
      { key: "public_attention", label: "Public Attention" },
      { key: "filing_present", label: "Filing Present" },
    ],
    quality_signals: ["filing_present"],
    dampen: 0.3,
  };
}

test("buildTip: renders name/score and breakdown rows sorted by points descending", () => {
  const { buildTip, state } = loadPanelHelpers();
  state.meta = baseMeta();
  const row = {
    name: "Local Inference",
    score: 67.9,
    dampened: false,
    score_breakdown: {
      a: { label: "Recency", field: "amount", raw: 500, weight: 2, points: 12.4 },
      b: { label: "Volume", field: "amount", raw: 1200, weight: 1, points: 40.0 },
    },
  };
  const html = buildTip(row);
  assert.match(html, /Local Inference/);
  assert.match(html, /67\.9<span>\/100<\/span>/);
  // higher-points entry ("Volume", 40.0) must render before the lower one ("Recency", 12.4)
  assert.ok(html.indexOf("Volume") < html.indexOf("Recency"));
  assert.match(html, /\$500/); // fmtRaw dispatched through state.meta.columns to fmtMoney
  assert.doesNotMatch(html, /ct-dampen/);
});

test("buildTip: shows the dampen note, naming only the missing quality signals, when row.dampened", () => {
  const { buildTip, state } = loadPanelHelpers();
  state.meta = baseMeta();
  const row = { name: "No Footprint", score: 12.0, dampened: true, score_breakdown: {} };
  const html = buildTip(row);
  assert.match(html, /ct-dampen">⚠ ×0\.3/);
  assert.match(html, /Filing Present/); // the one signal that IS in quality_signals
  assert.doesNotMatch(html, /Public Attention/); // not a quality signal, must not be listed
  assert.match(html, /no scored signals/); // empty breakdown falls back to this literal
});

test("buildTip: HTML-escapes the row name (score_breakdown labels flow through the same esc())", () => {
  const { buildTip, state } = loadPanelHelpers();
  state.meta = baseMeta();
  const row = { name: `<script>alert(1)</script>`, score: 1, dampened: false, score_breakdown: {} };
  const html = buildTip(row);
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /&lt;script&gt;/);
});
