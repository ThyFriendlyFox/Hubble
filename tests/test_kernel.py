"""Kernel unit tests — no network, pure logic.

These pin the behaviour every telescope inherits: normalisation, weight
renormalisation around missing signals, the quality dampener, and each event
rule's firing conditions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil                                                     # noqa: E402
import tempfile                                                   # noqa: E402
from contextlib import contextmanager                             # noqa: E402
from unittest.mock import patch                                  # noqa: E402

import requests                                                   # noqa: E402

from telescope import brief, http, notifier, ranking, registry    # noqa: E402
from telescope import series                                     # noqa: E402
from telescope.base import Telescope                             # noqa: E402
from telescope.cache import Cache                                # noqa: E402
from telescope.events import (ClimberRule, CrossoverRule, DeltaRule,  # noqa: E402
                              FlagFlipRule, NewEntrantRule, NewLeaderRule,
                              ThresholdRule)
from telescope.ranking import Signal                             # noqa: E402
from telescope.series import (Series, analyse, change,               # noqa: E402
                              fetch_panel, historical_panel)

SIGNALS = (
    Signal("a", "a", "A"),
    Signal("b", "b", "B"),
    Signal("cost", "cost", "COST", higher=False),
)
WEIGHTS = {"a": 50, "b": 30, "cost": 20}


def test_normalise_spans_0_100():
    assert ranking.normalise([1, 5, 10]) == [0.0, (4 / 9) * 100, 100.0]


def test_normalise_preserves_none():
    out = ranking.normalise([1, None, 10])
    assert out[1] is None and out[0] == 0.0 and out[2] == 100.0


def test_normalise_all_none():
    assert ranking.normalise([None, None]) == [None, None]


def test_normalise_identical_values_does_not_divide_by_zero():
    assert ranking.normalise([7, 7, 7]) == [0.0, 0.0, 0.0]


def test_lower_is_better_inverts():
    rows = [{"cost": 1}, {"cost": 10}]
    ranking.score(rows, SIGNALS, {"cost": 100})
    assert rows[0]["score"] == 100.0 and rows[1]["score"] == 0.0


def test_missing_signal_renormalises_rather_than_zeroing():
    """A row missing 'b' is judged on 'a' alone, not penalised for the gap.

    x and y are tied on 'a' (both top), but x is bottom on 'b'. Renormalising
    means y — which has no 'b' at all — outscores x rather than being dragged
    down by a signal it simply doesn't have.
    """
    rows = [
        {"key": "x", "a": 10, "b": 0},
        {"key": "y", "a": 10},           # no b
        {"key": "z", "a": 0, "b": 10},
    ]
    ranking.score(rows, SIGNALS, {"a": 50, "b": 50})
    by = {r["key"]: r for r in rows}
    assert by["y"]["score"] == 100.0
    assert by["x"]["score"] == 50.0
    assert "b" not in by["y"]["score_breakdown"]


def test_quality_dampener_applies_only_without_quality_signals():
    rows = [
        {"key": "has", "a": 10, "b": 10},   # tops both -> 100, undampened
        {"key": "lacks", "b": 5},           # mid on b, no quality signal
        {"key": "floor", "a": 0, "b": 0},
    ]
    ranking.score(rows, SIGNALS, {"a": 50, "b": 50},
                  quality_signals=("a",), dampen=0.5)
    by = {r["key"]: r for r in rows}
    assert by["has"]["score"] == 100.0
    # 'lacks' blends to 50 on b alone, then halves for carrying no quality signal.
    assert by["lacks"]["score"] == 25.0


def test_no_quality_config_means_no_dampening():
    rows = [{"key": "x", "b": 5}]
    ranking.score(rows, SIGNALS, {"b": 50}, quality_signals=(), dampen=0.5)
    assert rows[0]["score"] == 0.0   # single value normalises to 0, undampened


def test_score_breakdown_shows_the_work():
    """The breakdown must carry everything a tooltip needs to explain a score:
    the label, the raw field value, the normalised 0-100 value, the weight
    actually used, and this signal's point contribution to the blend."""
    rows = [{"key": "x", "a": 10, "b": 0}, {"key": "y", "a": 0, "b": 10}]
    ranking.score(rows, SIGNALS, {"a": 50, "b": 50})
    b = rows[0]["score_breakdown"]["a"]
    assert b["label"] == "A" and b["field"] == "a" and b["raw"] == 10
    assert b["normalized"] == 100.0 and b["weight"] == 50
    # Only 'a' contributes for this row (a=100 normalised, b=0 normalised),
    # so its points equal the row's own score.
    assert b["points"] == rows[0]["score"]


