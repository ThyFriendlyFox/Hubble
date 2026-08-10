"""💰 SIMONS — a telescope for capital.

Most market dashboards show you levels. Levels are the least informative thing
on a screen: everyone already knows where the 10-year is. What you actually
want to know each morning is *which gauges are reading abnormally* — where the
market has moved outside its own recent distribution.

So Simons ranks indicators by **attention**, not by "best": how far the current
reading sits from that series' own trailing normal (z-score), how violently
it's moved over 1m/3m, how extreme its position is in its 5-year range, and
whether its volatility is expanding. The board answers "what should I look at
today", and the event feed announces regime crossings.

Sources (all public, no keys):
  FRED (keyless CSV)   rates, curve, credit spreads, breakevens, dollar, VIX,
                       oil — the macro backbone
  Yahoo Finance chart  equity/bond/commodity ETF closes
  CoinGecko            crypto
  SEC EDGAR 13F-HR     quarter-over-quarter position deltas, curated funds

Latency note: FRED daily series publish with a 1-2 business day lag, and the
monthly series (CPI, unemployment) lag by weeks. Simons sees the recent past
clearly; it is a positioning instrument, not a trading signal.

── 13F whale tracking: what it actually shows ───────────────────────────────
13F-HR filings are due 45 days after quarter end, so "recent" here means
positioning from months ago, not now — Simons says so on the panel itself,
not just in this docstring. The join key (CUSIP) matched cleanly across
quarters in testing with no format drift, but a large filer's holdings are
often split across several manager rows for the *same* security (Berkshire's
`otherManager` subsidiary breakdown is a real example, found live) — summing
by CUSIP within one filing avoids double-counting before ever comparing
quarters. The informationTable XML filename is chosen by the filer, not fixed
like Form D's primary_doc.xml, so each filing needs its own index lookup
first. This is a curated watchlist of large, well-known filers, not the full
universe of 13F filers (thousands) — the same tradeoff Holmdel makes with its
topic list, for the same reason: a hand-picked universe you can actually
reason about beats a firehose you can't.
"""
import re
import time

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, NewLeaderRule, ThresholdRule, money
from telescope.http import try_json, try_text
from telescope.registry import register
from telescope.series import Series, change, fetch_panel, historical_panel

SEC_UA = {"User-Agent": "Observatory-Telescope/1.0 (thyfriendlyfox@gmail.com)"}
SEC_DELAY = 0.15           # SEC asks for <=10 req/s; stay comfortably under
ACCESSIONS_TTL = 6 * 3600  # how often to check for a newly-filed quarter
# A specific accession's informationTable never changes once filed — cache it
# for a long time regardless of the telescope's own force-refresh, so a
# manual refresh doesn't re-download a multi-MB historical filing that can't
# have changed.
POSITION_CACHE_TTL = 90 * 86400
WHALE_MIN_CHANGE = 10_000_000   # ignore position deltas under $10M as noise
WHALE_TOP_N = 20                # rows shown on the panel

# A curated watchlist of large, well-known 13F filers — CIKs confirmed live
# against SEC EDGAR's company search, not from memory.
WHALES = (
    ("Berkshire Hathaway", "0001067983"),
    ("Renaissance Technologies", "0001037389"),
    ("Citadel Advisors", "0001423053"),
    ("Bridgewater Associates", "0001350694"),
    ("Two Sigma Investments", "0001179392"),
    ("Millennium Management", "0001273087"),
    ("Point72 Asset Management", "0001603466"),
    ("Tiger Global Management", "0001167483"),
    ("ARK Investment Management", "0001697748"),
    ("Soros Fund Management", "0001029160"),
    ("Viking Global Investors", "0001103804"),
)

_INFO_TABLE_RE = re.compile(r"<infoTable>(.*?)</infoTable>", re.S | re.I)


def _tag(block, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.S | re.I)
    return m.group(1).strip() if m else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# The instrument panel. Deliberately broad but small enough to read at a glance.
