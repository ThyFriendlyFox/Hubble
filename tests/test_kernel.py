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

from telescope import ranking                                    # noqa: E402
from telescope.cache import Cache                                # noqa: E402
from telescope.events import (ClimberRule, CrossoverRule, DeltaRule,  # noqa: E402
                              NewEntrantRule, NewLeaderRule, ThresholdRule)
from telescope.ranking import Signal                             # noqa: E402

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


def test_bad_headline_template_does_not_raise():
    rule = NewEntrantRule(max_rank=10, headline="{nonexistent_field}")
    events = rule.row(None, {"name": "n", "rank": 1}, 0)
    assert events and events[0]["headline"]