def test_dampened_flag_matches_quality_gate():
    rows = [
        {"key": "has", "a": 10, "b": 10},
        {"key": "lacks", "b": 5},
    ]
    ranking.score(rows, SIGNALS, {"a": 50, "b": 50}, quality_signals=("a",), dampen=0.5)
    by = {r["key"]: r for r in rows}
    assert by["has"]["dampened"] is False
    assert by["lacks"]["dampened"] is True


def test_zero_weight_signal_is_skipped():
    rows = [{"key": "x", "a": 1, "b": 1}, {"key": "y", "a": 10, "b": 1}]
    ranking.score(rows, SIGNALS, {"a": 0, "b": 100})
    assert "a" not in rows[0]["score_breakdown"]


def test_ranks_are_assigned_and_nulls_sort_last():
    rows = [{"key": "x"}, {"key": "y", "a": 10}, {"key": "z", "a": 1}]
    ranked = ranking.score(rows, SIGNALS, WEIGHTS)
    assert [r["key"] for r in ranked] == ["y", "z", "x"]
    assert ranked[0]["rank"] == 1 and ranked[-1]["score"] is None


# ── event rules ──────────────────────────────────────────────────────────
def test_new_leader_fires_only_on_change():
    rule = NewLeaderRule()
    prev = [{"key": "a", "rank": 1}]
    same = [{"key": "a", "rank": 1, "name": "A", "score": 9}]
    diff = [{"key": "b", "rank": 1, "name": "B", "score": 9}]
    assert rule.board(prev, same, 0) == []
    assert len(rule.board(prev, diff, 0)) == 1


def test_new_entrant_respects_max_rank_and_required_fields():
    rule = NewEntrantRule(max_rank=10, require_any=("x",))
    assert rule.row(None, {"name": "n", "rank": 5, "x": 1, "score": 1}, 0)
    assert rule.row(None, {"name": "n", "rank": 50, "x": 1}, 0) == []   # too low
    assert rule.row(None, {"name": "n", "rank": 5}, 0) == []            # no x
    assert rule.row({"rank": 9}, {"name": "n", "rank": 5, "x": 1}, 0) == []  # not new


def test_climber_fires_on_rank_or_score():
    rule = ClimberRule(rank_delta=10, score_delta=5.0)
    base = {"name": "n", "rank": 5, "score": 1}
    assert rule.row({"rank": 20, "score": 1}, base, 0)                     # rank
    assert rule.row({"rank": 6, "score": 1}, {**base, "score": 9}, 0)      # score
    assert rule.row({"rank": 6, "score": 1}, base, 0) == []                # neither


def test_delta_rule_direction_and_min_abs():
    rule = DeltaRule(field="p", direction="down", frac=0.25, min_abs=10)
    row = {"name": "n"}
    assert rule.row({"p": 100}, {**row, "p": 50}, 0)          # -50%
    assert rule.row({"p": 100}, {**row, "p": 90}, 0) == []    # only -10%
    assert rule.row({"p": 100}, {**row, "p": 200}, 0) == []   # wrong direction
    # big fractional move but tiny absolute move is filtered by min_abs
    assert rule.row({"p": 10}, {**row, "p": 1}, 0) == []


def test_delta_rule_up_and_either():
    up = DeltaRule(field="p", direction="up", frac=0.25)
    either = DeltaRule(field="p", direction="either", frac=0.25)
    row = {"name": "n"}
    assert up.row({"p": 100}, {**row, "p": 200}, 0)
    assert up.row({"p": 100}, {**row, "p": 50}, 0) == []
    assert either.row({"p": 100}, {**row, "p": 50}, 0)
    assert either.row({"p": 100}, {**row, "p": 200}, 0)


def test_threshold_rule_detects_both_crossings():
    rule = ThresholdRule(field="v", level=0.0,
                         headline_above="up over {level}",
                         headline_below="down under {level}")
    row = {"name": "curve"}
    assert rule.row({"v": -0.1}, {**row, "v": 0.1}, 0)[0]["headline"] == "up over 0.0"
    assert rule.row({"v": 0.1}, {**row, "v": -0.1}, 0)[0]["headline"] == "down under 0.0"
    assert rule.row({"v": 0.1}, {**row, "v": 0.2}, 0) == []   # no crossing
    assert rule.row(None, {**row, "v": 0.2}, 0) == []         # nothing to cross from


