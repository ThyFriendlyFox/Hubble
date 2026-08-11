"""Domain-pack unit tests — pure logic and mocked-network business logic.

Neither existing suite covers this middle ground: test_kernel.py is
explicitly telescope/-only ("the behaviour every telescope inherits"), and
test_live.py deliberately hits real APIs on purpose. A domain pack's own
business logic (entity-name matching, the Greenhouse/Lever/Ashby fallback
chain, XML field extraction) is neither of those — it's pure/deterministic
but lives in observatories/, and test_live.py's warm-cache fixtures mean a
real live sweep often never even calls these producer functions at all
(cache.cached() only calls the producer on a miss). This file mocks
telescope/http.py's fetch primitives directly, the same technique
test_kernel.py already uses for telescope/http.py's own retry logic, so
this logic is pinned without touching the network or the real disk cache.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shutil                                                     # noqa: E402
from unittest.mock import patch                                  # noqa: E402

from observatories import jackson, kepler                        # noqa: E402
from observatories.jackson import Jackson                         # noqa: E402
from observatories.kepler import (Kepler, _core_name,             # noqa: E402
                                  _distinctive_enough,
                                  _looks_like_the_company, _slug)


def _isolated_kepler():
    """A fresh Kepler instance with its disk cache redirected to a throwaway
    tmp dir -- never touches data/kepler/, the real singleton's cache."""
    kep = Kepler()
    tmp = tempfile.mkdtemp()
    kep.cache.dir = tmp
    return kep, tmp


# ── pure logic: entity-name matching ─────────────────────────────────────
def test_core_name_strips_punctuation_suffixes_and_case():
    assert _core_name("Onkos Surgical, Inc.") == "onkos surgical"
    assert _core_name("Latitude Health, LLC") == "latitude health"
    assert _core_name(None) == ""


def test_core_name_only_strips_true_corporate_suffixes():
    """A word that looks generic but is load-bearing for matching (per the
    module's own comment: 'Health' or 'Robotics') must survive -- only the
    literal corporate-suffix words in _GENERIC_RE get dropped."""
    assert "robotics" in _core_name("Acme Robotics Corp")
    assert "health" in _core_name("Latitude Health Inc")


def test_slug_hyphenates_the_core_name():
    assert _slug("Onkos Surgical, Inc.") == "onkos-surgical"
    assert _slug("") is None
    assert _slug(None) is None


def test_distinctive_enough_gates_short_or_generic_slugs():
    assert _distinctive_enough("onkos-surgical") is True   # hyphenated, long
    assert _distinctive_enough("latitudehealth") is True   # no hyphen, >=10
    assert _distinctive_enough("acme") is False             # too short outright
    assert _distinctive_enough("short") is False            # 5 chars, no hyphen, <10
    assert _distinctive_enough(None) is False
    assert _distinctive_enough("") is False


def test_looks_like_the_company_requires_every_core_word_in_the_title():
    core = "onkos surgical"
    assert _looks_like_the_company(core, "<title>Onkos Surgical | Home</title>") is True
    # Missing one of the two core words -> not a match.
    assert _looks_like_the_company(core, "<title>Onkos | Home</title>") is False
    # No <title> tag at all -> not a match, not a crash.
    assert _looks_like_the_company(core, "<body>no title here</body>") is False
    assert _looks_like_the_company(core, "") is False
    # Words under 3 chars are excluded from the requirement (matches the
    # real `words = [w for w in core.split() if len(w) >= 3]` filter).
    assert _looks_like_the_company("ai co", "<title>Whatever</title>") is False


# ── _detail(): SEC primary_doc.xml field extraction ──────────────────────
FILING = {"cik": "0001234567", "path": "/edgar/data/1234567/xyz.txt",
          "company": "Fallback Co"}

XML_FULL = """<edgarSubmission>
  <industryGroupType>Biotechnology</industryGroupType>
  <totalOfferingAmount>5000000</totalOfferingAmount>
  <totalAmountSold>2500000</totalAmountSold>
  <stateOrCountry>DE</stateOrCountry>
  <dateOfFirstSale><value>2026-06-01</value></dateOfFirstSale>
  <entityName>Real Entity Name</entityName>
  <withinFiveYears>true</withinFiveYears>
</edgarSubmission>"""


