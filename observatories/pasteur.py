"""🧬 PASTEUR — a telescope for biotech pipelines.

Louis Pasteur's germ theory turned disease from a mystery into something
trackable. This telescope tracks the modern version: which companies have
real clinical-trial pipelines moving, and which of them the press is already
paying disproportionate attention to before that shows up in the trial data
itself.

Sources (all public, no keys):
  ClinicalTrials.gov API v2   active/recruiting trial counts, phase, sponsor,
                              conditions — sampled from the most recently
                              updated ~500 trials, not the full registry
  BioSpace (crawled)          a small, bounded, robots.txt-respecting crawl
                              of BioSpace's own press-release network,
                              ranked by PageRank — see `telescope/crawl.py`
                              and `telescope/graph.py` for the mechanics

── What this can't see ──────────────────────────────────────────────────────
The FDA does not publish a forward-looking PDUFA calendar (confirmed live —
every PDUFA-date aggregator found, e.g. BiopharmaWatch, is a paid product
with no free tier). This telescope tracks trial phase progression as its
"getting closer to approval" signal instead of PDUFA decision dates
specifically — a real but less precise proxy.

The backlink graph is a small, seeded crawl of exactly one outlet's
press-release network (BioSpace, chosen because its robots.txt explicitly
permits crawling with a published `Crawl-delay: 1`, which this crawl honours
exactly — most other biotech-news domains checked either block generic
clients outright or weren't verified). ~40 pages per sweep, not the whole
web: PRESS CENTRALITY reflects visibility within this one outlet's coverage,
not global importance, and most sponsors the crawl never reaches score zero
on it, not "no attention" — just "outside this sweep's small sample."

Matching a ClinicalTrials.gov sponsor name against BioSpace article text is
done by normalising both (lowercasing, stripping a trailing corporate-form
suffix like "Inc."/"LLC") and checking substring containment — cheap and
mostly right, but it can miss a real mention that phrases the name
differently, or occasionally attribute a mention to the wrong of two
similarly-named companies. Trial phase/status data can also lag real
company announcements by days to weeks.
"""
import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from urllib.parse import quote_plus

from telescope import Column, Signal, Telescope
from telescope.crawl import crawl as crawl_web
from telescope.events import CrossoverRule, NewEntrantRule, NewLeaderRule, ThresholdRule
from telescope.graph import pagerank
from telescope.http import try_json, try_text
from telescope.registry import register

CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_FIELDS = ",".join([
    "NCTId", "OverallStatus", "Phase", "LeadSponsorName",
    "LastUpdatePostDate", "Condition",
])
SITEMAP_URL = "https://www.biospace.com/news-sitemap-content.xml"
SITEMAP_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

MAX_TRIAL_PAGES = 5          # x100 pageSize = up to 500 of the most recently updated trials
MAX_PRESS_ITEMS = 60         # most recent press releases considered as crawl seeds
MAX_CRAWL_PAGES = 40         # hard cap on pages fetched per sweep
CRAWL_DELAY = 1.0            # matches www.biospace.com's own robots.txt Crawl-delay
BACKLINK_CACHE_TTL = 24 * 3600   # crawling is the expensive step; cache it longer than trials

# NA (observational studies, no phase) ranks lowest; PHASE4 (post-approval) highest.
PHASE_RANK = {
    "NA": 0, "EARLY_PHASE1": 1, "PHASE1": 2, "PHASE1/PHASE2": 3,
    "PHASE2": 4, "PHASE2/PHASE3": 5, "PHASE3": 6, "PHASE4": 7,
}
PHASE_LABEL = {
    0: "N/A", 1: "Early Phase 1", 2: "Phase 1", 3: "Phase 1/2",
    4: "Phase 2", 5: "Phase 2/3", 6: "Phase 3", 7: "Phase 4",
}
# Sits between PHASE2/PHASE3 (5) and PHASE3 (6) -- crossing it means a
# sponsor's most-advanced trial just reached Phase 3 or beyond.
PHASE_3_LEVEL = 5.5

_SUFFIX_RE = re.compile(
    r"[,.]?\s*\b(incorporated|inc|corporation|corp|limited|ltd|llc|plc|company|co)\b\.?\s*$",
    re.IGNORECASE,
)


def _normalize_name(name):
    """Lowercase, minus one trailing corporate-form suffix -- "Merck Sharp &
    Dohme LLC" and "Merck Sharp & Dohme" should be treated as the same
    entity for matching purposes, without stripping domain words like
    "Therapeutics" or "Pharmaceuticals" that are actually part of the name."""
    return _SUFFIX_RE.sub("", name).strip().lower()


