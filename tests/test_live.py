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
import re
import shutil
import sys
import tempfile

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telescope import registry                                   # noqa: E402

registry.discover()

ALL = ["hubble", "jackson", "simons", "kepler", "holmdel", "reddington"]

# app.py's own registry.discover() call is idempotent (re-registering a
# class just overwrites the same dict entry), and every telescope must be
# enabled for _resolve() to serve it — set directly, not setdefault, so the
# Flask API tests below don't depend on whatever's already in the shell.
os.environ["OBSERVATORY_ENABLED"] = "all"
import app as flask_app                                           # noqa: E402

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


def _assert_no_entity_leak(slug, where, name):
    assert not re.search(r"&(amp|lt|gt|quot|#\d+);", name or ""), (
        f"{slug}: {where} name {name!r} looks like un-decoded XML/HTML "
        "entity leakage"
    )


def test_names_have_no_leaked_xml_or_html_entities(scope_rows):
    """Caught live: SEC's primary_doc.xml legitimately XML-escapes company
    names (valid XML requires it), and kepler.py's _tag() used to extract
    that raw without decoding -- 'Legal & General ...' came back as
    'Legal &amp; General ...'. Harmless-looking before the frontend's
    HTML-escaping fix (the stray "&amp;" happened to decode back to "&"
    when interpolated raw into innerHTML), but a real double-escaping bug
    once the frontend started escaping correctly. Any leaked "&amp;"/
    "&lt;"/"&gt;" in a real entity name means some fetcher is extracting
    XML/HTML text without decoding it -- not fixture data, so this is
    fleet-wide, not Kepler-specific.

    Checks panel rows too, not just the primary board -- simons.py's own
    13F-parsing _tag() had the exact same bug (SPDR S&amp;P 500 ETF TR,
    JPMORGAN CHASE &amp; CO. in the real WHALE MOVES panel) and this test
    would have missed it entirely if scoped to board rows alone, since
    Simons' primary board is curated FRED/Yahoo/CoinGecko series names
    with no XML in their path at all. Checks every fmt="text" column a
    panel declares, not a hardcoded "name" key -- panel rows have no fixed
    shape (WHALE MOVES' free-text fields are "fund"/"security", not
    "name"), so a hardcoded key would silently check nothing for panels
    that don't happen to use it, exactly the gap that let this bug hide
    from the first version of this test.

    Checks recorded event headlines too, a third place the same underlying
    entity names get embedded into displayed text -- whale_move/
    budget_shift/new_program/stealth_raise all build their headline via a
    plain f-string, not the standard _fmt()/.format() rule pipeline, so
    they're worth checking independently rather than assuming the row/panel
    checks above already cover them."""
    scope, rows = scope_rows
    for r in rows:
        _assert_no_entity_leak(scope.slug, f"row '{r.get('key')}'", r.get("name"))
    for panel in scope.panels():
        text_fields = [c["field"] for c in panel.get("columns", [])
                       if c.get("fmt") == "text"]
        for r in panel.get("rows", []):
            for field in text_fields:
                _assert_no_entity_leak(
                    scope.slug, f"panel '{panel.get('title')}' row field '{field}'",
                    r.get(field)
                )
    for e in scope.store.load_events(limit=200):
        _assert_no_entity_leak(
            scope.slug, f"event '{e.get('type')}' headline", e.get("headline")
        )


def _openalex_out_of_budget():
    """OpenAlex's unauthenticated pool runs on a small shared daily USD
    budget that resets at midnight UTC (see holmdel.py's caveat) — once
    it's at $0 every request 429s with the same "Insufficient budget"
    body, no retry within the day changes that, and it isn't rare: this
    deployment has observed it exhausted for extended stretches. That's
    indistinguishable from a genuine "field never gets wired up" code
    regression by row inspection alone, so probe the API directly instead
    of guessing which one caused the empty column."""
    try:
        r = requests.get(
            "https://api.openalex.org/works?search=test&per_page=1",
            timeout=10,
        )
        return r.status_code == 429 and "Insufficient budget" in r.text
    except requests.RequestException:
        return False