SERIES = (
    # ── rates & curve ────────────────────────────────────────────────────
    Series("dgs10", "10Y Treasury", "RATES", "pct", "fred", "DGS10"),
    Series("dgs2", "2Y Treasury", "RATES", "pct", "fred", "DGS2"),
    Series("t10y2y", "2s10s Curve", "RATES", "pct", "fred", "T10Y2Y"),
    Series("t10y3m", "3m10y Curve", "RATES", "pct", "fred", "T10Y3M"),
    Series("dff", "Fed Funds", "RATES", "pct", "fred", "DFF"),
    Series("t10yie", "10Y Breakeven", "RATES", "pct", "fred", "T10YIE"),
    Series("dfii10", "10Y Real Yield", "RATES", "pct", "fred", "DFII10"),
    Series("mortgage30us", "30Y Mortgage", "RATES", "pct", "fred", "MORTGAGE30US"),
    # ── credit ───────────────────────────────────────────────────────────
    Series("hy_oas", "High Yield OAS", "CREDIT", "pct", "fred", "BAMLH0A0HYM2",
           note="wider = risk-off"),
    Series("ig_oas", "Inv Grade OAS", "CREDIT", "pct", "fred", "BAMLC0A0CM",
           note="wider = risk-off"),
    # ── risk & dollar ────────────────────────────────────────────────────
    Series("vix", "VIX", "RISK", "index", "fred", "VIXCLS",
           note="higher = risk-off"),
    Series("dxy", "Dollar Index", "RISK", "index", "fred", "DTWEXBGS"),
    Series("wti", "WTI Crude", "COMMODITY", "price", "fred", "DCOILWTICO"),
    # ── equities & duration (ETF proxies) ────────────────────────────────
    Series("spy", "S&P 500", "EQUITY", "price", "yahoo", "SPY"),
    Series("qqq", "Nasdaq 100", "EQUITY", "price", "yahoo", "QQQ"),
    Series("iwm", "Russell 2000", "EQUITY", "price", "yahoo", "IWM"),
    Series("tlt", "20Y+ Treasuries", "EQUITY", "price", "yahoo", "TLT"),
    Series("hyg", "High Yield Bonds", "CREDIT", "price", "yahoo", "HYG"),
    Series("gld", "Gold", "COMMODITY", "price", "yahoo", "GLD"),
    # ── crypto ───────────────────────────────────────────────────────────
    Series("btc", "Bitcoin", "CRYPTO", "price", "coingecko", "bitcoin"),
    Series("eth", "Ethereum", "CRYPTO", "price", "coingecko", "ethereum"),
)