# ── cache resilience ─────────────────────────────────────────────────────
def test_cache_keeps_stale_value_over_a_total_outage():
    """A transient total-outage fetch must not overwrite a still-usable
    cache with a blackout that then gets served as truth for the rest of
    the TTL — this is the bug that briefly zeroed out Holmdel's research
    signal when OpenAlex rate-limited an entire sweep at once."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp   # isolate from the real data/ directory
        good = {"a": 1, "b": 2}
        assert cache.cached("k", 3600, lambda: good) == good
        # ttl=0 forces a miss even though the good value is fresh.
        empty = cache.cached("k", 0, lambda: {}, is_empty=lambda r: not r)
        assert empty == good
        # A producer that succeeds should still overwrite normally.
        better = {"a": 9}
        assert cache.cached("k", 0, lambda: better, is_empty=lambda r: not r) == better
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_writes_empty_result_when_nothing_cached_yet():
    """No prior cache to fall back to -> the empty result is recorded, not
    silently discarded forever."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp
        assert cache.cached("k", 3600, lambda: {}, is_empty=lambda r: not r) == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── backfill ─────────────────────────────────────────────────────────────
def _isolated_scope(cls):
    """Instantiate a test Telescope with its cache/store redirected to a
    throwaway tmp dir, so tests never touch the real data/ directory beyond
    the harmless empty namespace folder __init__ always creates."""
    tmp = tempfile.mkdtemp()
    scope = cls()
    scope.cache.dir = tmp
    scope.store.dir = os.path.join(tmp, "history")
    scope.store.events_file = os.path.join(tmp, "events.json")
    os.makedirs(scope.store.dir, exist_ok=True)
    return scope, tmp


