"""Kernel unit tests — no network, pure logic.

These pin the behaviour every telescope inherits: normalisation, weight
renormalisation around missing signals, the quality dampener, and each event
rule's firing conditions.
"""
import os
import sys
import threading

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
from telescope.parse import to_float                             # noqa: E402
from telescope.events import (ClimberRule, CrossoverRule, DeltaRule,  # noqa: E402
                              FlagFlipRule, NewEntrantRule, NewLeaderRule,
                              ThresholdRule, money)
from telescope.ranking import Signal                             # noqa: E402
from telescope.series import (Series, analyse, change,               # noqa: E402
                              fetch_panel, historical_panel)
from telescope.snapshots import SnapshotStore                       # noqa: E402

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


def test_normalise_log_scales_before_mapping_to_0_100():
    """Several real telescopes declare log=True signals for heavy-tailed
    counts (Kepler's raise size, Jackson's obligations) -- normalise()'s
    own log-scaling branch had never been exercised by a kernel test,
    found via coverage.py."""
    out = ranking.normalise([1, 10, 100], log=True)
    assert out[0] == 0.0
    assert out[2] == 100.0
    # A log-scaled midpoint sits well below where the linear midpoint
    # would -- confirms the log transform actually ran, not a
    # coincidentally-passing linear scale.
    assert out[1] < 50.0


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


def test_new_entrant_require_field_partitions_without_overlap():
    """Kepler declares this rule twice (stealth_raise vs. new_candidate)
    with opposite require_value on the same field, relying on every new
    entrant firing exactly one of the two, never both and never neither."""
    stealth = NewEntrantRule(require_field="stealth", require_value=True,
                             type="stealth_raise")
    normal = NewEntrantRule(require_field="stealth", require_value=False,
                            type="new_candidate")
    row_stealth = {"name": "n", "rank": 5, "score": 1, "stealth": True}
    row_normal = {"name": "n", "rank": 5, "score": 1, "stealth": False}
    assert stealth.row(None, row_stealth, 0)[0]["type"] == "stealth_raise"
    assert normal.row(None, row_stealth, 0) == []
    assert normal.row(None, row_normal, 0)[0]["type"] == "new_candidate"
    assert stealth.row(None, row_normal, 0) == []


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


def test_threshold_rule_no_crossing_when_the_field_is_missing():
    """Distinct from 'nothing to cross from' (prev is None entirely) above
    -- prev exists but doesn't carry this field, e.g. a telescope adding a
    new gauge mid-run. Found via coverage.py."""
    rule = ThresholdRule(field="v", level=0.0)
    assert rule.row({"other": 1}, {"name": "n", "v": 0.2}, 0) == []


def test_new_leader_board_returns_nothing_when_no_one_holds_rank_1():
    """Distinct from 'the same leader as before' (already tested) --
    curr_rows might have no rank-1 row at all (e.g. every row got
    filtered to noise). Found via coverage.py."""
    rule = NewLeaderRule()
    assert rule.board([{"key": "a", "rank": 1}], [{"key": "b", "rank": 2}], 0) == []


def test_board_is_a_no_op_for_every_row_only_rule():
    """ClimberRule/ThresholdRule/CrossoverRule/FlagFlipRule only ever fire
    from row(); their board() is an intentional no-op stub -- never
    directly confirmed by any test, found via coverage.py."""
    for rule in (ClimberRule(), ThresholdRule(), CrossoverRule(), FlagFlipRule()):
        assert rule.board([{"key": "a", "rank": 1}], [{"key": "a", "rank": 2}], 0) == []


def test_climber_row_returns_nothing_without_a_real_prior_rank():
    """Distinct from 'moved but not enough' (already tested) -- a genuinely
    new entrant (prev=None) or a row missing rank entirely can't have a
    rank delta computed at all. Found via coverage.py."""
    rule = ClimberRule(rank_delta=10, score_delta=5.0)
    curr = {"name": "n", "rank": 5, "score": 1}
    assert rule.row(None, curr, 0) == []
    assert rule.row({"score": 1}, curr, 0) == []            # prev has no rank
    assert rule.row({"rank": 20, "score": 1}, {"name": "n", "score": 1}, 0) == []  # curr has no rank


