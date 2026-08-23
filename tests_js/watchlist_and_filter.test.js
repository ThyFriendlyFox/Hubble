const test = require("node:test");
const assert = require("node:assert/strict");
const { loadWatchlistHelpers, loadFilteredHelper } = require("./extract");

/* loadWatchlist()'s return value is a plain object built by JSON.parse
   running inside the vm context, so it belongs to that context's own
   realm -- same cross-realm gotcha as the vm-returned arrays in
   panel_and_tip.test.js, just with Object instead of Array. Comparing via
   JSON.stringify sidesteps it without needing another Array.from-style
   realm-crossing helper for objects. */
test("loadWatchlist: empty storage yields {}, not a crash", () => {
  const { loadWatchlist } = loadWatchlistHelpers();
  assert.equal(JSON.stringify(loadWatchlist()), "{}");
});

test("loadWatchlist: malformed JSON in storage falls back to {} rather than throwing", () => {
  const { loadWatchlist, storage } = loadWatchlistHelpers();
  storage.setItem("observatory_watchlist", "{not valid json");
  assert.equal(JSON.stringify(loadWatchlist()), "{}");
});

test("toggleWatch: adds on first call, removes on second (true toggle, not just add)", () => {
  const { toggleWatch, isWatched } = loadWatchlistHelpers();
  assert.equal(isWatched("holmdel", "localllm"), false);
  toggleWatch("holmdel", "localllm");
  assert.equal(isWatched("holmdel", "localllm"), true);
  toggleWatch("holmdel", "localllm");
  assert.equal(isWatched("holmdel", "localllm"), false);
});

test("toggleWatch: no-ops on a falsy slug or key rather than persisting garbage", () => {
  const { toggleWatch, storage } = loadWatchlistHelpers();
  toggleWatch(null, "localllm");
  toggleWatch("holmdel", null);
  assert.equal(storage.getItem("observatory_watchlist"), null);
});

test("toggleWatch: keeps each telescope's list independent under the same key", () => {
  const { toggleWatch, isWatched, storage } = loadWatchlistHelpers();
  toggleWatch("holmdel", "localllm");
  toggleWatch("jackson", "primecorp");
  assert.equal(isWatched("holmdel", "localllm"), true);
  assert.equal(isWatched("jackson", "localllm"), false); // same key, different telescope -- must not cross-contaminate
  assert.equal(isWatched("jackson", "primecorp"), true);
  assert.deepEqual(JSON.parse(storage.getItem("observatory_watchlist")), {
    holmdel: ["localllm"],
    jackson: ["primecorp"],
  });
});

function baseState() {
  return {
    slug: "holmdel",
    rows: [
      { key: "a", name: "Local Inference", score: 67.9, noise: false, tag: "ai" },
      { key: "b", name: "AI Agents", score: 60.2, noise: false, tag: "ai" },
      { key: "c", name: "Chatter Only", score: null, noise: false, tag: "hype" },
      { key: "d", name: "Noisy Thing", score: 10, noise: true, tag: "ai" },
    ],
    hideNoise: false,
    search: "",
    scoredOnly: false,
    watchedOnly: false,
    sort: { key: "name", dir: 1 },
  };
}

test("filtered(): hideNoise drops rows flagged noisy, leaves the rest untouched", () => {
  const { filtered, state } = loadFilteredHelper();
  Object.assign(state, baseState(), { hideNoise: true, sort: { key: "key", dir: 1 } });
  assert.deepEqual(
    Array.from(filtered(), (r) => r.key),
    ["a", "b", "c"]
  );
});

test("filtered(): scoredOnly excludes score===null but keeps score===0 (a real score, not a missing one)", () => {
  const { filtered, state } = loadFilteredHelper();
  Object.assign(state, baseState(), {
    rows: [
      { key: "zero", name: "Zero", score: 0, noise: false },
      { key: "none", name: "None", score: null, noise: false },
    ],
    scoredOnly: true,
    sort: { key: "key", dir: 1 },
  });
  assert.deepEqual(
    Array.from(filtered(), (r) => r.key),
    ["zero"]
  );
});

test("filtered(): search matches case-insensitively across any string field, not just name", () => {
  const { filtered, state } = loadFilteredHelper();
  Object.assign(state, baseState(), { search: "HYPE", sort: { key: "key", dir: 1 } });
  assert.deepEqual(
    Array.from(filtered(), (r) => r.key),
    ["c"]
  ); // matched on the "tag" field, not "name"
});

test("filtered(): watchedOnly filters by the real toggleWatch/isWatched state for state.slug", () => {
  const { filtered, state, toggleWatch } = loadFilteredHelper();
  Object.assign(state, baseState(), { watchedOnly: true, sort: { key: "key", dir: 1 } });
  toggleWatch("holmdel", "b");
  assert.deepEqual(
    Array.from(filtered(), (r) => r.key),
    ["b"]
  );
});

test("filtered(): filters compose (AND, not OR) and the trailing sort is applied to the filtered set", () => {
  const { filtered, state } = loadFilteredHelper();
  Object.assign(state, baseState(), {
    hideNoise: true,
    scoredOnly: true,
    sort: { key: "score", dir: -1 },
  });
  // hideNoise drops "d", scoredOnly drops "c" -- only "a" (67.9) and "b" (60.2) remain, sorted desc by score
  assert.deepEqual(
    Array.from(filtered(), (r) => r.key),
    ["a", "b"]
  );
});

test("filtered(): returns a new array, does not mutate state.rows", () => {
  const { filtered, state } = loadFilteredHelper();
  Object.assign(state, baseState(), { sort: { key: "key", dir: -1 } });
  const before = state.rows.map((r) => r.key);
  filtered();
  assert.deepEqual(state.rows.map((r) => r.key), before);
});
