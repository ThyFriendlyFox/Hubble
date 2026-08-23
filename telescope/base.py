"""The Telescope base class — everything a domain pack inherits.

A domain pack subclasses `Telescope` and supplies five things:

  1. identity      slug / name / domain / tagline / entity noun
  2. `collect()`   the fetchers + the join, returning one dict per entity
  3. `signals`     what's scoreable, which auto-generates the sliders
  4. `rules`       the event rules for the diff engine
  5. `columns`     what the dashboard table shows

Everything else — caching, scoring, snapshots, diffing, the feed, the API —
is inherited from the kernel.
"""
import time
import traceback
from dataclasses import dataclass

from telescope import ranking
from telescope.cache import Cache
from telescope.snapshots import SnapshotStore


@dataclass(frozen=True)
class Column:
    """One dashboard table column."""
    field: str
    label: str
    fmt: str = "num"   # num | int | score | rank | price | pct | text | date | money
    width: str = ""


class Telescope:
    # ── identity ─────────────────────────────────────────────────────────
    slug = "telescope"
    name = "TELESCOPE"
    domain = "GENERIC"
    tagline = ""
    entity_label = "ENTITIES"
    glyph = "🔭"
    sources_label = ""
    # A short, honest statement of what this instrument can and can't see.
    caveat = ""

    # ── scoring ──────────────────────────────────────────────────────────
    signals = ()
    default_weights = {}
    quality_signals = ()
    dampen = 0.80

    # ── presentation ─────────────────────────────────────────────────────
    columns = ()

    # ── change detection ─────────────────────────────────────────────────
    rules = ()
    snapshot_fields = ()

    # ── behaviour ────────────────────────────────────────────────────────
    cache_ttl = 3600
    # How often the background poller sweeps this telescope. Domains with slow
    # underlying data (quarterly filings) don't need hourly sweeps.
    poll_seconds = 6 * 3600

    def __init__(self):
        self.cache = Cache(self.slug)
        self.store = SnapshotStore(
            self.slug, rules=self.rules, extra_fields=self.snapshot_fields
        )
        self._last_error = None
        self._last_sweep = None

    # ── to implement ─────────────────────────────────────────────────────
    def collect(self, force=False):
        """Fetch every source and join them into one row per entity.

        Each row should carry at minimum: key, name, link, sources (list),
        noise (bool), plus whatever signal fields this telescope scores.
        """
        raise NotImplementedError

    def source_keys(self):
        """Cache keys whose age is shown in the freshness readout."""
        return []

    def historical_rows(self, rows):
        """Optional: reconstruct a real one-period-ago version of `rows` from
        fields this telescope already fetched about its own recent past (a
        prior-window value it already computes for growth, an earlier point
        already sitting in a cached time series) — never fabricated, only
        reused. When this returns something, the very first sweep this
        telescope ever runs can diff against a genuine baseline and announce
        real events immediately, instead of announcing nothing until a
        second live sweep happens, which for a 24h poll cadence can be a
        full day away. Returning None (the default) is always the honest
        fallback for telescopes with no real prior-period data to reuse —
        Kepler's Form D filings are discrete point-in-time events with
        nothing resembling "the recent past" to reconstruct.
        """
        return None

    def context(self, force=False):
        """Optional secondary panel(s) for domains where the ranked board
        isn't the whole story — tech-area spend for Jackson, the yield curve
        for Simons. Returns a single {"title", "columns", "rows"} dict, a
        list of them for telescopes with more than one, or None. May raise —
        `panels()` is what callers use, and it never does.
        """
        return None

    def panels(self, force=False):
        """context(), normalised to a list and safe to call — a broken panel
        shouldn't break the view."""
        try:
            raw = self.context(force=force)
        except Exception:
            traceback.print_exc()
            return []
        if not raw:
            return []
        return raw if isinstance(raw, list) else [raw]

    # ── inherited machinery ──────────────────────────────────────────────
    def ttl(self, force):
        return 0 if force else self.cache_ttl

    def rank(self, rows, weights=None):
        return ranking.score(
            rows,
            self.signals,
            self.default_weights,
            weights,
            quality_signals=self.quality_signals,
            dampen=self.dampen,
        )

    def view(self, weights=None, force=False):
        """The full payload the dashboard and API render from."""
        rows = self.collect(force=force)
        ranked = self.rank(rows, weights)
        panels = self.panels(force=force)
        self._last_error = None
        return {
            "telescope": self.meta(),
            "rows": ranked,
            "count": len(ranked),
            "panels": panels,
            "weights": {**self.default_weights, **(weights or {})},
            "ages": {k: self._age(k) for k in self.source_keys()},
        }

    def sweep(self, notifier=None):
        """Force-fetch, score with DEFAULT weights, detect changes, dispatch."""
        rows = self.collect(force=True)
        ranked = self.rank(rows)
        self._maybe_backfill(ranked)
        events = self.store.record(ranked)
        self._last_sweep = time.time()
        if notifier and events:
            notifier.dispatch(events, self)
        return events

    def _maybe_backfill(self, ranked):
        """On this telescope's very first-ever sweep, seed a real (not
        fabricated) baseline snapshot from historical_rows() so this same
        sweep can diff against something instead of the diff engine
        returning nothing until a second live sweep happens."""
        if self.store.latest() is not None:
            return
        backfill = self.historical_rows(ranked)
        if not backfill:
            return
        seeded = self.rank([dict(r) for r in backfill])
        # Dated one poll cycle back so downstream freshness math (anything
        # reading snapshot timestamps) stays coherent with what "the last
        # reading" actually means for this telescope's own cadence.
        self.store.seed(seeded, time.time() - self.poll_seconds)

    def safe_sweep(self, notifier=None):
        """sweep() that records the failure instead of propagating it.

        Clears _last_error on success, not just sets it on failure — without
        that, a telescope that failed once and then recovered would keep
        reporting the stale error via meta() forever, since nothing else on
        this path ever resets it. The poller relies on the same signal to
        decide whether this sweep actually happened, so an unrelated success
        must not be allowed to look like a failure just because a stale
        _last_error from long ago was never cleared.
        """
        try:
            events = self.sweep(notifier)
            self._last_error = None
            return events
        except Exception as e:
            self._last_error = str(e)
            traceback.print_exc()
            return []

    def _age(self, key):
        a = self.cache.age(key)
        return round(a) if a is not None else None

    def meta(self):
        return {
            "slug": self.slug,
            "name": self.name,
            "domain": self.domain,
            "tagline": self.tagline,
            "entity_label": self.entity_label,
            "glyph": self.glyph,
            "sources_label": self.sources_label,
            "caveat": self.caveat,
            "columns": [vars(c) for c in self.columns],
            "signals": [
                {"key": s.key, "label": s.label} for s in self.signals
            ],
            "default_weights": self.default_weights,
            "quality_signals": list(self.quality_signals),
            "dampen": self.dampen,
            "poll_seconds": self.poll_seconds,
            "snapshots": self.store.count(),
            "last_error": self._last_error,
            "last_sweep": self._last_sweep,
        }