def test_detail_extracts_every_declared_field():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_text", return_value=XML_FULL):
            d = kep._detail(FILING)
        assert d["entity"] == "Real Entity Name"
        assert d["industry"] == "Biotechnology"
        assert d["offering_amount"] == 5000000.0
        assert d["amount_sold"] == 2500000.0
        assert d["state"] == "DE"
        assert d["first_sale"] == "2026-06-01"
        assert d["new_issuer"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detail_falls_back_to_filing_company_when_entityName_missing():
    xml = XML_FULL.replace(
        "<entityName>Real Entity Name</entityName>", "")
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_text", return_value=xml):
            d = kep._detail(FILING)
        assert d["entity"] == "Fallback Co"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detail_returns_none_when_fetch_fails():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_text", side_effect=Exception("boom")):
            assert kep._detail(FILING) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_detail_first_sale_is_none_without_a_matching_value_tag():
    xml = XML_FULL.replace(
        "<dateOfFirstSale><value>2026-06-01</value></dateOfFirstSale>",
        "<dateOfFirstSale></dateOfFirstSale>")
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_text", return_value=xml):
            d = kep._detail(FILING)
        assert d["first_sale"] is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _domains(): the guessed-.com-verified-by-title flow ─────────────────
def test_domains_keeps_only_verified_matches():
    """A guessed domain is trusted only when the fetched title mentions the
    company -- the exact protection the module docstring calls load-bearing
    after a real guess once resolved to a squatted gambling site."""
    def fake_probe(url, timeout=6):
        if url == "https://onkossurgical.com":
            return "<title>Onkos Surgical - Home</title>"
        if url == "https://widgetco.com":
            # A guessed domain that resolves, but to an unrelated, squatted
            # site -- exactly the failure the module docstring says a real
            # verified filing once hit against an expired domain.
            return "<title>totally unrelated squatted domain</title>"
        return None   # google.com canary, or a dead guess

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "probe_text", side_effect=fake_probe):
            out = kep._domains(["Onkos Surgical, Inc.", "Widgetco, Inc."], 3600)
        assert out == {"Onkos Surgical, Inc.": "onkossurgical.com"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_domains_skips_names_too_short_to_guess_without_any_probe():
    """"Acme, Inc." -> core "acme" -> slug "acme", 4 chars, under the
    len(slug) < 8 guess-worthiness gate -- no probe against acme.com itself.
    The empty result still triggers the is_empty canary probe against
    google.com (a different, deliberate call), so only that one call should
    be seen."""
    calls = []

    def fake_probe(url, timeout=6):
        calls.append(url)
        return None

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "probe_text", side_effect=fake_probe):
            out = kep._domains(["Acme, Inc."], 3600)
        assert out == {}
        assert calls == ["https://google.com"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_domains_empty_result_with_network_up_is_cached_as_real():
    """Every guess missing is the documented common case, not a failure --
    confirmed via the same google.com canary _hiring() uses, so an empty
    sweep with the canary reachable is trusted rather than falling back to
    stale data that doesn't exist yet anyway."""
    def fake_probe(url, timeout=6):
        return "<title>google</title>" if url == "https://google.com" else None

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "probe_text", side_effect=fake_probe):
            out = kep._domains(["Widgetco Robotics, Inc."], 3600)
        assert out == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _hn(): domain-precise vs fuzzy-title attention matching ──────────────
def _hn_response(hits):
    return {"hits": hits}


def test_hn_matches_on_the_storys_own_domain_when_one_is_verified():
    hits = [
        {"url": "https://onkossurgical.com/launch", "title": "We launched", "points": 42},
        {"url": "https://techcrunch.com/onkos-covered", "title": "Onkos Surgical raises", "points": 99},
    ]
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_json", return_value=_hn_response(hits)):
            out = kep._hn(["Onkos Surgical, Inc."], 3600,
                          domains={"Onkos Surgical, Inc.": "onkossurgical.com"})
        # Only the story whose own linked URL is on the verified domain
        # counts -- the TechCrunch coverage story is excluded even though
        # its title matches, per the docstring's stated trade-off.
        assert out["Onkos Surgical, Inc."] == {"stories": 1, "points": 42}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hn_falls_back_to_fuzzy_title_matching_without_a_domain():
    hits = [
        {"url": "https://techcrunch.com/onkos-covered", "title": "Onkos Surgical raises seed", "points": 99},
        {"url": "https://example.com/unrelated", "title": "Nothing to do with it", "points": 10},
    ]
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_json", return_value=_hn_response(hits)):
            out = kep._hn(["Onkos Surgical, Inc."], 3600, domains={})
        assert out["Onkos Surgical, Inc."] == {"stories": 1, "points": 99}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hn_skips_names_too_short_or_generic_to_match_on():
    calls = []

    def fake_get_json(url, **kw):
        calls.append(url)
        return _hn_response([])

    kep, tmp = _isolated_kepler()
    try:
        # core "ai" -> len < 5, no space -> skipped before any request.
        with patch.object(kepler, "get_json", side_effect=fake_get_json):
            out = kep._hn(["AI, Inc."], 3600, domains={})
        assert out == {}
        assert calls == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hn_a_failed_lookup_is_skipped_not_crashed_on():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "get_json", side_effect=Exception("boom")):
            out = kep._hn(["Onkos Surgical, Inc."], 3600, domains={})
        assert out == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _hiring(): the Greenhouse -> Lever -> Ashby fallback chain ───────────