def test_delta_rule_row_returns_nothing_without_a_real_prior_or_current_value():
    """Distinct from 'moved but not enough' (already tested) -- a genuinely
    new entrant (prev=None), a zero/missing prior value (can't compute a
    fraction against it), or a missing current value. Found via
    coverage.py."""
    rule = DeltaRule(field="p", direction="down", frac=0.25)
    curr = {"name": "n", "p": 50}
    assert rule.row(None, curr, 0) == []
    assert rule.row({"p": 0}, curr, 0) == []            # can't fraction against zero
    assert rule.row({"p": 100}, {"name": "n"}, 0) == []   # curr missing the field


def test_delta_rule_either_direction_ignores_a_small_move():
    """test_delta_rule_up_and_either already confirms 'either' fires on a
    real move in both directions; this is the other half -- a move too
    small in either direction must not fire. Found via coverage.py."""
    rule = DeltaRule(field="p", direction="either", frac=0.25)
    assert rule.row({"p": 100}, {"name": "n", "p": 105}, 0) == []


def test_delta_rule_uses_a_custom_formatter_when_given_one():
    """money() is documented as shared because three domain packs
    independently needed it for exactly this -- DeltaRule with a real
    custom formatter had never actually been exercised, only the default
    numeric rendering. Found via coverage.py."""
    rule = DeltaRule(field="p", direction="up", frac=0.25, formatter=money,
                     headline="{name}: {old_fmt} -> {new_fmt}")
    events = rule.row({"p": 1_000_000}, {"name": "n", "p": 2_000_000}, 0)
    assert events[0]["headline"] == "n: $1.0M -> $2.0M"


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


