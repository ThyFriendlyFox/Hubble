"""Live source tests — these deliberately hit the real public APIs.

The whole point of a telescope is that it observes something real, so these
tests assert against live data rather than fixtures. That makes them slow and
occasionally flaky when an upstream source has an outage; that is the correct
tradeoff here, because a fixture passing while the real source has changed
shape is exactly the failure we care about catching.

    pytest tests/test_live.py -v          # all telescopes (slow: several min)
    pytest tests/test_live.py -v -k hubble

Fetched data is written to the normal disk cache, so re-runs are fast.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telescope import registry                                   # noqa: E402

registry.discover()

ALL = ["hubble", "jackson", "simons", "kepler", "holmdel", "reddington"]

# Minimum rows a healthy sweep should return. Set low enough to survive a
# partial source outage but high enough to catch "the API changed shape".
MIN_ROWS = {"hubble": 100, "jackson": 20, "simons": 10, "kepler": 30,
            "holmdel": 20, "reddington": 8}


@pytest.fixture(scope="module", params=ALL)
def scope_rows(request):
    scope = registry.get(request.param)
    return scope, scope.collect()


def test_no_domain_pack_failed_to_import():
    assert registry.load_errors() == {}


def test_every_telescope_registered():
    for slug in ALL:
        assert slug in registry.all_slugs()


def test_collect_returns_enough_rows(scope_rows):
    scope, rows = scope_rows
    assert len(rows) >= MIN_ROWS[scope.slug], (
        f"{scope.slug} returned {len(rows)} rows — a source may have changed"
    )


def test_rows_carry_the_kernel_contract(scope_rows):
    """Every row needs the fields the kernel and the frontend rely on."""
    scope, rows = scope_rows
    for r in rows:
        assert r.get("key"), f"{scope.slug}: row missing key"
        assert r.get("name"), f"{scope.slug}: row missing name"
        assert isinstance(r.get("noise"), bool)
        assert isinstance(r.get("sources"), list) and r["sources"]
        assert isinstance(r.get("link", ""), str)


def test_keys_are_unique(scope_rows):
    scope, rows = scope_rows
    keys = [r["key"] for r in rows]
    assert len(keys) == len(set(keys)), f"{scope.slug} has duplicate keys"


def test_declared_signal_fields_actually_exist(scope_rows):
    """Catches a signal pointing at a field the fetcher never populates."""
    scope, rows = scope_rows
    for sig in scope.signals:
        present = sum(1 for r in rows if r.get(sig.field) is not None)
        assert present > 0, (
            f"{scope.slug}: signal '{sig.key}' reads '{sig.field}', "
            "which no row populates"
        )


def test_declared_columns_exist(scope_rows):
    scope, rows = scope_rows
    for col in scope.columns:
        if col.field in ("score", "rank"):
            continue
        assert any(col.field in r for r in rows), (
            f"{scope.slug}: column '{col.field}' is on no row"
        )


def test_ranking_produces_scores_and_a_leader(scope_rows):
    scope, rows = scope_rows
    ranked = scope.rank([dict(r) for r in rows])
    assert ranked[0]["rank"] == 1
    scored = [r for r in ranked if r["score"] is not None]
    assert len(scored) >= MIN_ROWS[scope.slug] // 2
    for r in scored:
        assert 0 <= r["score"] <= 100
    # ranks must be dense and ordered
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))


def test_weights_actually_change_the_order(scope_rows):
    """A telescope whose sliders don't re-rank anything is misconfigured."""
    scope, rows = scope_rows
    sig_keys = [s.key for s in scope.signals]
    base = [r["key"] for r in scope.rank([dict(r) for r in rows])]
    flipped = {k: (0 if i else 100) for i, k in enumerate(sig_keys)}
    other = [r["key"] for r in scope.rank([dict(r) for r in rows], flipped)]
    assert base != other, f"{scope.slug}: weighting had no effect on order"


def test_snapshot_diff_roundtrip_emits_events(scope_rows):
    """Record a snapshot, mutate it, and confirm the diff engine speaks."""
    scope, rows = scope_rows
    ranked = scope.rank([dict(r) for r in rows])
    ts = 1.0
    prev = {"ts": 0.0, "rows": [scope.store._slim(r) for r in ranked]}
    # Nothing changed -> no events.
    assert scope.store.diff(prev, ranked, ts) == []
    # Drop the leader and everything shifts up: expect at least one event.
    moved = scope.rank([dict(r) for r in rows if r["key"] != ranked[0]["key"]])
    events = scope.store.diff(prev, moved, ts)
    assert events, f"{scope.slug}: diff engine emitted nothing on a real change"
    for e in events:
        assert e["headline"] and isinstance(e["headline"], str)
        assert e["telescope"] == scope.slug
        # A headline that still contains a format placeholder means a rule
        # references a field its telescope does not produce.
        assert "{" not in e["headline"], f"unrendered template: {e['headline']}"


def test_context_panel_is_wellformed(scope_rows):
    scope, _ = scope_rows
    panel = scope.context()
    if panel is None:
        return
    assert panel["title"] and panel["columns"] and panel["rows"]
    for col in panel["columns"]:
        assert {"field", "label", "fmt"} <= set(col)
        assert any(col["field"] in r for r in panel["rows"])
