"""Declarative event rules for the diff engine.

Hubble hard-coded four rules (new #1, new arrival, big climber, price drop).
Those are actually four *shapes* that recur in every domain, so they live here
as configurable rule objects and each telescope declares its own instances with
its own thresholds and headline copy.

A rule renders a headline from a template `.format()`-ed against the current
row plus any extras the rule computes, so domain packs stay declarative.
"""
from dataclasses import dataclass, field as dc_field


def money(v):
    """Format a dollar amount for a headline: 1_234_000 -> '$1.2M'. Shared
    because three domain packs (Jackson, Kepler, Simons) each independently
    needed the identical formatter for their DeltaRule headlines."""
    v = v or 0
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:,.1f}{unit}"
    return f"${v:,.0f}"


def _fmt(template, row, extra):
    # A field that's None (present but unknown, not missing from the row —
    # e.g. hn_growth when there weren't enough stories to compute one) would
    # otherwise render as the literal text "None" in a headline, since that's
    # just what str.format() does with it.
    ctx = {k: ("—" if v is None else v) for k, v in row.items()}
    ctx.update({k: ("—" if v is None else v) for k, v in extra.items()})
    try:
        return template.format(**ctx)
    except (KeyError, IndexError, ValueError):
        # A bad template shouldn't kill a sweep — degrade to something usable.
        return f"{row.get('name')} — {row.get('type', 'update')}"


def _event(kind, row, ts, headline, extra=None):
    e = {
        "type": kind,
        "key": row.get("key"),
        "name": row.get("name"),
        "rank": row.get("rank"),
        "score": row.get("score"),
        "link": row.get("link"),
        "ts": ts,
        "headline": headline,
    }
    if extra:
        e.update(extra)
    return e


@dataclass
class NewLeaderRule:
    """Fires when rank 1 changes hands."""
    headline: str = "🔭 New #1 — {name} takes the top spot (score {score})."
    type: str = "new_leader"

    def board(self, prev_rows, curr_rows, ts):
        prev_leader = next((r for r in prev_rows if r.get("rank") == 1), None)
        curr_leader = next((r for r in curr_rows if r.get("rank") == 1), None)
        if not curr_leader:
            return []
        if prev_leader and prev_leader.get("key") == curr_leader.get("key"):
            return []
        return [_event(self.type, curr_leader, ts,
                       _fmt(self.headline, curr_leader, {}))]

    def row(self, prev, curr, ts):
        return []


@dataclass
class NewEntrantRule:
    """Fires when an entity appears that wasn't in the previous snapshot."""
    max_rank: int = 150
    require_any: tuple = ()          # row must have >=1 of these fields set
    headline: str = "🔭 New entrant — {name} enters at #{rank} (score {score})."
    type: str = "new_entrant"

    def board(self, prev_rows, curr_rows, ts):
        return []

    def row(self, prev, curr, ts):
        if prev is not None:
            return []
        if self.require_any and not any(curr.get(f) is not None for f in self.require_any):
            return []
        if not curr.get("rank") or curr["rank"] > self.max_rank:
            return []
        return [_event(self.type, curr, ts, _fmt(self.headline, curr, {}))]


@dataclass
class ClimberRule:
    """Fires when an entity gains rank places or blended score."""
    rank_delta: int = 15
    score_delta: float = 2.5
    headline: str = "📈 {name} is climbing — up {rank_delta} to #{rank} (score {score})."
    type: str = "big_climber"

    def board(self, prev_rows, curr_rows, ts):
        return []

    def row(self, prev, curr, ts):
        if prev is None or not prev.get("rank") or not curr.get("rank"):
            return []
        rd = prev["rank"] - curr["rank"]
        sd = (curr.get("score") or 0) - (prev.get("score") or 0)
        if rd < self.rank_delta and sd < self.score_delta:
            return []
        extra = {"rank_delta": rd, "score_delta": round(sd, 1)}
        return [_event(self.type, curr, ts, _fmt(self.headline, curr, extra), extra)]


