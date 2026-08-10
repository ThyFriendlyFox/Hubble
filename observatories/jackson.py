"""🛡 JACKSON — a telescope for defense.

Ranks the defense industrial base by where DoD money is actually going, and —
more usefully — where it's *moving*. Scale tells you who's big; momentum tells
you who's winning.

Entity: a prime contractor, aggregated across its UEIs (large primes file under
several, so LOCKHEED MARTIN CORPORATION appears more than once in the raw feed).

Sources (all public, no keys):
  USAspending.gov  contract obligations by recipient, trailing 12m vs the
                   prior 12m — the ground truth for who is getting paid
  USAspending.gov  obligations by PSC (product/service code) — the tech-area
                   panel: which capability areas are growing
  SBIR.gov         recent DoD SBIR/STTR awards — the early-stage signal for
                   where the department is seeding new tech (best effort)

Latency note: USAspending lags actual award announcements by days to weeks, so
Jackson is a "where has the money moved" instrument, not a newswire.
"""
import datetime as dt
import re
import traceback

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, DeltaRule, NewEntrantRule, NewLeaderRule, money
from telescope.http import post_json, try_json
from telescope.registry import get as get_telescope, is_enabled, register

API = "https://api.usaspending.gov"
CONTRACT_TYPES = ["A", "B", "C", "D"]  # definitive contracts + IDV orders