@register
class Simons(Telescope):
    slug = "simons"
    name = "SIMONS"
    domain = "CAPITAL"
    glyph = "💰"
    tagline = "MARKET ATTENTION INDEX"
    entity_label = "INDICATORS"
    sources_label = "FRED · YAHOO · COINGECKO · SEC 13F-HR"
    caveat = ("Ranks by abnormality, not by opinion — a high score means the "
              "gauge is far from its own normal, not that it's bullish. Daily "
              "series lag 1-2 business days; monthly series lag weeks. The "
              "WHALE MOVES panel is a curated watchlist of ~10 large filers, "
              "not the full universe of 13F filers, and 13F-HR is due 45 "
              "days after quarter end — it shows positioning from months "
              "ago, not now.")

    cache_ttl = 3600
    poll_seconds = 12 * 3600

    signals = (
        Signal("abnormality", "abs_z", "ABNORMALITY"),
        Signal("momentum_1m", "abs_chg_1m", "1M MOVE"),
        Signal("momentum_3m", "abs_chg_3m", "3M MOVE"),
        Signal("extremity", "extremity", "RANGE EXTREMITY"),
        Signal("vol", "vol_ratio", "VOL EXPANSION"),
    )
    default_weights = {
        "abnormality": 35, "momentum_1m": 25, "momentum_3m": 15,
        "extremity": 15, "vol": 10,
    }
    quality_signals = ("abnormality",)

    columns = (
        Column("name", "INDICATOR", "text"),
        Column("group", "CLASS", "text"),
        Column("score", "ATTENTION", "score"),
        Column("level", "LEVEL"),
        Column("z", "Z-SCORE"),
        Column("chg_1m", "1M", "signed"),
        Column("chg_3m", "3M", "signed"),
        Column("chg_12m", "12M", "signed"),
        Column("pctile", "5Y %ILE", "pct"),
        Column("vol_ratio", "VOL RATIO"),
    )

    rules = (
        NewLeaderRule(
            headline="💰 {name} is now the most abnormal reading on the board "
                     "(z {z}, attention {score})."
        ),
        ClimberRule(
            rank_delta=6, score_delta=6.0,
            headline="📈 {name} is demanding attention — up {rank_delta} to "
                     "#{rank} (z {z}, 1m {chg_1m}).",
        ),
        # Regime boundaries that genuinely change how capital is positioned.
        ThresholdRule(
            field="level", level=0.0, type="regime_change",
            headline_above="🟢 Yield curve re-steepened — {name} back above zero "
                           "at {new_value}.",
            headline_below="🔴 Yield curve inverted — {name} crossed below zero "
                           "at {new_value}.",
        ),
    )
    snapshot_fields = (
        "level", "z", "abs_z", "pctile", "chg_1m", "chg_3m", "chg_12m",
        "vol_ratio", "group", "as_of",
    )

    def source_keys(self):
        return ["fred", "yahoo", "coingecko"]

    def collect(self, force=False):
        return fetch_panel(self, SERIES, self.ttl(force))

    def historical_rows(self, rows):
        return historical_panel(self, SERIES)

    # ── secondary panel: the shape of the curve right now ────────────────
    def context(self, force=False):
        panels = []
        curve = self._curve_panel(force)
        if curve:
            panels.append(curve)
        whales = self._whale_panel(force)
        if whales:
            panels.append(whales)
        return panels or None

    # ── secondary panel: the shape of the curve right now ────────────────
    def _curve_panel(self, force=False):
        ttl = self.ttl(force)
        tenors = [
            ("dff", "Fed Funds"), ("dgs2", "2 Year"), ("dgs10", "10 Year"),
        ]
        rows = []
        for key, label in tenors:
            s = next((x for x in SERIES if x.key == key), None)
            if not s:
                continue
            pts = self.cache.get(f"series_{s.key}", ttl) or []
            if not pts:
                continue
            vals = [v for _, v in pts]
            rows.append({
                "tenor": label,
                "level": round(vals[-1], 3),
                "chg_1m": change(vals, 21, "pct"),
                "chg_12m": change(vals, 252, "pct"),
            })
        if not rows:
            return None
        return {
            "title": "THE CURVE · WHAT RATES ARE DOING",
            "subtitle": "Levels in percent; changes in basis points",
            "columns": [
                {"field": "tenor", "label": "TENOR", "fmt": "text"},
                {"field": "level", "label": "LEVEL", "fmt": "num"},
                {"field": "chg_1m", "label": "1M (BP)", "fmt": "signed"},
                {"field": "chg_12m", "label": "12M (BP)", "fmt": "signed"},
            ],
            "rows": rows,
        }

    # ── secondary panel: 13F position deltas across a curated whale list ──
    def _accessions(self, cik, ttl):
        """The two most recent 13F-HR (accession, report_date) pairs for one
        filer, via SEC's structured submissions JSON — much cleaner than
        parsing the legacy atom feed."""
        def go():
            data = try_json(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                default=None, headers=SEC_UA,
            )
            if not data:
                return []
            recent = data.get("filings", {}).get("recent", {})
            out = []
            for f, a, d in zip(
                recent.get("form", []), recent.get("accessionNumber", []),
                recent.get("reportDate", []),
            ):
                if f == "13F-HR":
                    out.append({"accession": a, "report_date": d})
                if len(out) >= 2:
                    break
            return out
        return self.cache.cached(f"13f_accn_{cik}", ttl, go)

    def _info_table_url(self, cik, accession):
        """The informationTable XML's filename is filer-chosen, not fixed
        like Form D's primary_doc.xml — ask the filing's own index for it."""
        acc_nodash = accession.replace("-", "")
        idx = try_json(
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{acc_nodash}/index.json",
            default=None, headers=SEC_UA,
        )
        if not idx:
            return None
        for item in idx.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.endswith(".xml") and name != "primary_doc.xml":
                return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                        f"{acc_nodash}/{name}")
        return None

    def _positions(self, url):
        """CUSIP -> {name, value}, summed across rows.

        A filer can split one security across several manager rows —
        Berkshire's subsidiary managers via `otherManager` is a real,
        confirmed example, not a hypothetical — so this sums by CUSIP
        within one filing before any quarter-over-quarter comparison
        happens, or the same position would be double-counted.
        """
        xml = try_text(url, default=None, headers=SEC_UA)
        if not xml:
            return {}
        out = {}
        for block in _INFO_TABLE_RE.findall(xml):
            cusip = _tag(block, "cusip")
            if not cusip:
                continue
            value = _num(_tag(block, "value")) or 0.0
            name = _tag(block, "nameOfIssuer") or cusip
            slot = out.setdefault(cusip, {"name": name, "value": 0.0})
            slot["value"] += value
        return out

    def _whale_moves(self, force):
        """Top position deltas across the curated watchlist, biggest first."""
        ttl = self.ttl(force)
        moves, as_of = [], None
        for name, cik in WHALES:
            accns = self._accessions(cik, ttl)
            if len(accns) < 2:
                continue
            recent_acc, prior_acc = accns[0], accns[1]
            as_of = as_of or recent_acc["report_date"]
            recent_url = self._info_table_url(cik, recent_acc["accession"])
            time.sleep(SEC_DELAY)
            prior_url = self._info_table_url(cik, prior_acc["accession"])
            time.sleep(SEC_DELAY)
            if not recent_url or not prior_url:
                continue
            # Keyed on the accession itself, not force: a specific filing's
            # positions can never change once filed, so a manual refresh
            # shouldn't re-download a multi-MB historical document.
            recent_pos = self.cache.cached(
                f"13f_pos_{cik}_{recent_acc['accession']}",
                POSITION_CACHE_TTL, lambda u=recent_url: self._positions(u),
            )
            time.sleep(SEC_DELAY)
            prior_pos = self.cache.cached(
                f"13f_pos_{cik}_{prior_acc['accession']}",
                POSITION_CACHE_TTL, lambda u=prior_url: self._positions(u),
            )
            time.sleep(SEC_DELAY)
            for cusip in set(recent_pos) | set(prior_pos):
                r = recent_pos.get(cusip, {"name": None, "value": 0.0})
                p = prior_pos.get(cusip, {"name": None, "value": 0.0})
                delta = r["value"] - p["value"]
                if abs(delta) < WHALE_MIN_CHANGE:
                    continue
                direction = (
                    "NEW" if p["value"] == 0 else
                    "EXITED" if r["value"] == 0 else
                    "INCREASED" if delta > 0 else "DECREASED"
                )
                moves.append({
                    "fund": name,
                    "security": r["name"] or p["name"] or cusip,
                    "prior_value": p["value"],
                    "recent_value": r["value"],
                    "change": delta,
                    "direction": direction,
                })
        moves.sort(key=lambda m: abs(m["change"]), reverse=True)
        return moves[:WHALE_TOP_N], as_of

    def _whale_panel(self, force=False):
        moves, as_of = self._whale_moves(force)
        if not moves:
            return None
        rows = [{
            "fund": m["fund"],
            "security": m["security"],
            "prior_value": m["prior_value"],
            "recent_value": m["recent_value"],
            "change_fmt": money(m["change"]),
            "direction": m["direction"],
        } for m in moves]
        return {
            "title": "WHALE MOVES · 13F POSITION CHANGES",
            "subtitle": (
                f"Quarter-over-quarter deltas across {len(WHALES)} curated "
                f"large filers, as of {as_of or 'unknown'} — 13F-HR is due "
                "45 days after quarter end, so this is positioning from "
                "months ago, not now"
            ),
            "columns": [
                {"field": "fund", "label": "FUND", "fmt": "text"},
                {"field": "security", "label": "SECURITY", "fmt": "text"},
                {"field": "prior_value", "label": "PRIOR VALUE", "fmt": "money"},
                {"field": "recent_value", "label": "NEW VALUE", "fmt": "money"},
                {"field": "change_fmt", "label": "CHANGE", "fmt": "text"},
                {"field": "direction", "label": "MOVE", "fmt": "text"},
            ],
            "rows": rows,
        }