def _aggregate_by_sponsor(trials):
    """Pure join: raw ClinicalTrials.gov study records -> one summary per
    lead sponsor. No network, no caching -- easy to test directly."""
    by_sponsor = defaultdict(lambda: {
        "trial_count": 0, "max_phase": 0, "conditions": set(), "last_update": None,
    })
    for t in trials:
        section = t.get("protocolSection") or {}
        sponsor = ((section.get("sponsorCollaboratorsModule") or {})
                   .get("leadSponsor") or {}).get("name")
        if not sponsor:
            continue
        agg = by_sponsor[sponsor]
        agg["trial_count"] += 1
        phases = (section.get("designModule") or {}).get("phases") or ["NA"]
        agg["max_phase"] = max(agg["max_phase"], max(PHASE_RANK.get(p, 0) for p in phases))
        agg["conditions"].update((section.get("conditionsModule") or {}).get("conditions") or [])
        updated = ((section.get("statusModule") or {}).get("lastUpdatePostDateStruct") or {}).get("date")
        if updated and (agg["last_update"] is None or updated > agg["last_update"]):
            agg["last_update"] = updated
    return by_sponsor


def _parse_sitemap(text):
    """Pure parse: sitemap XML text -> a list of {url, lastmod, title,
    keywords} dicts, most recent first. No network -- easy to test directly."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    items = []
    for url_el in root.findall("sm:url", SITEMAP_NS):
        loc = url_el.findtext("sm:loc", default=None, namespaces=SITEMAP_NS)
        if not loc:
            continue
        items.append({
            "url": loc,
            "lastmod": url_el.findtext("sm:lastmod", default="", namespaces=SITEMAP_NS),
            "title": url_el.findtext("news:news/news:title", default="", namespaces=SITEMAP_NS),
            "keywords": url_el.findtext("news:news/news:keywords", default="", namespaces=SITEMAP_NS),
        })
    items.sort(key=lambda i: i["lastmod"], reverse=True)
    return items[:MAX_PRESS_ITEMS]


def _centrality(graph_edges, press_lookup, sponsor_names):
    """PageRank over the crawled graph, attributed back to sponsor names via
    substring match against each URL's known title/keywords (falling back to
    the URL's own slug for pages the crawl discovered but weren't in the
    original sitemap sample). Rescaled by node count so an "average" node
    scores about 1.0 -- raw PageRank fractions are all «1, which would
    otherwise flatten under this signal's log-scaling almost to nothing."""
    scores = pagerank(graph_edges)
    if not scores:
        return {}
    scale = len(scores)
    normalized = [(name, _normalize_name(name)) for name in sponsor_names]
    centrality = defaultdict(float)
    for url, score in scores.items():
        haystack = press_lookup.get(url)
        if haystack is None:
            haystack = url.rsplit("/", 1)[-1].replace("-", " ").lower()
        for name, norm in normalized:
            if norm and norm in haystack:
                centrality[name] += score * scale
    return centrality


