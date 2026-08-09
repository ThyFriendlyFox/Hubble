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
new ones as they appear.

Sources (all public, no keys):
  SEC EDGAR daily index   every Form D / D-A filed, by day
  SEC EDGAR filing XML    offering amount, amount sold, industry, state, date
  HN Algolia              public attention — or the conspicuous absence of it

Deliberately the outside-in complement to warm-intro dealflow: it sees what
nobody has introduced you to yet.
"""
import datetime as dt
import re
import time
from urllib.parse import quote_plus

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, NewEntrantRule, NewLeaderRule
from telescope.http import get_json, get_text
from telescope.registry import register

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


def _tag(xml, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    return m.group(1).strip() if m else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _money(v):
    v = v or 0
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


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


@register
class Kepler(Telescope):
    slug = "kepler"
    name = "KEPLER"
    domain = "STARTUPS"
    glyph = "🪐"
    tagline = "PRIVATE RAISE DETECTION"
    entity_label = "ISSUERS"
    sources_label = "SEC FORM D · EDGAR · HACKER NEWS"
    caveat = ("US Reg D filings only — no non-US raises, and equity crowdfunding "
              "and some 4(a)(2) private placements never file. Amounts are as "
              "reported by the issuer. Funds and SPVs are flagged as noise, not "
              "deleted; the classifier is name- and industry-based, so it errs. "
              "Attention is matched on company name against Hacker News, which "
              "is approximate — a missing HN signal is weaker evidence than a "
              "present one.")

    cache_ttl = 6 * 3600
    poll_seconds = 12 * 3600

    signals = (
        Signal("size", "raise_size", "RAISE SIZE", log=True),
        Signal("sold", "amount_sold", "CAPITAL IN", log=True),
        Signal("conviction", "sold_pct", "% OF ROUND CLOSED"),
        Signal("recency", "days_ago", "FRESHNESS", higher=False),
        Signal("buzz", "hn_points", "PUBLIC ATTENTION", log=True),
        Signal("tech", "tech_score", "TECH / DEEPTECH"),
    )
    default_weights = {
        "size": 30, "sold": 15, "conviction": 15, "recency": 20,
        "buzz": 5, "tech": 15,
    }
    # Note this dampens on *offering economics*, not on public attention — a
    # raise with zero public footprint is the whole point of Kepler, so `buzz`
    # is deliberately excluded here. What can't be ranked is a filing that
    # discloses no numbers at all.
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
    )
    snapshot_fields = (
        "offering_amount", "raise_size", "raise_fmt", "amount_sold",
        "sold_pct", "industry", "state",
        "filed", "hn_points", "cik", "stealth",
    )

    def source_keys(self):
        return ["filings", "details", "hn"]

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
        return self.cache.cached("filings", ttl, go)

    def _detail(self, filing):
        """Offering economics from one filing's primary_doc.xml."""
        acc = filing["path"].rsplit("/", 1)[-1].replace(".txt", "")
        url = (f"https://www.sec.gov/Archives/edgar/data/{filing['cik']}/"
               f"{acc.replace('-', '')}/primary_doc.xml")
        try:
            xml = get_text(url, headers=SEC_UA)
        except Exception:
            return None
        industry = _tag(xml, "industryGroupType")
        offering = _num(_tag(xml, "totalOfferingAmount"))
        sold = _num(_tag(xml, "totalAmountSold"))
        first_sale = _tag(xml, "dateOfFirstSale") or ""
        m = re.search(r"<value>(\d{4}-\d{2}-\d{2})</value>", first_sale)
        return {
            "entity": _tag(xml, "entityName") or filing["company"],
            "industry": industry,
            "offering_amount": offering,
            "amount_sold": sold,
            "state": _tag(xml, "stateOrCountry"),
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
        return self.cache.cached("details", ttl, go)

    def _hn(self, names, ttl):
        """Public attention per issuer: story count and best score on HN.

        Capped at ENRICH_TOP issuers per sweep to keep sweeps fast; the rest
        simply have no buzz signal and renormalise out of the blend.
        """
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
                # Algolia ranks fuzzily, so confirm the company name actually
                # appears in the title before believing the hit.
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
        return self.cache.cached("hn", ttl, go)

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
                "raise_fmt": _money(offering or sold),
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

        # Attention lookup, biggest non-fund raises first.
        candidates = sorted(
            (r for r in rows if not r["noise"]),
            key=lambda r: r["raise_size"] or 0, reverse=True,
        )
        hn = self._hn([r["name"] for r in candidates], ttl)
        for r in rows:
            info = hn.get(r["name"])
            r["hn_points"] = info["points"] if info else None
            r["hn_stories"] = info["stories"] if info else None
            # A real raise with zero public footprint is the headline case.
            r["stealth"] = bool(
                not r["noise"] and (r["raise_size"] or 0) >= 5e6
                and info is not None and info["stories"] == 0
            )
        return rows

    # ── secondary panel: where private capital is landing ────────────────
    def context(self, force=False):
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
