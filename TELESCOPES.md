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
| **1. Collect** | Fetch raw records from N independent sources, each cached separately | `fetchers.py` — HuggingFace, OpenRouter; `cache.py` TTL disk cache |
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

These tables were written before any telescope past Hubble existed, and for
most of them real implementation diverged from the plan — a paid source
turned out unnecessary, a signal turned out mathematically impossible with
free data, a cross-telescope join fired in the opposite direction from the
one proposed. Rather than pretend the plan was right, each row below is
corrected against what's actually in `observatories/` today, with the
original reasoning kept where it still explains *why* — Reddington's "the
good data is paywalled" is exactly as true now as when it was written, it's
just the *list* of sources that changed underneath it.

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
| **Join key** | None natural — the hardest resolve problem in the fleet. Shipped with a **curated watchlist of ~32 topic queries**; auto-expansion via the UNLISTED panel (trending HN stories matching none of the watchlist) landed as the discovery aid, full embedding-cluster resolution is still open — see `ROADMAP.md`. |
| **Sources (all free, no keys)** — *shipped, not the original plan below* | HN Algolia (story velocity, peak score, two adjacent windows) · Wikipedia pageviews (curiosity) · npm downloads (builder adoption, where a canonical package exists) · OpenAlex (paper velocity, quoted-phrase match — works unauthenticated) · arXiv (preprint velocity, kept independent from OpenAlex, which already ingests arXiv and would double-count if summed) · GitHub search (new-repo velocity, peak stars, 10 req/min unauthenticated). Semantic Scholar was tried and stays unusable (its unauthenticated quota is a small pool shared globally by every unkeyed caller, confirmed by retrying with backoff, not a proxy artifact); Google Trends and Reddit were only ever early-planning ideas below, never attempted; PyPI download stats were tried, also rate-limited, npm shipped instead. |
| **Signals** | HN/Wikipedia/OpenAlex/arXiv/GitHub growth (percent change between two adjacent windows, gated on a minimum combined volume so 1→6 stories can't read as a +500% spike) · raw volume/peak per surface · **cross-source spread** (how many of the six surfaces report the topic at all — Holmdel's version of Hubble's quality dampener) |
| **Events** | `faint_signal` — HN volume jump · `breakout` — Wikipedia curiosity jump · `research_signal` — paper or preprint growth (OpenAlex and arXiv each fire their own, since either source can be down independently of the other) · `repo_signal` — new-repo growth · `crossing_over` — a topic's papers/preprints accelerated last sweep and its repos are now following (research → builders, the highest-value transition, and the reason `telescope/events.py` has a dedicated `CrossoverRule`) |
| **Hard part** | Entity resolution. Shipped with ~32 hand-picked topics; the diff engine has since proven real value on them (real `crossing_over` fires have happened in production), so the case for automating discovery is stronger now than when this was written speculatively. |

---

### 🚛 Reddington — logistics *(operational)*

| | |
|---|---|
| **Entity** | A gauge (an economic series — tonnage, a freight-carrier equity), not the lane / mode / region ("China → US West Coast ocean") originally planned — the lane-level universe below turned out unreachable for free, so the join-key question mooted itself. |
| **Join key** | N/A — a fixed, hand-defined universe of ~12 series, same as the lanes originally planned would have been, just not lanes. |
| **Sources (shipped, not the plan below)** | FRED — truck tonnage, rail carloads, freight TSI (BTS all-mode index), diesel retail & Gulf spot, PPI trucking & general-freight LTL, all free and keyless · Yahoo Finance — BDRY (dry-bulk futures, the closest free proxy for the Baltic Dry Index), ZIM/MATX (ocean carriers), FDX (parcel), XPO (LTL trucking). None of the sources originally scoped below (FBX, Drewry, the Baltic Dry Index itself, EIA, Cass, BTS's own site, port dwell stats, DAT, MarineTraffic) were ever integrated — every one of them is either paywalled or was superseded once FRED/Yahoo proved a real national-index-level instrument was reachable for free, so the "paid-tier decision" this doc used to say Reddington was pending never had to be made. |
| **Signals** | abnormality (z-score vs trailing normal, the same framing Simons uses) · 1-period/3-period move · 5-year range extremity · volatility expansion — level and delta, not the FBX/DAT-style lane rate this section originally specified |
| **Events** | `rate_spike` / `rate_drop` (a gauge crossing ±8% — the fuel/PPI/volume series here move on a different scale than Hubble's `price_drop` it was generalised from) |
| **Hard part** | The best data is paywalled, confirmed rather than assumed: lane-level spot rates (DAT), container indices (FBX/Drewry) and port dwell times all sit behind commercial licences. Free tier gives you national index-level, not lane-level, resolution — enough for a "state of freight" instrument that ranks by abnormality and announces regime shifts, not enough for `congestion_alert`/`capacity_crunch`-style dwell-time events, which were never built for lack of a free dwell/congestion source. |

---

### 🛡 Jackson — defense *(operational)*

| | |
|---|---|
| **Entity** | Two boards in one telescope, as planned: the primary board ranks **prime contractors** by trailing-12m obligations and momentum; the **CAPABILITY AREAS** secondary panel ranks PSC product/service codes the same way `programs/tech areas` below envisioned. |
| **Join key** | Vendors: UEI, the actual government-issued join key, exactly as planned — several UEIs per large prime (e.g. Lockheed) collapsed onto one entity by normalised name. Programs: PSC code, not the hand-defined taxonomy originally scoped — USAspending's own category breakdown turned out to be the real, structured version of "tech area" this doc was reaching for. |
| **Sources (all free & public)** | USAspending.gov (`spending_by_category`/`spending_by_award`: obligations by recipient and by PSC code, the ground truth) · SBIR.gov (recent DoD awards — persistently rate-limited/unavailable in practice, see the caveat) · Kepler (cross-reference, for the UNMAPPED panel below). SAM.gov opportunities is still blocked without a registered API key, confirmed live nearly every iteration (`404` on the public search path); defense.gov contract announcements, DoD comptroller budget justification docs and GAO reports were never attempted. |
| **Signals** | obligations (trailing 12m) · momentum (% vs prior 12m) · award count · avg award size. **Vendor win rate & concentration** was attempted and correctly reverted, not shipped: USAspending's `Award Amount` field is a contract's total lifetime value, not the amount obligated within any requested window, so dividing it by a real 12-month obligations figure produced results like 140%+ concentration — apples to oranges regardless of the ratio, not fixable without the priced-out transaction-level API. See `ROADMAP.md`. |
| **Events** | `new_leader` / `big_climber` (primary board) · `new_prime` · `big_award` / `funding_drop` (±30%, $50M+) · `new_program` / `budget_shift` on the CAPABILITY AREAS panel — a real budget line appearing or an existing one moving ±30%/$50M+, tracked against the last-announced amount so slow drift still eventually crosses the bar |
| **Notes** | Cleanest data in the fleet — all public procurement, keyed, machine-readable. The SBIR-feed-as-Kepler-dealflow-input join below is still blocked: Jackson's own SBIR fetch has 429/403'd essentially every real request this project's whole testing history, worse than "rate-limits hard," so nothing is built on top of it. Kepler feeds Jackson instead (the UNMAPPED panel), the reverse of what this row originally proposed. |

---

### 💰 Simons — capital *(operational)*

| | |
|---|---|
| **Entity** | Markets/regimes on one board (rates, curve, credit, risk, equities, crypto), funds/whales on the WHALE MOVES panel, as planned — plus a third panel not originally scoped: AI CAPEX WATCH, a cross-telescope join with Hubble (see §5). |
| **Join key** | Series ID (markets, via `telescope/series.py`'s shared FRED/Yahoo/CoinGecko adapters) · CIK (funds — SEC-issued, exact), exactly as planned |
| **Sources** | FRED (rates, curve, credit spreads, breakevens, VIX, dollar, oil — Treasury yields included, no separate Treasury API needed) · Yahoo Finance (equity/bond/commodity ETF closes, including SMH for the AI CAPEX panel) · CoinGecko (BTC/ETH) · SEC EDGAR 13F-HR (a curated ~10-filer watchlist, CUSIP-summed within a filing to avoid double-counting a position split across manager rows) · Hubble (cross-reference). SEC Form ADV and ETF flow/AUM proxies were never attempted. |
| **Signals** | abnormality (z-score) · 1m/3m move · 5-year range extremity · vol expansion — "attention", not "level", is the ranking axis, same framing as Reddington above |
| **Events** | `regime_change` (yield curve crossing zero) · `whale_move` (a 13F delta above $10M, first time a qualifying quarterly filing produces one — persisted so an immutable filing never re-announces). `flow_reversal` was never built — no flow-proxy source exists to base it on. |
| **Hard part** | Latency honesty: 13Fs are 45 days delayed. Simons sees *the recent past with great clarity* — it's an allocation-posture instrument, not a trading signal. Said so on the dashboard, in the caveat, and on the WHALE MOVES panel itself. |

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
| **Entity** | A company (a Form D issuer). **Join key: a guessed `.com` domain**, verified against the fetched homepage's own `<title>` — not looked up from a directory, since Form D discloses no website. Fuzzy core-name matching (corporate suffixes stripped) as the fallback when no domain resolves. |
| **Sources** | **SEC EDGAR Form D** (free — every US raise must file within 15 days of first sale, often *before* any press; a real raise with zero public footprint is the stealth raise this telescope exists to detect) · a guessed-and-verified `.com` domain · HN Algolia (domain-precise story matching when a domain resolved, fuzzy title matching otherwise) · Greenhouse/Lever/Ashby public job-board JSON endpoints (hiring velocity — the single most honest traction signal, a guessed slug verified against Greenhouse's own board-info endpoint where available) · Holmdel (cross-reference, for the SECTOR HEAT panel). GitHub star velocity, Product Hunt, the YC directory, app store ranks, certificate-transparency logs, Crunchbase/PitchBook and LinkedIn headcount below were all considered and none were built — the domain+HN+hiring combination proved sufficient without them. |
| **Signals** | raise size & capital-in (log-scaled) · % of round closed · recency (freshness) · public attention (HN points) · tech/deeptech flag (SEC industry code) · hiring velocity — not "team pedigree (enrichment)" or generic "traction velocity", both unbuilt |
| **Weights → sliders** | The default weighting favours raise size and recency over buzz and hiring by design — a stealth company with zero public footprint and no hiring yet is exactly the headline case, not evidence against it, so `buzz`/`hiring` are deliberately excluded from the quality dampener. |
| **Events** | `new_candidate` (a real raise, not yet flagged stealth) · `stealth_raise` (Form D from a company with zero public HN footprint — the headline case) · `hiring_surge` · `stealth_graduated` (a stealth-flagged issuer's first real HN story — did Kepler beat the intro?) — not `breakout_traction`, never built for lack of a traction-velocity source |
| **Noise classifier** | A regex over fund/SPV/real-estate-syndicate language plus SEC's own `industryGroupType`, in the spirit of Hubble's `_is_noise()`: pattern-match and dampen (via excluding from ranking, not deleting the row), don't delete the filing itself. |
| **Hard part** | Entity resolution across sources (a guessed domain + fuzzy core-name matching) — confirmed live to be genuinely risky, not just theoretically: an early real domain guess resolved to an unrelated squatted gambling site on an expired domain, which is why the fetched homepage's own `<title>` verification is load-bearing, not a formality. |

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
- **Cross-telescope joins** are where it gets genuinely interesting, and two
  of the three shipped (the third, Jackson's SBIR feed → Kepler candidate,
  stays blocked on SBIR's own unreliable API, still true today): Kepler's
  stealth Form D feed → Jackson's UNMAPPED panel (the reverse of the
  direction originally proposed here); Holmdel's `crossing_over` topic →
  Kepler's SECTOR HEAT panel; Hubble's `new_leader` → Simons' AI CAPEX WATCH.

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
