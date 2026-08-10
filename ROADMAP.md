# 🔭 Observatory — feature roadmap

_Generated from `roadmap.py`, which also backs the dashboard's ROADMAP tab._

## PHASE 0 · THE KERNEL  ·  `shipped`  (7/7)

> Hubble's pipeline extracted into a domain-agnostic core. Adding a telescope now costs a source adapter plus a config block, not a rewrite.

- [x] **telescope/ kernel package** — cache, http, ranking, events, snapshots, notifier, registry
- [x] **Declarative signals** — each telescope's sliders and score breakdown generate themselves
- [x] **Declarative event rules** — NewLeader / NewEntrant / Climber / Delta / Threshold shapes
- [x] **Per-telescope toggles** — persisted to data/observatory.json; disabled scopes never fetch or poll
- [x] **Generic dashboard** — table, podium and sliders rendered from telescope metadata
- [x] **Merged observatory feed** — one newest-first stream across every enabled telescope
- [x] **Per-telescope poll cadence** — Hubble 6h, Jackson 24h — slow data isn't swept hourly

## PHASE 1 · THE FIRST FOUR INSTRUMENTS  ·  `shipped`  (5/5)

> All four run on live public data with no API keys.

- [x] **🔭 Hubble · AI** — HuggingFace + OpenRouter + Artificial Analysis + arena
- [x] **🛡 Jackson · Defense** — USAspending obligations, 12m vs prior 12m, + PSC capability panel
- [x] **💰 Simons · Capital** — FRED + Yahoo + CoinGecko, ranked by abnormality not opinion
- [x] **🪐 Kepler · Startups** — SEC Form D raise detection + HN attention + stealth flagging
- [x] **Live smoke tests** — tests/ hits every real source and asserts on shape, not fixtures

## PHASE 2 · THE REMAINING INSTRUMENTS  ·  `shipped`  (4/4)

> All six telescopes now run. Holmdel shipped without a research source: every free scholarly API is unusable from this deployment, so it reads attention and adoption and says so rather than faking scholarship.

- [x] **📡 Holmdel · Ideas** — HN + Wikipedia + npm velocity over a 32-topic curated watchlist
- [x] **🚛 Reddington · Logistics** — FRED freight volume/cost + freight-sector equities, index level
- [x] **Shared series kernel** — telescope/series.py — Simons and Reddington share adapters and analytics
- [x] **Small-denominator gate** — Holmdel reports no growth below 12 stories rather than a loud +500%

## PHASE 2B · UNBLOCKING HOLMDEL  ·  `next`  (2/6)

> Holmdel's research blindness is an access problem, not a design one. Each item below restores a source that exists but is unreachable from this deployment.

- [x] **OpenAlex research velocity** — no key needed — the prior 429s were the sandboxed deployment's shared egress IP, not OpenAlex itself; restores paper_growth and paper_recent to Holmdel on the same quoted-phrase, two-window pattern as its HN signal
- [ ] **arXiv via OAI-PMH** — arXiv's query API works fine unauthenticated with normal pacing (~1 req/3s) — bulk OAI-PMH harvest is still worth it as a second scholarly surface, not a fix for a block
- [x] **GitHub repo velocity** — new-repo creation velocity + peak stars, 180-day window vs prior, on the same quoted-phrase pattern as HN/OpenAlex. The real limit turned out stricter than first read: GitHub's *search* endpoint caps at 10 req/min unauthenticated, not the ~60/hour of its other APIs — a full 32-topic sweep now takes several minutes, paced accordingly. Matches repo name and description only, not READMEs
- [ ] **Holmdel crossover event** — 'research → builders' — papers source now exists; needs a new declarative rule shape in telescope/events.py that compares a leading signal in the prior snapshot against a lagging one in the current snapshot, not just a same-snapshot delta
- [ ] **Topic auto-discovery** — graduate from curated watchlist to embedding-cluster resolution
- [ ] **Semantic Scholar key** — still blocked without one — its unauthenticated quota is a small pool shared globally by every unkeyed caller, confirmed not a proxy artifact by retrying locally with backoff

## PHASE 3 · MAKING THE SIGNAL SHARPER  ·  `next`  (2/8)

> Everything here improves telescopes that already exist.

- [ ] **Kepler entity resolution** — resolve issuers to domains; replaces fuzzy HN name matching
- [ ] **Kepler hiring signal** — Greenhouse/Lever/Ashby board endpoints — the honest traction metric
- [ ] **Jackson solicitations** — SAM.gov opportunities as a leading indicator ahead of obligations
- [ ] **Jackson tech-area board** — promote the PSC panel into a rankable second board
- [x] **Jackson · unmapped defense startups** — cross-telescope join, not a new source — Jackson's obligations board can only see companies that already hold a DoD contract; a new panel reads Kepler's already-cached Form D feed (never forces it to refresh) and keeps filings whose name or industry reads defense/dual-use. Keyword-on-name only, so it misses deliberately-named stealth companies (Anduril doesn't say 'defense' anywhere) and is often empty — obvious-by-name defense filers are rare in any 12-day window. Needed a kernel change: telescope.panels() so a telescope can return more than one secondary board (Jackson now has two)
- [ ] **Simons 13F whale tracking** — EDGAR 13F position deltas; 45-day lag stated on the row
- [ ] **Backfill history** — seed snapshots from historical data so events fire on day one
- [x] **Per-signal explanations** — hover a score to see raw value, normalised value, weight and point contribution per signal, plus why a row was dampened if it was — generic across all six telescopes

## PHASE 4 · THE OBSERVATORY LAYER  ·  `later`  (0/6)

> Where a fleet beats a collection: cross-telescope joins.

- [ ] **Cross-telescope joins** — Jackson SBIR award → Kepler candidate; Holmdel crossover → Kepler sector
- [ ] **Saved views / theses** — name a slider configuration and return to it
- [ ] **Watchlists + alerts** — per-entity subscriptions rather than board-level events
- [ ] **Morning brief** — one digest across every telescope, pushed on a schedule
- [ ] **Channels** — Discord and Slack are wired; X needs credentials
- [ ] **Signal-quality feedback** — track which detections proved out — did Kepler beat the intro?