def test_backfill_seeds_a_real_baseline_so_first_sweep_can_announce():
    """historical_rows() lets a telescope's very first sweep diff against a
    genuine one-period-ago reading instead of announcing nothing until a
    second live sweep — which at a 24h poll cadence is a full day away."""
    class Scope(Telescope):
        slug = "test_backfill_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        rules = (DeltaRule(field="value", direction="up", frac=0.5, min_abs=1,
                           type="test_delta"),)
        snapshot_fields = ("value",)
        poll_seconds = 3600

        def collect(self, force=False):
            # A second, unchanging row gives normalisation something to
            # scale against -- with only one row ever in play, every score
            # normalises to the same flat 0 regardless of its raw value,
            # which would make prev and curr look "unchanged" for reasons
            # that have nothing to do with backfill.
            return [
                {"key": "a", "name": "A", "value": 10,
                 "noise": False, "sources": ["x"], "link": ""},
                {"key": "b", "name": "B", "value": 1,
                 "noise": False, "sources": ["x"], "link": ""},
            ]

        def historical_rows(self, rows):
            # A real prior-window reading, the same shape Holmdel/Jackson
            # already have on hand -- not fabricated for the test.
            return [
                {"key": "a", "name": "A", "value": 1,
                 "noise": False, "sources": ["x"], "link": ""},
                {"key": "b", "name": "B", "value": 1,
                 "noise": False, "sources": ["x"], "link": ""},
            ]

    scope, tmp = _isolated_scope(Scope)
    try:
        events = scope.sweep()
        assert events, "a genuine prior reading exists — the first sweep should announce it"
        assert events[0]["headline"] and "{" not in events[0]["headline"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_backfill_is_the_honest_default_on_a_true_first_sweep():
    """A telescope that doesn't override historical_rows() (Kepler: Form D
    filings are discrete events, nothing to reconstruct) must announce
    nothing on its first sweep rather than fabricate a baseline."""
    class Scope(Telescope):
        slug = "test_no_backfill_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        rules = (DeltaRule(field="value", direction="up", frac=0.5, min_abs=1,
                           type="test_delta"),)
        snapshot_fields = ("value",)

        def collect(self, force=False):
            return [{"key": "a", "name": "A", "value": 10,
                     "noise": False, "sources": ["x"], "link": ""}]

    scope, tmp = _isolated_scope(Scope)
    try:
        assert scope.sweep() == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_backfill_never_overwrites_real_history():
    """seed() must be a no-op once a real snapshot already exists — it's a
    first-sweep-only fallback, not a way to rewrite what actually happened."""
    class Scope(Telescope):
        slug = "test_backfill_no_overwrite_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        snapshot_fields = ("value",)

        def collect(self, force=False):
            return [{"key": "a", "name": "A", "value": 10,
                     "noise": False, "sources": ["x"], "link": ""}]

        def historical_rows(self, rows):
            return [{"key": "a", "name": "A", "value": 1,
                     "noise": False, "sources": ["x"], "link": ""}]

    scope, tmp = _isolated_scope(Scope)
    try:
        scope.sweep()
        first_count = scope.store.count()
        scope.store.seed(scope.rank([{"key": "a", "name": "A", "value": 999,
                                       "noise": False, "sources": ["x"], "link": ""}]),
                          0.0)
        assert scope.store.count() == first_count
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── safe_sweep() error tracking ──────────────────────────────────────────
def test_safe_sweep_sets_last_error_on_failure_without_crashing():
    class Scope(Telescope):
        slug = "test_safe_sweep_fail_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        snapshot_fields = ("value",)

        def collect(self, force=False):
            raise RuntimeError("source is down")

    scope, tmp = _isolated_scope(Scope)
    try:
        assert scope._last_error is None
        events = scope.safe_sweep()
        assert events == []
        assert scope._last_error == "source is down"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_safe_sweep_clears_a_stale_last_error_on_recovery():
    """A telescope that failed once and then recovered must not keep
    reporting the old error forever -- the poller relies on _last_error
    being accurate for *this* attempt, not some earlier one, to decide
    whether to advance its own retry schedule."""
    calls = {"n": 0}

    class Scope(Telescope):
        slug = "test_safe_sweep_recover_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        snapshot_fields = ("value",)

        def collect(self, force=False):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient outage")
            return [{"key": "a", "name": "A", "value": 10,
                     "noise": False, "sources": ["x"], "link": ""}]

    scope, tmp = _isolated_scope(Scope)
    try:
        scope.safe_sweep()
        assert scope._last_error == "transient outage"
        scope.safe_sweep()
        assert scope._last_error is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_crossover_rule_reads_leading_from_prev_and_lagging_from_curr():
    """Holmdel's 'research -> builders' shape: unlike every other rule, the
    two fields it compares live at two different points in time."""
    rule = CrossoverRule(
        leading_field="paper_growth", leading_min=40,
        lagging_field="repo_growth", lagging_min=75,
    )
    prev = {"paper_growth": 60}
    curr = {"name": "n", "repo_growth": 90}
    assert rule.row(prev, curr, 0)
    # A *current* paper_growth spike doesn't count -- it must come from prev.
    assert rule.row({"paper_growth": 0}, {**curr, "paper_growth": 90}, 0) == []
    # Leading was too weak.
    assert rule.row({"paper_growth": 10}, curr, 0) == []
    # Lagging didn't clear its own bar.
    assert rule.row(prev, {**curr, "repo_growth": 10}, 0) == []
    # No prior snapshot at all -- nothing to lead from.
    assert rule.row(None, curr, 0) == []
    # Either field simply absent (signal never populated) -- no false fire.
    assert rule.row({}, curr, 0) == []
    assert rule.row(prev, {"name": "n"}, 0) == []
    # Lagging was ALREADY elevated last sweep too -- a steady state, not a
    # transition. Found live: with a real, non-None leading signal, an
    # unchanged snapshot compared against itself was firing every time,
    # since nothing previously required the lagging signal to be new.
    assert rule.row({"paper_growth": 60, "repo_growth": 90}, curr, 0) == []


def test_bad_headline_template_does_not_raise():
    rule = NewEntrantRule(max_rank=10, headline="{nonexistent_field}")
    events = rule.row(None, {"name": "n", "rank": 1}, 0)
    assert events and events[0]["headline"]


def test_none_field_renders_as_a_dash_not_the_word_none():
    """A field that's None (present, unknown -- not absent from the row)
    used to render as the literal text 'None' in a headline: found live
    while verifying backfill, which fires enough climbers at once to make
    this common combination (a real rank climb, no HN growth reading yet)
    actually show up."""
    rule = ClimberRule(rank_delta=1, score_delta=0,
                        headline="{name} moved (HN {hn_growth}%).")
    events = rule.row({"rank": 5, "score": 1}, {"name": "n", "rank": 1, "score": 1,
                                                 "hn_growth": None}, 0)
    assert events[0]["headline"] == "n moved (HN —%)."


