# 🔭 Hubble

A local dashboard that consolidates **what people actually use, like, and rank highest** across the LLM landscape into one "best models right now" leaderboard.

It pulls from several live sources, joins them on a single model identity, and blends the signals into a configurable score.

## Sources

| Source | Signals | How |
|--------|---------|-----|
| **HuggingFace Hub** | Downloads, likes | `api/models?sort=downloads` (public, no key) |
| **OpenRouter** | Real-world weekly **usage rank**, pricing, context length, the `hugging_face_id` join key | `api/v1/models?order=top-weekly` (the array order *is* the usage ranking) |
| **Benchmarks** (Artificial Analysis, via OpenRouter) | `intelligence_index`, `coding_index`, `agentic_index` | embedded in the OpenRouter model payload |
| **Arena** (Design Arena via OpenRouter, + best-effort LMArena) | Elo / win-rate | embedded benchmarks + community leaderboard mirror |

No API keys required — every source is public.

## Run

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

First load fetches live data (a few seconds) and caches it to `data/` for 1 hour.
Hit **↻ Refresh data** in the UI to force a re-fetch.

## How the score works

Each signal is normalised to 0–100 across the current dataset, then combined with
the weights you set via the sliders. Missing signals don't count against a model
(the remaining weights renormalise), **except** that models with no benchmark/arena
data at all are dampened 20% so usage-only router pseudo-models don't top a
"best LLMs" board on popularity alone.

Default weights favour intelligence + real usage. Drag the sliders to re-rank live —
e.g. crank **Coding** to find the best coder, or **Cheap price** for best value.

## Views (tabs)

The dashboard has three tabs under the wordmark:

- **INDEX** — the ranked leaderboard, weighting sliders, and full sortable table.
- **HARDWARE FIT** — enter your GPU/VRAM/RAM + quant preference and Hubble
  estimates each open-weight model's memory footprint and recommends the best
  ones that actually fit. VRAM estimate = `params × bytes/weight × 1.2`
  (Q4 ≈ 0.55, Q8 ≈ 1.0, FP16 ≈ 2.0 bytes/param; the 1.2 covers KV-cache &
  activations). Apple unified memory uses ~75% of system RAM as the budget.
  Param counts are parsed from the repo name (MoE totals handled:
  `235B-A22B` → 235B, `8x22B` → 176B); models without a size in the name are
  skipped. Only models with a HuggingFace repo are considered "local".
- **WHAT'S NEW** — the detected-events feed (see below).

## Change detection & the events feed

Hubble keeps a history so it can announce what's *new*, not just what's current.

- Every sweep saves a slim timestamped snapshot to `data/history/` and diffs it
  against the previous one, emitting **events**: `new_leader`, `new_model`,
  `big_climber`, `price_drop` — each with a ready-to-post headline.
- A **background poller** (default every 6h, set `HUBBLE_POLL_SECONDS`) runs the
  sweep even when nobody's watching. A manual **↻ Refresh** also triggers it.
- Events are logged to `data/events.json` and surfaced in the dashboard's
  **WHAT'S NEW** panel and at `GET /api/whats-new?since=<epoch>&limit=N`.

### Wiring a bot (X / Discord / etc.)

Any output channel is a thin reader on top of the event log. Two ways:

1. **Push** — fill in the stubs in [`notifier.py`](notifier.py). `dispatch()` is
   already called each sweep with the new events; set the env vars
   (`X_API_KEY`/… or `HUBBLE_DISCORD_WEBHOOK`) and uncomment the body. Only
   `new_leader` / `new_model` are pushed to social by default (see `SOCIAL_TYPES`).
2. **Pull** — have an external bot poll `GET /api/whats-new?since=<last_seen_ts>`
   on a cron and post whatever comes back.

So "🔭 Hubble detected new intelligence at Anthropic today" is just
`notifier.post_to_x` with credentials dropped in.

## Layout

```
app.py          Flask server + /api/models + /api/whats-new + background poller
fetchers.py     per-source fetchers + the unified join + noise classifier
ranking.py      normalisation + weighted blended score
snapshots.py    snapshot history + diff engine that emits events
notifier.py     output dispatcher + X/Discord stubs
cache.py        TTL disk cache (data/)
templates/      dashboard HTML
static/         CSS + vanilla-JS frontend (feed, podium, sortable table, sliders)
```

## Notes / extending

- **LMArena**: true Chatbot Arena text Elo moved behind lmarena.ai and has no
  stable public JSON. `fetch_lmarena` tries a community CSV mirror and degrades
  to empty if it's gone — Design Arena Elo from OpenRouter covers the arena slot
  meanwhile. Drop a real source into `fetch_lmarena` to light it up.
- Add a source by writing a `fetch_*` function and joining it in `build_unified`.
- Cache TTL is `CACHE_TTL` in `app.py`.