@dataclass
class DeltaRule:
    """Fires when a numeric field moves by a fraction in a given direction.

    Generalises Hubble's `price_drop`: any field, either direction, with a
    formatter so headlines can render dollars, percents or raw counts.
    """
    field: str = "price_completion"
    direction: str = "down"          # "down" | "up" | "either"
    frac: float = 0.25
    type: str = "delta"
    headline: str = "{name} moved — {old_fmt} → {new_fmt}."
    formatter: object = None         # callable(value) -> str
    min_abs: float = 0.0             # ignore moves smaller than this in absolute terms

    def _render(self, v):
        if self.formatter:
            return self.formatter(v)
        return f"{v:,.2f}" if isinstance(v, float) else str(v)

    def board(self, prev_rows, curr_rows, ts):
        return []

    def row(self, prev, curr, ts):
        if prev is None:
            return []
        old, new = prev.get(self.field), curr.get(self.field)
        if not old or new is None:
            return []
        change = (new - old) / abs(old)
        if abs(new - old) < self.min_abs:
            return []
        if self.direction == "down" and change > -self.frac:
            return []
        if self.direction == "up" and change < self.frac:
            return []
        if self.direction == "either" and abs(change) < self.frac:
            return []
        extra = {
            "old_value": old,
            "new_value": new,
            "old_fmt": self._render(old),
            "new_fmt": self._render(new),
            "pct": round(change * 100, 1),
        }
        return [_event(self.type, curr, ts, _fmt(self.headline, curr, extra), extra)]


@dataclass
class ThresholdRule:
    """Fires when a field crosses a fixed boundary between sweeps.

    For regime-style telescopes: "10y-2y spread went negative", "dwell time
    crossed 5 days". Direction is inferred from which side it landed on.
    """
    field: str = ""
    level: float = 0.0
    type: str = "threshold"
    headline_above: str = "{name} crossed above {level}."
    headline_below: str = "{name} crossed below {level}."
    labels: dict = dc_field(default_factory=dict)

    def board(self, prev_rows, curr_rows, ts):
        return []

    def row(self, prev, curr, ts):
        if prev is None:
            return []
        old, new = prev.get(self.field), curr.get(self.field)
        if old is None or new is None:
            return []
        extra = {"level": self.level, "old_value": old, "new_value": new}
        if old <= self.level < new:
            return [_event(self.type, curr, ts,
                           _fmt(self.headline_above, curr, extra), extra)]
        if old >= self.level > new:
            return [_event(self.type, curr, ts,
                           _fmt(self.headline_below, curr, extra), extra)]
        return []


@dataclass
class CrossoverRule:
    """Fires when a leading signal, elevated *last* sweep, is followed by a
    lagging signal elevated *this* sweep — Holmdel's "research becomes
    builders" shape, generalised. Every other rule compares one field across
    two points in time on the same row; this one compares two *different*
    fields across two different points in time, which is why it can't be
    built out of DeltaRule/ThresholdRule.

    Deliberately simple: it does not require the leading signal to have since
    cooled, because a two-point snapshot diff can't reliably tell "declining"
    from "still high" without a third point. "Leading was elevated, lagging
    is elevated now" is an honest, checkable claim; "and receding" is not,
    with only two samples.

    It DOES require the lagging signal to be newly elevated — absent or
    below its bar last sweep, above it now. Without that, a topic that
    simply stays elevated on both fields sweep after sweep would refire the
    same "crossover" every single time nothing has actually changed, which
    is exactly backwards: a crossover is a transition, not a steady state.
    """
    leading_field: str = ""
    leading_min: float = 0.0     # leading_field in the PREVIOUS row must clear this
    lagging_field: str = ""
    lagging_min: float = 0.0     # lagging_field in the CURRENT row must clear this
    type: str = "crossing_over"
    headline: str = "{name} crossed over from {leading_field} to {lagging_field}."

    def board(self, prev_rows, curr_rows, ts):
        return []

    def row(self, prev, curr, ts):
        if prev is None:
            return []
        lead = prev.get(self.leading_field)
        lag = curr.get(self.lagging_field)
        if lead is None or lag is None:
            return []
        if lead < self.leading_min or lag < self.lagging_min:
            return []
        lag_before = prev.get(self.lagging_field)
        if lag_before is not None and lag_before >= self.lagging_min:
            return []    # already elevated last sweep too -- not a new crossing
        extra = {
            "leading_value": round(lead, 1),
            "lagging_value": round(lag, 1),
        }
        return [_event(self.type, curr, ts, _fmt(self.headline, curr, extra), extra)]
