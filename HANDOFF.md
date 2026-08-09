# Handoff — continuing the Observatory build loop

Context for an agent picking this repo up locally. Read `README.md` (what the
system is), `TELESCOPES.md` (why the pattern is shaped this way) and
`ROADMAP.md` (what's next) before changing anything.

## State

Six telescopes run on live public data, no API keys. Kernel in `telescope/`,
one domain pack per telescope in `observatories/`, one generic frontend driven
entirely by telescope metadata. 73 tests pass.

Work is on branch `claude/telescope-dashboard-concept-lo1ay8`, open as **PR #1**.
Pushing to that branch updates the PR — do not open a new one.

```bash
pip install -r requirements.txt
OBSERVATORY_ENABLED=all python app.py     # http://127.0.0.1:5000
python -m pytest tests/test_kernel.py -q  # pure logic, fast
python -m pytest tests/test_live.py -q    # hits real sources, slow when cold
```

## Start here: re-probe the sources that were blocked

The previous environment was a sandboxed container behind a shared egress
proxy. Several sources were unreachable **there** that are probably fine from a
normal local machine. This is the highest-value first task, because it unblocks
the one telescope that shipped incomplete.

| Source | Symptom in the container | Worth retrying locally |
|---|---|---|
| arXiv query API | `429 Rate exceeded`, then timeouts | yes |
| Semantic Scholar | `429` | yes |
| OpenAlex | `429 Insufficient budget` (shared IP quota) | yes |
| GitHub search API | `403` — proxy binds GitHub to configured repos | yes |
| pypistats.org | `429` | yes |
| Crossref | works, but its query API is an **OR** match — a quoted 3-word topic returns millions of rows, so counts are not topic counts | no, it is semantically wrong, not blocked |

Verify with a quick script before building on any of them. If arXiv, Semantic
Scholar or OpenAlex works locally, add a **research velocity** source to
Holmdel — papers are the earliest stage of an idea and Holmdel currently cannot
see them at all. That also unlocks the `crossing_over` event (research →
builders), which is specced but deliberately unimplemented rather than faked.

If GitHub search works, add repo-creation and star velocity to Holmdel as a
fourth independent surface.

## Then work the roadmap

`roadmap.py` is the source of truth; it renders both the dashboard's ROADMAP tab
and `ROADMAP.md`. Regenerate with `python roadmap.py > ROADMAP.md` after edits.
Phase 2B (unblocking Holmdel) then Phase 3 (sharpening existing telescopes).

## Conventions that must not be broken

1. **Kernel vs domain pack.** Anything domain-agnostic belongs in `telescope/`.
   A domain pack declares identity, `collect()`, `signals`, `rules`, `columns`
   and nothing else. If two telescopes need the same helper, lift it into the
   kernel — `telescope/series.py` exists because Simons and Reddington both
   needed time-series analytics.
2. **State blind spots.** Every telescope sets a `caveat` describing what it
   *cannot* see, rendered on its board. When a source is unavailable, say so
   and drop the signal — never substitute a proxy and present it as the real
   thing. Holmdel's missing research source is documented, not hidden.
3. **Live tests, not fixtures.** `tests/test_live.py` hits the real APIs on
   purpose: a fixture passing while an upstream source changed shape is the
   exact failure worth catching. It asserts every declared signal and column is
   populated, that weights genuinely re-rank, and that event headlines render
   with no leftover `{placeholders}`. Add new telescopes to its `ALL` and
   `MIN_ROWS`.
4. **Missing signals renormalise, they don't zero.** This is the core of
   `telescope/ranking.py`. Before adding a signal, check it can't dominate when
   others are absent — that bug has already appeared twice (Kepler ranked
   no-economics filings #1 on recency; Holmdel ranked 1→6 stories as +500%).
   The fixes were a fallback field and a minimum-volume gate respectively.
5. **Watch small denominators and fuzzy name matching.** Both produce
   confident-looking garbage. Gate growth on absolute volume; quote phrases
   when an API supports it (Algolia does, Crossref does not).
6. **`quality_signals` is the dampener.** Rows carrying none of those signals
   are multiplied by `dampen`. Use it for "can't really be judged", not for
   "unpopular" — Kepler deliberately excludes public attention from it, because
   a raise with no footprint is the point of the telescope.

## Loop

Dynamic-mode `/loop` (no interval) dropped its wakeup in the previous
environment and never fired. Give it an explicit interval so it uses cron:

```
/loop 30m Continue the Observatory build. Read HANDOFF.md and ROADMAP.md, pick
the highest-value unfinished item, implement it, verify it against live sources,
run both test suites, then commit and push to
claude/telescope-dashboard-concept-lo1ay8 (updates PR #1). Update roadmap.py and
regenerate ROADMAP.md when an item completes. Report what you changed and any
source that proved unreliable.
```

Do one coherent item per iteration and leave the tree green and pushed — the
loop may be interrupted between iterations.
