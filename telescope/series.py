"""Time-series adapters and analytics, shared by indicator telescopes.

Simons (capital) and Reddington (logistics) both rank a fixed panel of gauges
by how abnormally each is reading rather than by its level, so the fetching and
the statistics live here instead of being copied between them.

The analytics deliberately answer "how unusual is this right now" — z-score
against the trailing year, position in the 5-year range, and whether volatility
is expanding — because a level alone ("the 10-year is 4.7") tells you nothing
you didn't already know.
"""
import datetime as dt
import statistics
from dataclasses import dataclass

from telescope.http import get_json, get_text

TRADING_DAYS = {"1m": 21, "3m": 63, "12m": 252}


@dataclass(frozen=True)
class Series:
    """One gauge on an indicator board.

    unit   "pct" for rates/spreads (changes reported in basis points),
           anything else for prices/indices (changes reported in percent)
    """
    key: str
    name: str
    group: str
    unit: str
    source: str
    ident: str
    note: str = ""


# ── source adapters: each returns [(iso_date, value)], oldest first ──────
def fred_series(ident):
    """FRED's keyless CSV export. Missing observations are marked '.'."""
    text = get_text(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={ident}")
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date, raw = parts[0].strip(), parts[1].strip()
        if raw in (".", "", "NA"):
            continue
        try:
            out.append((date, float(raw)))
        except ValueError:
            continue
    return out


def yahoo_series(ident, rng="5y"):
    data = get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ident}"
        f"?range={rng}&interval=1d"
    )
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    stamps = r.get("timestamp") or []
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        out.append((d, float(c)))
    return out


def coingecko_series(ident):
    data = get_json(
        f"https://api.coingecko.com/api/v3/coins/{ident}/market_chart"
        "?vs_currency=usd&days=365&interval=daily"
    )
    out = []
    for ms, price in data.get("prices", []):
        d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()
        out.append((d, float(price)))
    return out


ADAPTERS = {
    "fred": fred_series,
    "yahoo": yahoo_series,
    "coingecko": coingecko_series,
}

LINKS = {
    "fred": "https://fred.stlouisfed.org/series/{ident}",
    "yahoo": "https://finance.yahoo.com/quote/{ident}",
    "coingecko": "https://www.coingecko.com/en/coins/{ident}",
}


# ── analytics ────────────────────────────────────────────────────────────
def change(values, n, unit):
    """Change over n observations: basis points for rates, percent otherwise."""
    if len(values) <= n:
        return None
    old, new = values[-1 - n], values[-1]
    if unit == "pct":
        return round((new - old) * 100, 1)
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def analyse(s, points, min_points=30):
    """Turn a raw series into a rankable row. Returns None if too short.

    Note the windows are in *observations*, not calendar days — a monthly
    series like truck tonnage has ~12 observations a year, so its "trailing
    year" window is the whole available history rather than 252 points. That's
    intentional: the comparison stays within the series' own cadence.
    """
    values = [v for _, v in points]
    if len(values) < min_points:
        return None
    latest = values[-1]
    window = values[-252:] if len(values) >= 252 else values
    mean = statistics.fmean(window)
    sd = statistics.pstdev(window) or None
    z = round((latest - mean) / sd, 2) if sd else None

    five_y = values[-1260:] if len(values) >= 1260 else values
    pctile = round(sum(1 for v in five_y if v <= latest) / len(five_y) * 100, 1)

    diffs = [b - a for a, b in zip(values[-253:], values[-252:])]
    recent = diffs[-21:]
    vol_ratio = None
    if len(diffs) > 40 and len(recent) == 21:
        base = statistics.pstdev(diffs)
        cur = statistics.pstdev(recent)
        if base:
            vol_ratio = round(cur / base, 2)

    chg = {k: change(values, n, s.unit) for k, n in TRADING_DAYS.items()}
    return {
        "key": s.key,
        "name": s.name,
        "group": s.group,
        "unit": s.unit,
        "note": s.note,
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


def fetch_panel(scope, series, ttl, min_points=30):
    """Fetch and analyse a whole panel, caching each series independently.

    A dead source yields an empty list rather than taking down the sweep, so
    one delisted ticker can't blank the board.
    """
    rows, seen_sources = [], set()
    for s in series:
        def go(s=s):
            try:
                return ADAPTERS[s.source](s.ident)
            except Exception:
                return []
        points = scope.cache.cached(f"series_{s.key}", ttl, go)
        if not points:
            continue
        row = analyse(s, points, min_points=min_points)
        if row:
            rows.append(row)
            seen_sources.add(s.source)
    # Marker files so the freshness readout has something to age.
    for src in seen_sources:
        scope.cache.cached(src, 0, lambda: True)
    return rows
