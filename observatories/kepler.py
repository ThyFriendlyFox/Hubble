"""🪐 KEPLER — a telescope for finding startups.

Kepler never saw a planet. It stared at 150,000 stars and found thousands of
worlds from the tiny periodic dips they caused in brightness. That is exactly
how you find companies before they announce: not from press releases, but from
the disturbances they make.

The loudest disturbance a private company makes is legally mandatory. Under
Reg D, essentially every US private raise must file a **Form D** with the SEC
within 15 days of first sale — with the offering size, the amount already sold,
the industry and the state. It is public, structured, free, and it very often
lands *before* any coverage. A Form D from a company with no other footprint is
a stealth raise, detected.

Kepler ranks those issuers by how interesting the raise looks, cross-checks
each against public attention (Hacker News) and hiring signal, and announces
new ones as they appear — and, when a stealth flag it raised earlier turns
out to have been right, says so: see "signal-quality feedback" below.

Sources (all public, no keys):
  SEC EDGAR daily index   every Form D / D-A filed, by day
  SEC EDGAR filing XML    offering amount, amount sold, industry, state, date
  A guessed .com domain   verified against the homepage's own <title>
  HN Algolia              public attention — precise when a domain resolved,
                          fuzzy title matching otherwise
  Greenhouse/Lever/Ashby  open-role counts — the honest traction signal

Deliberately the outside-in complement to warm-intro dealflow: it sees what
nobody has introduced you to yet.

── Entity resolution, and why it's still partly a guess ────────────────────
Form D discloses no website, so a domain is guessed from the company's core
name (one shot, `.com` only) and only trusted if the fetched homepage's own
<title> mentions the company. That check is load-bearing, not a formality:
verifying this exact feature against live filings, a random real Form D
filer's guessed domain resolved to a completely unrelated gambling site on
an expired, squatted domain.

Once a domain is verified, Hacker News matching switches from fuzzy title-
text search to checking whether the *story's own linked URL* belongs to
that domain. This is a trade, not a strict upgrade: it catches a submission
of the company's own site (a launch, a Show HN) with far higher confidence
than title text can, because the phrase happening to appear in a title
proves the story *mentions* the company, not that it's *about* their own
page. But it will miss real news coverage entirely — a TechCrunch story
about the company links to techcrunch.com, not the company's domain, so
domain matching sees nothing where title matching would have caught it.
Kepler takes the trade because a false "no attention" is the correct
failure mode here (renormalises away, costs nothing) while a false
"attention found" is the one this instrument exists to avoid. Most issuers
still get no domain at all: many pre-launch companies have no live site
yet, and a single short-timeout guess against `.com` alone misses anyone on
another TLD or a different domain shape entirely.

── A stated limitation on the hiring signal ─────────────────────────────────
Unlike Form D's CIK or USAspending's UEI, nothing ties a guessed job-board
slug to the filer that actually owns it — the "join key" here is a guess
derived from the company name, tried against three ATS platforms in turn.
Greenhouse alone has an independent check: its board-info endpoint returns
the company's own display name, so a Greenhouse hit is verified against it
before being trusted. Lever and Ashby have no such endpoint, so a hit there
rests only on the guessed slug being distinctive enough that colliding with
an unrelated real company is implausible — the same minimum-length gate
already used for HN attention matching, and the same weaker-evidence caveat.

── SECTOR HEAT: a cross-telescope join, not a new source ────────────────────
Holmdel's crossing_over event (a topic where research became builders — see
telescope/events.py CrossoverRule) means something for startup discovery: a
field where papers/preprints led and repos are now following is a field
where founders may already be building. This panel reads Holmdel's already-
recorded crossing_over events (never forces Holmdel to sweep) and surfaces
Kepler's own issuers whose SEC industry maps to that topic's field. The
mapping is hand-curated and approximate — SEC's finite industry list has no
"aerospace" or "security" category at all, so those map to the closest
generic tech/manufacturing bucket — and it disappears entirely if Holmdel is
disabled, or if nothing has crossed over in the last 30 days, which is the
common case, not a bug.

── Signal-quality feedback: did Kepler beat the intro? ──────────────────────
`stealth` can only ever go True -> False for a given issuer, never the other
way: an HN story appearing is permanent, and a filing's own economics never
change after it's submitted. That makes the transition unambiguous — it's
the exact moment a detection Kepler made with zero public footprint gets its
first public confirmation, which is what "did Kepler beat the intro" is
actually asking. No real graduation has happened yet in this deployment's
own history (accumulated snapshots are still hours old, and a company's
first-ever HN story landing in exactly the window between two 12h sweeps is
a genuinely low-probability event on any given day, the same way Holmdel's
crossing_over was rare before it wasn't) — verified instead against a real
issuer row with `stealth` patched to simulate the transition, the same
approach used to verify crossing_over before it had ever fired for real.
"""
import datetime as dt
import re
import time
import traceback
from urllib.parse import quote_plus, urlparse