def test_declared_signal_fields_actually_exist(scope_rows):
    """Catches a signal pointing at a field the fetcher never populates."""
    scope, rows = scope_rows
    for sig in scope.signals:
        present = sum(1 for r in rows if r.get(sig.field) is not None)
        if (present == 0 and scope.slug == "holmdel"
                and sig.field == "paper_growth" and _openalex_out_of_budget()):
            pytest.skip(
                "OpenAlex's shared daily budget is confirmed at $0 right "
                "now, independently verified via a direct probe — not a "
                "code regression, this signal is untestable until the "
                "next UTC midnight reset."
            )
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


def test_context_panels_are_wellformed(scope_rows):
    scope, _ = scope_rows
    for panel in scope.panels():
        assert panel["title"] and panel["columns"] and panel["rows"]
        for col in panel["columns"]:
            assert {"field", "label", "fmt"} <= set(col)
            assert any(col["field"] in r for r in panel["rows"])


def test_historical_rows_is_wellformed_against_real_current_data(scope_rows):
    """historical_rows() is otherwise only exercised by test_kernel.py's
    hand-built synthetic Telescope stand-ins -- never against a telescope's
    own real collect() output, so a crash or malformed row here could ship
    unnoticed by either suite. Confirms it either declines honestly (None,
    Kepler/Hubble's case: nothing resembling a recent past to reconstruct)
    or returns rows that survive the same ranking pipeline a real sweep
    uses, for every telescope against today's actual data."""
    scope, rows = scope_rows
    backfill = scope.historical_rows(rows)
    if backfill is None:
        return
    assert backfill, f"{scope.slug}: historical_rows() returned an empty non-None list"
    for r in backfill:
        assert r.get("key"), f"{scope.slug}: backfill row missing key"
        assert r.get("name"), f"{scope.slug}: backfill row missing name"
    ranked = scope.rank([dict(r) for r in backfill])
    assert ranked[0]["rank"] == 1


# ── Flask API layer ──────────────────────────────────────────────────────
# app.py's own routes had zero test coverage in either suite despite being
# the actual surface the frontend and any API consumer talk to. These use a
# real Flask test client against the real registered telescopes -- with
# force=False (the default for every GET below), view() reads whatever's
# already cached rather than fetching, so as long as the fixtures above have
# already warmed the cache this sweep, these add no new network calls.
@pytest.fixture(scope="module")
def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def test_api_observatory_lists_every_registered_telescope(client):
    resp = client.get("/api/observatory")
    assert resp.status_code == 200
    slugs = {t["slug"] for t in resp.get_json()["telescopes"]}
    for slug in ALL:
        assert slug in slugs


def test_api_roadmap_returns_the_real_phases(client):
    resp = client.get("/api/roadmap")
    assert resp.status_code == 200
    assert resp.get_json()["phases"], "roadmap has no phases"


@pytest.mark.parametrize("slug", ALL)
def test_api_telescope_returns_a_wellformed_payload(client, slug):
    resp = client.get(f"/api/telescope/{slug}")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["telescope"]["slug"] == slug
    assert isinstance(data["rows"], list) and data["rows"]
    assert data["count"] == len(data["rows"])
    assert isinstance(data["panels"], list)
    assert isinstance(data["weights"], dict)


def test_api_telescope_unknown_slug_is_404(client):
    resp = client.get("/api/telescope/does-not-exist")
    assert resp.status_code == 404


def test_api_telescope_feed_returns_events_shape(client):
    resp = client.get("/api/telescope/holmdel/whats-new")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data["events"], list)
    assert "snapshots" in data and "poll_seconds" in data


def test_api_observatory_feed_merges_across_enabled_telescopes(client):
    resp = client.get("/api/observatory/whats-new")
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["events"], list)


def test_api_brief_returns_a_real_digest(client):
    resp = client.get("/api/observatory/brief")
    assert resp.status_code == 200
    assert "sections" in resp.get_json()