def test_hiring_trusts_a_greenhouse_hit_verified_against_the_boards_own_name():
    def fake_try_json(url, default=None):
        if url.endswith("/boards/onkos-surgical"):
            return {"name": "Onkos Surgical, Inc."}
        if url.endswith("/boards/onkos-surgical/jobs"):
            return {"jobs": [{}, {}, {}]}
        raise AssertionError(f"should not reach lever/ashby: {url}")

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "try_json", side_effect=fake_try_json):
            out = kep._hiring(["Onkos Surgical, Inc."], 3600)
        assert out == {"Onkos Surgical, Inc.": {"count": 3, "platform": "greenhouse"}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hiring_falls_through_to_lever_when_greenhouse_name_does_not_match():
    def fake_try_json(url, default=None):
        if url.endswith("/boards/onkos-surgical"):
            # A real Greenhouse board exists at this slug, but for an
            # unrelated company -- the verification check must reject it.
            return {"name": "Some Other Company"}
        if "lever.co" in url:
            return [{}, {}]
        if "ashbyhq.com" in url:
            raise AssertionError("should have matched on lever first")
        return default

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "try_json", side_effect=fake_try_json):
            out = kep._hiring(["Onkos Surgical, Inc."], 3600)
        assert out == {"Onkos Surgical, Inc.": {"count": 2, "platform": "lever"}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hiring_falls_through_to_ashby_as_the_last_resort():
    def fake_try_json(url, default=None):
        if "ashbyhq.com" in url:
            return {"jobs": [{}]}
        return default   # greenhouse and lever both miss

    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "try_json", side_effect=fake_try_json):
            out = kep._hiring(["Onkos Surgical, Inc."], 3600)
        assert out == {"Onkos Surgical, Inc.": {"count": 1, "platform": "ashby"}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hiring_skips_slugs_that_are_not_distinctive_enough():
    calls = []

    def fake_try_json(url, default=None):
        calls.append(url)
        return default

    kep, tmp = _isolated_kepler()
    try:
        # "Acme, Inc." -> slug "acme" -> fails _distinctive_enough entirely.
        with patch.object(kepler, "try_json", side_effect=fake_try_json):
            out = kep._hiring(["Acme, Inc."], 3600)
        assert out == {}
        assert calls == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hiring_absent_from_output_when_all_three_platforms_miss():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "try_json", return_value=None):
            out = kep._hiring(["Onkos Surgical, Inc."], 3600)
        assert out == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── collect(): the malformed-filing-date fallback ────────────────────────
def test_collect_falls_back_to_today_on_an_unparseable_filed_date():
    kep, tmp = _isolated_kepler()
    filing = {"cik": "1", "company": "Weird Co", "filed": "not-a-date",
              "path": "/x"}
    detail = {"entity": "Weird Co", "industry": "Technology",
              "offering_amount": 1e6, "amount_sold": None, "state": "CA",
              "first_sale": None, "new_issuer": True}
    try:
        with patch.object(kep, "_filings", return_value=[filing]), \
             patch.object(kep, "_details", return_value={"1": detail}), \
             patch.object(kep, "_domains", return_value={}), \
             patch.object(kep, "_hn", return_value={}), \
             patch.object(kep, "_hiring", return_value={}):
            rows = kep.collect(force=True)
        assert len(rows) == 1
        assert rows[0]["days_ago"] == 0   # "today" minus "today"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _capital_panel(): no operating-company filings this window ──────────