from telescope import Column, Signal, Telescope
from telescope.events import (ClimberRule, DeltaRule, FlagFlipRule,
                              NewEntrantRule, NewLeaderRule, money)
from telescope.http import get_json, get_text, probe_text, try_json, xml_tag
from telescope.parse import to_float
from telescope.registry import get as get_telescope, is_enabled, register

SEC_UA = {"User-Agent": "Observatory-Telescope/1.0 (thyfriendlyfox@gmail.com)"}
SEC_DELAY = 0.12          # SEC asks for <=10 req/s; stay comfortably under
LOOKBACK_DAYS = 12        # business-day window of filings to sweep
MAX_FILINGS = 220         # cap the per-sweep XML fetches
ENRICH_TOP = 40           # how many issuers get the HN attention lookup

# Form D is used by operating companies AND by every fund, SPV and real-estate
# syndicate in America. Those dominate by count and are not what Kepler is for.
_FUND_RE = re.compile(
    r"\b(fund|fund\s+[ivxl]+|l\.?p\.?|lp|partners|partnership|capital|ventures?"
    r"|management|advisors?|holdings?|trust|reit|realty|real\s+estate|properties"
    r"|syndicate|spv|series\s+[a-z0-9]+\s+llc|opportunit|income|yield|credit"
    r"|acquisition\s+corp|investors?|equity)\b",
    re.IGNORECASE,
)
# Industry groups on the filing itself — the most reliable discriminator.
_FUND_INDUSTRIES = {
    "Pooled Investment Fund", "Other Investment Fund", "Hedge Fund",
    "Private Equity Fund", "Venture Capital Fund", "Real Estate",
    "Commercial", "Residential", "REITS & Finance", "Construction",
    "Oil & Gas", "Investing", "Insurance", "Banking & Financial Services",
}
_TECH_INDUSTRIES = {
    "Technology", "Computers", "Telecommunications", "Other Technology",
    "Biotechnology", "Pharmaceuticals", "Health Care", "Other Health Care",
    "Electronics", "Manufacturing", "Energy", "Clean Technology",
    "Medical Devices", "Hospitals & Physicians", "Airlines & Airports",
}

CROSSOVER_WINDOW_DAYS = 30   # how far back a Holmdel crossing_over still counts

# Holmdel's topic fields, hand-mapped to Kepler's SEC industryGroupType
# values — a real approximation, not a verified match. SEC's finite
# industry list has no "aerospace" or "security" category at all, so those
# two map to the closest generic tech/manufacturing buckets, same
# limitation the UNMAPPED panel's keyword match already has to live with.
GROUP_TO_INDUSTRIES = {
    "AI": {"Technology", "Computers", "Other Technology", "Telecommunications",
           "Electronics"},
    "COMPUTE": {"Technology", "Computers", "Other Technology", "Electronics"},
    "BIO": {"Biotechnology", "Pharmaceuticals", "Health Care",
            "Other Health Care", "Medical Devices"},
    "ENERGY": {"Energy", "Clean Technology"},
    "MATERIALS": {"Manufacturing", "Clean Technology"},
    "SPACE": {"Other Technology", "Manufacturing"},
    "SECURITY": {"Technology", "Other Technology", "Computers"},
}


def _is_fund(name, industry):
    if industry and industry in _FUND_INDUSTRIES:
        return True
    return bool(_FUND_RE.search(name or ""))


