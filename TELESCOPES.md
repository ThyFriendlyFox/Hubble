# 🔭 The Observatory — generalising Hubble into a family of telescopes

Hubble is not really an "LLM dashboard." It's an instance of a reusable pattern:

> **A telescope is a machine that watches a noisy landscape through several
> independent public signals, joins them onto one entity identity, blends them
> into a tunable ranking, and — critically — remembers what it saw last time so
> it can announce what *changed*.**

The dashboard is just the eyepiece. The telescope is the pipeline behind it.
This doc distills that pipeline out of the Hubble code, defines the reusable
kernel, and specs the rest of the fleet: **Holmdel** (ideas), **Reddington**
(logistics), **Jackson** (defense), **Simons** (capital), and **Kepler**
(startup discovery / dealflow). All six are now built and operational — this
doc is kept as the design rationale (why the pattern is shaped this way, the
three hard problems, the reasoning behind each telescope's join key and
signal choices), not a todo list. `ROADMAP.md` is the todo list.

---

## 1. Anatomy of a telescope (what Hubble actually is)

Every telescope is the same six-stage pipeline:

| Stage | What it does | Hubble's implementation |
|-------|--------------|-------------------------|
| **1. Collect** | Fetch raw records from N independent sources, each cached separately | `fetchers.py` — HuggingFace, OpenRouter, LMArena; `cache.py` TTL disk cache |
| **2. Resolve** | Join sources onto one entity identity | `build_unified()` — keyed on HF repo id, falling back to OpenRouter slug |
| **3. Classify** | Filter noise so junk doesn't pollute the board | `_is_noise()` regex (quant repacks, embeddings, test stubs…) |
| **4. Rank** | Normalise every signal 0–100 across the dataset, blend with user-tunable weights, renormalise around missing signals, dampen entities with no quality signal | `ranking.py` |
| **5. Remember & diff** | Snapshot each sweep, diff against the last, emit typed **events** with ready-to-post headlines | `snapshots.py` — `new_leader`, `new_model`, `big_climber`, `price_drop` |
| **6. Announce** | Fan events out to channels: dashboard feed, `/api/whats-new`, X, Discord | `notifier.py` + background poller in `app.py` |

Two design decisions make the pattern strong, and both must survive every port:

1. **Multiple independent signal families, never one.** Hubble crosses *usage*
   (OpenRouter rank), *quality* (benchmarks/arena), *popularity* (downloads/
   likes), and *cost*. Any single source can be gamed, stale, or broken; the
   blend degrades gracefully (missing signals renormalise instead of zeroing).
2. **The diff engine is the product.** A leaderboard tells you the state of the
   world; the events feed tells you *what just happened* — which is the thing
   people actually check dashboards for. "🔭 New intelligence detected" is the
   feature. Every telescope must keep history and speak in events.

## 2. What's already generic vs. domain-specific

The codebase is ~80% domain-agnostic today:

| File | Verdict |
|------|---------|
| `cache.py` | **Generic.** Nothing LLM about it. |
| `ranking.py` | **Generic** once `SIGNALS` / `DEFAULT_WEIGHTS` become config. The normalise-blend-renormalise-dampen logic ports untouched. |
| `snapshots.py` | **Generic** except the four event rules and their headline copy. |
| `notifier.py` | **Generic.** Channels + `SOCIAL_TYPES` filter are config. |
| `app.py` | **Generic.** Routes, weight parsing, poller — all reusable. |
| `static/`, `templates/` | **Generic** except column definitions, the wordmark, and the HARDWARE FIT tab (Hubble-specific view). |
| `fetchers.py` | **Domain-specific.** This is the objective lens — the only part you regrind per telescope. |

So the refactor is small: extract a `telescope/` kernel package, and define
each telescope as a **domain pack**:

```
telescope/              # the kernel (cache, ranking, snapshots, notifier, server)
observatories/
  hubble/               # first domain pack (current fetchers.py + config)
    sources.py          #   fetch_* functions + join + noise classifier
    config.py           #   SIGNALS, DEFAULT_WEIGHTS, event rules, columns, branding
  kepler/
  jackson/
  ...
```

A domain pack supplies exactly five things:

1. **Fetchers** — `fetch_*() -> list[dict]`, one per source, independently cached.
2. **A join** — how sources map onto one entity key (the hard part, see §4).
3. **A signal schema** — `SIGNALS` (field, direction) + `DEFAULT_WEIGHTS`,
   which auto-generate the sliders and score breakdown.
4. **Event rules** — thresholds + headline templates for the diff engine
   (the generic shapes: *new #1*, *new entrant above rank N*, *big mover*,
   *threshold crossed on field X by Y%*).
5. **Presentation** — table columns, wordmark, tagline, custom tabs.

Everything else — polling, caching, scoring, snapshot/diff, the feed, the
notifier, the API — is inherited.

## 3. The fleet

### 🔭 Hubble — AI *(operational)*

The reference implementation. Entities: models. Sources: HuggingFace,
OpenRouter, benchmarks, arena. Already documented in `README.md`.