def test_cache_hit_never_calls_the_producer_again():
    """The single most common real-world path -- a sweep landing inside a
    still-fresh TTL -- had never actually been confirmed: every existing
    test only ever exercised the miss/stale-fallback branches. Found via
    coverage.py: get()'s own hit branch had zero direct coverage despite
    being what every telescope's own routine, in-TTL re-collect() actually
    hits most of the time."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp
        calls = {"n": 0}

        def producer():
            calls["n"] += 1
            return {"v": calls["n"]}

        first = cache.cached("k", 3600, producer)
        second = cache.cached("k", 3600, producer)
        assert first == second == {"v": 1}
        assert calls["n"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_age_reports_seconds_since_write_and_none_when_missing():
    """age() feeds meta()'s freshness readout shown on every board -- had
    no test of its own at all, found via coverage.py."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp
        assert cache.age("never-written") is None
        cache.set("k", {"v": 1})
        age = cache.age("k")
        assert age is not None and 0 <= age < 5
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_get_treats_a_corrupted_file_as_a_miss():
    """A cache file truncated mid-write (a real crash-during-write risk,
    not just hypothetical -- set() writes to a .tmp file and os.replace()s
    it specifically to avoid this, but a pre-existing corrupted file from
    some other cause should still degrade to a clean miss, not raise and
    take a sweep down."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp
        os.makedirs(tmp, exist_ok=True)
        with open(cache._path("k"), "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert cache.get("k", 3600) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_set_survives_concurrent_writers_on_the_same_key():
    """Found live: a ?refresh=1 request's background announce-sweep thread
    racing a second forced sweep of the same telescope crashed with
    FileNotFoundError, because set() wrote every key to a single shared
    "<key>.tmp" path -- the loser's os.replace() found its own tmp file
    already consumed by the winner's rename. Two real threads hammering the
    same key concurrently must all succeed and leave a valid last-write-
    wins value on disk, not race on the tmp filename."""
    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_ns")
        cache.dir = tmp
        errors = []

        def write(n):
            try:
                cache.set("k", {"v": n})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent set() raised: {errors}"
        assert cache.get("k", 3600)["v"] in range(16)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── lazy directory creation ──────────────────────────────────────────────
# Found by inspecting the real data/ directory and finding a dozen empty
# leftover namespace folders (test_snapshot_ns, test_panels_shape_ns, and
# more) -- every _isolated_scope()/_isolated_store() call used to leak one,
# because Cache.__init__/SnapshotStore.__init__ eagerly created their
# on-disk directory under the real ROOT before the test ever got a chance
# to redirect .dir/.dir+events_file elsewhere. Directory creation is now
# deferred to the first real write.
def test_cache_construction_does_not_touch_disk():
    tmp = tempfile.mkdtemp()
    try:
        with patch("telescope.cache.ROOT", tmp):
            cache = Cache("never_written_ns")
            assert not os.path.exists(cache.dir)
            assert cache.get("k", 3600) is None    # reads tolerate it too
            assert not os.path.exists(cache.dir)
            cache.set("k", {"a": 1})
            assert os.path.exists(cache.dir)        # only a real write creates it
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_store_construction_does_not_touch_disk():
    tmp = tempfile.mkdtemp()
    try:
        with patch("telescope.snapshots.ROOT", tmp):
            store = SnapshotStore("never_written_ns")
            ns_dir = os.path.join(tmp, "never_written_ns")
            assert not os.path.exists(ns_dir)
            assert store.latest() is None           # reads tolerate it too
            assert not os.path.exists(ns_dir)
            store.record([{"key": "a", "name": "A", "rank": 1, "score": 1,
                           "noise": False}])
            assert os.path.exists(store.dir)         # only a real write creates it
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── snapshot store ───────────────────────────────────────────────────────
# telescope/snapshots.py backs store.latest() (relied on this session by
# brief.py and every cross-telescope panel read), record()'s diff/dedupe,
# and seed()'s backfill guarantee -- load-bearing kernel behaviour with no
# direct test of its own before this, only indirect coverage through
# Telescope.sweep() in the backfill tests below.
def _isolated_store(**kw):
    tmp = tempfile.mkdtemp()
    store = SnapshotStore("test_snapshot_ns", **kw)
    store.dir = os.path.join(tmp, "history")
    store.events_file = os.path.join(tmp, "events.json")
    os.makedirs(store.dir, exist_ok=True)
    return store, tmp


def test_snapshot_store_latest_is_none_before_any_save():
    store, tmp = _isolated_store()
    try:
        assert store.latest() is None
        assert store.count() == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_saves_a_snapshot_and_returns_no_events_on_first_sweep():
    """Nothing to diff against yet, so no events -- but the snapshot must
    still be persisted, or the *next* sweep would have nothing to diff
    against either and no telescope would ever announce anything."""
    store, tmp = _isolated_store(rules=[NewLeaderRule()])
    try:
        rows = [{"key": "a", "name": "A", "rank": 1, "score": 90, "noise": False}]
        assert store.record(rows) == []
        assert store.count() == 1
        assert store.latest()["rows"][0]["key"] == "a"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_fires_events_and_saves_a_new_snapshot_on_a_real_change():
    store, tmp = _isolated_store(rules=[NewLeaderRule()])
    try:
        rows_a = [{"key": "a", "name": "A", "rank": 1, "score": 90, "noise": False}]
        rows_b = [{"key": "b", "name": "B", "rank": 1, "score": 95, "noise": False},
                  {"key": "a", "name": "A", "rank": 2, "score": 80, "noise": False}]
        # Two real _save() calls in the same test could otherwise land on
        # the same millisecond-granularity filename and silently overwrite
        # each other -- a real risk this fast, never a real one in
        # production where sweeps are hours apart.
        with patch("telescope.snapshots.time.time", side_effect=[1000.0, 1001.0]):
            store.record(rows_a)
            events = store.record(rows_b)
        assert len(events) == 1
        assert events[0]["type"] == "new_leader"
        assert events[0]["telescope"] == "test_snapshot_ns"   # diff()'s setdefault
        assert store.count() == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_record_skips_saving_a_new_snapshot_when_nothing_changed():
    """The _unchanged() short-circuit: identical rank/score across a sweep
    must not grow the history file or fire events -- this is what keeps a
    quiet telescope's history from filling with near-duplicate snapshots."""
    store, tmp = _isolated_store(rules=[NewLeaderRule()])
    try:
        rows = [{"key": "a", "name": "A", "rank": 1, "score": 90, "noise": False}]
        store.record(rows)
        assert store.count() == 1
        assert store.record(list(rows)) == []   # a fresh list, same values
        assert store.count() == 1                # no new file written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_store_latest_treats_a_corrupted_file_as_none():
    """A snapshot file truncated or otherwise corrupted must degrade to
    'nothing recorded yet', not crash every route that reads latest() --
    found via coverage.py, the third instance this session of the same
    corrupted-file resilience shape (Cache.get(), registry._read_state(),
    now here)."""
    store, tmp = _isolated_store()
    try:
        store._save([{"key": "a", "name": "A"}], 1000.0)
        path = os.path.join(store.dir, store._files()[0])
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert store.latest() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_store_load_events_treats_a_corrupted_file_as_empty():
    store, tmp = _isolated_store()
    try:
        store.append_events([{"ts": 1, "type": "x"}])
        with open(store.events_file, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        assert store.load_events() == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_snapshot_store_prunes_old_snapshots_beyond_keep_limit():
    """KEEP_SNAPSHOTS bounds disk usage to the most recent N -- never
    previously confirmed to actually prune anything, found via
    coverage.py."""
    store, tmp = _isolated_store()
    try:
        with patch("telescope.snapshots.KEEP_SNAPSHOTS", 3):
            for i in range(5):
                store._save([{"key": "a", "name": "A", "score": i}], 1000.0 + i)
        assert store.count() == 3
        assert store.latest()["rows"][0]["score"] == 4   # the most recent 3 survive
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_diff_excludes_noise_rows_from_per_row_rules_but_not_board_rules():
    """A 'noise' row (can't really be judged) must not fire per-row event
    rules like NewEntrantRule even if it would otherwise qualify -- but
    board-level rules (NewLeaderRule, seeing curr_rows directly) still see
    it, since a noisy row can still legitimately be the new #1. Never
    directly confirmed before, found via coverage.py."""
    store, tmp = _isolated_store(rules=[NewEntrantRule(max_rank=10), NewLeaderRule()])
    try:
        prev = {"rows": []}
        noisy_new_leader = [{"key": "a", "name": "A", "rank": 1, "score": 90,
                             "noise": True}]
        events = store.diff(prev, noisy_new_leader, 0)
        types = [e["type"] for e in events]
        assert "new_leader" in types
        assert "new_entrant" not in types
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_seed_writes_only_when_history_is_genuinely_empty():
    """seed() is Telescope._maybe_backfill's synthetic-but-real baseline --
    it must never overwrite real recorded history, only fill a true gap."""
    store, tmp = _isolated_store()
    try:
        store.seed([{"key": "a", "name": "A", "rank": 1, "score": 50}], ts=1000.0)
        assert store.count() == 1
        assert store.latest()["ts"] == 1000.0

        store.record([{"key": "a", "name": "A", "rank": 1, "score": 99,
                        "noise": False}])
        assert store.count() == 2

        store.seed([{"key": "z", "name": "Z", "rank": 1, "score": 1}], ts=2000.0)
        assert store.count() == 2                        # refused to overwrite
        assert store.latest()["rows"][0]["key"] == "a"    # real history intact
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_load_events_respects_since_and_limit_and_stays_newest_first():
    store, tmp = _isolated_store()
    try:
        store._append_events([{"ts": 100, "type": "x"}])
        store._append_events([{"ts": 200, "type": "y"}])   # newest prepended
        assert [e["ts"] for e in store.load_events()] == [200, 100]
        assert store.load_events(since=150) == [{"ts": 200, "type": "y"}]
        assert len(store.load_events(limit=1)) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_append_events_is_the_public_counterpart_to_record():
    """append_events() exists for a domain pack that detects real events
    outside the normal per-sweep row diff -- Simons' 13F whale-move
    detection is the first case: those events come from comparing two SEC
    filings, not two ranked snapshots, so record()'s diff() doesn't apply,
    but the events still need to land in the same events.json record() itself
    writes to."""
    store, tmp = _isolated_store()
    try:
        store.append_events([{"ts": 100, "type": "whale_move"}])
        assert store.load_events() == [{"ts": 100, "type": "whale_move"}]
        store.append_events([])   # a no-op, not a crash
        assert store.load_events() == [{"ts": 100, "type": "whale_move"}]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── backfill ─────────────────────────────────────────────────────────────
def _isolated_scope(cls):
    """Instantiate a test Telescope with its cache/store redirected to a
    throwaway tmp dir. Cache/SnapshotStore only create their on-disk
    directory lazily on first write, not in __init__, so redirecting here
    before any read or write happens means the real data/ directory is
    never touched at all -- not even an empty namespace folder."""
    tmp = tempfile.mkdtemp()
    scope = cls()
    scope.cache.dir = tmp
    scope.store.dir = os.path.join(tmp, "history")
    scope.store.events_file = os.path.join(tmp, "events.json")
    os.makedirs(scope.store.dir, exist_ok=True)
    return scope, tmp


# ── panel-data event detection (Simons/Jackson) ──────────────────────────
# _whale_move_events()/_psc_events() were only ever verified with ad-hoc
# scripts against real cached data while building them -- pure logic once
# you have rows, the same "no network" reasoning as everything else in this
# file, so pinning them here rather than leaving them with zero automated
# regression coverage.
def test_whale_move_events_deduplicates_by_filing_not_by_sweep():
    from observatories.simons import Simons
    scope, tmp = _isolated_scope(Simons)
    try:
        move = {
            "fund": "Citadel", "security": "TESLA INC",
            "prior_value": 100.0, "recent_value": 50.0, "change": -50.0,
            "direction": "DECREASED", "cik": "1", "cusip": "X",
            "accession": "acc-1",
        }
        events = scope._whale_move_events([move])
        assert len(events) == 1
        assert events[0]["type"] == "whale_move"
        assert "Citadel" in events[0]["headline"] and "TESLA" in events[0]["headline"]
        # Same filing again (e.g. a later sweep re-reading the same cached
        # accession) -> already announced, must not re-fire.
        assert scope._whale_move_events([move]) == []
        # A genuinely new quarterly filing (different accession) for the
        # same fund/security -> fires again.
        move2 = {**move, "accession": "acc-2", "change": -80.0}
        assert scope._whale_move_events([move2])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whale_key_matches_between_panel_rows_and_events():
    """The panel row's watch key and the whale_move event's own key used to
    be two independently-written f-strings that happened to match -- a real
    risk of silent drift (a panel row whose key stopped matching its
    event's key would silently break watching with no error anywhere).
    Both now call the same _whale_key() helper; this pins that they still
    agree, not just that each one individually looks reasonable."""
    from observatories.simons import Simons
    move = {"fund": "Citadel", "security": "TESLA INC",
            "cik": "1423053", "cusip": "88160R101"}
    assert Simons._whale_key(move) == "1423053:88160R101"
    scope, tmp = _isolated_scope(Simons)
    try:
        full_move = {**move, "prior_value": 100.0, "recent_value": 50.0,
                     "change": -50.0, "direction": "DECREASED",
                     "accession": "acc-1"}
        events = scope._whale_move_events([full_move])
        assert events[0]["key"] == Simons._whale_key(move)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_new_program_then_budget_shift_on_real_drift():
    from observatories.jackson import Jackson
    scope, tmp = _isolated_scope(Jackson)
    try:
        row = {"code": "1234", "name": "Test Category", "amount": 100e6,
               "growth_pct": None}
        # Never seen before, and growth_pct=None (nothing comparable a
        # year ago) -> a new_program event, not budget_shift.
        events = scope._psc_events([row])
        assert len(events) == 1 and events[0]["type"] == "new_program"
        # A later sweep with only a small move since the last announcement
        # must not re-fire.
        assert scope._psc_events([{**row, "growth_pct": 5.0}]) == []
        # A substantial move (>=30%) since the last announcement -> budget_shift.
        events2 = scope._psc_events([{**row, "amount": 140e6, "growth_pct": 40.0}])
        assert len(events2) == 1 and events2[0]["type"] == "budget_shift"
        # Below the $50M floor -> never fires regardless of growth_pct.
        tiny = {"code": "9999", "name": "Tiny", "amount": 1e6, "growth_pct": None}
        assert scope._psc_events([tiny]) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


# ── panels() resilience ──────────────────────────────────────────────────
def test_panels_normalises_none_dict_and_list():
    class Scope(Telescope):
        slug = "test_panels_shape_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}

        def collect(self, force=False):
            return []

    scope, tmp = _isolated_scope(Scope)
    try:
        scope.context = lambda force=False: None
        assert scope.panels() == []

        one = {"title": "T", "columns": [], "rows": []}
        scope.context = lambda force=False: one
        assert scope.panels() == [one]

        many = [{"title": "A"}, {"title": "B"}]
        scope.context = lambda force=False: many
        assert scope.panels() == many
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_panels_swallows_a_broken_context_instead_of_crashing_the_view():
    """A broken secondary panel must never take down the whole board -- the
    same resilience contract safe_sweep() has for the sweep path, but for
    the read path every single dashboard/API request goes through."""
    class Scope(Telescope):
        slug = "test_panels_broken_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}

        def collect(self, force=False):
            return [{"key": "a", "name": "A", "value": 1, "noise": False,
                     "sources": ["x"], "link": ""}]

        def context(self, force=False):
            raise ValueError("boom")

    scope, tmp = _isolated_scope(Scope)
    try:
        assert scope.panels() == []
        payload = scope.view()
        assert payload["panels"] == []
        assert payload["count"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Telescope base class: defaults and machinery ─────────────────────────
# base.py is the single most load-bearing file in the kernel -- every domain
# pack inherits it -- and coverage.py found several of its own methods had
# never been exercised directly, only incidentally through other tests that
# happened not to hit these specific branches.
def test_collect_is_not_implemented_by_default():
    """The one method every domain pack MUST override -- confirms the base
    class fails loudly, not silently, if a pack forgets to."""
    try:
        Telescope().collect()
        assert False, "expected NotImplementedError"
    except NotImplementedError:
        pass


def test_context_returns_none_by_default():
    """panels()'s None/dict/list normalisation was already tested via a
    patched lambda; the base class's own actual default had never been
    hit directly."""
    assert Telescope().context() is None


def test_ttl_returns_zero_when_forced_else_cache_ttl():
    scope = Telescope()
    assert scope.ttl(force=True) == 0
    assert scope.ttl(force=False) == scope.cache_ttl


def test_age_rounds_seconds_since_cache_write_and_none_when_missing():
    """_age() feeds view()'s freshness readout shown on every board -- had
    no test of its own at all."""
    class Scope(Telescope):
        slug = "test_age_ns"

    scope, tmp = _isolated_scope(Scope)
    try:
        assert scope._age("missing") is None
        scope.cache.set("k", {"v": 1})
        age = scope._age("k")
        assert age is not None and 0 <= age < 5
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_maybe_backfill_skips_calling_historical_rows_once_history_exists():
    """A distinct guard from SnapshotStore.seed()'s own overwrite check
    (already tested elsewhere): _maybe_backfill() must not even CALL
    historical_rows() once real history exists, not just rely on seed() to
    neutralise its result -- historical_rows() can be a real, non-trivial
    computation in a domain pack, not free to call speculatively every
    sweep."""
    calls = {"n": 0}

    class Scope(Telescope):
        slug = "test_backfill_skip_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        snapshot_fields = ("value",)

        def collect(self, force=False):
            return [{"key": "a", "name": "A", "value": 10,
                     "noise": False, "sources": ["x"], "link": ""}]

        def historical_rows(self, rows):
            calls["n"] += 1
            return [{"key": "a", "name": "A", "value": 1,
                     "noise": False, "sources": ["x"], "link": ""}]

    scope, tmp = _isolated_scope(Scope)
    try:
        scope.sweep()
        assert calls["n"] == 1
        scope.sweep()   # real history now exists
        assert calls["n"] == 1   # historical_rows() must not run again
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _RecordingNotifier:
    def __init__(self):
        self.calls = []

    def dispatch(self, events, scope):
        self.calls.append((events, scope))


def test_sweep_dispatches_to_notifier_when_events_fire():
    """sweep()'s own notifier.dispatch() call -- every other sweep test
    passes notifier=None, so this specific call had never actually run."""
    calls = {"n": 0}

    class Scope(Telescope):
        slug = "test_sweep_notify_ns"
        signals = (Signal("v", "value", "VALUE"),)
        default_weights = {"v": 100}
        rules = (NewLeaderRule(),)
        snapshot_fields = ("value",)

        def collect(self, force=False):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"key": "a", "name": "A", "value": 10,
                         "noise": False, "sources": ["x"], "link": ""}]
            return [{"key": "b", "name": "B", "value": 20,
                     "noise": False, "sources": ["x"], "link": ""},
                    {"key": "a", "name": "A", "value": 5,
                     "noise": False, "sources": ["x"], "link": ""}]

    scope, tmp = _isolated_scope(Scope)
    try:
        notifier = _RecordingNotifier()
        scope.sweep(notifier)   # first sweep: nothing to diff against yet
        assert notifier.calls == []
        events = scope.sweep(notifier)   # second sweep: leader changes a -> b
        assert events
        assert notifier.calls == [(events, scope)]
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


