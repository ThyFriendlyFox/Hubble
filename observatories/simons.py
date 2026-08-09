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

Latency note: FRED daily series publish with a 1-2 business day lag, and the
monthly series (CPI, unemployment) lag by weeks. Simons sees the recent past
clearly; it is a positioning instrument, not a trading signal.
"""
import statistics
from dataclasses import dataclass

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, NewLeaderRule, ThresholdRule
from telescope.http import get_json, get_text
from telescope.registry import register


@dataclass(frozen=True)
class Series:
    key: str
    name: str
    group: str
    unit: str          # "pct" (rates/spreads) | "price" | "index"
    source: str        # fred | yahoo | coingecko
    ident: str
    invert: bool = False   # True when "up" is risk-off (e.g. spreads)


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
    Series("hy_oas", "High Yield OAS", "CREDIT", "pct", "fred",
           "BAMLH0A0HYM2", invert=True),
    Series("ig_oas", "Inv Grade OAS", "CREDIT", "pct", "fred",
           "BAMLC0A0CM", invert=True),
    # ── risk & dollar ────────────────────────────────────────────────────
    Series("vix", "VIX", "RISK", "index", "fred", "VIXCLS", invert=True),
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

TRADING_DAYS = {"1m": 21, "3m": 63, "12m": 252}


def _pct(v):
    return None if v is None else f"{v:+.1f}%"


# ── source adapters: each returns a list of (date, value), oldest first ──
def _fred_series(ident):
    text = get_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={ident}")
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date, raw = parts[0].strip(), parts[1].strip()
        if raw in (".", "", "NA"):   # FRED marks holidays/missing with "."
            continue
        try:
            out.append((date, float(raw)))
        except ValueError:
            continue
    return out


def _yahoo_series(ident):
    data = get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ident}"
        "?range=5y&interval=1d"
    )
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    stamps = r.get("timestamp") or []
    closes = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    import datetime as dt
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        out.append((dt.datetime.utcfromtimestamp(ts).date().isoformat(), float(c)))
    return out


def _coingecko_series(ident):
    data = get_json(
        f"https://api.coingecko.com/api/v3/coins/{ident}/market_chart"
        "?vs_currency=usd&days=365&interval=daily"
    )
    import datetime as dt
    out = []
    for ms, price in data.get("prices", []):
        d = dt.datetime.utcfromtimestamp(ms / 1000).date().isoformat()
        out.append((d, float(price)))
    return out


ADAPTERS = {"fred": _fred_series, "yahoo": _yahoo_series, "coingecko": _coingecko_series}

LINKS = {
    "fred": "https://fred.stlouisfed.org/series/{ident}",
    "yahoo": "https://finance.yahoo.com/quote/{ident}",
    "coingecko": "https://www.coingecko.com/en/coins/{ident}",
}


# ── analytics ────────────────────────────────────────────────────────────
def _change(values, n, unit):
    """Change over n observations. Percent for prices, absolute for rates."""
    if len(values) <= n:
        return None
    old, new = values[-1 - n], values[-1]
    if unit == "pct":            # rates: report basis-point moves, not % of %
        return round((new - old) * 100, 1)
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _analyse(s, points):
    """Turn a raw series into the row Simons ranks."""
    values = [v for _, v in points]
    if len(values) < 30:
        return None
    latest = values[-1]
    window = values[-252:] if len(values) >= 252 else values
    mean = statistics.fmean(window)
    sd = statistics.pstdev(window) or None
    z = round((latest - mean) / sd, 2) if sd else None

    five_y = values[-1260:] if len(values) >= 1260 else values
    below = sum(1 for v in five_y if v <= latest)
    pctile = round(below / len(five_y) * 100, 1)

    # Realised vol: stdev of daily changes, recent vs the trailing year.
    diffs = [b - a for a, b in zip(values[-253:], values[-252:])]
    recent = diffs[-21:]
    vol_ratio = None
    if len(diffs) > 40 and len(recent) == 21:
        base = statistics.pstdev(diffs)
        cur = statistics.pstdev(recent)
        if base:
            vol_ratio = round(cur / base, 2)

    chg = {k: _change(values, n, s.unit) for k, n in TRADING_DAYS.items()}
    return {
        "key": s.key,
        "name": s.name,
        "group": s.group,
        "unit": s.unit,
        "link": LINKS[s.source].format(ident=s.ident),
        "level": round(latest, 4 if s.unit == "pct" else 2),
        "z": z,
        "abs_z": abs(z) if z is not None else None,
        "pctile": pctile,
        "extremity": round(abs(pctile - 50) * 2, 1),
        "chg_1m": chg["1m"],
        "chg_3m": chg["3m"],
        "chg_12m": chg["12m"],
        "abs_chg_1m": abs(chg["1m"]) if chg["1m"] is not None else None,
        "abs_chg_3m": abs(chg["3m"]) if chg["3m"] is not None else None,
        "vol_ratio": vol_ratio,
        "as_of": points[-1][0],
        "sources": [s.source],
        "noise": False,
    }


@register
class Simons(Telescope):
    slug = "simons"
    name = "SIMONS"
    domain = "CAPITAL"
    glyph = "💰"
    tagline = "MARKET ATTENTION INDEX"
    entity_label = "INDICATORS"
    sources_label = "FRED · YAHOO · COINGECKO"
    caveat = ("Ranks by abnormality, not by opinion — a high score means the "
              "gauge is far from its own normal, not that it's bullish. Daily "
              "series lag 1-2 business days; monthly series lag weeks.")

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

    def _series(self, s, ttl):
        """One cached series. A dead source yields an empty list, not a crash."""
        def go():
            try:
                return ADAPTERS[s.source](s.ident)
            except Exception:
                return []
        return self.cache.cached(f"series_{s.key}", ttl, go)

    def collect(self, force=False):
        ttl = self.ttl(force)
        rows = []
        stamps = {}
        for s in SERIES:
            points = self._series(s, ttl)
            if not points:
                continue
            row = _analyse(s, points)
            if row:
                rows.append(row)
                stamps.setdefault(s.source, True)
        # Freshness readout keys off the per-source marker files.
        for src in stamps:
            self.cache.cached(src, 0, lambda: True)
        return rows

    # ── secondary panel: the shape of the curve right now ────────────────
    def context(self, force=False):
        ttl = self.ttl(force)
        tenors = [
            ("dff", "Fed Funds"), ("dgs2", "2 Year"), ("dgs10", "10 Year"),
        ]
        rows = []
        for key, label in tenors:
            s = next((x for x in SERIES if x.key == key), None)
            if not s:
                continue
            pts = self._series(s, ttl)
            if not pts:
                continue
            vals = [v for _, v in pts]
            rows.append({
                "tenor": label,
                "level": round(vals[-1], 3),
                "chg_1m": _change(vals, 21, "pct"),
                "chg_12m": _change(vals, 252, "pct"),
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
