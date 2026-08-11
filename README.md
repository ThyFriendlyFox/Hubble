# 🔭 The Observatory

A dashboard of **telescopes** — instruments that each watch one noisy landscape
through several independent public sources, join them onto a single entity
identity, blend them into a tunable ranking, and remember what they saw last
time so they can announce **what changed**.

Hubble (the LLM index) was the first one. It turned out the interesting part
wasn't the LLM data — it was the *shape*, so the shape got extracted into a
kernel and pointed at other domains.

```bash
pip install -r requirements.txt
OBSERVATORY_ENABLED=all python app.py
# open http://127.0.0.1:5000
```

No API keys. Every source below is public.

## The instruments

| | Telescope | Domain | Entity | Sources |
|---|---|---|---|---|
| 🔭 | **Hubble** | AI | models | HuggingFace · OpenRouter · Artificial Analysis · Design Arena (both embedded in OpenRouter) |
| 🛡 | **Jackson** | Defense | primes | USAspending obligations · PSC capability areas · SBIR · Kepler (cross-reference) |
| 💰 | **Simons** | Capital | indicators | FRED · Yahoo Finance · CoinGecko · SEC 13F-HR · Hubble (cross-reference) |
| 🪐 | **Kepler** | Startups | issuers | SEC Form D · EDGAR · Hacker News · Greenhouse/Lever/Ashby · Holmdel (cross-reference) |
| 📡 | **Holmdel** | Ideas | topics | Hacker News · Wikipedia pageviews · npm · OpenAlex · arXiv · GitHub |
| 🚛 | **Reddington** | Logistics | gauges | FRED freight series · freight-sector equities |

Each is **independently toggleable** from the TELESCOPES tab. A disabled
telescope is never fetched and never polled, so you can run just Hubble on a
laptop or light the whole observatory on a server. Toggle state persists to
`data/observatory.json`; `OBSERVATORY_ENABLED` seeds the defaults on first run.

### What each one is actually for

- **Hubble** — "which model should I use right now", crossing real usage
  against benchmarks, arena Elo, popularity and price.
- **Jackson** — where defense money is *moving*, not just who is big. Ranks
  primes on trailing-12-month obligations against the prior 12 months, with a
  capability-area panel showing which mission areas are growing.
- **Simons** — deliberately **not** a levels dashboard. It ranks indicators by
  how far each is reading from its own trailing normal (z-score, range
  extremity, vol expansion), so the board answers "what should I look at today"
  and the feed announces regime crossings. A secondary **WHALE MOVES** panel
  tracks quarter-over-quarter 13F position deltas across a curated list of
  large filers — positioning from months ago (13F-HR is due 45 days after
  quarter end), not now, and the caveat says so. A qualifying delta also
  fires a real feed event the first time a new filing produces one, not
  just a passive panel entry. A second panel, **AI CAPEX
  WATCH**, pairs Hubble's new-leader events with how SMH (semiconductors)
  moved since — a loose observational correlation, not a signal.
- **Holmdel** — idea velocity across a curated watchlist, ranked on
  acceleration rather than volume, with a cross-source **spread** signal
  (one surface is a rumour, three is a trend) across six independent
  surfaces, including real research velocity from OpenAlex and arXiv.
  Semantic Scholar stays unusable (its unauthenticated quota is a small
  pool shared globally, not a proxy artifact) and Crossref's API is an OR
  match whose counts aren't real topic counts, so neither is used. A
  `crossing_over` event fires when a topic's papers accelerate first and
  its repos follow — research becoming builders.
- **Reddington** — freight at the resolution the free tier honestly supports:
  national index level, not lane level. Volume (tonnage, carloads) against cost
  (diesel, freight PPI), plus the market's own read via carrier equities.
- **Kepler** — startup discovery, outside-in. Under Reg D essentially every US
  private raise must file a **Form D** within 15 days of first sale, with the
  offering size and amount sold. It's public, structured, and usually lands
  before any press. A real raise with no public footprint fires a distinct
  `STEALTH` event at detection — the headline case this telescope exists
  for — and a `GRADUATED` event fires the moment it gets its first real
  public attention. Hiring velocity (open roles on Greenhouse, Lever or
  Ashby, guessed from the company name) is layered on as the single most
  honest traction signal available for free. This is the complement to
  warm-intro dealflow: it surfaces what nobody has introduced you to.