# Corporate suffixes stripped when collapsing a prime's several UEIs into one
# entity. Deliberately conservative — we only merge on an exact normalised match.
_SUFFIX_RE = re.compile(
    r"\b(corporation|corp|incorporated|inc|company|co|llc|l\.l\.c|ltd|limited"
    r"|lp|l\.p|plc|holdings|holding|group|the)\b\.?",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")

# DoD obligates enormous sums on things that aren't defense *technology*:
# managed healthcare, base construction, fuel resale, university research
# admin. These stay on the board (they're real spend) but are flagged so the
# dashboard can hide them by default — the same treatment Hubble gives quant
# repacks and embedding models.
_NOISE_RE = re.compile(
    r"(healthcare|health care|health net|humana|triwest|hospital|medic"
    r"|construction|builders|contracting corp|paving|dredg"
    r"|university|college|regents|institute of technology|research foundation"
    r"|fuel|petroleum|energy services|supply company|food|catering"
    r"|staffing|temporary|janitorial|logistics services)",
    re.IGNORECASE,
)


def _is_noise(name):
    return bool(_NOISE_RE.search(name or ""))


# Keyword heuristic for "reads as defense/dual-use", used only to filter
# Kepler's Form D feed for the UNMAPPED panel below. Deliberately broad
# (dual-use terms, mission areas, service branches) because false positives
# just mean an irrelevant row on a 25-row panel; false negatives mean a real
# stealth defense raise never surfaces at all, which is the worse failure here.
_DEFENSE_RE = re.compile(
    r"(defense|defence|aerospace|aeronautic|munitions?|ballistic|missile"
    r"|hypersonic|autonom(y|ous)|unmanned|\bdrone|\buas\b|\buav\b"
    r"|satellite|space systems?|orbital|counter-?uas|counter-?drone"
    r"|electronic warfare|\bew\b|cyber ?defense|cybersecurity"
    r"|signals? intelligence|\bsigint\b|\bisr\b|surveillance|reconnaissance"
    r"|naval|maritime defense|submarine|\bradar\b|directed energy"
    r"|laser weapon|\barmor\b|ammunition|national security|homeland security"
    r"|\barmy\b|\bnavy\b|air force|space force|dual.use|tactical)",
    re.IGNORECASE,
)


def _is_defense_relevant(name, industry):
    return bool(_DEFENSE_RE.search(f"{name or ''} {industry or ''}"))


def _norm_company(name):
    s = _PUNCT_RE.sub(" ", (name or "").lower())
    s = _SUFFIX_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _window(months_back_start, months_back_end):
    """(start, end) ISO dates for a window N..M months before today."""
    today = dt.date.today()
    start = today - dt.timedelta(days=int(months_back_start * 30.44))
    end = today - dt.timedelta(days=int(months_back_end * 30.44))
    return start.isoformat(), end.isoformat()


def _filters(start, end):
    return {
        "time_period": [{"start_date": start, "end_date": end}],
        "agencies": [
            {"type": "awarding", "tier": "toptier", "name": "Department of Defense"}
        ],
        "award_type_codes": CONTRACT_TYPES,
    }


PAGE_MAX = 100  # USAspending rejects limit > 100 with a 422


def _category(category, start, end, limit=100):
    """spending_by_category, paged — the workhorse query."""
    url = f"{API}/api/v2/search/spending_by_category/{category}/"
    filters = _filters(start, end)
    out, page = [], 1
    while len(out) < limit:
        payload = {
            "filters": filters,
            "limit": min(PAGE_MAX, limit - len(out)),
            "page": page,
        }
        data = post_json(url, payload)
        results = data.get("results", [])
        out.extend(results)
        if not results or not data.get("page_metadata", {}).get("hasNext"):
            break
        page += 1
    return out[:limit]


@register
class Jackson(Telescope):
    slug = "jackson"
    name = "JACKSON"
    domain = "DEFENSE"
    glyph = "🛡"
    tagline = "DEFENSE INDUSTRIAL BASE INDEX"
    entity_label = "PRIMES"
    sources_label = "USASPENDING · PSC TECH AREAS · SBIR · KEPLER (CROSS-REF)"
    caveat = ("Obligations, not announcements — USAspending lags awards by days "
              "to weeks. Subcontract flows are not visible at the prime level. "
              "The board itself can only see companies that already hold a DoD "
              "contract, which by definition excludes anything not yet on the "
              "map; the UNMAPPED panel below cross-references Kepler's Form D "
              "feed by keyword on the company name and SEC industry code, "
              "which has no defense category at all. It will miss most "
              "deliberately-named stealth defense startups — Anduril doesn't "
              "say 'defense' anywhere — and only catches the ones that do. "
              "It only appears while Kepler is enabled, and often shows "
              "nothing at all: obvious-by-name defense filers are rare in "
              "any given 12-day window, which is expected, not a bug.")

    # Defense money moves on quarterly rhythms; no need to sweep hourly.
    cache_ttl = 12 * 3600
    poll_seconds = 24 * 3600

    signals = (
        Signal("obligations", "obligations_12m", "OBLIGATIONS", log=True),
        Signal("growth", "growth_pct", "MOMENTUM"),
        Signal("awards", "award_count", "AWARD COUNT", log=True),
        Signal("avg_award", "avg_award", "AVG AWARD SIZE", log=True),
    )
    default_weights = {
        "obligations": 35, "growth": 35, "awards": 20, "avg_award": 10,
    }
    # A prime with no prior-period history is a first-time entrant; dampen it so
    # one large debut award can't top a board of established incumbents.
    quality_signals = ("growth",)

    columns = (
        Column("name", "PRIME", "text"),
        Column("score", "SCORE", "score"),
        Column("obligations_12m", "OBLIGATIONS 12M", "money"),
        Column("growth_pct", "MOMENTUM", "pct"),
        Column("obligations_prior", "PRIOR 12M", "money"),
        Column("award_count", "AWARDS", "int"),
        Column("avg_award", "AVG AWARD", "money"),
        Column("uei_count", "UEIs", "int"),
    )

    rules = (
        NewLeaderRule(
            headline="🛡 New top prime — {name} is now the largest DoD "
                     "obligation recipient (score {score})."
        ),
        NewEntrantRule(
            max_rank=60, require_any=("obligations_12m",),
            type="new_prime",
            headline="🛡 New prime on the board — {name} enters at #{rank} "
                     "with {obligations_12m} in trailing obligations.",
        ),
        ClimberRule(
            rank_delta=8, score_delta=3.0,
            headline="📈 {name} is winning share — up {rank_delta} to #{rank} "
                     "in DoD obligations.",
        ),
        DeltaRule(
            field="obligations_12m", direction="up", frac=0.30,
            type="big_award", min_abs=50e6, formatter=money,
            headline="💥 {name} obligations jumped {pct}% — {old_fmt} → "
                     "{new_fmt} trailing 12m.",
        ),
        DeltaRule(
            field="obligations_12m", direction="down", frac=0.30,
            type="funding_drop", min_abs=50e6, formatter=money,
            headline="📉 {name} obligations fell {pct}% — {old_fmt} → {new_fmt} "
                     "trailing 12m.",
        ),
    )
    snapshot_fields = (
        "obligations_12m", "obligations_prior", "growth_pct", "award_count",
        "avg_award", "uei_count",
    )

    def source_keys(self):
        return ["recipients_12m", "recipients_prior", "psc", "sbir"]

    # ── sources ──────────────────────────────────────────────────────────
    def _recipients(self, cache_key, start, end, ttl, limit=100):
        return self.cache.cached(
            cache_key, ttl, lambda: _category("recipient", start, end, limit)
        )

    def _award_counts(self, start, end, ttl, pages=5):
        """Award counts per prime, from the largest `pages`×100 awards.

        Biased toward big-ticket contracts by construction — it's a "how many
        major programs is this prime running" signal, not a total award count.
        Primes outside the top awards simply have no value and renormalise out.
        """
        def go():
            counts = {}
            for page in range(1, pages + 1):
                payload = {
                    "filters": _filters(start, end),
                    "fields": ["Recipient Name", "Award Amount"],
                    "sort": "Award Amount", "order": "desc",
                    "limit": PAGE_MAX, "page": page, "subawards": False,
                }
                data = post_json(f"{API}/api/v2/search/spending_by_award/", payload)
                results = data.get("results", [])
                for r in results:
                    k = _norm_company(r.get("Recipient Name"))
                    if k:
                        counts[k] = counts.get(k, 0) + 1
                if not results or not data.get("page_metadata", {}).get("hasNext"):
                    break
            return counts
        return self.cache.cached("award_counts", ttl, go)

    def _psc(self, start, end, ttl):
        return self.cache.cached(
            "psc", ttl, lambda: _category("psc", start, end, 25)
        )

    def _psc_prior(self, start, end, ttl):
        return self.cache.cached(
            "psc_prior", ttl, lambda: _category("psc", start, end, 60)
        )

    def _sbir(self, ttl):
        """Recent DoD SBIR awards — best effort, the API rate-limits hard."""
        def go():
            year = dt.date.today().year
            data = try_json(
                "https://api.www.sbir.gov/public/api/awards"
                f"?agency=DOD&year={year}&rows=100",
                default=[],
            )
            return data if isinstance(data, list) else []
        return self.cache.cached("sbir", ttl, go)

    # ── join ─────────────────────────────────────────────────────────────
    def collect(self, force=False):
        ttl = self.ttl(force)
        cur_start, cur_end = _window(12, 0)
        pri_start, pri_end = _window(24, 12)

        current = self._recipients("recipients_12m", cur_start, cur_end, ttl, 100)
        prior = self._recipients("recipients_prior", pri_start, pri_end, ttl, 200)
        counts = self._award_counts(cur_start, cur_end, ttl)

        # Collapse each prime's several UEIs into one entity.
        prior_by = {}
        for r in prior:
            k = _norm_company(r.get("name"))
            if k:
                prior_by[k] = prior_by.get(k, 0.0) + (r.get("amount") or 0.0)

        agg = {}
        for r in current:
            k = _norm_company(r.get("name"))
            if not k:
                continue
            slot = agg.setdefault(k, {
                "key": k,
                "name": (r.get("name") or "").title(),
                "obligations_12m": 0.0,
                "ueis": [],
            })
            slot["obligations_12m"] += r.get("amount") or 0.0
            if r.get("uei"):
                slot["ueis"].append(r["uei"])

        rows = []
        for k, slot in agg.items():
            prev = prior_by.get(k)
            growth = None
            if prev and prev > 0:
                growth = round((slot["obligations_12m"] - prev) / prev * 100, 1)
            n_awards = counts.get(k)
            rows.append({
                "key": k,
                "name": slot["name"],
                # USAspending's recipient profile pages key off UEI.
                "link": (f"https://www.usaspending.gov/recipient/{slot['ueis'][0]}"
                         if slot["ueis"] else "https://www.usaspending.gov/"),
                "obligations_12m": round(slot["obligations_12m"], 2),
                "obligations_prior": round(prev, 2) if prev else None,
                "growth_pct": growth,
                "award_count": n_awards,
                "avg_award": (round(slot["obligations_12m"] / n_awards, 2)
                              if n_awards else None),
                "uei_count": len(slot["ueis"]),
                "uei": slot["ueis"][0] if slot["ueis"] else None,
                "sources": ["usaspending"],
                "noise": _is_noise(slot["name"]),
            })
        return rows

    # ── backfill: a real trailing-12m reading from ~a year ago, already on hand
    def historical_rows(self, rows):
        """obligations_prior is already a genuine reading — the trailing-12m
        obligations figure as of roughly a year before today's fetch, not a
        number invented for this. Reusing it as a synthetic baseline means
        the very first sweep can announce real momentum (a prime whose
        obligations grew since that reading) instead of nothing until a
        second live sweep, which at a 24h poll cadence is a full day away.
        award_count/avg_award have no stored prior figure and are left None
        rather than carried forward from the current reading."""
        out = []
        for r in rows:
            prior = r.get("obligations_prior")
            if prior is None:
                continue
            out.append({
                "key": r["key"], "name": r["name"], "link": r.get("link"),
                "obligations_12m": prior, "obligations_prior": None,
                "growth_pct": None, "award_count": None, "avg_award": None,
                "uei_count": r.get("uei_count"), "uei": r.get("uei"),
                "sources": ["usaspending"], "noise": r.get("noise", False),
            })
        return out

    # ── secondary panels: where money is flowing, and who isn't on the map yet
    def context(self, force=False):
        panels = []
        psc = self._psc_panel(force)
        if psc:
            panels.append(psc)
        unmapped = self._unmapped_panel()
        if unmapped:
            panels.append(unmapped)
        return panels or None

    def _psc_panel(self, force=False):
        ttl = self.ttl(force)
        cur_start, cur_end = _window(12, 0)
        pri_start, pri_end = _window(24, 12)
        cur = self._psc(cur_start, cur_end, ttl)
        pri = {p.get("code"): (p.get("amount") or 0.0)
               for p in self._psc_prior(pri_start, pri_end, ttl)}
        if not cur:
            return None

        rows = []
        for p in cur:
            amount = p.get("amount") or 0.0
            prev = pri.get(p.get("code"))
            growth = round((amount - prev) / prev * 100, 1) if prev else None
            rows.append({
                "code": p.get("code"),
                "name": (p.get("name") or "").title(),
                "amount": amount,
                "growth_pct": growth,
            })
        rows.sort(key=lambda r: r["amount"], reverse=True)

        sbir = self._sbir(ttl)
        subtitle = "DoD obligations by product/service code, trailing 12 months"
        if sbir:
            subtitle += f" · {len(sbir)} recent DoD SBIR awards tracked"
        return {
            "title": "CAPABILITY AREAS · WHERE THE MONEY IS MOVING",
            "subtitle": subtitle,
            "columns": [
                {"field": "name", "label": "CAPABILITY AREA", "fmt": "text"},
                {"field": "amount", "label": "12M OBLIGATIONS", "fmt": "money"},
                {"field": "growth_pct", "label": "VS PRIOR 12M", "fmt": "pct"},
            ],
            "rows": rows,
        }

    def _unmapped_panel(self):
        """Defense-relevant Form D filings from Kepler's stealth-raise feed.

        Not a new source — a cross-telescope join. Jackson's own board can
        only see companies that already hold a DoD contract; Kepler already
        watches every Reg D filing in the country. This reads Kepler's
        already-cached rows (never forces a refresh: Kepler's SEC pipeline
        runs on its own cadence, and forcing it from here would mean loading
        Jackson silently triggers a slow SEC EDGAR sweep) and keeps the ones
        whose name or SEC industry code reads as defense/dual-use. It's a
        keyword heuristic, not a verified classification, and it disappears
        entirely if Kepler is turned off — see the caveat.
        """
        if not is_enabled("kepler"):
            return None
        try:
            rows = get_telescope("kepler").collect(force=False)
        except Exception:
            traceback.print_exc()
            return None

        matches = [
            r for r in rows
            if not r.get("noise") and _is_defense_relevant(r.get("name"), r.get("industry"))
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: r.get("raise_size") or 0, reverse=True)

        rows_out = [
            {
                "name": r["name"],
                "raise_fmt": r.get("raise_fmt"),
                "raise_size": r.get("raise_size"),
                "industry": r.get("industry"),
                "state": r.get("state"),
                "filed": r.get("filed"),
                "stealth": "STEALTH" if r.get("stealth") else "—",
            }
            for r in matches[:25]
        ]
        return {
            "title": "UNMAPPED · DEFENSE-ADJACENT STEALTH RAISES",
            "subtitle": ("Recent SEC Form D filings, via Kepler, whose name or "
                         "industry reads defense/dual-use — none of these carry "
                         "a DoD obligation history above, which is the point"),
            "columns": [
                {"field": "name", "label": "COMPANY", "fmt": "text"},
                {"field": "raise_fmt", "label": "RAISE", "fmt": "text"},
                {"field": "industry", "label": "INDUSTRY", "fmt": "text"},
                {"field": "state", "label": "ST", "fmt": "text"},
                {"field": "filed", "label": "FILED", "fmt": "date"},
                {"field": "stealth", "label": "FOOTPRINT", "fmt": "text"},
            ],
            "rows": rows_out,
        }