# ── signal-quality feedback ──────────────────────────────────────────────
def test_flag_flip_rule_fires_only_on_the_declared_direction():
    """Kepler's 'stealth graduated' shape: a detection proving out is a flag
    that used to be true no longer being so. Must not fire the other way,
    on an unrelated field, or when the flag was already flipped before."""
    rule = FlagFlipRule(field="stealth", from_value=True, to_value=False,
                        headline="{name} graduated.")
    assert rule.row({"stealth": True}, {"name": "n", "stealth": False}, 0)
    # Wrong direction -- gaining stealth isn't a graduation.
    assert rule.row({"stealth": False}, {"name": "n", "stealth": True}, 0) == []
    # Already flipped -- not a new transition.
    assert rule.row({"stealth": False}, {"name": "n", "stealth": False}, 0) == []
    # Never was stealth -- nothing to graduate from.
    assert rule.row({"stealth": True}, {"name": "n", "stealth": True}, 0) == []
    # Field simply absent on either side -- no false fire.
    assert rule.row({}, {"name": "n", "stealth": False}, 0) == []
    assert rule.row({"stealth": True}, {"name": "n"}, 0) == []
    assert rule.row(None, {"name": "n", "stealth": False}, 0) == []


# ── morning brief ──────────────────────────────────────────────────────────
def test_brief_render_text_pure_formatting():
    """render_text() is what actually gets pushed to Discord/Slack, so its
    exact shape is worth pinning even though build() itself (which touches
    the registry and each telescope's real collect()) is verified live."""
    digest = {
        "generated_at": 0, "since": 0,
        "sections": [
            {"slug": "x", "name": "X", "glyph": "🔭", "tagline": "T",
             "entity_label": "E", "leader": {"name": "Widget", "score": 88.5},
             "event_count": 2,
             "top_events": [{"headline": "A thing happened.", "type": "t"}]},
            {"slug": "y", "name": "Y", "glyph": "💰", "tagline": "T2",
             "entity_label": "E2", "leader": None, "event_count": 0,
             "top_events": []},
        ],
    }
    text = brief.render_text(digest)
    assert "Widget (88.5)" in text
    assert "A thing happened." in text
    assert "no leader yet" in text
    assert "0 new event(s)" in text


def test_brief_render_text_handles_no_enabled_telescopes():
    text = brief.render_text({"generated_at": 0, "since": 0, "sections": []})
    assert "No telescopes enabled" in text


def test_brief_build_never_calls_collect():
    """Regression test for a real bug: build() used to call
    collect(force=False), which is not the same as cheap -- a telescope's
    cache simply expiring via its own TTL still triggers a real, potentially
    slow, paced live fetch, exactly what a real sweep would do. That
    contradicted build()'s own documented 'always cheap' contract and made
    visiting the BRIEF tab hang for minutes whenever any enabled telescope's
    cache happened to be stale. build() must read store.latest() only."""
    class Scope(Telescope):
        slug = "test_brief_ns"
        name = "TEST"
        glyph = "x"
        tagline = "T"
        entity_label = "E"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        snapshot_fields = ("value",)

        def collect(self, force=False):
            raise AssertionError("brief.build() must never call collect()")

    scope, tmp = _isolated_scope(Scope)
    try:
        scope.store.seed(scope.rank([{"key": "a", "name": "A", "value": 10,
                                       "noise": False, "sources": ["x"], "link": ""}]),
                          0.0)
        with patch.object(registry, "enabled_slugs", return_value=["test_brief_ns"]), \
             patch.object(registry, "get", return_value=scope):
            digest = brief.build(hours=24)
        assert digest["sections"][0]["leader"]["name"] == "A"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── series analytics (Simons/Reddington shared kernel) ──────────────────────
# analyse()/change() are pure functions with no network of their own — real
# coverage previously came only from test_live.py's live fetches, which can't
# tell "the shape happens to still work" from "the math is right". Pinned
# here against synthetic, deterministic history instead.
def _synthetic_points(n=300, start=100.0, step=1.0):
    return [(f"d{i:04d}", start + i * step) for i in range(n)]


def test_change_pct_unit_reports_basis_points():
    assert change([4.50, 4.60], 1, "pct") == 10.0   # 0.10pt move -> 10bps