---

### 📡 Holmdel — ideas *(operational)*

*The Holmdel horn antenna picked up an annoying background hiss that turned out
to be the cosmic microwave background — the biggest discovery hiding in the
noise. Exactly the mission: detect faint idea-signals before they're obvious.*

| | |
|---|---|
| **Entity** | An idea / topic / technique (e.g. "state-space models", "electric last-mile", "GLP-1 for X") |
| **Join key** | None natural — the hardest resolve problem in the fleet. Start with a **curated watchlist of topic queries** that auto-expands (new arXiv keywords, new GH topics), graduate to embedding-cluster matching later. |
| **Sources (all free)** | arXiv API (submission velocity per topic) · Semantic Scholar API (citation acceleration) · HN Algolia API (mention velocity, points) · GitHub search (new repos per topic, star velocity) · PyPI/npm download stats (builder adoption) · Wikipedia pageviews API · Google Trends via pytrends (unofficial) · Reddit API |
| **Signals** | mention velocity · citation acceleration · builder adoption · search interest · **cross-source spread** (how many independent surfaces an idea appears on — Holmdel's version of Hubble's quality dampener: an idea seen in only one place gets dampened) |
| **Events** | `faint_signal` — first time a topic clears noise floor on ≥2 sources · `breakout` — big climber · `crossing_over` — moved from papers to repos (research → builders, the highest-value transition) |
| **Hard part** | Entity resolution. Ship v1 with ~50 hand-picked topics and let the diff engine prove value before automating discovery. |

---

### 🚛 Reddington — logistics *(operational)*

| | |
|---|---|
| **Entity** | A lane / mode / region (e.g. "China → US West Coast ocean", "US dry-van truckload spot", "transatlantic air") |
| **Join key** | Easy — lanes are a fixed, hand-defined universe. |
| **Sources** | Freightos Baltic Index (FBX, daily, public page) · Drewry World Container Index (weekly, public) · Baltic Dry Index · EIA diesel & jet fuel (free API) · Cass Freight Index (monthly, public) · BTS freight data · port-published dwell/throughput stats. **Tier-2 (paid, if it earns it):** DAT spot rates, MarineTraffic congestion. |
| **Signals** | spot rate level · rate 7/30-day delta · fuel cost · congestion/dwell · demand indices |
| **Events** | `rate_spike` / `rate_drop` (the `price_drop` rule, generalised to ±) · `congestion_alert` · `capacity_crunch` (rate up + dwell up simultaneously) |
| **Hard part** | The best data is paywalled. Free tier gives you index-level (not lane-level) resolution — still enough for a "state of freight" telescope that announces regime shifts. |

---

### 🛡 Jackson — defense *(operational)*

