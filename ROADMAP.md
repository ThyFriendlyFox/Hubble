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

## PHASE 2 · THE REMAINING INSTRUMENTS  ·  `building`  (0/4)

> Holmdel is the hardest in the fleet: ideas have no natural join key, so it ships with a curated watchlist first.

- [ ] **📡 Holmdel · Ideas** — arXiv + HN + GitHub + Wikipedia velocity over a curated topic list
- [ ] **🚛 Reddington · Logistics** — freight indices + EIA fuel + port throughput; free tier is index-level
- [ ] **Holmdel crossover event** — 'research → builders' — the highest-value transition to detect
- [ ] **Topic auto-discovery** — graduate Holmdel from curated watchlist to embedding-cluster resolution

## PHASE 3 · MAKING THE SIGNAL SHARPER  ·  `next`  (0/7)

> Everything here improves telescopes that already exist.

- [ ] **Kepler entity resolution** — resolve issuers to domains; replaces fuzzy HN name matching
- [ ] **Kepler hiring signal** — Greenhouse/Lever/Ashby board endpoints — the honest traction metric
- [ ] **Jackson solicitations** — SAM.gov opportunities as a leading indicator ahead of obligations
- [ ] **Jackson tech-area board** — promote the PSC panel into a rankable second board
- [ ] **Simons 13F whale tracking** — EDGAR 13F position deltas; 45-day lag stated on the row
- [ ] **Backfill history** — seed snapshots from historical data so events fire on day one
- [ ] **Per-signal explanations** — click a score to see exactly which signals produced it

## PHASE 4 · THE OBSERVATORY LAYER  ·  `later`  (0/6)

> Where a fleet beats a collection: cross-telescope joins.

- [ ] **Cross-telescope joins** — Jackson SBIR award → Kepler candidate; Holmdel crossover → Kepler sector
- [ ] **Saved views / theses** — name a slider configuration and return to it
- [ ] **Watchlists + alerts** — per-entity subscriptions rather than board-level events
- [ ] **Morning brief** — one digest across every telescope, pushed on a schedule
- [ ] **Channels** — Discord and Slack are wired; X needs credentials
- [ ] **Signal-quality feedback** — track which detections proved out — did Kepler beat the intro?

