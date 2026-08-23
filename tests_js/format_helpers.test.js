const test = require("node:test");
const assert = require("node:assert/strict");
const { loadFormatHelpers } = require("./extract");

const H = loadFormatHelpers();

test("esc escapes every HTML-significant character", () => {
  assert.equal(H.esc(`<a href="x">&'</a>`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
});

test("esc treats null/undefined as empty, not the string 'null'", () => {
  assert.equal(H.esc(null), "");
  assert.equal(H.esc(undefined), "");
});

test("esc coerces non-strings", () => {
  assert.equal(H.esc(42), "42");
});

test("fmtNum shows the dim dash for null/undefined/empty", () => {
  assert.equal(H.fmtNum(null), "<span class=dim>—</span>");
  assert.equal(H.fmtNum(undefined), "<span class=dim>—</span>");
  assert.equal(H.fmtNum(""), "<span class=dim>—</span>");
});

test("fmtNum rounds to 2dp and adds thousands separators", () => {
  assert.equal(H.fmtNum(1234.5678), "1,234.57");
  assert.equal(H.fmtNum(0), "0");
});

test("fmtInt steps K/M/B at the 1e3/1e6/1e9 boundaries, not before", () => {
  assert.equal(H.fmtInt(999), "999");
  assert.equal(H.fmtInt(1000), "1.0K");
  assert.equal(H.fmtInt(999999), "1000.0K"); // stays K right up to 1e6, doesn't round up to "1.0M"
  assert.equal(H.fmtInt(1e6), "1.0M");
  assert.equal(H.fmtInt(1e9), "1.0B");
});

test("fmtInt preserves sign through the magnitude branches", () => {
  assert.equal(H.fmtInt(-1500), "-1.5K");
});

test("fmtMoney applies the same magnitude ladder with a $ prefix", () => {
  assert.equal(H.fmtMoney(null), "<span class=dim>—</span>");
  assert.equal(H.fmtMoney(500), "$500");
  assert.equal(H.fmtMoney(2.5e6), "$2.5M");
  assert.equal(H.fmtMoney(-3e9), "$-3.0B");
});

test("fmtPrice scales by 1e6 (stored-price-in-millions convention) to 2dp", () => {
  assert.equal(H.fmtPrice(null), "<span class=dim>—</span>");
  assert.equal(H.fmtPrice(0.0001234), "$123.40");
});

test("fmtPct fixes to 1dp and never crashes on 0", () => {
  assert.equal(H.fmtPct(null), "<span class=dim>—</span>");
  assert.equal(H.fmtPct(0), "0.0%");
  assert.equal(H.fmtPct(-3.456), "-3.5%");
});

test("fmtSigned picks up/down/neutral class and only adds '+' when positive", () => {
  assert.equal(H.fmtSigned(null), "<span class=dim>—</span>");
  assert.equal(H.fmtSigned(5), '<span class="up">+5.00</span>');
  assert.equal(H.fmtSigned(-5), '<span class="down">-5.00</span>');
  assert.equal(H.fmtSigned(0), '<span class="">0.00</span>');
});

test("fmtText escapes through esc, and dims only on null/undefined/empty", () => {
  assert.equal(H.fmtText(null), "<span class=dim>—</span>");
  assert.equal(H.fmtText(""), "<span class=dim>—</span>");
  assert.equal(H.fmtText("<b>hi</b>"), "&lt;b&gt;hi&lt;/b&gt;");
});

test("fmtUrl builds an escaped, noopener-safe anchor and dims on falsy input", () => {
  assert.equal(H.fmtUrl(""), "<span class=dim>—</span>");
  assert.equal(H.fmtUrl(null), "<span class=dim>—</span>");
  assert.equal(
    H.fmtUrl(`example.com/"><script>`),
    `<a href="https://example.com/&quot;&gt;&lt;script&gt;" target="_blank" rel="noopener">example.com/&quot;&gt;&lt;script&gt;</a>`
  );
});

test("ago steps through s/m/h/d using a plain em dash for null (not the dim span the fmt* helpers use)", () => {
  assert.equal(H.ago(null), "—");
  assert.equal(H.ago(59), "59s");
  assert.equal(H.ago(60), "1m");
  assert.equal(H.ago(3599), "60m"); // rounds up to 60m rather than rolling over to 1h
  assert.equal(H.ago(3600), "1h");
  assert.equal(H.ago(86399), "24h"); // same boundary quirk, one level up
  assert.equal(H.ago(86400), "1d");
});

test("cell dispatches on col.fmt via FORMATTERS and falls back to fmtNum for an unknown fmt", () => {
  assert.equal(H.cell({ amount: 2.5e6 }, { field: "amount", fmt: "money" }), "$2.5M");
  assert.equal(H.cell({ amount: 1234.5 }, { field: "amount", fmt: "not-a-real-format" }), "1,234.5");
});
