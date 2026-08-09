"""🚛 REDDINGTON — a telescope for logistics.

Freight is the economy's pulse: it moves before the numbers everyone quotes do.
But it's also the one domain in the fleet where the *good* data is paywalled —
lane-level spot rates (DAT), container indices (FBX, Drewry) and port dwell
times all sit behind commercial licences.

So Reddington is deliberately built at the resolution the free tier actually
supports: **national index level**, not lane level. It watches volume
(tonnage, carloads), cost (diesel, freight PPI) and the market's own read on
freight (carrier and shipping equities, dry-bulk futures), and ranks each gauge
by how far it is from its own normal — the same abnormality framing Simons
uses, because "which freight gauge is off its baseline" is the useful question.

Sources (all public, no keys):
  FRED     truck tonnage, rail carloads, freight TSI, diesel, freight PPI
  Yahoo    BDRY (dry bulk futures), ZIM/MATX (ocean), FDX/XPO (parcel & LTL)

Cadence note: the FRED volume indices are monthly and land ~2 months after the
period they describe, while diesel and the equities are daily. A "1M change"
therefore means one *observation*, not one calendar month — see the caveat.
"""
from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, DeltaRule, NewLeaderRule
from telescope.registry import register
from telescope.series import Series, fetch_panel

SERIES = (
    # ── volume: what is actually moving ──────────────────────────────────
    Series("truck_tonnage", "Truck Tonnage", "VOLUME", "index", "fred",
           "TRUCKD11", note="ATA for-hire tonnage, monthly"),
    Series("rail_carloads", "Rail Carloads", "VOLUME", "index", "fred",
           "RAILFRTCARLOADSD11", note="monthly"),
    Series("freight_tsi", "Freight TSI", "VOLUME", "index", "fred",
           "TSIFRGHT", note="BTS all-mode freight index, monthly"),
    # ── cost: what it costs to move it ───────────────────────────────────
    Series("diesel_retail", "Diesel Retail", "FUEL", "price", "fred",
           "GASDESW", note="US average $/gal, weekly"),
    Series("diesel_gulf", "Gulf Diesel Spot", "FUEL", "price", "fred",
           "DDFUELUSGULF", note="daily spot"),
    Series("ppi_trucking", "PPI Trucking", "RATES", "index", "fred",
           "WPU3012", note="monthly"),
    Series("ppi_ltl", "PPI General Freight LTL", "RATES", "index", "fred",
           "PCU484121484121", note="monthly"),
    # ── the market's own read on freight ─────────────────────────────────
    Series("bdry", "Dry Bulk Freight", "OCEAN", "price", "yahoo", "BDRY",
           note="dry bulk futures ETF — the closest free proxy for the BDI"),
    Series("zim", "Container Shipping", "OCEAN", "price", "yahoo", "ZIM"),
    Series("matx", "Ocean / Jones Act", "OCEAN", "price", "yahoo", "MATX"),
    Series("fdx", "Parcel", "PARCEL", "price", "yahoo", "FDX"),
    Series("xpo", "LTL Trucking", "TRUCK", "price", "yahoo", "XPO"),
)


@register
class Reddington(Telescope):
    slug = "reddington"
    name = "REDDINGTON"
    domain = "LOGISTICS"
    glyph = "🚛"
    tagline = "FREIGHT PRESSURE INDEX"
    entity_label = "GAUGES"
    sources_label = "FRED · YAHOO"
    caveat = ("National index level, not lane level — lane-resolution spot "
              "rates (DAT), container indices (FBX/Drewry) and port dwell "
              "times are all paywalled. Volume indices are monthly and land "
              "~2 months late, so a '1M change' is one observation, not one "
              "calendar month; the equities alongside them are daily.")

    cache_ttl = 6 * 3600
    poll_seconds = 24 * 3600

    signals = (
        Signal("abnormality", "abs_z", "ABNORMALITY"),
        Signal("momentum_1m", "abs_chg_1m", "RECENT MOVE"),
        Signal("momentum_3m", "abs_chg_3m", "3-PERIOD MOVE"),
        Signal("extremity", "extremity", "RANGE EXTREMITY"),
        Signal("vol", "vol_ratio", "VOL EXPANSION"),
    )
    default_weights = {
        "abnormality": 35, "momentum_1m": 25, "momentum_3m": 15,
        "extremity": 15, "vol": 10,
    }
    quality_signals = ("abnormality",)

    columns = (
        Column("name", "GAUGE", "text"),
        Column("group", "SEGMENT", "text"),
        Column("score", "PRESSURE", "score"),
        Column("level", "LEVEL"),
        Column("z", "Z-SCORE"),
        Column("chg_1m", "1P", "signed"),
        Column("chg_3m", "3P", "signed"),
        Column("chg_12m", "12P", "signed"),
        Column("pctile", "5Y %ILE", "pct"),
        Column("as_of", "AS OF", "date"),
    )

    rules = (
        NewLeaderRule(
            headline="🚛 {name} is now the most stressed freight gauge "
                     "(z {z}, pressure {score})."
        ),
        ClimberRule(
            rank_delta=4, score_delta=6.0,
            headline="📈 {name} is moving — up {rank_delta} to #{rank} "
                     "(z {z}, recent {chg_1m}).",
        ),
        DeltaRule(
            field="level", direction="up", frac=0.08, type="rate_spike",
            headline="🔺 {name} spiked {pct}% — {old_fmt} → {new_fmt}.",
        ),
        DeltaRule(
            field="level", direction="down", frac=0.08, type="rate_drop",
            headline="🔻 {name} fell {pct}% — {old_fmt} → {new_fmt}.",
        ),
    )
    snapshot_fields = (
        "level", "z", "abs_z", "pctile", "chg_1m", "chg_3m", "chg_12m",
        "vol_ratio", "group", "as_of",
    )

    def source_keys(self):
        return ["fred", "yahoo"]

    def collect(self, force=False):
        # Monthly series carry far fewer observations than daily ones, so the
        # minimum bar has to clear a monthly history rather than a daily one.
        return fetch_panel(self, SERIES, self.ttl(force), min_points=24)

    # ── secondary panel: cost vs volume, the core freight tension ────────
    def context(self, force=False):
        rows = []
        for row in self.collect(force=force):
            if row["group"] not in ("VOLUME", "FUEL"):
                continue
            rows.append({
                "gauge": row["name"],
                "segment": row["group"],
                "level": row["level"],
                "chg_3m": row["chg_3m"],
                "chg_12m": row["chg_12m"],
                "as_of": row["as_of"],
            })
        if not rows:
            return None
        rows.sort(key=lambda r: (r["segment"], r["gauge"]))
        return {
            "title": "COST VS VOLUME · THE CORE FREIGHT TENSION",
            "subtitle": ("Rising cost against falling volume is a margin "
                         "squeeze; both rising is real demand"),
            "columns": [
                {"field": "gauge", "label": "GAUGE", "fmt": "text"},
                {"field": "segment", "label": "SEGMENT", "fmt": "text"},
                {"field": "level", "label": "LEVEL", "fmt": "num"},
                {"field": "chg_3m", "label": "3 PERIODS", "fmt": "signed"},
                {"field": "chg_12m", "label": "12 PERIODS", "fmt": "signed"},
                {"field": "as_of", "label": "AS OF", "fmt": "date"},
            ],
            "rows": rows,
        }