@register
class Pasteur(Telescope):
    slug = "pasteur"
    name = "PASTEUR"
    domain = "BIOTECH"
    tagline = "CLINICAL PIPELINE & PRESS BACKLINK INDEX"
    entity_label = "SPONSORS"
    glyph = "🧬"
    sources_label = "CLINICALTRIALS.GOV · BIOSPACE (CRAWLED)"
    caveat = ("Tracks active/recruiting trials sampled from the ~500 most "
              "recently updated on ClinicalTrials.gov, not the full "
              "registry, joined by lead sponsor. The FDA publishes no "
              "public PDUFA calendar, so this tracks trial phase "
              "progression as its \"approaching approval\" signal instead "
              "of PDUFA decision dates. PRESS CENTRALITY comes from a "
              "small, bounded crawl of one outlet's (BioSpace) own "
              "press-release network — about 40 pages per sweep, "
              "respecting its published crawl-delay — not the whole web, "
              "so a zero there means \"outside this sweep's sample,\" not "
              "\"no attention.\" Sponsor names are matched against article "
              "text by normalised substring containment, which is cheap "
              "and mostly right but can miss a differently-phrased mention "
              "or misattribute between two similarly-named companies.")

    signals = (
        Signal("pipeline", "trial_count", "TRIAL PIPELINE", log=True),
        Signal("advance", "max_phase", "PHASE ADVANCE"),
        Signal("centrality", "backlink_score", "PRESS CENTRALITY", log=True),
    )
    default_weights = {"pipeline": 1.0, "advance": 1.0, "centrality": 0.6}
    quality_signals = ("pipeline", "advance")
    dampen = 0.80

    columns = (
        Column("name", "SPONSOR", "text"),
        Column("score", "SCORE", "score"),
        Column("trial_count", "TRIALS", "int"),
        Column("phase_label", "MOST ADVANCED", "text"),
        Column("backlink_score", "PRESS CENTRALITY", "num"),
        Column("conditions", "CONDITIONS", "text"),
        Column("last_update_days_ago", "LAST UPDATE", "int"),
    )

    rules = (
        NewLeaderRule(headline="🧬 New #1 — {name} takes the top spot (score {score})."),
        NewEntrantRule(
            headline="🧬 New entrant — {name} enters at #{rank} with {trial_count} "
                     "active trial(s), most advanced at {phase_label}.",
        ),
        ThresholdRule(
            field="max_phase", level=PHASE_3_LEVEL, type="phase_3",
            headline_above="🧬 {name}'s most advanced trial just reached Phase 3.",
            headline_below="{name}'s most advanced trial fell back below Phase 3.",
        ),
        CrossoverRule(
            leading_field="backlink_score", leading_min=2.0,
            lagging_field="trial_count", lagging_min=3,
            type="crossing_over",
            headline="🧬 {name} had elevated press attention before its trial "
                     "pipeline visibly grew — now at {trial_count} active trials.",
        ),
    )
    snapshot_fields = ("trial_count", "max_phase", "backlink_score")

    cache_ttl = 12 * 3600
    poll_seconds = 12 * 3600

    def source_keys(self):
        return ["trials", "press", "backlinks"]

    def _trials(self, ttl):
        def go():
            trials = []
            page_token = None
            for _ in range(MAX_TRIAL_PAGES):
                params = (
                    "filter.overallStatus=RECRUITING%2CACTIVE_NOT_RECRUITING"
                    f"&sort=LastUpdatePostDate:desc&pageSize=100&fields={CTGOV_FIELDS}"
                )
                if page_token:
                    params += f"&pageToken={page_token}"
                data = try_json(f"{CTGOV_BASE}?{params}", default=None)
                if not data:
                    break
                trials.extend(data.get("studies") or [])
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
            return trials
        return self.cache.cached("trials", ttl, go, is_empty=lambda r: not r)

    def _press_releases(self, ttl):
        def go():
            text = try_text(SITEMAP_URL, default=None)
            return _parse_sitemap(text) if text else []
        return self.cache.cached("press", ttl, go, is_empty=lambda r: not r)

    def _backlink_graph(self, press_releases, sponsor_names):
        def go():
            normalized = [_normalize_name(s) for s in sponsor_names if s]
            seeds = []
            for item in press_releases:
                haystack = (item["title"] + " " + item.get("keywords", "")).lower()
                if any(norm and norm in haystack for norm in normalized):
                    seeds.append(item["url"])
            if not seeds:
                return {}

            def relevant(url):
                stripped = url.rstrip("/")
                return (url.startswith("https://www.biospace.com/")
                        and "/sitemap" not in url
                        and not stripped.endswith(("/companies", "/events", "/podcasts")))

            return crawl_web(
                seeds, max_pages=MAX_CRAWL_PAGES, delay=CRAWL_DELAY,
                same_domain_only=True, link_filter=relevant,
            )
        return self.cache.cached("backlinks", BACKLINK_CACHE_TTL, go, is_empty=lambda r: not r)

    def collect(self, force=False):
        ttl = self.ttl(force)
        trials = self._trials(ttl)
        by_sponsor = _aggregate_by_sponsor(trials)
        sponsor_names = list(by_sponsor)

        press = self._press_releases(ttl)
        press_lookup = {
            item["url"]: (item["title"] + " " + item.get("keywords", "")).lower()
            for item in press
        }
        graph_edges = self._backlink_graph(press, sponsor_names)
        centrality = _centrality(graph_edges, press_lookup, sponsor_names)

        today = dt.date.today()
        rows = []
        for sponsor, agg in by_sponsor.items():
            days_ago = None
            if agg["last_update"]:
                try:
                    days_ago = (today - dt.date.fromisoformat(agg["last_update"])).days
                except ValueError:
                    days_ago = None
            rows.append({
                "key": _normalize_name(sponsor),
                "name": sponsor,
                "link": f"https://clinicaltrials.gov/search?spons={quote_plus(sponsor)}",
                "sources": ["clinicaltrials", "biospace"],
                "noise": False,
                "trial_count": agg["trial_count"],
                "max_phase": agg["max_phase"],
                "phase_label": PHASE_LABEL.get(agg["max_phase"], "—"),
                "conditions": ", ".join(sorted(agg["conditions"])[:3]) or "—",
                "last_update_days_ago": days_ago,
                "backlink_score": round(centrality.get(sponsor, 0.0), 3),
            })
        return rows
