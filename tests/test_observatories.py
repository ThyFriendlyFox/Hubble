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

import datetime as dt                                             # noqa: E402
import shutil                                                     # noqa: E402
from unittest.mock import patch                                  # noqa: E402

from observatories import holmdel, hubble, jackson, kepler, simons  # noqa: E402
from observatories.holmdel import Holmdel, Topic, _growth         # noqa: E402
from observatories.hubble import Hubble, _clean_price, _price_str  # noqa: E402
from observatories.jackson import Jackson                         # noqa: E402
from observatories.kepler import (Kepler, _core_name,             # noqa: E402
                                  _distinctive_enough,
                                  _looks_like_the_company, _slug)
from observatories import pasteur                                 # noqa: E402
from observatories.pasteur import (Pasteur, _aggregate_by_sponsor,  # noqa: E402
                                   _centrality, _normalize_name,
                                   _parse_sitemap)
from observatories.reddington import Reddington                   # noqa: E402
from observatories.simons import Simons                           # noqa: E402


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


# ═══════════════════════════════════════════════════════════════════════
# HOLMDEL
# ═══════════════════════════════════════════════════════════════════════
def _isolated_holmdel():
    hol = Holmdel()
    tmp = tempfile.mkdtemp()
    hol.cache.dir = tmp
    return hol, tmp


# ── _growth(): the shared velocity-percentage math ───────────────────────
def test_growth_returns_none_when_either_side_is_missing():
    assert _growth(None, 10) is None
    assert _growth(10, None) is None


def test_growth_returns_none_below_the_volume_floor():
    """1 -> 6 stories is +500% and would top any board -- one slow news
    week, not a real trend. Below the combined-observations floor, no
    growth signal is reported at all."""
    assert _growth(3, 2, floor=10) is None   # combined 5 < floor 10


def test_growth_handles_a_zero_prior_without_dividing_by_zero():
    assert _growth(5, 0, floor=0) == 100.0
    assert _growth(0, 0, floor=0) == 0.0


def test_growth_computes_a_real_percentage():
    assert _growth(150, 100, floor=0) == 50.0
    assert _growth(50, 100, floor=0) == -50.0