# Only true corporate suffixes are stripped. Words like "Health" or "Robotics"
# look generic but are what makes a two-word name distinctive enough to match
# on — dropping them turns "Latitude Health" into "latitude", which matches
# every story about latitude.
_GENERIC_RE = re.compile(
    r"\b(inc|llc|l\.l\.c|corp|corporation|co|ltd|limited|company|holdings"
    r"|holding|group|the)\b\.?",
    re.IGNORECASE,
)


def _core_name(name):
    """The distinctive part of a company name, for attention matching.

    'Onkos Surgical, Inc.' -> 'onkos surgical'. Used to reject Hacker News
    hits that merely share a common word — without this, 'PRP Concepts, Inc.'
    matches any story containing 'concepts' and scores as a viral company.
    """
    s = re.sub(r"[^A-Za-z0-9 ]", " ", name or "")
    s = _GENERIC_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _slug(name):
    """A guessed job-board slug: 'Onkos Surgical, Inc.' -> 'onkos-surgical'.

    Real slugs are chosen by the company and often diverge from this (an
    abbreviation, a totally different brand, no hyphens) — this is a guess,
    not a lookup, which is exactly why a hit needs independent verification
    where one is available (see `_hiring`)."""
    core = _core_name(name)
    return re.sub(r"\s+", "-", core) if core else None


def _distinctive_enough(slug):
    """Same risk _hn's length gate guards against: a short/generic guessed
    slug could collide with an unrelated real company's real board."""
    return bool(slug) and (len(slug) >= 5 and ("-" in slug or len(slug) >= 10))


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _looks_like_the_company(core, html):
    """A fetched homepage is trusted only if its own <title> mentions the
    company — not merely "did something answer at this domain". Dead
    startup domains get squatted; a guessed domain resolving to a live page
    proves nothing about who is currently running it."""
    m = _TITLE_RE.search(html or "")
    if not m:
        return False
    title = m.group(1).lower()
    words = [w for w in core.split() if len(w) >= 3]
    return bool(words) and all(w in title for w in words)