| | |
|---|---|
| **Entity** | Two boards in one telescope: **programs/tech areas** (hypersonics, counter-UAS, space domain awareness…) and **vendors** |
| **Join key** | Programs: hand-defined taxonomy mapped to solicitation keywords + budget line items. Vendors: DUNS/UEI — an actual government-issued join key. |
| **Sources (all free & public)** | USAspending.gov API (contract obligations, the ground truth) · SAM.gov opportunities API (solicitations = leading indicator) · defense.gov daily contract announcements (>$7.5M awards) · SBIR/STTR awards API (early-stage tech signal) · DoD comptroller budget justification docs · GAO reports |
| **Signals** | obligation velocity per program · new-solicitation rate per tech area · SBIR award clustering (where DoD is seeding startups) · vendor win rate & concentration |
| **Events** | `big_award` · `new_program` (budget line appears) · `budget_shift` (program's obligations ±X% YoY) · `rising_vendor` (the `big_climber` rule on the vendor board) |
| **Notes** | Cleanest data in the fleet — all public procurement, keyed, machine-readable. **Bonus:** the SBIR feed is also a dealflow input for Kepler (below). |

---

### 💰 Simons — capital *(operational)*

| | |
|---|---|
| **Entity** | Markets/regimes on one board (rates, credit, equities factors, crypto, commodities), funds/whales on another |
| **Join key** | Tickers/series IDs (markets) · CIK numbers (funds — SEC-issued, exact) |
| **Sources** | FRED API (free — the macro backbone) · Treasury yield API · SEC EDGAR 13F filings (whale position deltas, free) · SEC Form ADV · CoinGecko (free tier) · ETF flow proxies via volume/AUM |
| **Signals** | regime indicators (curve shape, credit spreads, real rates) · 13F position deltas · flows · realised vol |
| **Events** | `regime_change` (indicator crosses a defined boundary) · `whale_move` (13F delta above threshold) · `flow_reversal` |
| **Hard part** | Latency honesty: 13Fs are 45 days delayed. Simons sees *the recent past with great clarity* — it's an allocation-posture instrument, not a trading signal. Say so on the dashboard. |

---

### 🪐 Kepler — startup discovery *(operational)*

*Kepler never saw a planet. It watched 150,000 stars for tiny periodic dips in
brightness and found thousands of worlds from the perturbations they caused.
That is precisely how you find startups before they announce: not from press,
but from the disturbances they make — a Form D filing, a hiring spike, a repo
suddenly accelerating.*

This is the deliberate complement to Boardy: Boardy is warm-intro,
network-inward; Kepler is signal-detection, outside-in. The overlap report
("Kepler flagged it 3 weeks before the intro") is itself a signal-quality feedback loop.

| | |
|---|---|
| **Entity** | A company. **Join key: the domain name** — the best natural key in the fleet after government IDs. Fuzzy company-name matching as fallback. |
| **Sources** | **SEC EDGAR Form D** (free — every US raise must file within 15 days of first sale, often *before* any press; new filer + no press = stealth raise detected) · Greenhouse/Lever/Ashby public job-board JSON endpoints (hiring velocity — the single most honest traction signal) · GitHub org star/contributor velocity · HN Show HN + launch threads · Product Hunt API · YC directory · app store ranks · new-domain certificate-transparency logs (speculative, noisy). **Tier-2 (paid):** Crunchbase/PitchBook for backfill, LinkedIn headcount. |
| **Signals** | hiring velocity · funding recency & size (Form D) · traction velocity (stars/upvotes/ranks) · team pedigree (enrichment) · cross-source spread |
| **Weights → sliders** | The Hubble sliders become an investment-thesis dial: crank *hiring* to find quiet compounders, crank *traction* for breakouts, filter by sector/stage the way Hubble filters by params/quant. |
| **Events** | `new_candidate` (entity clears noise floor on ≥2 sources) · `stealth_raise` (Form D from a company with no other footprint) · `hiring_surge` · `breakout_traction` |
| **Noise classifier** | Direct port of Hubble's `_is_noise()` concept: agencies, consultancies, crypto spam, side projects — pattern-match and dampen, don't delete. |
| **Hard part** | Entity resolution across sources (domain key + fuzzy names) and launch-day noise. Both are problems Hubble already solves in miniature. |

---

## 4. The three hard problems (ranked)

Porting a telescope is easy or hard in direct proportion to these, so check
them before starting any domain:

1. **The join key.** Hubble got lucky — OpenRouter ships `hugging_face_id`.
   Fleet ranking, easiest → hardest: Jackson (UEI) ≈ Simons (CIK/tickers) >
   Reddington (fixed lanes) > Kepler (domains + fuzz) ≫ Holmdel (no key; needs
   clustering or curation).
2. **Source access.** Hubble/Jackson/Kepler/Holmdel run entirely on free public
   APIs. Simons is free but latency-honest. Reddington is the only one where
   the good stuff is paywalled.
3. **Signal independence.** The blend only means something if the sources can
   disagree. Two sites republishing the same index = one signal, not two.

## 5. Shared infrastructure: the Observatory

Once ≥2 telescopes exist, add the thin meta-layer:

- **One event schema** (already implicit in `snapshots._ev`): `type`, `key`,
  `name`, `rank`, `score`, `ts`, `headline`, plus a new `telescope` field.
- **A merged feed** — one page/API (`/api/observatory/whats-new`) interleaving
  every telescope's events; the morning read.
- **One notifier config** — channels and `SOCIAL_TYPES` per telescope, one
  dispatch implementation.
- **Cross-telescope joins** are where it gets genuinely interesting:
  Jackson SBIR award → Kepler candidate; Holmdel `crossing_over` topic →
  Kepler sector filter; Hubble `new_leader` → Simons AI-capex watch.

## 6. Build order (as it actually happened)

Kept as a record, not advice — there's nothing left in this list to build.
Per the commit that shipped them, the actual order was: kernel extraction
first (Hubble became the first domain pack and proved the interface), then
Jackson, Simons and Kepler together in that same commit, then Holmdel and
Reddington last, sharing `telescope/series.py`'s time-series analytics.
Notably not the order originally recommended below — Kepler shipped third,
not first, and Holmdel (the hardest join-key problem) landed last as
recommended, but alongside Reddington rather than on its own. Each domain
pack after the kernel extraction cost roughly what the original `fetchers.py`
cost — a few hundred lines of source adapters plus a config block, matching
what §1 predicted.

The original recommendation, for reference:

1. **Extract the kernel** (small refactor, ~a day): move `cache/ranking/
   snapshots/notifier/app` into `telescope/`, parameterise `SIGNALS`,
   `DEFAULT_WEIGHTS`, event rules, and columns. Hubble becomes the first
   domain pack and proves the interface.
2. **Kepler** — highest direct ROI (dealflow), tractable join key, all-free
   sources, and it exercises the kernel with a non-trivial noise classifier.
3. **Jackson** — cleanest data in the fleet; fast win; feeds Kepler.
4. **Holmdel** — start with a curated 50-topic watchlist; hardest resolve
   problem, so let the kernel stabilise first.
5. **Simons**, then **Reddington** — Simons once the regime-indicator set is
   chosen; Reddington last, pending a paid-data decision.