def test_change_non_pct_unit_reports_percent():
    assert change([100.0, 110.0], 1, "index") == 10.0


def test_change_none_when_not_enough_history():
    assert change([1.0, 2.0], 5, "index") is None


def test_analyse_returns_none_below_min_points():
    s = Series("k", "K", "G", "index", "fred", "X")
    assert analyse(s, _synthetic_points(10), min_points=30) is None


def test_analyse_computes_a_real_row_from_synthetic_history():
    s = Series("k", "K", "G", "index", "fred", "X")
    points = _synthetic_points(300)
    row = analyse(s, points, min_points=30)
    assert row["level"] == points[-1][1]
    assert row["as_of"] == points[-1][0]
    # Monotonically rising series -> the latest point sits above its own
    # trailing mean and at the very top of its own range.
    assert row["z"] > 0
    assert row["pctile"] == 100.0
    assert row["chg_1m"] == change([v for _, v in points], 21, "index")
    assert row["noise"] is False


def test_historical_panel_reconstructs_one_reading_back():
    """historical_panel() backs Simons/Reddington's historical_rows() —
    it must re-run analyse() on the cached points with the latest
    observation dropped, not on the full series, so a telescope's very
    first sweep diffs against a genuinely earlier reading, not today's."""
    class FakeScope:
        def __init__(self, cache):
            self.cache = cache

    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_series_ns")
        cache.dir = tmp
        s = Series("k", "K", "G", "index", "fred", "X")
        points = _synthetic_points(300)
        cache.set(f"series_{s.key}", points)
        scope = FakeScope(cache)

        rows = historical_panel(scope, (s,), min_points=30)
        assert len(rows) == 1
        prior, current = rows[0], analyse(s, points, min_points=30)
        # Series was still rising -- the prior reading must be genuinely
        # lower than today's, not a copy of it.
        assert prior["level"] < current["level"]
        assert prior["as_of"] == points[-2][0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_historical_panel_skips_series_with_no_cached_points():
    """No cache file yet (a telescope's very first sweep, before fetch_panel
    has ever run) -> skip that series rather than crash or fabricate one."""
    class FakeScope:
        def __init__(self, cache):
            self.cache = cache

    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_series_empty_ns")
        cache.dir = tmp
        s = Series("k", "K", "G", "index", "fred", "X")
        scope = FakeScope(cache)
        assert historical_panel(scope, (s,), min_points=30) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fetch_panel_keeps_stale_series_over_a_transient_outage():
    """Each series has its own cache key, so a transient outage on one
    ticker must not overwrite its real cached history with an empty list
    that then gets served as truth for the rest of ttl -- the same failure
    mode that once briefly zeroed Holmdel's OpenAlex signal."""
    class FakeScope:
        def __init__(self, cache):
            self.cache = cache

    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_fetch_panel_ns")
        cache.dir = tmp
        scope = FakeScope(cache)
        s = Series("k", "K", "G", "index", "fred", "X")
        good_points = _synthetic_points(60)

        with patch.dict(series.ADAPTERS, {"fred": lambda ident: good_points}):
            rows = fetch_panel(scope, (s,), ttl=3600, min_points=30)
        assert len(rows) == 1

        # Source goes down entirely on the next sweep (ttl=0 forces a miss).
        with patch.dict(series.ADAPTERS, {"fred": lambda ident: []}):
            rows_after_outage = fetch_panel(scope, (s,), ttl=0, min_points=30)
        assert len(rows_after_outage) == 1
        assert rows_after_outage[0]["level"] == good_points[-1][1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── http retry policy ────────────────────────────────────────────────────
# _request()'s retry/no-retry branching had a real bug (a plain 404 used to
# fall into the same retry loop as a rate limit, wasting ~2.5s of backoff on
# an expected-common case like guessing a job-board slug that doesn't
# exist — fixed, but never pinned by a test). Mocked here rather than hitting
# real network: what's under test is the branching, not any one API.
class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._json_data


def test_request_retries_429_then_succeeds():
    """A rate limit is worth retrying -- confirms a source recovering
    mid-backoff still produces a result rather than giving up early."""
    responses = [_FakeResponse(429), _FakeResponse(200, json_data={"ok": True})]
    with patch("telescope.http.requests.request", side_effect=responses) as mock_req, \
         patch("telescope.http.time.sleep"):
        assert http.get_json("https://example.test/x") == {"ok": True}
    assert mock_req.call_count == 2


def test_request_does_not_retry_a_plain_404():
    """The regression test for the bug described above: a 404 means 'no
    such resource', not 'try again', and must not sleep/retry at all."""
    with patch("telescope.http.requests.request",
               return_value=_FakeResponse(404)) as mock_req, \
         patch("telescope.http.time.sleep") as mock_sleep:
        try:
            http.get_json("https://example.test/missing")
            assert False, "expected SourceError"
        except http.SourceError:
            pass
    assert mock_req.call_count == 1
    mock_sleep.assert_not_called()


def test_request_retries_5xx_until_exhausted():
    with patch("telescope.http.requests.request",
               return_value=_FakeResponse(503)) as mock_req, \
         patch("telescope.http.time.sleep"):
        try:
            http.get_json("https://example.test/down")
            assert False, "expected SourceError"
        except http.SourceError:
            pass
    assert mock_req.call_count == http.RETRIES


def test_request_retries_network_exceptions_until_exhausted():
    with patch("telescope.http.requests.request",
               side_effect=requests.ConnectionError("boom")) as mock_req, \
         patch("telescope.http.time.sleep"):
        try:
            http.get_json("https://example.test/unreachable")
            assert False, "expected SourceError"
        except http.SourceError:
            pass
    assert mock_req.call_count == http.RETRIES


def test_try_json_returns_default_on_permanent_failure():
    with patch("telescope.http.requests.request",
               return_value=_FakeResponse(404)), \
         patch("telescope.http.time.sleep"):
        assert http.try_json("https://example.test/missing",
                             default="fallback") == "fallback"


def test_probe_text_makes_exactly_one_attempt():
    """probe_text is for speculative guesses (most wrong, e.g. a guessed
    company domain) -- it must never retry, unlike get_text/try_text's
    known-good-API backoff policy. A regression here would turn a batch of
    32 domain guesses into minutes of pure waste, the same failure mode the
    404-retry bug caused elsewhere."""
    with patch("telescope.http.requests.get",
               side_effect=requests.ConnectionError("boom")) as mock_get:
        assert http.probe_text("https://guessed-domain.test/") is None
    assert mock_get.call_count == 1


# ── notifier ─────────────────────────────────────────────────────────────
# Every channel here is opt-in via an env var, and dispatch() is supposed to
# keep "just movers" out of social entirely -- both properties had zero
# tests despite being exactly the kind of silent-failure-mode logic (a
# missing env var accidentally still POSTing, or a new event type quietly
# starting to spam a timeline) worth pinning.
class _StubScope:
    slug = "test_scope"
    name = "TEST SCOPE"


def test_dispatch_only_pushes_social_event_types_to_channels():
    """Movers/deltas stay in the dashboard feed only -- a new event type
    must opt into SOCIAL_TYPES explicitly, not reach a channel by default."""
    calls = []

    def fake_channel(event, scope):
        calls.append(event["type"])

    events = [
        {"type": "new_leader", "headline": "A"},   # in SOCIAL_TYPES
        {"type": "big_climber", "headline": "B"},  # not
    ]
    with patch("telescope.notifier.CHANNELS", (fake_channel,)):
        notifier.dispatch(events, _StubScope())
    assert calls == ["new_leader"]


def test_post_to_discord_noops_without_a_webhook_configured():
    """Unconfigured is the honest default: no crash, no accidental POST to
    an empty URL, just False."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_to_discord({"headline": "x"}, _StubScope()) is False
    mock_post.assert_not_called()


def test_post_to_discord_posts_when_webhook_configured():
    env = {"OBSERVATORY_DISCORD_WEBHOOK": "https://discord.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_to_discord({"headline": "x"}, _StubScope()) is True
    mock_post.assert_called_once()


def test_post_to_discord_swallows_a_request_exception():
    env = {"OBSERVATORY_DISCORD_WEBHOOK": "https://discord.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post",
               side_effect=requests.ConnectionError("boom")):
        assert notifier.post_to_discord({"headline": "x"}, _StubScope()) is False


def test_post_to_x_noops_without_credentials():
    """Must short-circuit on the env-var check before ever importing tweepy
    -- tweepy is an optional dependency (pip install tweepy) that may not
    even be present in an unconfigured install."""
    with patch.dict(os.environ, {}, clear=True):
        assert notifier.post_to_x({"headline": "x"}, _StubScope()) is False


def test_dispatch_brief_always_logs_regardless_of_channel_config():
    """A digest with no webhooks configured still reaches stdout -- the
    same unconfigured-is-honest guarantee every other channel here has."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post, \
         patch("builtins.print") as mock_print:
        notifier.dispatch_brief("the digest text")
    mock_print.assert_called_once()
    mock_post.assert_not_called()


# ── registry (enable/disable toggle state) ──────────────────────────────
# Pure logic, no network -- but it's module-global state (_classes,
# _instances, a JSON file on disk), so every test isolates it completely
# rather than touch the real registry other tests/the app rely on.
class _FakeTelescope:
    slug = "fake_a"
    name = "FAKE A"
    domain = "TEST"
    tagline = "T"
    glyph = "x"
    entity_label = "E"
    sources_label = "S"
    caveat = "C"
    poll_seconds = 3600


class _FakeTelescopeB(_FakeTelescope):
    slug = "fake_b"
    name = "FAKE B"


@contextmanager
def _isolated_registry():
    saved = (dict(registry._classes), dict(registry._instances),
              dict(registry._load_errors), registry.STATE_FILE)
    registry._classes.clear()
    registry._instances.clear()
    registry._load_errors.clear()
    tmp = tempfile.mkdtemp()
    registry.STATE_FILE = os.path.join(tmp, "observatory.json")
    try:
        yield
    finally:
        classes, instances, errors, state_file = saved
        registry._classes.clear()
        registry._classes.update(classes)
        registry._instances.clear()
        registry._instances.update(instances)
        registry._load_errors.clear()
        registry._load_errors.update(errors)
        registry.STATE_FILE = state_file
        shutil.rmtree(tmp, ignore_errors=True)


def test_register_adds_to_the_class_table():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        assert registry.all_slugs() == ["fake_a"]


def test_default_state_all_enables_every_registered_slug():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        registry.register(_FakeTelescopeB)
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "all"}, clear=True):
            assert registry._default_state() == {"fake_a": True, "fake_b": True}