def test_brief_build_skips_a_slug_that_disappears_between_the_two_registry_calls():
    """Defensive guard: enabled_slugs() and registry.get() both read the
    same registered-classes table, so this shouldn't diverge in normal
    operation, but a slug that vanishes between the two calls must be
    skipped, not take down the whole digest. Found via coverage.py."""
    with patch.object(registry, "enabled_slugs", return_value=["ghost"]), \
         patch.object(registry, "get", side_effect=KeyError("ghost")):
        digest = brief.build(hours=24)
    assert digest["sections"] == []


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


def test_change_none_when_old_value_is_zero():
    """A genuine ZeroDivisionError guard, not just a defensive-looking
    branch -- a rate hovering at exactly 0% or a newly-listed asset's first
    reading are both real values this could hit."""
    assert change([0.0, 5.0], 1, "index") is None


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


def test_analyse_computes_vol_ratio_from_varying_history():
    """_synthetic_points()'s default constant step makes every first
    difference identical, so pstdev(diffs) is always exactly 0 and
    vol_ratio silently stays None in every other test here -- a real gap
    found via coverage.py, not a guess. An alternating step gives genuine
    variance to measure."""
    s = Series("k", "K", "G", "index", "fred", "X")
    points = [(f"d{i:04d}", 100.0 + (i % 2) * 5) for i in range(300)]
    row = analyse(s, points, min_points=30)
    assert row["vol_ratio"] is not None and row["vol_ratio"] > 0


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


