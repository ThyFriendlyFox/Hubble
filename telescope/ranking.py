"""Generic normalise → weight → blend scorer, shared by every telescope.

This is Hubble's original scoring logic lifted out of the LLM domain: every
signal is normalised 0-100 across the current dataset, then combined with
caller-supplied weights. Missing signals simply don't contribute (the weight
renormalises per-row) so an entity isn't punished for a source lacking data.

Entities carrying none of a telescope's `quality_signals` are dampened, so
popularity-only rows can't top a board on volume alone.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """One scoreable dimension of a telescope.

    key      slider name, also the score_breakdown key
    field    the row field it reads
    label    UI label for the slider
    higher   True if bigger numbers are better (False for ranks/prices)
    log      log10-scale before normalising (for heavy-tailed counts)
    """
    key: str
    field: str
    label: str
    higher: bool = True
    log: bool = False


def normalise(values, log=False):
    """Map a list of numbers to 0-100, preserving None."""
    nums = [v for v in values if v is not None]
    if not nums:
        return [None] * len(values)
    if log:
        nums = [math.log10(v + 1) for v in nums]
    lo, hi = min(nums), max(nums)
    span = hi - lo or 1.0

    def scale(v):
        if v is None:
            return None
        x = math.log10(v + 1) if log else v
        return (x - lo) / span * 100.0

    return [scale(v) for v in values]


def score(rows, signals, default_weights, weights=None,
          quality_signals=(), dampen=0.80):
    """Blend `signals` into `rows[i]['score']` and rank them in place.

    signals          iterable of Signal
    default_weights  {signal key: weight}
    weights          caller overrides, merged over the defaults
    quality_signals  keys that count as "real" evidence; a row with none of
                     them present gets its score multiplied by `dampen`
    """
    weights = {**default_weights, **(weights or {})}
    by_key = {s.key: s for s in signals}

    norm_cols = {}
    for s in signals:
        raw = [r.get(s.field) for r in rows]
        col = normalise(raw, log=s.log)
        if not s.higher:
            col = [None if v is None else 100.0 - v for v in col]
        norm_cols[s.key] = col

    quality = set(quality_signals)
    for i, r in enumerate(rows):
        total, wsum = 0.0, 0.0
        contrib = {}
        has_quality = not quality  # no quality set configured -> never dampen
        for key, w in weights.items():
            if w <= 0 or key not in by_key:
                continue
            v = norm_cols[key][i]
            if v is None:
                continue
            if key in quality:
                has_quality = True
            total += v * w
            wsum += w
            contrib[key] = round(v, 1)
        s_val = total / wsum if wsum else None
        if s_val is not None and not has_quality:
            s_val *= dampen
        r["score"] = round(s_val, 1) if s_val is not None else None
        r["score_breakdown"] = contrib

    ranked = sorted(
        rows, key=lambda r: (r["score"] is not None, r["score"] or 0), reverse=True
    )
    for rank, r in enumerate(ranked, start=1):
        r["rank"] = rank
    return ranked