def test_default_state_comma_list_only_enables_named_slugs():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        registry.register(_FakeTelescopeB)
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "fake_a"}, clear=True):
            assert registry._default_state() == {"fake_a": True, "fake_b": False}


def test_unknown_registered_slug_inherits_the_default_not_silently_enabled():
    """The documented subtlety in registry.state(): a slug with no entry in
    the persisted file yet (a newly-added pack on an existing install) must
    fall back to the env default, not come up enabled just for existing."""
    with _isolated_registry():
        registry.register(_FakeTelescope)
        registry.register(_FakeTelescopeB)
        registry._write_state({"fake_a": True})   # fake_b was never saved
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "hubble"}, clear=True):
            assert registry.state() == {"fake_a": True, "fake_b": False}


def test_set_enabled_persists_without_clobbering_other_slugs():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        registry.register(_FakeTelescopeB)
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "none"}, clear=True):
            registry.set_enabled("fake_a", True)
            registry.set_enabled("fake_b", True)
            assert registry.is_enabled("fake_a") is True
            registry.set_enabled("fake_a", False)
            # Flipping fake_a back off must not disturb fake_b's own state.
            assert registry.is_enabled("fake_a") is False
            assert registry.is_enabled("fake_b") is True


def test_set_enabled_unknown_slug_raises():
    with _isolated_registry():
        try:
            registry.set_enabled("nope", True)
            assert False, "expected KeyError"
        except KeyError:
            pass


def test_get_unknown_slug_raises():
    with _isolated_registry():
        try:
            registry.get("nope")
            assert False, "expected KeyError"
        except KeyError:
            pass


def test_get_memoizes_the_instance():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        assert registry.get("fake_a") is registry.get("fake_a")


def test_enabled_slugs_returns_only_enabled_ones_sorted():
    with _isolated_registry():
        registry.register(_FakeTelescopeB)
        registry.register(_FakeTelescope)
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "none"}, clear=True):
            registry.set_enabled("fake_a", True)
            assert registry.enabled_slugs() == ["fake_a"]


def test_catalog_reports_static_metadata_and_enabled_flag_without_fetching():
    with _isolated_registry():
        registry.register(_FakeTelescope)
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "none"}, clear=True):
            registry.set_enabled("fake_a", True)
            entry = registry.catalog()[0]
        assert entry["slug"] == "fake_a"
        assert entry["name"] == "FAKE A"
        assert entry["enabled"] is True
