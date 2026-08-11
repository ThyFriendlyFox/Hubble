# Handoff — continuing the Observatory build loop

Context for an agent picking this repo up locally. Read `README.md` (what the
system is), `TELESCOPES.md` (why the pattern is shaped this way) and
`ROADMAP.md` (what's next) before changing anything.

## State

Six telescopes run on live public data, no API keys. Kernel in `telescope/`,
one domain pack per telescope in `observatories/`, one generic frontend driven
entirely by telescope metadata. 109 kernel tests + 86 live tests (both suites
grow as the build continues — check the actual count with `-q`, don't trust
this number for long).

Every Phase 0-4 roadmap item is either shipped or genuinely, repeatedly
re-confirmed blocked on something external (SAM.gov and Semantic Scholar both
need a registered key; Jackson's own SBIR fetch 429s independent of any key).
Holmdel's research source (OpenAlex + arXiv), GitHub repo velocity, and the
`crossing_over` event this section used to tell you to build are all done —
if you're being told to "add OpenAlex to Holmdel" by an old standing prompt,
check `git log` and `ROADMAP.md` first; that work is finished, don't redo it.
Phase 5 (hardening) is where most recent work has landed, now spanning several
distinct classes: cache-poisoning resilience across every fetcher, poller/
brief-scheduler retry scheduling, cross-telescope joins that were silently
capable of forcing a sweep of the telescope they read from, a frontend race
condition where switching telescopes mid-refresh could revert to stale data,
a dead source removed rather than left silently broken (Hubble's LMArena
fallback), real data-correctness bugs found by inspecting live production
data rather than just reading code (un-decoded XML entities leaking into
company names in two telescopes' SEC parsing, unescaped HTML interpolation
silently truncating the ROADMAP tab), code deduplication per convention #1
below (`xml_tag`, `to_float`, a `NewEntrantRule` kernel extension), and — most
recently — a full pass making three telescopes' secondary panels (Simons'
WHALE MOVES, Kepler's stealth detection, Jackson's CAPABILITY AREAS) actually
fire the real feed events `TELESCOPES.md` designed them to, which none of
them had ever done. `roadmap.py`'s own Phase 5 entries are the detailed
log — this is only the shape of it.

Work is on branch `claude/telescope-dashboard-concept-lo1ay8`, open as **PR #1**.
Pushing to that branch updates the PR — do not open a new one.

```bash
pip install -r requirements.txt
OBSERVATORY_ENABLED=all python app.py     # http://127.0.0.1:5000
python -m pytest tests/test_kernel.py -q  # pure logic, fast
python -m pytest tests/test_live.py -q    # hits real sources, slow when cold
```

## Start here: re-probe what's still blocked, then read the roadmap

Before picking a task, re-check the small list of sources still genuinely
blocked — these get re-verified almost every iteration and the answer has
been consistently unchanged for a long time, but "consistently unchanged for
a long time" is not the same as "permanently impossible," so don't skip this:

| Source | Symptom | Notes |
|---|---|---|
| SAM.gov opportunities | `404` on the public search path | needs a registered API key |
| Semantic Scholar | `429`, occasionally a genuine `200` | the unauthenticated quota is a small pool shared globally by every unkeyed caller; an isolated success is not a real unblocking — confirmed by immediately retrying several more times, still 429 |
| Jackson's own SBIR fetch | `429` / `403`, worse than "rate-limits hard" | independent of SAM.gov's key requirement |

If all three are still blocked (expect this), read `ROADMAP.md` for what's
already done rather than re-deriving it, then look for real, previously-
unflagged gaps rather than re-treading covered ground: audit a class of
fetcher for the same failure mode that's already been fixed elsewhere (cache
resilience, retry scheduling, a dead upstream URL), verify a documentation
file against current reality the way this section itself needed fixing, or
extend test coverage into a genuinely untested corner. `roadmap.py`'s own
Phase 5 entries are a log of exactly this kind of iteration — read a few
before starting to calibrate scope and the level of live verification
expected.

## Then work the roadmap

`roadmap.py` is the source of truth; it renders both the dashboard's ROADMAP tab
and `ROADMAP.md`. Regenerate with `python roadmap.py > ROADMAP.md` after edits.

## Conventions that must not be broken

1. **Kernel vs domain pack.** Anything domain-agnostic belongs in `telescope/`.
   A domain pack declares identity, `collect()`, `signals`, `rules`, `columns`
   and nothing else. If two telescopes need the same helper, lift it into the
   kernel — `telescope/series.py` exists because Simons and Reddington both
   needed time-series analytics.
2. **State blind spots.** Every telescope sets a `caveat` describing what it
   *cannot* see, rendered on its board. When a source is unavailable, say so
   and drop the signal — never substitute a proxy and present it as the real
   thing. Applies both ways: Reddington's caveat says plainly that lane-level
   spot rates are paywalled and it only sees index level; when Hubble's
   LMArena fallback turned out to have been silently 404ing (dead code
   contributing nothing, no error ever surfaced), the fix was to remove it
   and state the resulting limitation, not to leave it quietly doing
   nothing while implying it still worked.
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