def test_capital_panel_is_none_when_every_filing_is_a_fund():
    kep, tmp = _isolated_kepler()
    detail = {"entity": "Some Fund LP", "industry": "Hedge Fund",
              "offering_amount": 1e6, "amount_sold": None, "state": "NY",
              "first_sale": None, "new_issuer": True}
    try:
        with patch.object(kep, "_filings", return_value=[{"cik": "1"}]), \
             patch.object(kep, "_details", return_value={"1": detail}):
            assert kep._capital_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _crossed_over_groups() / _sector_heat_panel(): the Holmdel join ─────
def test_crossed_over_groups_is_empty_when_holmdel_is_disabled():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "is_enabled", return_value=False):
            assert kep._crossed_over_groups() == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_crossed_over_groups_swallows_a_broken_holmdel_read():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kepler, "is_enabled", return_value=True), \
             patch.object(kepler, "get_telescope", side_effect=Exception("boom")):
            assert kep._crossed_over_groups() == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sector_heat_panel_is_none_when_nothing_has_crossed_over():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kep, "_crossed_over_groups", return_value={}):
            assert kep._sector_heat_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sector_heat_panel_is_none_when_the_crossed_group_has_no_industry_mapping():
    kep, tmp = _isolated_kepler()
    try:
        # A made-up group key that isn't in GROUP_TO_INDUSTRIES at all.
        with patch.object(kep, "_crossed_over_groups",
                          return_value={"some-topic": "NOT-A-REAL-GROUP"}):
            assert kep._sector_heat_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sector_heat_panel_is_none_when_no_current_issuer_matches_the_industry():
    kep, tmp = _isolated_kepler()
    try:
        with patch.object(kep, "_crossed_over_groups", return_value={"ai-topic": "AI"}), \
             patch.object(kep, "collect", return_value=[
                 {"noise": False, "industry": "Real Estate", "name": "Not AI Co"},
             ]):
            assert kep._sector_heat_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# JACKSON
# ═══════════════════════════════════════════════════════════════════════
def _isolated_jackson():
    """A fresh Jackson instance with both its disk cache AND its snapshot
    store redirected to a throwaway tmp dir -- _psc_events()/sweep() write
    real state (psc_events_seen, appended feed events), so this needs the
    same two-attribute isolation test_kernel.py's own SnapshotStore tests
    use, not just the cache redirection Kepler's tests above needed."""
    kep = Jackson()
    tmp = tempfile.mkdtemp()
    kep.cache.dir = tmp
    kep.store.dir = os.path.join(tmp, "history")
    kep.store.events_file = os.path.join(tmp, "events.json")
    os.makedirs(kep.store.dir, exist_ok=True)
    return kep, tmp


# ── pure logic ────────────────────────────────────────────────────────
def test_filters_builds_the_dod_toptier_time_window_filter():
    f = jackson._filters("2026-01-01", "2026-06-01")
    assert f["time_period"] == [{"start_date": "2026-01-01", "end_date": "2026-06-01"}]
    assert f["agencies"] == [
        {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}
    ]
    assert f["award_type_codes"] == jackson.CONTRACT_TYPES


# ── collect(): the empty-normalized-name guard ───────────────────────────
def test_collect_skips_a_recipient_whose_name_normalizes_to_nothing():
    """_norm_company() strips punctuation and corporate suffixes -- a
    recipient name that's entirely punctuation (a real, if rare, shape in
    USAspending's raw feed) normalizes to an empty string, which can't be
    used as a join key and must be dropped rather than silently merged
    with every other empty-key row."""
    kep, tmp = _isolated_jackson()

    def fake_recipients(cache_key, start, end, ttl, limit=100):
        if cache_key == "recipients_12m":
            return [{"name": "!!! ---", "amount": 1e6, "uei": "U1"}]
        return []

    try:
        with patch.object(kep, "_recipients", side_effect=fake_recipients), \
             patch.object(kep, "_award_counts", return_value={}):
            rows = kep.collect(force=True)
        assert rows == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _psc_panel(): empty-window and SBIR-subtitle branches ───────────────