# ── _hn(): story count, peak score, two adjacent windows ─────────────────
def test_hn_extracts_recent_and_prior_counts_and_peak_points():
    t = Topic("t1", "Topic One", "AI", "topic one query")
    responses = [
        {"nbHits": 20, "hits": [{"points": 50}, {"points": 90}]},   # recent window
        {"nbHits": 8, "hits": [{"points": 10}]},                     # prior window
    ]
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=responses), \
             patch("time.sleep"):
            out = hol._hn(3600)
        assert out == {"t1": {"recent": 20, "prior": 8, "points": 90}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _wiki(): pageviews, only for topics with a real article title ───────
def test_wiki_skips_topics_without_an_article_title():
    calls = []

    def fake_try_json(url, default=None):
        calls.append(url)
        return default

    t = Topic("t1", "No Wiki", "AI", "query one")   # wiki="" by default
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=fake_try_json), \
             patch("time.sleep"):
            out = hol._wiki(3600)
        assert out == {}
        assert calls == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wiki_sums_daily_views_across_the_window():
    t = Topic("t1", "Has Wiki", "AI", "query one", wiki="Some_Article")
    responses = [
        {"items": [{"views": 100}, {"views": 200}]},   # recent
        {"items": [{"views": 50}]},                      # prior
    ]
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=responses), \
             patch("time.sleep"):
            out = hol._wiki(3600)
        assert out == {"t1": {"recent": 300, "prior": 50}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wiki_a_renamed_or_missing_article_returns_none_not_a_crash():
    t = Topic("t1", "Has Wiki", "AI", "query one", wiki="Renamed_Article")
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", return_value=None), \
             patch("time.sleep"):
            out = hol._wiki(3600)
        assert out == {"t1": {"recent": None, "prior": None}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _openalex(): meta.count extraction and the sustained-outage breaker ──
def test_openalex_extracts_meta_count_for_both_windows():
    t = Topic("t1", "Topic One", "AI", "query one")
    responses = [{"meta": {"count": 42}}, {"meta": {"count": 30}}]
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=responses), \
             patch("time.sleep"):
            out = hol._openalex(3600)
        assert out == {"t1": {"recent": 42, "prior": 30}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_openalex_stops_on_the_first_fully_silent_topic():
    """Found live, not in a test: OpenAlex sometimes accepts the connection
    and never answers at all, rather than failing fast with a 429/503 (both
    already handled). try_json's default={} means a hung/failed call still
    returns promptly here in a mocked test, but the *real* http.py burns a
    full ~90s retry budget per call in that scenario -- with 32 topics that
    is up to ~90 minutes of a live board hanging on data that was never
    coming. An earlier version of this breaker waited for three consecutive
    silent topics before giving up; confirmed live, that was still slow
    enough that a real request never completed inside a 10-minute test
    ceiling. One topic with neither window returning a count -- already 6
    real failed network attempts (3 retries x 2 windows, each already
    exhausting http.py's own backoff budget) -- is stopped on immediately;
    topics after it must never even be attempted."""
    topics = tuple(Topic(f"t{i}", f"Topic {i}", "AI", f"query {i}") for i in range(10))
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", topics), \
             patch.object(holmdel, "try_json", return_value={}), \
             patch("time.sleep"):
            out = hol._openalex(3600)
        assert set(out.keys()) == {"t0"}
        assert out["t0"] == {"recent": None, "prior": None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_openalex_does_not_trip_on_a_topic_with_only_one_silent_window():
    """A topic where *one* window comes back empty but the other has a real
    count is a normal, expected result (e.g. a brand-new topic with no
    prior-window papers yet), not evidence of an outage -- only both windows
    coming back empty at once counts as a signal worth stopping on."""
    t = Topic("t1", "Topic One", "AI", "query one")
    responses = [{"meta": {"count": 7}}, {}]   # recent: real count; prior: empty
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=responses), \
             patch("time.sleep"):
            out = hol._openalex(3600)
        assert out == {"t1": {"recent": 7, "prior": None}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _arxiv(): quoted-phrase totals via regex, and the rotation guarantee ─
def test_arxiv_extracts_total_from_valid_opensearch_xml():
    t = Topic("t1", "Topic One", "AI", "query one")
    xml = "<feed><opensearch:totalResults>1234</opensearch:totalResults></feed>"
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_text", return_value=xml), \
             patch("time.sleep"):
            out = hol._arxiv(3600)
        assert out == {"t1": {"recent": 1234, "prior": 1234}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_arxiv_returns_none_for_malformed_or_missing_xml():
    t = Topic("t1", "Topic One", "AI", "query one")
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_text", return_value="<not-the-right-tag/>"), \
             patch("time.sleep"):
            out = hol._arxiv(3600)
        assert out == {"t1": {"recent": None, "prior": None}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_arxiv_processes_every_topic_regardless_of_rotation_offset():
    """Topics are rotated by day-of-year so the throttle doesn't always cut
    off the same ones -- but regardless of where the rotation starts, every
    topic must still end up with an entry in the output."""
    topics = tuple(Topic(f"t{i}", f"Topic {i}", "AI", f"query {i}") for i in range(5))
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", topics), \
             patch.object(holmdel, "try_text",
                          return_value="<opensearch:totalResults>1</opensearch:totalResults>"), \
             patch("time.sleep"):
            out = hol._arxiv(3600)
        assert set(out.keys()) == {t.key for t in topics}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _github(): repo velocity + peak stars from the recent window only ───
def test_github_extracts_repo_count_and_peak_stars_from_recent_window_only():
    t = Topic("t1", "Topic One", "COMPUTE", "topic one query")
    responses = [
        {"total_count": 42, "items": [{"stargazers_count": 100}, {"stargazers_count": 250}]},
        {"total_count": 10, "items": [{"stargazers_count": 999}]},   # prior -- stars discarded
    ]
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=responses), \
             patch("time.sleep"):
            out = hol._github(3600)
        assert out == {"t1": {"recent": 42, "prior": 10, "stars": 250}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _npm(): the one source with per-topic optionality by design ─────────
def test_npm_skips_topics_without_a_canonical_package():
    calls = []

    def fake_try_json(url, default=None):
        calls.append(url)
        return default

    t = Topic("t1", "No NPM", "AI", "query one")   # npm="" by default
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", side_effect=fake_try_json), \
             patch("time.sleep"):
            out = hol._npm(3600)
        assert out == {}
        assert calls == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_npm_records_a_real_download_count():
    t = Topic("t1", "Has NPM", "AI", "query one", npm="somepkg")
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", return_value={"downloads": 12345}), \
             patch("time.sleep"):
            out = hol._npm(3600)
        assert out == {"t1": 12345}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_npm_preserves_stale_data_over_a_total_outage():
    t = Topic("t1", "Has NPM", "AI", "query one", npm="somepkg")
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", return_value={"downloads": 500}), \
             patch("time.sleep"):
            first = hol._npm(3600)
        assert first == {"t1": 500}
        # Total outage: every request fails and try_json degrades to None.
        with patch.object(holmdel, "TOPICS", (t,)), \
             patch.object(holmdel, "try_json", return_value=None), \
             patch("time.sleep"):
            second = hol._npm(0)   # ttl=0 forces a real miss, not a cache hit
        assert second == {"t1": 500}   # stale-but-real value preserved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _unlisted_panel(): trending HN stories outside the curated watchlist ─
def test_unlisted_panel_is_none_when_no_hn_hits_at_all():
    hol, tmp = _isolated_holmdel()
    try:
        with patch.object(holmdel, "try_json", return_value={"hits": []}):
            assert hol._unlisted_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unlisted_panel_is_none_when_every_hit_matches_the_watchlist():
    hol, tmp = _isolated_holmdel()
    hits = {"hits": [{"title": "New AI Agents breakthrough", "points": 200,
                       "num_comments": 10, "objectID": "1"}]}
    try:
        # The real watchlist includes an "AI agents" query -- this title
        # matches it, so it must not appear as "unlisted".
        with patch.object(holmdel, "try_json", return_value=hits):
            assert hol._unlisted_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unlisted_panel_surfaces_a_real_unlisted_story_sorted_by_points():
    hol, tmp = _isolated_holmdel()
    hits = {"hits": [
        {"title": "Something totally unrelated to any topic", "points": 90,
         "num_comments": 5, "objectID": "1"},
        {"title": "An even bigger unrelated story", "points": 300,
         "num_comments": 40, "objectID": "2"},
    ]}
    try:
        with patch.object(holmdel, "try_json", return_value=hits):
            panel = hol._unlisted_panel(force=True)
        assert panel is not None
        assert [r["points"] for r in panel["rows"]] == [300, 90]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── context(): FIELDS aggregation, noise-skipping and the empty case ────
def test_context_skips_noise_rows_when_aggregating_by_field():
    hol, tmp = _isolated_holmdel()
    rows = [
        {"noise": True, "group": "AI", "hn_growth": 999, "wiki_growth": 999},
        {"noise": False, "group": "AI", "hn_growth": 20.0, "wiki_growth": 10.0},
    ]
    try:
        with patch.object(hol, "collect", return_value=rows), \
             patch.object(hol, "_unlisted_panel", return_value=None):
            panels = hol.context(force=True)
        fields_panel = panels[0]
        assert fields_panel["rows"][0]["topics"] == 1   # only the non-noise row counted
        assert fields_panel["rows"][0]["hn_growth"] == 20.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_context_is_none_when_every_topic_is_currently_noise():
    """An extreme edge case -- every one of Holmdel's 6 independent sources
    reporting nothing for every topic at once -- but this documents the
    real, current behaviour: the early return also skips calling the
    independently-sourced UNLISTED panel this sweep, even though it
    doesn't depend on the FIELDS aggregation at all."""
    hol, tmp = _isolated_holmdel()
    rows = [{"noise": True, "group": "AI", "hn_growth": None, "wiki_growth": None}]
    calls = []
    try:
        with patch.object(hol, "collect", return_value=rows), \
             patch.object(hol, "_unlisted_panel",
                          side_effect=lambda force: calls.append(force)):
            panels = hol.context(force=True)
        assert panels is None
        assert calls == []   # _unlisted_panel never even got called
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# SIMONS
# ═══════════════════════════════════════════════════════════════════════
def _isolated_simons():
    sim = Simons()
    tmp = tempfile.mkdtemp()
    sim.cache.dir = tmp
    sim.store.dir = os.path.join(tmp, "history")
    sim.store.events_file = os.path.join(tmp, "events.json")
    os.makedirs(sim.store.dir, exist_ok=True)
    return sim, tmp


# ── _positions(): 13F XML parsing, and the real Berkshire dedup case ────
def test_positions_returns_empty_dict_when_fetch_fails():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_text", return_value=None):
            assert sim._positions("https://x") == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_positions_extracts_cusip_name_and_value():
    xml = ("<informationTable><infoTable><nameOfIssuer>Widgetco Inc</nameOfIssuer>"
           "<cusip>123456789</cusip><value>50000</value></infoTable></informationTable>")
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_text", return_value=xml):
            out = sim._positions("https://x")
        assert out == {"123456789": {"name": "Widgetco Inc", "value": 50000.0}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_positions_sums_duplicate_cusips_across_manager_rows():
    """A real, confirmed case, not a hypothetical: Berkshire splits one
    security across several otherManager subsidiary rows for the SAME
    security. Summing by CUSIP within one filing must happen before any
    quarter-over-quarter comparison, or the position gets double-counted."""
    xml = ("<informationTable>"
           "<infoTable><nameOfIssuer>Widgetco Inc</nameOfIssuer><cusip>123456789</cusip><value>30000</value></infoTable>"
           "<infoTable><nameOfIssuer>Widgetco Inc</nameOfIssuer><cusip>123456789</cusip><value>20000</value></infoTable>"
           "</informationTable>")
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_text", return_value=xml):
            out = sim._positions("https://x")
        assert out == {"123456789": {"name": "Widgetco Inc", "value": 50000.0}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_positions_skips_a_block_with_no_cusip():
    xml = ("<informationTable><infoTable><nameOfIssuer>No Cusip Co</nameOfIssuer>"
           "<value>10000</value></infoTable></informationTable>")
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_text", return_value=xml):
            assert sim._positions("https://x") == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _curve_panel(): rates aggregation from already-cached series ────────
def test_curve_panel_skips_a_tenor_key_absent_from_SERIES():
    """Structurally can't happen today -- the hardcoded tenors list and
    SERIES are kept in sync by hand -- but the guard exists for exactly the
    case where they drift, so it's worth pinning directly rather than
    trusting they'll always agree."""
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "SERIES", tuple(
            s for s in simons.SERIES if s.key != "dff"
        )), patch.object(sim.cache, "get", return_value=[("2026-01-01", 4.0),
                                                          ("2026-06-01", 4.5)]):
            panel = sim._curve_panel(force=True)
        assert panel is not None
        assert all(r["tenor"] != "Fed Funds" for r in panel["rows"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_curve_panel_is_none_when_no_tenor_has_cached_data():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(sim.cache, "get", return_value=None):
            assert sim._curve_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_curve_panel_computes_level_and_change_from_cached_series():
    pts = [(f"2026-01-{(i % 28) + 1:02d}", 4.0) for i in range(252)] + [("2026-12-31", 4.5)]
    sim, tmp = _isolated_simons()
    try:
        with patch.object(sim.cache, "get", return_value=pts):
            panel = sim._curve_panel(force=True)
        assert panel is not None
        row = panel["rows"][0]
        assert row["level"] == 4.5
        assert row["chg_1m"] == 50.0   # (4.5 - 4.0) * 100, basis points
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _accessions(): the two most recent 13F-HR filings ────────────────────
def test_accessions_returns_empty_when_fetch_fails():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_json", return_value=None):
            assert sim._accessions("0001234567", 3600) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_accessions_extracts_the_two_most_recent_13fhr_filings():
    data = {"filings": {"recent": {
        "form": ["10-K", "13F-HR", "13F-HR", "13F-HR"],
        "accessionNumber": ["A0", "A1", "A2", "A3"],
        "reportDate": ["2026-01-01", "2026-06-30", "2026-03-31", "2025-12-31"],
    }}}
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_json", return_value=data):
            out = sim._accessions("0001234567", 3600)
        assert out == [
            {"accession": "A1", "report_date": "2026-06-30"},
            {"accession": "A2", "report_date": "2026-03-31"},
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _info_table_url(): the filer-chosen XML filename lookup ─────────────
def test_info_table_url_returns_none_when_index_fetch_fails():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_json", return_value=None):
            assert sim._info_table_url("1234567", "0001234567-26-000001") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_info_table_url_finds_the_filers_own_named_xml():
    idx = {"directory": {"item": [
        {"name": "primary_doc.xml"},
        {"name": "form13fInfoTable.xml"},
        {"name": "0001234567-26-000001-index.htm"},
    ]}}
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_json", return_value=idx):
            url = sim._info_table_url("1234567", "0001234567-26-000001")
        assert url == ("https://www.sec.gov/Archives/edgar/data/1234567/"
                       "000123456726000001/form13fInfoTable.xml")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_info_table_url_returns_none_when_only_primary_doc_present():
    idx = {"directory": {"item": [{"name": "primary_doc.xml"}]}}
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "try_json", return_value=idx):
            assert sim._info_table_url("1234567", "0001234567-26-000001") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _whale_moves(): the direction classification and min-change floor ───
def test_whale_moves_skips_a_filer_with_fewer_than_two_accessions():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(sim, "_accessions",
                          return_value=[{"accession": "A1", "report_date": "2026-06-30"}]):
            moves, as_of = sim._whale_moves(force=True)
        assert moves == []
        assert as_of is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whale_moves_classifies_new_exited_increased_decreased():
    accns = [
        {"accession": "A1", "report_date": "2026-06-30"},
        {"accession": "A0", "report_date": "2026-03-31"},
    ]
    recent = {
        "NEWCUSIP0": {"name": "New Co", "value": 20_000_000.0},
        "INCCUSIP0": {"name": "Inc Co", "value": 80_000_000.0},
        "DECCUSIP0": {"name": "Dec Co", "value": 10_000_000.0},
    }
    prior = {
        "INCCUSIP0": {"name": "Inc Co", "value": 50_000_000.0},
        "DECCUSIP0": {"name": "Dec Co", "value": 40_000_000.0},
        "EXTCUSIP0": {"name": "Exit Co", "value": 15_000_000.0},
    }

    def fake_positions(url):
        return recent if url == "recent-url" else prior

    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "WHALES", (("Test Fund", "0001234567"),)), \
             patch.object(sim, "_accessions", return_value=accns), \
             patch.object(sim, "_info_table_url",
                          side_effect=lambda cik, acc: "recent-url" if acc == "A1" else "prior-url"), \
             patch.object(sim, "_positions", side_effect=fake_positions), \
             patch("time.sleep"):
            moves, as_of = sim._whale_moves(force=True)
        by_cusip = {m["cusip"]: m for m in moves}
        assert by_cusip["NEWCUSIP0"]["direction"] == "NEW"
        assert by_cusip["INCCUSIP0"]["direction"] == "INCREASED"
        assert by_cusip["DECCUSIP0"]["direction"] == "DECREASED"
        assert by_cusip["EXTCUSIP0"]["direction"] == "EXITED"
        assert as_of == "2026-06-30"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whale_moves_filters_deltas_under_the_10m_floor():
    accns = [
        {"accession": "A1", "report_date": "2026-06-30"},
        {"accession": "A0", "report_date": "2026-03-31"},
    ]
    recent = {"SMALLCUSIP": {"name": "Small Co", "value": 5_000_000.0}}
    prior = {"SMALLCUSIP": {"name": "Small Co", "value": 0.0}}

    def fake_positions(url):
        return recent if url == "recent-url" else prior

    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "WHALES", (("Test Fund", "0001234567"),)), \
             patch.object(sim, "_accessions", return_value=accns), \
             patch.object(sim, "_info_table_url",
                          side_effect=lambda cik, acc: "recent-url" if acc == "A1" else "prior-url"), \
             patch.object(sim, "_positions", side_effect=fake_positions), \
             patch("time.sleep"):
            moves, _ = sim._whale_moves(force=True)
        assert moves == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _whale_move_events(): fires once, never re-announces the same filing ─
def test_whale_move_events_on_empty_moves_fires_nothing():
    sim, tmp = _isolated_simons()
    try:
        assert sim._whale_move_events([]) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whale_move_events_fires_once_and_never_reannounces_the_same_filing():
    move = {"fund": "Test Fund", "security": "Widgetco", "cik": "0001234567",
            "cusip": "123456789", "accession": "A1", "change": 30_000_000.0,
            "recent_value": 80_000_000.0, "direction": "INCREASED"}
    sim, tmp = _isolated_simons()
    try:
        first = sim._whale_move_events([move])
        assert len(first) == 1
        assert first[0]["type"] == "whale_move"
        assert "Widgetco" in first[0]["headline"]
        second = sim._whale_move_events([move])
        assert second == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── sweep(): Simons' own override, layering whale events on the base sweep
def test_sweep_appends_and_dispatches_whale_events_on_top_of_the_base_sweep():
    sim, tmp = _isolated_simons()
    base_event = {"type": "regime_change", "key": "t10y2y", "name": "2s10s Curve",
                  "ts": 0, "telescope": "simons", "headline": "base event"}
    whale_event = {"type": "whale_move", "key": "0001234567:123456789",
                   "name": "Test Fund · Widgetco", "ts": 0,
                   "telescope": "simons", "headline": "whale event"}
    dispatched = []

    class FakeNotifier:
        def dispatch(self, events, scope):
            dispatched.append(list(events))

    try:
        with patch.object(simons.Telescope, "sweep", return_value=[base_event]), \
             patch.object(sim, "_whale_moves", return_value=([{"cik": "x"}], "2026-06-30")), \
             patch.object(sim, "_whale_move_events", return_value=[whale_event]):
            events = sim.sweep(notifier=FakeNotifier())
        assert events == [base_event, whale_event]
        stored = sim.store.load_events(limit=10)
        assert any(e["type"] == "whale_move" for e in stored)
        assert dispatched == [[whale_event]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sweep_does_not_append_or_dispatch_when_no_whale_events_fire():
    sim, tmp = _isolated_simons()
    base_event = {"type": "regime_change", "key": "t10y2y", "name": "2s10s Curve",
                  "ts": 0, "telescope": "simons", "headline": "base event"}
    dispatched = []

    class FakeNotifier:
        def dispatch(self, events, scope):
            dispatched.append(list(events))

    try:
        with patch.object(simons.Telescope, "sweep", return_value=[base_event]), \
             patch.object(sim, "_whale_moves", return_value=([], None)), \
             patch.object(sim, "_whale_move_events", return_value=[]):
            events = sim.sweep(notifier=FakeNotifier())
        assert events == [base_event]
        assert sim.store.load_events(limit=10) == []
        assert dispatched == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _whale_panel() ────────────────────────────────────────────────────
def test_whale_panel_is_none_when_no_moves():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(sim, "_whale_moves", return_value=([], None)):
            assert sim._whale_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_whale_panel_builds_rows_from_real_moves():
    move = {"cik": "0001234567", "cusip": "123456789", "fund": "Test Fund",
            "security": "Widgetco", "prior_value": 50_000_000.0,
            "recent_value": 80_000_000.0, "change": 30_000_000.0,
            "direction": "INCREASED", "accession": "A1"}
    sim, tmp = _isolated_simons()
    try:
        with patch.object(sim, "_whale_moves", return_value=([move], "2026-06-30")):
            panel = sim._whale_panel(force=True)
        assert panel is not None
        assert panel["rows"][0]["key"] == "0001234567:123456789"
        assert "2026-06-30" in panel["subtitle"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── _ai_capex_panel(): the Hubble cross-telescope join ───────────────────
def test_ai_capex_panel_is_none_when_hubble_is_disabled():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=False):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_swallows_a_broken_hubble_read():
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", side_effect=Exception("boom")):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_is_none_without_any_new_leader_events():
    class FakeStore:
        def load_events(self, limit=200):
            return [{"type": "big_award", "ts": 1, "name": "x"}]

    class FakeHubble:
        store = FakeStore()

    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", return_value=FakeHubble()):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_is_none_without_enough_smh_price_history():
    class FakeStore:
        def load_events(self, limit=200):
            return [{"type": "new_leader", "ts": 1700000000, "name": "GPT-X"}]

    class FakeHubble:
        store = FakeStore()

    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", return_value=FakeHubble()), \
             patch.object(sim.cache, "get", return_value=[("2023-11-14", 100.0)]):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_skips_a_leader_event_missing_ts_or_name():
    class FakeStore:
        def load_events(self, limit=200):
            return [{"type": "new_leader", "ts": None, "name": "GPT-X"}]

    class FakeHubble:
        store = FakeStore()

    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", return_value=FakeHubble()), \
             patch.object(sim.cache, "get",
                          return_value=[("2020-01-01", 1.0), ("2020-02-01", 2.0)]):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_skips_an_event_with_no_matching_price_point():
    ts = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()

    class FakeStore:
        def load_events(self, limit=200):
            return [{"type": "new_leader", "ts": ts, "name": "GPT-X"}]

    class FakeHubble:
        store = FakeStore()

    # Every cached point predates the event -- no point with date >= it.
    points = [("2020-01-01", 50.0), ("2020-06-01", 60.0)]
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", return_value=FakeHubble()), \
             patch.object(sim.cache, "get", return_value=points):
            assert sim._ai_capex_panel(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ai_capex_panel_pairs_a_new_leader_event_with_smhs_move_since():
    ts = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()

    class FakeStore:
        def load_events(self, limit=200):
            return [{"type": "new_leader", "ts": ts, "name": "GPT-X"}]

    class FakeHubble:
        store = FakeStore()

    points = [("2026-01-01", 100.0), ("2026-06-01", 150.0)]
    sim, tmp = _isolated_simons()
    try:
        with patch.object(simons, "is_enabled", return_value=True), \
             patch.object(simons, "get_telescope", return_value=FakeHubble()), \
             patch.object(sim.cache, "get", return_value=points):
            panel = sim._ai_capex_panel(force=True)
        assert panel is not None
        row = panel["rows"][0]
        assert row["model"] == "GPT-X"
        assert row["smh_then"] == 100.0
        assert row["smh_now"] == 150.0
        assert row["chg_pct"] == 50.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# HUBBLE
# ═══════════════════════════════════════════════════════════════════════
def _isolated_hubble():
    hub = Hubble()
    tmp = tempfile.mkdtemp()
    hub.cache.dir = tmp
    return hub, tmp


# ── _clean_price() / _price_str(): pure formatting logic ────────────────
def test_clean_price_treats_a_negative_sentinel_as_unknown():
    """OpenRouter uses negative sentinels like -1000000 for variable/router
    pricing -- treating that as a real price would skew normalisation
    toward "impossibly cheap" instead of "unknown"."""
    assert _clean_price(-1000000) is None
    assert _clean_price(None) is None


def test_clean_price_passes_through_a_real_non_negative_value():
    assert _clean_price("0.0000025") == 0.0000025
    assert _clean_price(0) == 0.0   # zero itself is a real, valid price


def test_price_str_formats_per_million_tokens():
    assert _price_str(0.0000025) == "$2.50"
    assert _price_str(None) == "$0.00"


# ── fetch_huggingface(): id fallback and missing-field defaults ─────────
def test_fetch_huggingface_falls_back_to_modelId_and_defaults_missing_fields():
    data = [
        {"id": "org/model-a", "downloads": 500, "likes": 10, "createdAt": "2026-01-01"},
        {"modelId": "org/model-b"},   # no "id", no downloads/likes at all
    ]
    hub, tmp = _isolated_hubble()
    try:
        with patch.object(hubble, "try_json", return_value=data):
            out = hub.fetch_huggingface(3600)
        assert out[0] == {"hf_id": "org/model-a", "downloads": 500, "likes": 10,
                          "created_at": "2026-01-01"}
        assert out[1] == {"hf_id": "org/model-b", "downloads": 0, "likes": 0,
                          "created_at": None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── fetch_openrouter(): rank, pricing, benchmark and arena-elo extraction
def test_fetch_openrouter_extracts_rank_pricing_and_benchmarks():
    data = {"data": [
        {
            "id": "openrouter/model-a", "name": "Model A",
            "hugging_face_id": "org/model-a",
            "context_length": 128000,
            "pricing": {"prompt": "0.000001", "completion": "0.000003"},
            "benchmarks": {
                "artificial_analysis": {
                    "intelligence_index": 55, "coding_index": 60, "agentic_index": 40,
                },
                "design_arena": [{"elo": 1200}, {"elo": 1350}],
            },
        },
        {"id": "openrouter/model-b", "name": "Model B"},   # sparse: no pricing/benchmarks/hf id
    ]}
    hub, tmp = _isolated_hubble()
    try:
        with patch.object(hubble, "try_json", return_value=data):
            out = hub.fetch_openrouter(3600)
        first, second = out
        assert first["usage_rank"] == 1
        assert first["hf_id"] == "org/model-a"
        assert first["price_prompt"] == 0.000001
        assert first["price_completion"] == 0.000003
        assert first["intelligence_index"] == 55
        assert first["coding_index"] == 60
        assert first["agentic_index"] == 40
        assert first["arena_elo"] == 1350   # the max of the two design_arena entries

        assert second["usage_rank"] == 2
        assert second["hf_id"] is None
        assert second["price_prompt"] is None
        assert second["intelligence_index"] is None
        assert second["arena_elo"] is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fetch_openrouter_cleans_a_negative_price_sentinel_within_the_join():
    data = {"data": [{
        "id": "openrouter/model-a", "name": "Model A",
        "pricing": {"prompt": -1000000, "completion": "0.000003"},
    }]}
    hub, tmp = _isolated_hubble()
    try:
        with patch.object(hubble, "try_json", return_value=data):
            out = hub.fetch_openrouter(3600)
        assert out[0]["price_prompt"] is None
        assert out[0]["price_completion"] == 0.000003
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# REDDINGTON
# ═══════════════════════════════════════════════════════════════════════
# The smallest domain pack -- collect()/historical_rows() delegate entirely
# to telescope/series.py's fetch_panel()/historical_panel(), already at
# 100% kernel coverage of their own. The only domain-specific logic here is
# context()'s VOLUME/FUEL filter, including the one branch a real sweep
# with plenty of freight data never happens to hit: every gauge filtered
# out entirely.
def test_context_is_none_when_no_gauge_is_volume_or_fuel():
    red = Reddington()
    tmp = tempfile.mkdtemp()
    red.cache.dir = tmp
    rows = [{"name": "Dry Bulk Freight", "group": "OCEAN", "level": 10,
             "chg_3m": 1, "chg_12m": 2, "as_of": "2026-01-01"}]
    try:
        with patch.object(red, "collect", return_value=rows):
            assert red.context(force=True) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════
# PASTEUR
# ═══════════════════════════════════════════════════════════════════════
# The one domain pack that fetches from a real crawl (telescope/crawl.py)
# rather than a single known API -- crawl.crawl() itself is already fully
# tested in test_kernel.py with a mocked http layer, so what's tested here
# is Pasteur's own business logic: joining ClinicalTrials.gov records by
# sponsor, parsing BioSpace's sitemap, and matching crawled pages back to
# sponsor names -- all pure/deterministic, none of it touching the network.
def _isolated_pasteur():
    """A fresh Pasteur instance with its disk cache redirected to a
    throwaway tmp dir -- never touches data/pasteur/, the real singleton's
    cache."""
    pas = Pasteur()
    tmp = tempfile.mkdtemp()
    pas.cache.dir = tmp
    return pas, tmp


# ── _normalize_name(): fuzzy sponsor-name matching ───────────────────────
def test_normalize_name_strips_one_trailing_corporate_suffix():
    assert _normalize_name("Merck Sharp & Dohme LLC") == "merck sharp & dohme"
    assert _normalize_name("Genentech, Inc.") == "genentech"
    assert _normalize_name("Vertex Pharmaceuticals Incorporated") == "vertex pharmaceuticals"


def test_normalize_name_does_not_strip_domain_words():
    """"Therapeutics"/"Pharmaceuticals" are part of the actual company name,
    not a generic corporate-form suffix -- stripping them would make
    unrelated companies collide on the same normalized name."""
    assert _normalize_name("Acme Therapeutics") == "acme therapeutics"
    assert _normalize_name("Acme Pharmaceuticals") == "acme pharmaceuticals"


def test_normalize_name_is_case_insensitive():
    assert _normalize_name("PFIZER INC") == _normalize_name("Pfizer Inc.")


# ── _aggregate_by_sponsor(): pure join, no network ───────────────────────
def _study(sponsor, phase="PHASE1", condition="Lymphoma", updated="2026-08-01"):
    return {
        "protocolSection": {
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor}},
            "designModule": {"phases": [phase]},
            "conditionsModule": {"conditions": [condition]},
            "statusModule": {"lastUpdatePostDateStruct": {"date": updated}},
        }
    }


def test_aggregate_by_sponsor_counts_trials_per_sponsor():
    trials = [_study("Acme Bio"), _study("Acme Bio"), _study("Other Bio")]
    agg = _aggregate_by_sponsor(trials)
    assert agg["Acme Bio"]["trial_count"] == 2
    assert agg["Other Bio"]["trial_count"] == 1


def test_aggregate_by_sponsor_takes_the_most_advanced_phase():
    trials = [_study("Acme Bio", phase="PHASE1"), _study("Acme Bio", phase="PHASE3")]
    agg = _aggregate_by_sponsor(trials)
    assert agg["Acme Bio"]["max_phase"] == pasteur.PHASE_RANK["PHASE3"]


def test_aggregate_by_sponsor_skips_studies_with_no_sponsor_name():
    trials = [{"protocolSection": {}}, _study("Acme Bio")]
    agg = _aggregate_by_sponsor(trials)
    assert list(agg.keys()) == ["Acme Bio"]


def test_aggregate_by_sponsor_dedupes_conditions_and_keeps_latest_update():
    trials = [
        _study("Acme Bio", condition="Lymphoma", updated="2026-08-01"),
        _study("Acme Bio", condition="Lymphoma", updated="2026-08-15"),
        _study("Acme Bio", condition="Leukemia", updated="2026-07-01"),
    ]
    agg = _aggregate_by_sponsor(trials)
    assert agg["Acme Bio"]["conditions"] == {"Lymphoma", "Leukemia"}
    assert agg["Acme Bio"]["last_update"] == "2026-08-15"


def test_aggregate_by_sponsor_defaults_missing_phase_to_not_applicable():
    trials = [{"protocolSection": {
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Bio"}},
    }}]
    agg = _aggregate_by_sponsor(trials)
    assert agg["Acme Bio"]["max_phase"] == pasteur.PHASE_RANK["NA"]


# ── _parse_sitemap(): pure XML parse, no network ─────────────────────────
def _sitemap_xml(entries):
    urls = "".join(
        f'<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>'
        f'<news:news><news:title>{title}</news:title>'
        f'<news:keywords>{kw}</news:keywords></news:news></url>'
        for loc, lastmod, title, kw in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        f'{urls}</urlset>'
    )


def test_parse_sitemap_extracts_fields_and_sorts_most_recent_first():
    xml = _sitemap_xml([
        ("https://www.biospace.com/a", "2026-08-01T00:00:00-04:00", "A Announces X", "kw1"),
        ("https://www.biospace.com/b", "2026-08-20T00:00:00-04:00", "B Announces Y", "kw2"),
    ])
    items = _parse_sitemap(xml)
    assert [i["url"] for i in items] == [
        "https://www.biospace.com/b", "https://www.biospace.com/a",
    ]
    assert items[0]["title"] == "B Announces Y"
    assert items[0]["keywords"] == "kw2"


def test_parse_sitemap_returns_empty_list_on_malformed_xml():
    assert _parse_sitemap("<not><valid") == []


def test_parse_sitemap_caps_at_max_press_items():
    entries = [
        (f"https://www.biospace.com/{i}", f"2026-08-{i:02d}T00:00:00-04:00", "T", "k")
        for i in range(1, 3)
    ]
    with patch.object(pasteur, "MAX_PRESS_ITEMS", 1):
        items = _parse_sitemap(_sitemap_xml(entries))
    assert len(items) == 1
    assert items[0]["url"] == "https://www.biospace.com/2"   # the more recent one


# ── _centrality(): PageRank scores attributed back to sponsor names ──────
def test_centrality_attributes_score_via_known_title_and_keywords():
    graph = {"https://www.biospace.com/a": ["https://www.biospace.com/b"]}
    press_lookup = {
        "https://www.biospace.com/a": "acme bio raises funding",
        "https://www.biospace.com/b": "acme bio phase 3 results",
    }
    centrality = _centrality(graph, press_lookup, ["Acme Bio", "Other Bio"])
    assert centrality["Acme Bio"] > 0
    assert "Other Bio" not in centrality


def test_centrality_falls_back_to_url_slug_for_unknown_pages():
    """A page the crawl discovered by following a link, but which wasn't in
    the original sitemap sample, has no known title/keywords -- matching
    must fall back to the URL's own slug rather than silently attributing
    nothing."""
    graph = {"https://www.biospace.com/seed": ["https://www.biospace.com/acme-bio-update"]}
    centrality = _centrality(graph, {}, ["Acme Bio"])
    assert centrality["Acme Bio"] > 0


def test_centrality_returns_empty_for_an_empty_graph():
    assert _centrality({}, {}, ["Acme Bio"]) == {}


# ── collect(): the full join, network mocked ─────────────────────────────
def _trials_page(studies, next_token=None):
    result = {"studies": studies}
    if next_token:
        result["nextPageToken"] = next_token
    return result


def test_collect_builds_one_row_per_sponsor_with_expected_shape():
    pas, tmp = _isolated_pasteur()
    trials_response = _trials_page([_study("Acme Bio", phase="PHASE2")])
    try:
        with patch.object(pasteur, "try_json", return_value=trials_response), \
             patch.object(pasteur, "try_text", return_value=None), \
             patch.object(pasteur, "crawl_web", return_value={}):
            rows = pas.collect(force=True)
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "Acme Bio"
        assert row["trial_count"] == 1
        assert row["phase_label"] == "Phase 2"
        assert row["key"] == "acme bio"
        assert row["link"] == "https://clinicaltrials.gov/search?spons=Acme+Bio"
        assert row["backlink_score"] == 0.0
        assert row["noise"] is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_paginates_trials_until_no_next_page_token():
    pas, tmp = _isolated_pasteur()
    pages = [
        _trials_page([_study("Acme Bio")], next_token="p2"),
        _trials_page([_study("Other Bio")]),
    ]
    try:
        with patch.object(pasteur, "try_json", side_effect=pages), \
             patch.object(pasteur, "try_text", return_value=None), \
             patch.object(pasteur, "crawl_web", return_value={}):
            rows = pas.collect(force=True)
        assert {r["name"] for r in rows} == {"Acme Bio", "Other Bio"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_stops_paginating_at_max_trial_pages_even_with_more_available():
    """A source that always returns a nextPageToken must not turn one sweep
    into an unbounded fetch loop."""
    pas, tmp = _isolated_pasteur()
    calls = {"n": 0}

    def fake_try_json(url, default=None, **kw):
        calls["n"] += 1
        return _trials_page([_study(f"Sponsor {calls['n']}")], next_token="more")

    try:
        with patch.object(pasteur, "try_json", side_effect=fake_try_json), \
             patch.object(pasteur, "try_text", return_value=None), \
             patch.object(pasteur, "crawl_web", return_value={}):
            pas.collect(force=True)
        assert calls["n"] == pasteur.MAX_TRIAL_PAGES
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collect_wires_real_backlink_scores_into_rows():
    """End-to-end (with crawl_web mocked, since crawl.crawl() itself is
    tested in test_kernel.py): a sponsor whose press release the crawl
    reaches ends up with a real, non-zero backlink_score on its row."""
    pas, tmp = _isolated_pasteur()
    trials_response = _trials_page([_study("Acme Bio")])
    sitemap = _sitemap_xml([
        ("https://www.biospace.com/acme", "2026-08-20T00:00:00-04:00",
         "Acme Bio Announces Results", "biotech"),
    ])
    graph = {"https://www.biospace.com/acme": []}
    try:
        with patch.object(pasteur, "try_json", return_value=trials_response), \
             patch.object(pasteur, "try_text", return_value=sitemap), \
             patch.object(pasteur, "crawl_web", return_value=graph):
            rows = pas.collect(force=True)
        row = next(r for r in rows if r["name"] == "Acme Bio")
        assert row["backlink_score"] > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
