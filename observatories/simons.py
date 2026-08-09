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
from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, NewLeaderRule, ThresholdRule
from telescope.registry import register
from telescope.series import Series, change, fetch_panel


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

    def collect(self, force=False):
        return fetch_panel(self, SERIES, self.ttl(force))

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
