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

from observatories import kepler                                  # noqa: E402
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