## How a telescope works

Six stages, all but one inherited from the kernel:

| Stage | Where |
|---|---|
| **Collect** — fetch N independent sources, cached separately | domain pack `collect()` |
| **Resolve** — join sources onto one entity key | domain pack |
| **Classify** — flag noise so junk doesn't pollute the board | domain pack |
| **Rank** — normalise each signal 0-100, blend by weight, renormalise around missing signals, dampen entities lacking quality evidence | `telescope/ranking.py` |
| **Remember & diff** — snapshot each sweep, diff, emit typed events | `telescope/snapshots.py` + `events.py` |
| **Announce** — dashboard feed, merged API, Discord/Slack/X | `telescope/notifier.py` |

Two properties are load-bearing and every telescope keeps them:

1. **Multiple independent signal families.** Any single source can be gamed,
   stale or broken. Missing signals renormalise rather than scoring zero, so a
   partial outage degrades the blend instead of destroying it.
2. **The diff engine is the product.** A leaderboard tells you the state of the
   world; the event feed tells you what just happened.

## Adding a telescope

Subclass `Telescope` in `observatories/` and declare five things — identity,
`collect()`, `signals`, `rules`, `columns`. Everything else is inherited: the
sliders, the table, the podium, caching, snapshots, the diff engine, the feed,
the API, the poller.

```python
@register
class Jackson(Telescope):
    slug, name, domain = "jackson", "JACKSON", "DEFENSE"
    signals = (Signal("obligations", "obligations_12m", "OBLIGATIONS", log=True),
               Signal("growth", "growth_pct", "MOMENTUM"))
    default_weights = {"obligations": 35, "growth": 35}
    quality_signals = ("growth",)          # no prior-period history -> dampened
    columns = (Column("name", "PRIME", "text"), ...)
    rules = (NewLeaderRule(headline="🛡 New top prime — {name} ..."), ...)

    def collect(self, force=False): ...    # the only real work
```

Event rules are declarative shapes: `NewLeaderRule`, `NewEntrantRule`,
`ClimberRule`, `DeltaRule` (any field, either direction, with a formatter) and
`ThresholdRule` (crossing a fixed boundary). Headlines are format templates
rendered against the row.

## Honesty by construction

Every telescope declares a `caveat` describing what it *cannot* see, and it's
shown on the board — Jackson lags awards by weeks, Simons ranks abnormality
rather than opinion, Kepler misses non-US raises and matches attention fuzzily.
An instrument that doesn't state its blind spots invites you to over-trust it.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/observatory` | every telescope's identity + toggle state (never fetches) |
| `POST /api/observatory/<slug>/toggle` | enable/disable, `{"enabled": bool}` |
| `GET /api/observatory/whats-new` | merged newest-first feed across enabled telescopes |
| `GET /api/telescope/<slug>` | the board — `?weights=sig:val,…` `&refresh=1` |
| `GET /api/telescope/<slug>/whats-new` | that telescope's events |
| `POST /api/telescope/<slug>/sweep` | force a sweep + change detection |
| `GET /api/roadmap` | the roadmap that backs the ROADMAP tab |

## Tests

```bash
python -m pytest tests/test_kernel.py -q   # pure logic, no network
python -m pytest tests/test_live.py -q     # hits every real source
```

`test_live.py` deliberately queries the live APIs rather than fixtures — a
fixture passing while the upstream source changed shape is exactly the failure
worth catching. It asserts each telescope returns enough rows, that every
declared signal and column is actually populated, that weights genuinely
re-rank, and that the diff engine emits fully-rendered headlines.

## Layout

```
app.py               Flask server, per-telescope routes, toggles, poller
roadmap.py           the roadmap, as data (also renders ROADMAP.md)
telescope/           the kernel — domain-agnostic
  base.py            Telescope + Column
  ranking.py         Signal + normalise/blend/dampen
  events.py          declarative event rules
  snapshots.py       snapshot history + diff engine
  registry.py        registration + on/off toggles
  series.py         shared time-series adapters + analytics
  cache.py http.py notifier.py
observatories/       one module per telescope — the only domain-specific code
templates/ static/   one generic frontend, driven by telescope metadata
```

See [ROADMAP.md](ROADMAP.md) for what's next and [TELESCOPES.md](TELESCOPES.md)
for the design notes behind the pattern.