@register
class Kepler(Telescope):
    slug = "kepler"
    name = "KEPLER"
    domain = "STARTUPS"
    glyph = "🪐"
    tagline = "PRIVATE RAISE DETECTION"
    entity_label = "ISSUERS"
    sources_label = ("SEC FORM D · EDGAR · DOMAIN RESOLUTION · HACKER NEWS · "
                      "GREENHOUSE/LEVER/ASHBY · HOLMDEL (CROSS-REF)")
    caveat = ("US Reg D filings only — no non-US raises, and equity crowdfunding "
              "and some 4(a)(2) private placements never file. Amounts are as "
              "reported by the issuer. Funds and SPVs are flagged as noise, not "
              "deleted; the classifier is name- and industry-based, so it errs. "
              "Form D discloses no website, so WEBSITE is a single guessed "
              "'.com' verified against the homepage's own title — most "
              "issuers get none, either because they have no live site yet "
              "or because the guess missed. When a domain does resolve, "
              "Hacker News attention is matched on the story's own linked "
              "URL, which is precise; without one it falls back to fuzzy "
              "title-text matching, which is approximate — a missing HN "
              "signal is weaker evidence than a present one either way. "
              "Hiring is a guessed job-board slug, not a real identifier — "
              "verified against the company's own name on Greenhouse, "
              "unverified on Lever/Ashby, and absent for most issuers simply "
              "because the guess didn't land on anything. The SECTOR HEAT "
              "panel cross-references Holmdel's crossing_over events by a "
              "hand-mapped, approximate industry-to-field table, only "
              "appears while Holmdel is enabled, and is usually empty — "
              "nothing crossing over in the last 30 days is the common "
              "case, not a bug. A GRADUATED event fires the one time a "
              "stealth flag is actually confirmed — the issuer's first "
              "real HN story — which can take a while to happen for any "
              "single company and may not have happened yet at all.")

    cache_ttl = 6 * 3600
    poll_seconds = 12 * 3600

    signals = (
        Signal("size", "raise_size", "RAISE SIZE", log=True),
        Signal("sold", "amount_sold", "CAPITAL IN", log=True),
        Signal("conviction", "sold_pct", "% OF ROUND CLOSED"),
        Signal("recency", "days_ago", "FRESHNESS", higher=False),
        Signal("buzz", "hn_points", "PUBLIC ATTENTION", log=True),
        Signal("tech", "tech_score", "TECH / DEEPTECH"),
        Signal("hiring", "hiring_count", "HIRING VELOCITY", log=True),
    )
    default_weights = {
        "size": 28, "sold": 13, "conviction": 13, "recency": 18,
        "buzz": 5, "tech": 13, "hiring": 10,
    }
    # Note this dampens on *offering economics*, not on public attention — a
    # raise with zero public footprint is the whole point of Kepler, so `buzz`
    # is deliberately excluded here. What can't be ranked is a filing that
    # discloses no numbers at all. `hiring` stays out for the same reason as
    # `buzz`: a stealth company hiring nobody yet is not evidence against it.
    quality_signals = ("size",)

    columns = (
        Column("name", "ISSUER", "text"),
        Column("score", "SCORE", "score"),
        Column("raise_size", "RAISE", "money"),
        Column("amount_sold", "SOLD", "money"),
        Column("sold_pct", "CLOSED", "pct"),
        Column("industry", "INDUSTRY", "text"),
        Column("state", "ST", "text"),
        Column("filed", "FILED", "date"),
        Column("hn_points", "HN", "int"),
        Column("hiring_count", "OPEN ROLES", "int"),
        Column("hiring_platform", "ATS", "text"),
        Column("domain", "WEBSITE", "url"),
    )

    rules = (
        NewLeaderRule(
            headline="🪐 {name} is the largest new private raise on the board — "
                     "{raise_fmt} raise."
        ),
        NewEntrantRule(
            max_rank=80, require_any=("raise_size",),
            type="new_candidate",
            headline="🪐 New raise detected — {name} filed a Form D for "
                     "{raise_fmt} ({industry}, {state}).",
        ),
        ClimberRule(
            rank_delta=12, score_delta=4.0,
            headline="📈 {name} moved up {rank_delta} to #{rank} on the Kepler board.",
        ),
        DeltaRule(
            field="hiring_count", direction="up", frac=0.50, min_abs=3,
            type="hiring_surge",
            headline="👷 {name} is hiring fast — open roles up {pct}% "
                     "({old_fmt} → {new_fmt}).",
        ),
        # Signal-quality feedback: stealth can only ever go True -> False (an
        # HN story appearing is permanent; the filing's own economics never
        # change), so this is the one direction worth watching — the exact
        # moment a detection Kepler made with zero public footprint gets its
        # first public confirmation.
        FlagFlipRule(
            field="stealth", from_value=True, to_value=False,
            type="stealth_graduated",
            headline="🎓 {name} graduated from stealth — Kepler flagged "
                     "{raise_fmt} raised with zero public footprint, and it "
                     "just picked up its first Hacker News attention.",
        ),
    )
    snapshot_fields = (
        "offering_amount", "raise_size", "raise_fmt", "amount_sold",
        "sold_pct", "industry", "state",
        "filed", "hn_points", "cik", "stealth",
        "hiring_count", "hiring_platform", "domain",
    )

    def source_keys(self):
        return ["filings", "details", "domains", "hn", "hiring"]

    # ── sources ──────────────────────────────────────────────────────────
    def _index_day(self, day):
        """Form D filings for one day from EDGAR's daily index."""
        q = (day.month - 1) // 3 + 1
        url = (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/"
               f"QTR{q}/form.{day:%Y%m%d}.idx")
        try:
            text = get_text(url, headers=SEC_UA)
        except Exception:
            return []          # weekends, holidays, not-yet-published days
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not (s.startswith("D ") or s.startswith("D/A ")):
                continue
            parts = re.split(r"\s{2,}", s)
            if len(parts) < 5:
                continue
            form, company, cik, filed, path = parts[:5]
            out.append({
                "form": form, "company": company.strip(), "cik": cik.strip(),
                "filed": filed.strip(), "path": path.strip(),
            })
        return out

    def _filings(self, ttl):
        """The recent Form D filing list across the lookback window."""
        def go():
            seen, out = set(), []
            day = dt.date.today()
            checked = 0
            while checked < LOOKBACK_DAYS and len(out) < MAX_FILINGS * 2:
                day -= dt.timedelta(days=1)
                if day.weekday() >= 5:
                    continue
                checked += 1
                for f in self._index_day(day):
                    if f["cik"] in seen:
                        continue
                    seen.add(f["cik"])
                    out.append(f)
                time.sleep(SEC_DELAY)
            return out
        # _index_day() already swallows a single day's failure (weekends,
        # holidays, an outage on that one day) by returning []. Every
        # business day has real Form D activity, so only a total outage
        # across the *entire* lookback window empties this out -- worth
        # falling back to stale-but-real filings for, not caching as the
        # new truth.
        return self.cache.cached("filings", ttl, go, is_empty=lambda r: not r)

    def _detail(self, filing):
        """Offering economics from one filing's primary_doc.xml."""
        acc = filing["path"].rsplit("/", 1)[-1].replace(".txt", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{filing['cik']}/"
               f"{acc.replace('-', '')}/primary_doc.xml")
        try:
            xml = get_text(url, headers=SEC_UA)
        except Exception:
            return None
        industry = xml_tag(xml, "industryGroupType")
        offering = to_float(xml_tag(xml, "totalOfferingAmount"))
        sold = to_float(xml_tag(xml, "totalAmountSold"))
        first_sale = xml_tag(xml, "dateOfFirstSale") or ""
        m = re.search(r"<value>(\d{4}-\d{2}-\d{2})</value>", first_sale)
        return {
            "entity": xml_tag(xml, "entityName") or filing["company"],
            "industry": industry,
            "offering_amount": offering,
            "amount_sold": sold,
            "state": xml_tag(xml, "stateOrCountry"),
            "first_sale": m.group(1) if m else None,
            "new_issuer": "<withinFiveYears>true</withinFiveYears>" in xml,
        }

    def _details(self, filings, ttl):
        def go():
            out = {}
            for f in filings[:MAX_FILINGS]:
                d = self._detail(f)
                if d:
                    out[f["cik"]] = d
                time.sleep(SEC_DELAY)
            return out
        # Unlike _domains()/_hiring(), _detail() only ever returns None on a
        # genuine fetch failure (get_text() raising) -- xml_tag()/to_float() are
        # regex/try-except helpers that never raise, so every filing whose
        # primary_doc.xml is actually reachable adds a real entry to out,
        # regardless of how sparse its individual fields are. An aggregate-
        # empty result across a real batch of filings is therefore a total
        # outage, not "no economics data found" -- no canary needed, same
        # reasoning as _filings()/_hn().
        return self.cache.cached("details", ttl, go, is_empty=lambda r: not r)

    def _domains(self, names, ttl):
        """A guessed .com domain per issuer, verified against the fetched
        homepage's own <title>.

        Form D discloses no website, so this is a one-shot guess (core name,
        no spaces, `.com` only) — not an attempt at broad TLD coverage, which
        would multiply the request count for a search this speculative.
        `probe_text` makes a single attempt with a short timeout per guess
        rather than the retry/backoff every other fetch here uses, because a
        dead domain is the common case, not a transient failure worth
        retrying three times.
        """
        def go():
            out = {}
            for name in names[:ENRICH_TOP]:
                core = _core_name(name)
                slug = core.replace(" ", "")
                if len(slug) < 8:
                    continue
                html = probe_text(f"https://{slug}.com", timeout=6)
                if html and _looks_like_the_company(core, html):
                    out[name] = f"{slug}.com"
                time.sleep(0.1)
            return out

        def totally_failed(result):
            if result:
                return False
            # Most guesses are wrong by design (dead domain, no live site
            # yet), so an empty result is the expected common case, not a
            # failure signal on its own -- unlike Holmdel's per-topic
            # sources, there's no aggregate "did anything come back" to
            # check here, since a real success never adds an entry unless
            # the guess is BOTH live AND verified as the right company.
            # Probing a domain that's always up is what actually tells a
            # real "all 40 guesses missed" apart from "our own DNS/network
            # is down right now" -- only the latter should fall back to
            # stale data instead of caching the empty sweep as truth.
            return probe_text("https://google.com", timeout=6) is None

        return self.cache.cached("domains", ttl, go, is_empty=totally_failed)

    def _hn(self, names, ttl, domains=None):
        """Public attention per issuer: story count and best score on HN.

        Capped at ENRICH_TOP issuers per sweep to keep sweeps fast; the rest
        simply have no buzz signal and renormalise out of the blend. When a
        domain has been verified for this issuer, matching switches from
        fuzzy title-text search to checking whether the *story's own linked
        URL* belongs to that domain. Higher-confidence, not higher-coverage:
        it catches the company's own launch/Show-HN posts very reliably, but
        misses third-party news coverage that links to a news site instead
        of the company's own domain, which title matching would have caught.
        """
        domains = domains or {}

        def go():
            out = {}
            for name in names[:ENRICH_TOP]:
                core = _core_name(name)
                # Too short or too generic to match on — record "no signal"
                # rather than a wrong one. Single-word names are the risky
                # case ("Latitude", "Practice"), so they need real length.
                if len(core) < 5 or (" " not in core and len(core) < 10):
                    continue
                try:
                    data = get_json(
                        "https://hn.algolia.com/api/v1/search"
                        f"?query={quote_plus(core)}&tags=story&hitsPerPage=30"
                    )
                except Exception:
                    continue
                domain = domains.get(name)
                if domain:
                    hits = [
                        h for h in data.get("hits", [])
                        if domain == (urlparse(h.get("url") or "").netloc or "")
                            .removeprefix("www.")
                    ]
                else:
                    # Algolia ranks fuzzily, so confirm the company name
                    # actually appears in the title before believing the hit.
                    pattern = re.compile(rf"\b{re.escape(core)}\b", re.IGNORECASE)
                    hits = [
                        h for h in data.get("hits", [])
                        if pattern.search(h.get("title") or "")
                    ]
                out[name] = {
                    "stories": len(hits),
                    "points": max([h.get("points") or 0 for h in hits], default=0),
                }
                time.sleep(0.15)
            return out
        # An entry is added for every name that clears the length filter and
        # gets a successful response -- zero hits still gets one, only a
        # raised exception skips it -- so an empty result means every
        # attempted lookup failed, not that no issuer had HN attention.
        return self.cache.cached("hn", ttl, go, is_empty=lambda r: not r)

    def _hiring(self, names, ttl):
        """Open-role count per issuer, tried against Greenhouse, then Lever,
        then Ashby, using a guessed slug from the company's core name.

        The single most honest traction signal available for free — but
        unlike Form D's CIK or USAspending's UEI, the slug is a guess, not a
        real identifier. Greenhouse's board-info endpoint returns the
        company's own display name, so a Greenhouse hit is verified against
        it before being trusted; Lever and Ashby have no equivalent, so a
        hit there is kept only when the slug is distinctive enough that an
        accidental collision with an unrelated real company is implausible.
        Capped at ENRICH_TOP issuers, same as `_hn`.
        """
        def go():
            out = {}
            for name in names[:ENRICH_TOP]:
                slug = _slug(name)
                if not _distinctive_enough(slug):
                    continue
                core = _core_name(name)

                info = try_json(
                    f"https://boards-api.greenhouse.io/v1/boards/{slug}",
                    default=None,
                )
                if info and info.get("name") and _core_name(info["name"]) == core:
                    jobs = try_json(
                        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                        default=None,
                    )
                    if jobs and jobs.get("jobs") is not None:
                        out[name] = {
                            "count": len(jobs["jobs"]), "platform": "greenhouse",
                        }
                        time.sleep(0.15)
                        continue

                jobs = try_json(
                    f"https://api.lever.co/v0/postings/{slug}?mode=json",
                    default=None,
                )
                if isinstance(jobs, list) and jobs:
                    out[name] = {"count": len(jobs), "platform": "lever"}
                    time.sleep(0.15)
                    continue

                data = try_json(
                    f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                    default=None,
                )
                if data and data.get("jobs"):
                    out[name] = {"count": len(data["jobs"]), "platform": "ashby"}

                time.sleep(0.15)
            return out

        def totally_failed(result):
            if result:
                return False
            # Same reasoning as _domains(): a real hit rate this low (1/40 is
            # the documented honest outcome — most guessed slugs simply don't
            # exist on any of the three boards) makes an empty result the
            # expected common case, not a failure signal by itself. Only a
            # probe against something that's always up can tell "every guess
            # missed" apart from "our own network is down right now".
            return probe_text("https://google.com", timeout=6) is None

        return self.cache.cached("hiring", ttl, go, is_empty=totally_failed)

    # ── join ─────────────────────────────────────────────────────────────
    def collect(self, force=False):
        ttl = self.ttl(force)
        filings = self._filings(ttl)
        details = self._details(filings, ttl)
        today = dt.date.today()

        rows = []
        for f in filings:
            d = details.get(f["cik"])
            if not d:
                continue
            name = d["entity"]
            industry = d["industry"]
            offering = d["offering_amount"]
            sold = d["amount_sold"]
            try:
                filed = dt.datetime.strptime(f["filed"], "%Y%m%d").date()
            except ValueError:
                filed = today
            sold_pct = None
            if offering and offering > 0 and sold is not None:
                sold_pct = round(min(sold / offering, 1.0) * 100, 1)
            rows.append({
                "key": f["cik"],
                "cik": f["cik"],
                "name": name,
                "link": ("https://www.sec.gov/cgi-bin/browse-edgar?action="
                         f"getcompany&CIK={f['cik']}&type=D&dateb=&owner=include"),
                "offering_amount": offering,
                "amount_sold": sold,
                # Reg D permits an indefinite offering. When the issuer used
                # one, the capital actually sold is the honest floor for how
                # big the raise is — without this fallback the size signal
                # goes missing and the row floats up on recency alone.
                "raise_size": offering or sold,
                "size_is_floor": bool(not offering and sold),
                "raise_fmt": money(offering or sold),
                "sold_pct": sold_pct,
                "industry": industry or "—",
                "state": d["state"] or "—",
                "filed": filed.isoformat(),
                "days_ago": (today - filed).days,
                "new_issuer": d["new_issuer"],
                "tech_score": 100.0 if industry in _TECH_INDUSTRIES else 0.0,
                "sources": ["sec"],
                # Funds/SPVs aren't what Kepler is for, and a filing that
                # discloses no economics at all (Reg D permits an indefinite
                # offering) can't be ranked — otherwise it floats to the top of
                # the board on recency alone, since every filing here is recent.
                "noise": (_is_fund(name, industry) or not (offering or sold)),
            })

        # Entity resolution, attention and hiring lookups, biggest non-fund
        # raises first. Domains are resolved before HN so attention matching
        # can use them.
        candidates = sorted(
            (r for r in rows if not r["noise"]),
            key=lambda r: r["raise_size"] or 0, reverse=True,
        )
        names = [r["name"] for r in candidates]
        domains = self._domains(names, ttl)
        hn = self._hn(names, ttl, domains=domains)
        hiring = self._hiring(names, ttl)
        for r in rows:
            r["domain"] = domains.get(r["name"])
            info = hn.get(r["name"])
            r["hn_points"] = info["points"] if info else None
            r["hn_stories"] = info["stories"] if info else None
            # A real raise with zero public footprint is the headline case.
            r["stealth"] = bool(
                not r["noise"] and (r["raise_size"] or 0) >= 5e6
                and info is not None and info["stories"] == 0
            )
            hire = hiring.get(r["name"])
            r["hiring_count"] = hire["count"] if hire else None
            r["hiring_platform"] = hire["platform"] if hire else None
        return rows

    # ── secondary panel: where private capital is landing ────────────────
    def context(self, force=False):
        panels = []
        capital = self._capital_panel(force)
        if capital:
            panels.append(capital)
        heat = self._sector_heat_panel()
        if heat:
            panels.append(heat)
        return panels or None

    def _capital_panel(self, force=False):
        ttl = self.ttl(force)
        filings = self._filings(ttl)
        details = self._details(filings, ttl)
        agg = {}
        for cik, d in details.items():
            if _is_fund(d["entity"], d["industry"]):
                continue
            ind = d["industry"] or "Unspecified"
            slot = agg.setdefault(ind, {"industry": ind, "count": 0, "total": 0.0})
            slot["count"] += 1
            slot["total"] += d["offering_amount"] or 0.0
        rows = sorted(agg.values(), key=lambda r: r["total"], reverse=True)
        if not rows:
            return None
        return {
            "title": "WHERE PRIVATE CAPITAL IS LANDING",
            "subtitle": (f"Operating-company Form D filings, last {LOOKBACK_DAYS} "
                         "business days (funds and SPVs excluded)"),
            "columns": [
                {"field": "industry", "label": "INDUSTRY", "fmt": "text"},
                {"field": "count", "label": "RAISES", "fmt": "int"},
                {"field": "total", "label": "TOTAL OFFERED", "fmt": "money"},
            ],
            "rows": rows,
        }

    # ── secondary panel: cross-telescope join, Holmdel's crossovers → here ─
    def _crossed_over_groups(self):
        """Recent Holmdel crossing_over events (research becoming builders —
        see telescope/events.py CrossoverRule), resolved to each topic's
        field/group. Reads Holmdel's already-recorded events and
        already-cached rows only; never forces Holmdel to sweep, and
        disappears entirely if Holmdel is disabled — the same discipline
        Jackson's UNMAPPED panel already follows for its own Kepler read.

        Reads store.latest() rather than collect(force=False) to actually
        keep that promise: force=False still triggers a real, slow, paced
        live fetch if Holmdel's own cache has simply expired via its own
        TTL (Holmdel's arXiv/GitHub sources being the slowest in the whole
        fleet), which is exactly the silent multi-minute Kepler-page-load
        this docstring says it avoids. store.latest() is a pure disk read
        of Holmdel's last real sweep, so it can't force one.
        """
        if not is_enabled("holmdel"):
            return {}
        try:
            holmdel = get_telescope("holmdel")
            events = holmdel.store.load_events(limit=200)
            snapshot = holmdel.store.latest()
            rows = snapshot["rows"] if snapshot else []
        except Exception:
            traceback.print_exc()
            return {}
        group_by_key = {r["key"]: r.get("group") for r in rows}
        cutoff = time.time() - CROSSOVER_WINDOW_DAYS * 86400
        out = {}
        for e in events:
            if e.get("type") != "crossing_over" or (e.get("ts") or 0) < cutoff:
                continue
            group = group_by_key.get(e.get("key"))
            if group:
                out.setdefault(e["name"], group)
        return out

    def _sector_heat_panel(self):
        crossed = self._crossed_over_groups()
        if not crossed:
            return None
        # Several topics can map to the same industry (two AI topics both
        # land on "Other Technology") -- attribute all of them, not just
        # whichever happened to be inserted first, or a row would silently
        # look like it's tied to one topic when it's really tied to both.
        topic_for_industry = {}
        for topic, group in crossed.items():
            for industry in GROUP_TO_INDUSTRIES.get(group, ()):
                existing = topic_for_industry.get(industry)
                topic_for_industry[industry] = (
                    f"{existing}, {topic}" if existing else topic
                )
        if not topic_for_industry:
            return None
        matches = [
            r for r in self.collect(force=False)
            if not r["noise"] and r.get("industry") in topic_for_industry
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get("raise_size") or 0, reverse=True)
        rows = [{
            "name": r["name"],
            "industry": r["industry"],
            "raise_fmt": r.get("raise_fmt"),
            "state": r.get("state"),
            "filed": r.get("filed"),
            "topic": topic_for_industry.get(r["industry"], "—"),
        } for r in matches[:15]]
        topics = ", ".join(sorted(set(crossed)))
        return {
            "title": "SECTOR HEAT · IDEAS CROSSING OVER TO BUILDERS",
            "subtitle": (
                f"Kepler issuers in a field where Holmdel detected research "
                f"becoming builders in the last {CROSSOVER_WINDOW_DAYS} days "
                f"({topics}) — industry-to-field is a hand-mapped "
                "approximation, not a verified match"
            ),
            "columns": [
                {"field": "name", "label": "ISSUER", "fmt": "text"},
                {"field": "industry", "label": "INDUSTRY", "fmt": "text"},
                {"field": "topic", "label": "CROSSED-OVER TOPIC", "fmt": "text"},
                {"field": "raise_fmt", "label": "RAISE", "fmt": "text"},
                {"field": "state", "label": "ST", "fmt": "text"},
                {"field": "filed", "label": "FILED", "fmt": "date"},
            ],
            "rows": rows,
        }
