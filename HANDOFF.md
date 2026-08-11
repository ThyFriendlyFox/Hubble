# Handoff — continuing the Observatory build loop

Context for an agent picking this repo up locally. Read `README.md` (what the
system is), `TELESCOPES.md` (why the pattern is shaped this way) and
`ROADMAP.md` (what's next) before changing anything.

## State

Six telescopes run on live public data, no API keys. Kernel in `telescope/`,
one domain pack per telescope in `observatories/`, one generic frontend driven
entirely by telescope metadata. 118 kernel tests + 101 domain-pack tests +
92 live tests (all three suites grow as the build continues — check the
actual count with `-q`, don't trust this number for long).

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
them had ever done, and a multi-iteration coverage.py-guided sweep (installed
locally each time for verification only, never added as a dependency) that
took every file in `telescope/` from unmeasured to individually audited —
7 of 11 now sit at 100%, the package as a whole at ~92%, and every remaining
gap is a deliberately-judged omission (an optional dependency not installed,
a rare double-failure edge case, or genuinely network-bound adapter code
already covered by `test_live.py` instead) rather than an oversight. The same
sweep then extended to `app.py` (69% -> 75%, every remaining line a real
thread/process-lifecycle omission) and, in the process of testing `?refresh=1`,
surfaced and fixed a genuine concurrency bug: `Cache.set()` wrote every key to
one shared `.tmp` path, so a background poller sweep racing a manual refresh
of the same telescope could crash with `FileNotFoundError` — not
test-only, a real production race. Fixed with a per-writer-unique tmp path.
The sweep then moved to `observatories/` — never individually measured
before, unlike `telescope/` and `app.py` — and found Kepler at 55%, the
lowest of any file in the whole project: most of its own business logic
(entity-name matching, the Greenhouse/Lever/Ashby fallback chain, HN
domain-vs-title matching) was never exercised by either suite, since
`test_live.py`'s warm cache skips the producer functions that logic lives
in. New file `tests/test_observatories.py` mocks `http.py`'s fetch
primitives to pin that logic directly; Kepler: 55% -> 87%, the remainder
being SEC daily-index-parsing plumbing flagged as a follow-up rather than
covered. Same pass then closed Jackson (67% -> 86%): its CAPABILITY AREAS
panel's `new_program`/`budget_shift` event logic (`_psc_events()`) and its
own `sweep()` override had never been exercised by either suite at all —
not cache-skipped, genuinely never called — including the "compare
against the last announcement, not the last sweep" baseline-drift property
the module docstring calls out. Holmdel closed next, and cleanly this
time — 67% -> 100%, the first domain pack in the sweep with no remaining
gap at all: per-source extraction logic (arXiv's regex XML parsing, HN/
Wiki/GitHub's window extraction, npm's stale-data-over-a-total-outage
resilience) plus the UNLISTED panel's watchlist matching and `context()`'s
FIELDS aggregation, including one honestly-documented real quirk — an
all-noise sweep skips the independently-sourced UNLISTED panel too, since
both sit behind the same early return. Simons closed to 100% next: 13F
XML parsing (`_positions()`, including the documented real Berkshire-
otherManager duplicate-CUSIP dedup), whale-move direction classification
and its $10M noise floor, `_whale_move_events()`/`sweep()` (the same
never-exercised event-firing shape Jackson's `_psc_events()` had), and the
Hubble cross-telescope AI CAPEX WATCH join with its real date-matching
arithmetic. The Hubble telescope itself (the LLM index, not the AI CAPEX
panel above) closed to 100% next — smallest domain pack in the fleet, its
own price-cleaning (`_clean_price()`'s negative-sentinel handling) and
OpenRouter join logic (rank/pricing/benchmark/arena-elo-max extraction)
pinned directly. `reddington.py` closed the theme out last — the smallest
file in the fleet, one edge case (no gauge in the VOLUME/FUEL segments) —
which makes `observatories/` all six domain packs at 100%, alongside
`telescope/` (kernel, ~92%+ with every remaining gap a deliberately-judged
omission) and `app.py` (75%, same standard): the whole codebase has now
been individually, deliberately audited by this coverage.py methodology,
not just the parts that happened to get touched by feature work.
`roadmap.py`'s own Phase 5 entries are the detailed log — this is only the
shape of it.

Work is on branch `claude/telescope-dashboard-concept-lo1ay8`, open as **PR #1**.
Pushing to that branch updates the PR — do not open a new one.

```bash
pip install -r requirements.txt
OBSERVATORY_ENABLED=all python app.py         # http://127.0.0.1:5000
python -m pytest tests/test_kernel.py -q       # kernel pure logic, fast
python -m pytest tests/test_observatories.py -q  # domain-pack logic, mocked network, fast
python -m pytest tests/test_live.py -q         # hits real sources, slow when cold
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

**A finding, not a fix — needs a human call:** the documented `api.sam.gov`
partner API still 404s without a key (table above, unchanged). But SAM.gov's
own website backend — the undocumented internal endpoint its public search
page's own JS calls, `sam.gov/api/prod/sgs/v1/search/?index=opp&...` — does
return real, live, unauthenticated opportunity data (verified 2026-08-11:
keyword and NAICS filtering both work, real solicitations with agency
hierarchy). Deliberately **not** wired into Jackson: that endpoint exists to
route around GSA's own registration requirement for this exact data, which
is a ToS/legal judgment call, not a technical one — not mine to make
unilaterally in an unattended loop. Flagged here for a human decision rather
than silently used or silently dropped.

If all three named-in-the-table sources are still blocked (expect this), read `ROADMAP.md` for what's
already done rather than re-deriving it, then look for real, previously-
unflagged gaps rather than re-treading covered ground: audit a class of
fetcher for the same failure mode that's already been fixed elsewhere (cache
resilience, retry scheduling, a dead upstream URL), or verify a documentation
file against current reality the way this section itself needed fixing.
The coverage.py-guided sweep that used to be the default fallback here is
now genuinely done, not just quiet for a while: every Python file that
matters — all 11 in `telescope/`, `app.py`, and all six domain packs in
`observatories/` — has been individually, deliberately audited, and every
real gap that methodology could find has been closed (what's left in each
is a documented, judged omission: thread/process lifecycle code, an
optional dependency, SEC-index-parsing plumbing). Don't reach for "extend
test coverage" as the default next move anymore — it'll mostly find
nothing, because there's nothing left to find that way. `static/app.js`
(826 lines, the whole frontend) has never been tested at all and is a
real gap, but introducing a JS test framework is a bigger decision than a
single iteration should make unilaterally — surface it as an option, don't
just start installing a dependency. `roadmap.py`'s own Phase 5 entries are
a log of the kind of iteration that still fits this section — read a few
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
   `MIN_ROWS`. A domain pack's own business logic (entity-name matching, a
   multi-source fallback chain, XML field extraction) still needs pinning even
   though it isn't kernel code — `test_live.py`'s warm-cache fixtures mean a
   real sweep often never even calls a source's producer function at all
   (`cache.cached()` only calls it on a miss), so that logic can go completely
   unverified by a passing live suite. `tests/test_observatories.py` is the
   home for that: mocks `telescope/http.py`'s fetch primitives directly (the
   same technique `test_kernel.py` already uses for `http.py`'s own retry
   logic), never touches the network or the real disk cache.
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