def test_fetch_panel_survives_one_series_adapter_raising():
    """A single buggy/malformed-response adapter must not crash the whole
    sweep -- go()'s try/except converts a raised exception into an empty
    result for just that series, the same resilience shape panels() already
    has for a broken secondary panel (checked live via coverage.py: this
    except Exception branch had never actually been exercised by either
    test suite despite existing specifically to prevent one dead ticker
    from blanking Simons'/Reddington's entire board)."""
    class FakeScope:
        def __init__(self, cache):
            self.cache = cache

    tmp = tempfile.mkdtemp()
    try:
        cache = Cache("test_fetch_panel_raise_ns")
        cache.dir = tmp
        scope = FakeScope(cache)
        broken = Series("broken", "Broken", "G", "index", "fred", "X")
        good = Series("good", "Good", "G", "index", "yahoo", "Y")
        good_points = _synthetic_points(60)

        def raises(ident):
            raise ValueError("malformed response")

        with patch.dict(series.ADAPTERS,
                        {"fred": raises, "yahoo": lambda ident: good_points}):
            rows = fetch_panel(scope, (broken, good), ttl=3600, min_points=30)
        assert len(rows) == 1
        assert rows[0]["key"] == "good"
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


def test_post_json_merges_headers_and_returns_parsed_body():
    """post_json's own header-merging (Content-Type plus whatever the
    caller passes) had never been exercised by a mocked test -- only
    live, via Jackson's real USAspending POSTs -- found via coverage.py."""
    with patch("telescope.http.requests.request",
               return_value=_FakeResponse(200, json_data={"ok": True})) as mock_req:
        result = http.post_json("https://example.test/search", {"q": "x"},
                                headers={"X-Custom": "1"})
    assert result == {"ok": True}
    call_headers = mock_req.call_args.kwargs["headers"]
    assert call_headers["Content-Type"] == "application/json"
    assert call_headers["X-Custom"] == "1"