def test_psc_panel_is_none_with_no_current_psc_data():
    kep, tmp = _isolated_jackson()
    try:
        with patch.object(kep, "_psc", return_value=[]), \
             patch.object(kep, "_psc_prior", return_value=[]), \
             patch.object(kep, "_sbir", return_value=[]):
            assert kep._psc_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_panel_subtitle_mentions_sbir_count_when_sbir_data_present():
    kep, tmp = _isolated_jackson()
    cur = [{"code": "1234", "name": "Hypersonics", "amount": 1e8}]
    try:
        with patch.object(kep, "_psc", return_value=cur), \
             patch.object(kep, "_psc_prior", return_value=[]), \
             patch.object(kep, "_sbir", return_value=[{"a": 1}, {"a": 2}]):
            panel = kep._psc_panel(force=True)
        assert panel is not None
        assert "2 recent DoD SBIR awards tracked" in panel["subtitle"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _psc_events(): the CAPABILITY AREAS panel's new_program/budget_shift ─
# event logic -- entirely uncovered by either suite before this: sweep()
# is the only caller, and nothing had ever exercised Jackson's own sweep()
# override at all. Persistence is against the last ANNOUNCED amount, not
# the last sweep's reading -- see the docstring on _psc_events() itself.
def test_psc_events_on_empty_rows_fires_nothing():
    kep, tmp = _isolated_jackson()
    try:
        assert kep._psc_events([]) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_skips_a_row_with_no_code():
    kep, tmp = _isolated_jackson()
    try:
        rows = [{"code": None, "name": "Whatever", "amount": 100e6, "growth_pct": None}]
        assert kep._psc_events(rows) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_skips_amounts_below_the_50m_floor():
    kep, tmp = _isolated_jackson()
    try:
        rows = [{"code": "1234", "name": "Small Area", "amount": 10e6, "growth_pct": None}]
        events = kep._psc_events(rows)
        assert events == []
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_fires_new_program_for_a_code_with_no_prior_reading():
    kep, tmp = _isolated_jackson()
    try:
        rows = [{"code": "1234", "name": "Hypersonics", "amount": 80e6, "growth_pct": None}]
        events = kep._psc_events(rows)
        assert len(events) == 1
        assert events[0]["type"] == "new_program"
        assert events[0]["key"] == "1234"
        assert "Hypersonics" in events[0]["headline"]
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {"1234": 80e6}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_records_a_new_codes_baseline_silently_when_a_prior_reading_exists():
    """Not in psc_events_seen yet, but growth_pct is present -- meaning
    _psc_prior() *did* have this code a year ago, so this is the event-
    tracking cache catching up on first run, not a genuine 'appeared out of
    nowhere' program. No event, just a recorded baseline."""
    kep, tmp = _isolated_jackson()
    try:
        rows = [{"code": "1234", "name": "Hypersonics", "amount": 80e6, "growth_pct": 15.0}]
        events = kep._psc_events(rows)
        assert events == []
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {"1234": 80e6}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_does_not_advance_the_baseline_on_a_belowthreshold_move():
    """The exact property the module docstring calls out: comparing against
    the last ANNOUNCEMENT rather than the last sweep, so slow cumulative
    drift keeps accumulating against a fixed baseline instead of resetting
    every sweep and never crossing the threshold."""
    kep, tmp = _isolated_jackson()
    kep.cache.set("psc_events_seen", {"1234": 80e6})
    try:
        rows = [{"code": "1234", "name": "Hypersonics", "amount": 90e6, "growth_pct": None}]
        events = kep._psc_events(rows)   # +12.5%, under the 30% bar
        assert events == []
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {"1234": 80e6}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_fires_budget_shift_surged_on_a_real_jump():
    kep, tmp = _isolated_jackson()
    kep.cache.set("psc_events_seen", {"1234": 80e6})
    try:
        rows = [{"code": "1234", "name": "Hypersonics", "amount": 120e6, "growth_pct": None}]
        events = kep._psc_events(rows)   # +50%
        assert len(events) == 1
        assert events[0]["type"] == "budget_shift"
        assert "surged" in events[0]["headline"]
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {"1234": 120e6}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_psc_events_fires_budget_shift_fell_on_a_real_drop():
    kep, tmp = _isolated_jackson()
    kep.cache.set("psc_events_seen", {"1234": 100e6})
    try:
        rows = [{"code": "1234", "name": "Hypersonics", "amount": 60e6, "growth_pct": None}]
        events = kep._psc_events(rows)   # -40%
        assert len(events) == 1
        assert events[0]["type"] == "budget_shift"
        assert "fell" in events[0]["headline"]
        assert kep.cache.get("psc_events_seen", 10 ** 9) == {"1234": 60e6}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── sweep(): Jackson's own override, layering psc events on the base sweep ─
def test_sweep_appends_and_dispatches_psc_events_on_top_of_the_base_sweep():
    """Isolates Jackson.sweep()'s own added logic from Telescope.sweep()'s
    machinery (already covered by telescope/base.py's own kernel tests) by
    patching the base class method directly -- super().sweep() still
    resolves through the normal MRO to the patched version."""
    kep, tmp = _isolated_jackson()
    base_event = {"type": "new_prime", "key": "widgetco", "name": "Widgetco",
                  "ts": 0, "telescope": "jackson", "headline": "base event"}
    psc_event = {"type": "new_program", "key": "1234", "name": "Hypersonics",
                 "ts": 0, "telescope": "jackson", "headline": "psc event"}
    dispatched = []

    class FakeNotifier:
        def dispatch(self, events, scope):
            dispatched.append(list(events))

    try:
        with patch.object(jackson.Telescope, "sweep", return_value=[base_event]), \
             patch.object(kep, "_psc_panel", return_value={"rows": [{"code": "1234"}]}), \
             patch.object(kep, "_psc_events", return_value=[psc_event]):
            events = kep.sweep(notifier=FakeNotifier())
        assert events == [base_event, psc_event]
        stored = kep.store.load_events(limit=10)
        assert any(e["type"] == "new_program" for e in stored)
        # The base sweep's own dispatch is mocked away with it; only
        # Jackson's own psc-events dispatch should be observed here.
        assert dispatched == [[psc_event]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sweep_does_not_append_or_dispatch_when_no_psc_events_fire():
    kep, tmp = _isolated_jackson()
    base_event = {"type": "new_prime", "key": "widgetco", "name": "Widgetco",
                  "ts": 0, "telescope": "jackson", "headline": "base event"}
    dispatched = []

    class FakeNotifier:
        def dispatch(self, events, scope):
            dispatched.append(list(events))

    try:
        with patch.object(jackson.Telescope, "sweep", return_value=[base_event]), \
             patch.object(kep, "_psc_panel", return_value=None), \
             patch.object(kep, "_psc_events", return_value=[]):
            events = kep.sweep(notifier=FakeNotifier())
        assert events == [base_event]
        assert kep.store.load_events(limit=10) == []
        assert dispatched == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _unmapped_panel(): the Kepler cross-telescope join ───────────────────
def test_unmapped_panel_is_none_when_kepler_is_disabled():
    kep, tmp = _isolated_jackson()
    try:
        with patch.object(jackson, "is_enabled", return_value=False):
            assert kep._unmapped_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unmapped_panel_swallows_a_broken_kepler_read():
    kep, tmp = _isolated_jackson()
    try:
        with patch.object(jackson, "is_enabled", return_value=True), \
             patch.object(jackson, "get_telescope", side_effect=Exception("boom")):
            assert kep._unmapped_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unmapped_panel_is_none_when_no_kepler_row_reads_as_defense():
    kep, tmp = _isolated_jackson()

    class FakeStore:
        def latest(self):
            return {"rows": [
                {"key": "1", "name": "Latitude Health", "industry": "Biotechnology",
                 "noise": False},
            ]}

    class FakeKepler:
        store = FakeStore()

    try:
        with patch.object(jackson, "is_enabled", return_value=True), \
             patch.object(jackson, "get_telescope", return_value=FakeKepler()):
            assert kep._unmapped_panel() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unmapped_panel_surfaces_a_defense_relevant_kepler_row():
    kep, tmp = _isolated_jackson()

    class FakeStore:
        def latest(self):
            return {"rows": [
                {"key": "1", "name": "Anduril Aerospace Systems",
                 "industry": "Other Technology", "noise": False,
                 "raise_size": 5e7, "raise_fmt": "$50.0M", "state": "CA",
                 "filed": "2026-01-01", "stealth": False},
                {"key": "2", "name": "Latitude Health", "industry": "Biotechnology",
                 "noise": False},
            ]}

    class FakeKepler:
        store = FakeStore()

    try:
        with patch.object(jackson, "is_enabled", return_value=True), \
             patch.object(jackson, "get_telescope", return_value=FakeKepler()):
            panel = kep._unmapped_panel()
        assert panel is not None
        assert len(panel["rows"]) == 1
        assert panel["rows"][0]["name"] == "Anduril Aerospace Systems"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