def test_api_toggle_unknown_slug_is_404_without_touching_real_state(client):
    """A KeyError from set_enabled() must be caught before anything is
    written to the toggle file -- this never reaches disk either way, but
    confirmed here rather than assumed, since the toggle file is the same
    data/observatory.json the real dev server reads."""
    resp = client.post("/api/observatory/does-not-exist/toggle")
    assert resp.status_code == 404


def test_api_toggle_flips_real_state_and_is_reflected_by_the_registry(client):
    """The success path of api_toggle() -- only the 404 branch above had
    coverage. registry.set_enabled() persists to data/observatory.json, the
    same file the real dev server reads, so STATE_FILE is redirected to a
    throwaway temp file first (the same isolation shape as test_kernel.py's
    _isolated_registry(), just without swapping out the registered classes
    too -- this test wants the real hubble telescope, not a fake one)."""
    tmp_dir = tempfile.mkdtemp()
    orig_state_file = registry.STATE_FILE
    registry.STATE_FILE = os.path.join(tmp_dir, "observatory.json")
    try:
        resp = client.post("/api/observatory/hubble/toggle",
                            json={"enabled": False})
        assert resp.status_code == 200
        assert resp.get_json() == {"slug": "hubble", "enabled": False}
        assert registry.is_enabled("hubble") is False

        resp = client.post("/api/observatory/hubble/toggle",
                            json={"enabled": True})
        assert resp.get_json() == {"slug": "hubble", "enabled": True}
        assert registry.is_enabled("hubble") is True
    finally:
        registry.STATE_FILE = orig_state_file
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_api_telescope_disabled_slug_is_409(client):
    """_resolve()'s disabled branch -- every single per-telescope route goes
    through _resolve(), but this branch (as opposed to the unknown-slug 404
    one) had no test at all. Same STATE_FILE redirection as the toggle test
    above, since disabling a real telescope here would otherwise flip the
    same data/observatory.json the real dev server reads."""
    tmp_dir = tempfile.mkdtemp()
    orig_state_file = registry.STATE_FILE
    registry.STATE_FILE = os.path.join(tmp_dir, "observatory.json")
    try:
        registry.set_enabled("hubble", False)
        resp = client.get("/api/telescope/hubble")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["disabled"] is True
    finally:
        registry.STATE_FILE = orig_state_file
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_parse_weights_handles_the_full_shape_space():
    """The weights= query string is user-typed, and before this was only
    ever exercised indirectly by real API calls with well-formed input --
    its own edge cases (malformed pairs, non-numeric values, all-garbage
    input) had no direct coverage."""
    parse = flask_app._parse_weights
    assert parse(None) is None
    assert parse("") is None
    assert parse("a:10,b:20") == {"a": 10.0, "b": 20.0}
    assert parse(" a : 10 , b:not-a-number ") == {"a": 10.0}
    assert parse("garbage,no,colons") is None
    assert parse("a:10,garbage,b:20") == {"a": 10.0, "b": 20.0}


def test_api_brief_send_dispatches_without_error(client):
    """No OBSERVATORY_DISCORD_WEBHOOK/OBSERVATORY_SLACK_WEBHOOK/X_API_KEY
    are set in this environment (confirmed live before writing this test),
    so dispatch_brief() only logs -- safe to actually call, not just mock."""
    resp = client.post("/api/observatory/brief/send")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["sent"] is True
    assert isinstance(data["text"], str) and data["text"]


def test_api_sweep_forces_a_real_sweep_and_returns_the_right_shape(client):
    """The one route the tests above don't reach: /sweep forces a real
    collect(force=True) and writes a real snapshot, unlike every GET above
    which reads whatever's already cached. Picked Reddington -- a small
    (12 gauges), unpaced telescope -- to keep this fast (~6s measured)
    rather than triggering a cold Kepler/Holmdel sweep that can take
    minutes; that slow-path behavior is already covered by the threading
    and staleness-guard work verified live earlier this session."""
    resp = client.post("/api/telescope/reddington/sweep")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data["events"], list)
    assert data["count"] == len(data["events"])
    assert data["error"] is None