def test_try_text_returns_default_on_permanent_failure():
    """try_json's exact counterpart for text responses (arXiv's Atom feed,
    SEC's XML) -- try_json's version of this had a test, try_text's never
    did, found via coverage.py."""
    with patch("telescope.http.requests.request",
               return_value=_FakeResponse(404)), \
         patch("telescope.http.time.sleep"):
        assert http.try_text("https://example.test/missing",
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


def test_probe_text_returns_real_text_on_success():
    """The success path itself -- only the give-up-cleanly failure path
    above had ever been tested, found via coverage.py. A guessed domain
    that DOES resolve is the entire reason this function exists (Kepler's
    entity resolution), not just the common failure case. Passes a custom
    header too, closing the one remaining gap: probe_text's own
    if-headers-merge branch had never run either."""
    with patch("telescope.http.requests.get",
               return_value=_FakeResponse(200, text="<title>Real Co</title>")) as mock_get:
        result = http.probe_text("https://real-domain.test/",
                                 headers={"X-Custom": "1"})
    assert result == "<title>Real Co</title>"
    assert mock_get.call_args.kwargs["headers"]["X-Custom"] == "1"


# ── shared parsing helpers (telescope/http.py, telescope/parse.py) ──────
def test_xml_tag_decodes_entities():
    """SEC's XML legitimately escapes special characters in text content
    (a raw "&" is illegal XML) -- caught live as a real bug: "JPMORGAN
    CHASE & CO." extracted raw as "JPMORGAN CHASE &amp; CO." instead of
    decoding back, which became a visible double-escaped glyph once the
    frontend started HTML-escaping free text correctly. Both kepler.py's
    Form D parsing and simons.py's 13F parsing had this exact bug before
    being deduplicated into this one shared helper."""
    xml = "<nameOfIssuer>JPMORGAN CHASE &amp; CO.</nameOfIssuer>"
    assert http.xml_tag(xml, "nameOfIssuer") == "JPMORGAN CHASE & CO."


def test_xml_tag_returns_none_when_tag_is_missing():
    assert http.xml_tag("<a>x</a>", "b") is None


def test_to_float_filters_nan():
    """A raw NaN is a valid float() result by Python's own rules, so this
    needs an explicit check -- some APIs use the literal string "NaN" as
    a null sentinel, which would otherwise silently pass through as a
    real-looking number instead of the absence of one. hubble.py's
    original copy of this helper caught this; kepler.py's/simons.py's
    didn't, until all three were deduplicated into this one."""
    assert to_float("nan") is None
    assert to_float(float("nan")) is None


def test_to_float_handles_none_and_bad_types():
    assert to_float(None) is None
    assert to_float("not a number") is None
    assert to_float("3.5") == 3.5


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


def test_post_to_slack_noops_without_a_webhook_configured():
    """post_to_slack() had zero tests of its own despite being nearly
    identical in shape to post_to_discord() (which does) -- found via
    coverage.py, not a guess: 56% on this file vs. 90%+ everywhere else in
    the kernel, the clear outlier once the two live-hitting test suites are
    combined."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_to_slack({"headline": "x"}, _StubScope()) is False
    mock_post.assert_not_called()


def test_post_to_slack_posts_when_webhook_configured():
    env = {"OBSERVATORY_SLACK_WEBHOOK": "https://slack.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_to_slack({"headline": "x"}, _StubScope()) is True
    mock_post.assert_called_once()


def test_post_to_slack_swallows_a_request_exception():
    env = {"OBSERVATORY_SLACK_WEBHOOK": "https://slack.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post",
               side_effect=requests.ConnectionError("boom")):
        assert notifier.post_to_slack({"headline": "x"}, _StubScope()) is False


def test_post_text_to_discord_and_slack_noop_without_webhooks():
    """The brief digest's own posting helpers -- same unconfigured-is-
    honest shape as the per-event channels, but never directly tested."""
    with patch.dict(os.environ, {}, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_text_to_discord("digest") is False
        assert notifier.post_text_to_slack("digest") is False
    mock_post.assert_not_called()


def test_post_text_to_discord_and_slack_post_when_configured():
    env = {"OBSERVATORY_DISCORD_WEBHOOK": "https://discord.test/hook",
           "OBSERVATORY_SLACK_WEBHOOK": "https://slack.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post") as mock_post:
        assert notifier.post_text_to_discord("digest") is True
        assert notifier.post_text_to_slack("digest") is True
    assert mock_post.call_count == 2


def test_post_text_to_discord_and_slack_swallow_a_request_exception():
    env = {"OBSERVATORY_DISCORD_WEBHOOK": "https://discord.test/hook",
           "OBSERVATORY_SLACK_WEBHOOK": "https://slack.test/hook"}
    with patch.dict(os.environ, env, clear=True), \
         patch("telescope.notifier.requests.post",
               side_effect=requests.ConnectionError("boom")):
        assert notifier.post_text_to_discord("digest") is False
        assert notifier.post_text_to_slack("digest") is False


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


def test_read_state_treats_a_corrupted_state_file_as_empty():
    """data/observatory.json truncated mid-write or otherwise corrupted
    must degrade to 'nothing persisted yet' (falling back to the env
    default), not crash every route that touches registry.state() --
    found via coverage.py, the same corrupted-file resilience shape
    Cache.get() was just given its own test for."""
    with _isolated_registry():
        registry.register(_FakeTelescope)
        os.makedirs(os.path.dirname(registry.STATE_FILE), exist_ok=True)
        with open(registry.STATE_FILE, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with patch.dict(os.environ, {"OBSERVATORY_ENABLED": "fake_a"}, clear=True):
            assert registry.state() == {"fake_a": True}


def test_load_errors_returns_an_independent_copy():
    """A caller mutating the returned dict must not corrupt the registry's
    own internal error table."""
    with _isolated_registry():
        registry._load_errors["broken_pack"] = "ImportError: boom"
        errors = registry.load_errors()
        assert errors == {"broken_pack": "ImportError: boom"}
        errors["broken_pack"] = "mutated"
        assert registry._load_errors["broken_pack"] == "ImportError: boom"


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
