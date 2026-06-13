"""Blend the per-source signals into a single 0-100 'best overall' score.

Every signal is normalised to 0-100 across the current dataset, then combined
with caller-supplied weights.  Missing signals simply don't contribute (and the
weight is renormalised per-row) so a model isn't punished for, say, lacking an
arena score.
"""
import math

# Signal -> (accessor, higher_is_better)
SIGNALS = {
    "intelligence": ("intelligence_index", True),
    "coding": ("coding_index", True),
    "agentic": ("agentic_index", True),
    "arena": ("arena_elo", True),
    "usage": ("usage_rank", False),      # rank 1 == best
    "downloads": ("downloads", True),
    "likes": ("likes", True),
    "price": ("price_completion", False),  # cheaper == better
}

DEFAULT_WEIGHTS = {
    "intelligence": 30,
    "coding": 15,
    "agentic": 10,
    "arena": 10,
    "usage": 20,
    "downloads": 5,
    "likes": 5,
    "price": 5,
}


def _normalise(values, log=False):
    """Map a list of numbers to 0-100. None stays None. Optional log scaling
    for heavy-tailed signals (downloads)."""
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


def score(models, weights=None):
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    # Pre-compute normalised columns for each signal.
    norm_cols = {}
    for sig, (field, higher) in SIGNALS.items():
        raw = [m.get(field) for m in models]
        col = _normalise(raw, log=(sig == "downloads"))
        if not higher:
            col = [None if v is None else 100.0 - v for v in col]
        norm_cols[sig] = col

    quality_signals = ("intelligence", "coding", "agentic", "arena")
    for i, m in enumerate(models):
        total, wsum = 0.0, 0.0
        contrib = {}
        has_quality = False
        for sig, w in weights.items():
            if w <= 0:
                continue
            v = norm_cols.get(sig, [None] * len(models))[i]
            if v is None:
                continue
            if sig in quality_signals:
                has_quality = True
            total += v * w
            wsum += w
            contrib[sig] = round(v, 1)
        s = total / wsum if wsum else None
        # Dampen models with no benchmark/arena signal at all (e.g. usage-only
        # router pseudo-models) so they don't top a "best LLMs" board on
        # popularity alone. They still appear, just ranked more honestly.
        if s is not None and not has_quality:
            s *= 0.80
        m["score"] = round(s, 1) if s is not None else None
        m["score_breakdown"] = contrib

    ranked = sorted(
        models, key=lambda m: (m["score"] is not None, m["score"] or 0), reverse=True
    )
    for rank, m in enumerate(ranked, start=1):
        m["rank"] = rank
    return ranked
