# Handoff — continuing the Observatory build loop

Context for an agent picking this repo up locally. Read `README.md` (what the
system is), `TELESCOPES.md` (why the pattern is shaped this way) and
`ROADMAP.md` (what's next, generated from `roadmap.py`) before changing
anything.

## State

Seven telescopes (Hubble, Jackson, Simons, Kepler, Holmdel, Reddington,
Pasteur) run on live public data, no API keys. Kernel in `telescope/`, one
domain pack per telescope in `observatories/`, one generic frontend driven
entirely by telescope metadata. 143 kernel tests + 129 domain-pack tests +
112 live tests + 33 zero-dependency Node tests covering every DOM-free
function in static/app.js (check the actual counts with `-q` / `node
--test`, don't trust these numbers for long).

Every Phase 0–4 roadmap item (the original six telescopes and their core
signals) is shipped; Pasteur, a seventh, landed later (Phase 9) with its own
web crawler and PageRank kernel primitives. Phase 5 (hardening) closed out a
long tail of real bugs found by
testing against live data rather than just reading code — cache-poisoning
resilience, poller/scheduler retry correctness, cross-telescope joins that
could silently trigger a sweep, a frontend stale-data race, dead sources
removed rather than left silently broken, XML-entity and HTML-escaping
correctness bugs, secondary panels that never fired the events they were
designed for — and a multi-iteration coverage.py-guided sweep that took the
**entire Python codebase** (all of `telescope/`, `app.py`, and all six
`observatories/*.py`) from unmeasured to individually audited, finding and
fixing a real concurrent-writer race in `Cache.set()` along the way. Phase 6
verified documentation against reality: `TELESCOPES.md`'s fleet tables turned
out to describe the pre-implementation *plan* for five of six telescopes, not
what shipped, and got corrected; a systematic domain-pack-docstring audit
found one real drift (Jackson's SBIR caveat claimed the wrong HTTP status).
Phase 7 drove the actual running dashboard in a real browser — the first
iterations in this build to do that rather than only read or test the code —
covering all six telescopes' boards and every major interactive control
(weight sliders, SAVE VIEW's full apply/delete/create lifecycle, the
enable/disable toggle, narrow-viewport layout, column sorting, the per-row
watch star, panel-level sort/watch, the cross-telescope watched-only feed
filter, BRIEF's SEND NOW button, the hover score-breakdown tooltip). It
found and fixed two real bugs along the way: a `Cache.cached()`
cache-stampede race (concurrent cold-cache callers each redundantly
re-running the same slow fetch instead of one paying the cost and the rest
waiting, fixed with a per-instance lock), and a header layout bug where a
telescope with many sources (Holmdel, 7) could squeeze the nav tabs into
wrapping even at full desktop width because its own freshness readout had
no width cap (fixed by capping `.readout` with ellipsis truncation). A third:
`Cache`'s stampede-fix lock was one-per-telescope rather than one-per-key, so
a single stuck source (OpenAlex hanging rather than failing fast) blocked
reads of every other, already-fresh cached source too — fixed with per-key
locking, plus a circuit breaker in `_openalex()` for the sustained-hang case
itself (see the blocked-sources table below). Every wired interactive
control in `app.js` has now been driven live at least once.

**All of this is exhaustively detailed in `roadmap.py`** (and its generated
form, `ROADMAP.md` / the dashboard's ROADMAP tab) — this section is only the
shape of it. Don't re-narrate history here each iteration; add the detail to
`roadmap.py` and keep this section a short current-state summary. If this
section starts creeping past ~20 lines again, that's a sign it needs
trimming back down, the same way this rewrite trimmed it from ~160 lines of
accumulated iteration-by-iteration prose.

Work is on branch `claude/telescope-dashboard-concept-lo1ay8`, open as **PR #1**.
Pushing to that branch updates the PR — do not open a new one.

```bash
pip install -r requirements.txt
OBSERVATORY_ENABLED=all python app.py            # http://127.0.0.1:5000
python -m pytest tests/test_kernel.py -q         # kernel pure logic, fast
python -m pytest tests/test_observatories.py -q  # domain-pack logic, mocked network, fast
python -m pytest tests/test_live.py -q           # hits real sources, slow when cold
node --test tests_js/*.test.js                   # app.js's pure formatters, zero npm deps
```

**Check the dev server is actually up at the start of every iteration** — don't
assume it survived from last time. It's been found dead not just after a multi-day
gap but after a ~45-minute one too, so treat "still running" as something to verify,
not assume, regardless of how short the gap looks. If it's died (`ps aux | grep
app.py` shows nothing, or the last-known port refuses connections), don't just
background `scripts/dev.sh` yourself: the port it lands on is whatever the
browser-preview tooling's own `autoPort` assigns (`.claude/launch.json` names port
5000, but that's a starting point, not a fixed value, and it changes on every
restart), so restart it through that tool (`preview_start` with name `hf-dash`) and
read the actual port back from its own result — don't reuse a port remembered from
an earlier iteration.

## Start here: re-probe what's still blocked, then read the roadmap

Before picking a task, re-check the sources still genuinely blocked — these
get re-verified almost every iteration and the answer has been consistently
unchanged for a long time, but "consistently unchanged for a long time" is
not the same as "permanently impossible," so don't skip this:

| Source | Symptom | Notes |
|---|---|---|
| SAM.gov opportunities | `404` on the public search path | needs a registered API key |
| Semantic Scholar | `429`, occasionally a genuine `200` | small pool shared globally by every unkeyed caller; an isolated success isn't real unblocking — confirmed by immediately retrying several more times, still 429 |
| Jackson's own SBIR fetch | `429` / `403`, inconsistently either | independent of SAM.gov's key requirement |
| Holmdel's OpenAlex source | cycles between `429` (small daily USD budget, resets midnight UTC, easily exhausted by this project's own real testing volume), occasional `503` (an unrelated, external OpenAlex-side cluster-load issue), and occasionally just hangs, accepting the TCP connection and never responding at all | not a code bug — `Cache.cached()`'s stampede fix, the `is_empty` stale-fallback guard, per-key cache locking, and `_openalex()`'s circuit breaker (trips on the first fully-silent topic) all now handle every one of these gracefully; verified live both ways — a 47.3s end-to-end board load while a multi-day hang was actively ongoing, and later, after it resolved on its own, a full successful 32-topic fetch (real `meta.count`s, `cost_usd` billing) confirming the fix doesn't get in the way once the source recovers |

**A finding, not a fix — needs a human call:** the documented `api.sam.gov`
partner API still 404s without a key (table above). But SAM.gov's own
website backend — the undocumented internal endpoint its public search
page's own JS calls, `sam.gov/api/prod/sgs/v1/search/?index=opp&...` — does
return real, live, unauthenticated opportunity data. Deliberately **not**
wired into Jackson: that endpoint exists to route around GSA's own
registration requirement for this exact data, a ToS/legal judgment call, not
a technical one — not mine to make unilaterally in an unattended loop.

If all the named sources are still blocked (expect this), read `ROADMAP.md`
for what's already done, then look for real, previously-unflagged gaps. Six
fallback avenues have each already had a full pass and are exhausted for
now — don't default back into any of them without a genuinely new angle
(new code that could have introduced new drift, a specific claim worth
re-checking, a section of the app never yet touched):

1. **Extend test coverage.** Done for the entire Python codebase now,
   including `app.py` itself — a `coverage.py --branch` pass over
   `telescope/crawl.py`, `telescope/graph.py`, and `observatories/pasteur.py`
   found and closed 7 real gaps (a non-anchor HTML tag, the parser-exception
   guard, a failed fetch's empty-edges case, a link discovered from two
   pages, an off-domain link recorded but not followed, a sitemap `<url>`
   missing `<loc>`, `source_keys()`, a trials page-fetch failure, the
   backlink seed filter's per-item skip, the crawl's own `link_filter`
   closure, and both branches of `collect()`'s last-update date handling)
   plus one in `telescope/graph.py` (`pagerank()`'s loop exhausting its
   iteration budget rather than converging early). `telescope/crawl.py` sits
   at 98% (one line, `if url in visited: continue` inside `crawl()`,
   documented inline as unreachable — the enqueue-time dedup check already
   makes it impossible to trigger without breaking that invariant on
   purpose). A follow-up sweep found `app.py` itself was at only 72% despite
   its routes being well covered: `_poller()` and `_brief_scheduler()`
   — the two background threads, each carrying a documented real bug fix
   in its own docstring (`last` must only advance on success) — had zero
   tests, since nothing in the existing Flask-route tests ever actually
   starts them. Added mocked, no-network tests in `tests/test_live.py`
   (fully deterministic — `time.sleep`/`time.time` patched to drive each
   infinite loop a fixed number of ticks before a sentinel exception
   escapes it) pinning both the retry-on-failure and wait-after-success
   halves of that fix, plus the debug-reloader launch guards on
   `_start_poller()`/`_start_brief_scheduler()`. `app.py` is now at 96% —
   the only gap left is the unexecutable-without-a-live-server
   `if __name__ == "__main__":` block, the same standard omission as any
   other module's entry point. Every DOM-free function in `static/app.js`
   has real coverage too (see below); the remaining Python gaps are
   deliberately-judged omissions and the rendering/event-wiring half of
   app.js would need a real DOM shim.
2. **Verify a documentation file against reality.** Done for
   `TELESCOPES.md`, `README.md`, and every domain pack's own module
   docstring. `ROADMAP.md` is generated so it can't drift; `app.js`/
   `style.css`/`index.html` have no prose docs to drift from.
3. **Drive the dashboard in a real browser.** Done for all seven telescopes'
   boards and every wired interactive control in `app.js`, down to the
   panel-level sort/watch, the cross-telescope watched-only feed filter,
   SEND NOW, and the hover score-breakdown tooltip. Pasteur only got a
   partial spot-check when it first shipped (basic rendering, sliders, the
   tooltip); a follow-up pass ran the same full checklist Phase 7 gave the
   original six (column sort, watch star, WATCHED ONLY, SAVE VIEW's
   create/apply/delete cycle, narrow-viewport layout) and found a real bug:
   the weight sliders in `static/app.js` had no `step` attribute, so the
   browser's implicit `step=1` silently snapped Pasteur's own
   `centrality: 0.6` default weight to `1` on the native slider element
   (every other telescope's default weights are whole integers, so nothing
   had ever triggered this before) — fixed with `step="0.1"`. This is the
   avenue that's found real bugs *four times* now
   (all fixed); the most recent pass on the other six telescopes found
   nothing new. Still worth a re-check after new code lands or if a flow
   gets reshaped — but there's no untested control left to find
   on the app as it stands today.
4. **Re-probe blocked sources for a real change.** Ongoing, every
   iteration, via the table above — cheap and already part of the loop.
5. **Reason about untrusted external data reaching an output channel.**
   Coverage stats can't find this category — `telescope/notifier.py`'s
   Discord/Slack tests already had full line coverage before this pass, and
   the gap was invisible to `coverage.py` because it's about payload
   *content*, not which lines execute. Every event headline embeds a
   `name` pulled straight from an unauthenticated public source (a GitHub
   repo, an npm package, an SEC Form D filer, a ClinicalTrials.gov sponsor,
   an HN post title, ...) — anyone can register one of those under any
   string they like, including literal `@everyone`/`@here`. Discord parses
   mentions out of plain webhook message text by default, so an
   attacker-chosen entity name could page an entire Discord server the
   moment its event fired — a real content-injection path from unauthenticated
   public data into a push-notification channel, not a theoretical one.
   Fixed by setting `allowed_mentions: {"parse": []}` on every Discord
   payload (Discord's own documented mechanism for exactly this case).
   Slack's webhook `text` field doesn't have this problem — plain
   "@everyone" renders as literal text there; real mentions need Slack's
   own bracketed syntax, which no external name field can produce by
   accident. A follow-up pass checked the third and last channel,
   `post_to_x()`: X's `create_tweet()` has no `allowed_mentions`-equivalent
   opt-out at all, and a literal `@handle` in a posted tweet always pings
   that real account if one exists — using the deployer's own authenticated
   X identity, with npm scoped package names (`@angular`, `@babel`, ...)
   making a real-handle collision plausible rather than purely theoretical.
   Fixed with the standard technique for this exact gap: a zero-width space
   right after every `@` (`_defang_mentions()`), which breaks mention
   parsing while reading identically to a human. All three output channels
   are now checked; worth a re-check if a new one is ever added, or if any
   existing one starts embedding raw external text somewhere new. A third
   pass turned this same lens on the dashboard's own rendering, not an
   output channel: six `innerHTML` sites in `static/app.js` interpolated a
   server error message or a caught JS exception (`${data.error}`/`${e}`)
   with no `esc()` — a real, unmitigated stored/reflected-XSS-shaped gap
   (confirmed live: a mocked error response containing
   `<img src=x onerror="...">` executed the handler through the
   unescaped path and rendered as inert text through the fixed one; no
   CSP header exists to have caught this as a second layer). The concrete
   trigger is narrow today — `to_float()` and friends already swallow the
   coercion errors that would otherwise echo raw external values back
   verbatim, and no domain pack currently raises with untrusted content in
   the message — but fixed anyway as defense-in-depth, matching this
   project's standard for a real gap rather than an already-proven live
   exploit. All six wrapped with `esc()` (which already safely stringifies
   non-string values, so no separate `String()` conversion needed).
6. **Keyboard/screen-reader operability of custom (non-native) interactive
   elements.** Distinct from avenue 3, which only ever drove controls with a
   mouse click — never checked whether the same controls work without one.
   The watch star (a `<td>`), the sort headers (a `<th>`), and the SAVE VIEW
   chip's apply/delete actions (a `<b>`/`<span>`) are all click-only: none of
   them is a native `<button>`, so none gets keyboard focus or Enter/Space
   activation for free, and none had a `role`/`aria-label`/`aria-pressed`
   telling a screen reader what it does. That made the watchlist — arguably
   this app's single most load-bearing cross-cutting feature, the thing the
   cross-telescope watched-only feed filter is built on — completely
   unusable without a mouse. Fixed with `role="button"`/`tabindex="0"` plus a
   `keydown` handler that reuses the existing click logic (`el.click()` from
   inside the delegated listeners, a shared named function where listeners
   are wired per-element) rather than duplicating it, and `aria-pressed`/
   `aria-label` built from each row's own `name` (main table) or its panel's
   own declared text columns (panels have no fixed shape, so no field name
   could be hardcoded). Verified live via real `KeyboardEvent`s (focus +
   Enter, focus + Space), not just reasoned about — confirmed on both the
   main table and a panel (Jackson's CAPABILITY AREAS). A follow-up pass
   covered the one custom control left untouched: the score-breakdown
   tooltip, wired only to `mousemove`/`mouseleave`, so a keyboard-only user
   tabbing through the exact cells that trigger it (the score column, the
   podium score) could never see it at all — the only place in the whole
   dashboard showing a signal-by-signal breakdown of how a score was
   computed. Fixed by adding `focusin`/`focusout` handlers alongside the
   existing mouse ones (delegated the same way, since `focusin`/`focusout`
   bubble like `mousemove` does, unlike plain `focus`/`blur`), positioning
   from the focused element's own bounding rect instead of a cursor
   position but reusing `positionTip()`'s viewport-clamping unchanged, plus
   `role="tooltip"` on the tip element and `aria-describedby` on its two
   semantically-relevant triggers (deliberately not the watch star too,
   even though it shares the same mouse-hover trigger surface — its own
   `aria-label` is about watching, not scoring, and describing it with
   score-breakdown content would be a misleading screen-reader association
   even though visually consistent for a sighted keyboard user). Verified
   live with a real, CDP-injected click (a page-script `.focus()` call is
   silently suppressed by the browser when the tab lacks real OS focus, a
   testing-environment quirk this session hit and diagnosed rather than
   mistook for a bug) plus a real `Tab` keypress moving focus row to row —
   confirmed the tip's content correctly follows focus across rows, not
   just appears once. A third pass found the piece the first two left
   incomplete: none of the six elements made keyboard-focusable across both
   passes (`.watch-cell`, `thead th`, `.scorecell`, `.pod-score`,
   `.view-chip b`, `.view-chip .del`) had any visible focus indicator —
   confirmed live via computed style (`outline-style: none`) on a genuinely
   Tab-focused sort header, not assumed. Browsers give `<button>`/`<a>`/form
   controls a default focus ring automatically but not `<td>`/`<th>`/`<b>`/
   `<span>` even with `tabindex`, so operability without visibility is a
   real, distinct failure mode of its own — a sighted keyboard user could
   now activate these but not see which one was about to activate. Fixed
   with one shared `:focus-visible` rule (`outline: 2px solid var(--paper);
   outline-offset: -2px`) — `:focus-visible` specifically, not `:focus`, so
   a mouse click doesn't also show a ring these elements never had before,
   matching how `:hover` already gives mouse users their own feedback.
   Verified live with genuine keyboard `Tab` navigation (`element.matches(
   ':focus-visible')` confirmed `true` with the exact intended computed
   style) — a plain scripted `.focus()` call does not trigger
   `:focus-visible` matching in real browsers regardless of the OS-focus
   quirk noted above, a second, distinct testing-environment trap this
   session had to route around correctly rather than let produce a false
   negative. A fourth pass moved to a different accessibility dimension
   entirely — color contrast, not keyboard behavior — and computed real
   WCAG relative-luminance ratios (not eyeballed) for every named color in
   `style.css`'s palette against every background it's actually used on.
   `--ink-faint` (`#5a5a57`) failed WCAG AA everywhere: 2.76-2.86:1 against
   all three dark backgrounds, below even the lenient 3:1 large-text floor,
   let alone 4.5:1 for normal text. Not decorative — it's the rank column,
   the status line, empty-state copy, and the score-breakdown tooltip's own
   RAW/WEIGHT figures (the exact content the second pass worked to make
   keyboard-reachable). Fixed at the one root cause (the custom property
   itself, `#80807d`) rather than touching each of the 27 individual rules
   that reference it, landing at 4.82-5.00:1 against the three backgrounds
   checked plus the tooltip's own slightly-lighter background (4.68:1) —
   confirmed live via the browser's own computed `color`, not just the
   source value. A fifth pass checked motion: `.loading::after` runs
   `animation: blink 1s steps(4) infinite` — shown on every telescope
   switch, refresh, and weight-change re-fetch — with no way to pause it,
   exactly what WCAG 2.2.2 (Pause, Stop, Hide) exists to prevent for users
   with vestibular disorders or motion sickness triggered by animation (a
   real OS-level accessibility setting, `prefers-reduced-motion`, not a
   rare edge case). Nothing in `style.css` handled that media feature at
   all. Fixed with the standard universal override
   (`*, *::before, *::after { animation-duration: 0.01ms !important; ... }`
   under `@media (prefers-reduced-motion: reduce)`) rather than hand-picking
   each animated selector. Verified live in both directions: injected the
   exact same rule unconditionally and confirmed the loading blink's
   computed `animation-iteration-count` actually drops from `infinite` to
   `1` (not just that the CSS parsed), then removed it and confirmed normal
   motion is completely unaffected (`animation-duration: 1s`,
   `prefers-reduced-motion` correctly reads `false` in this environment). A
   sixth pass moved from individual-widget accessibility to page-level
   navigation structure: the whole app has exactly one heading (`<h1>` for
   the wordmark) — every real section (TOP RANKED, WEIGHTING MATRIX, FULL
   INDEX, every panel, and each of the other four tabs' own single section)
   was a plain `.section-label` `<div>`, not a heading at all. Screen-reader
   users navigate primarily by heading (WebAIM's own screen-reader survey
   consistently ranks it the single most-used navigation method), so this
   meant literally no way to jump directly to a section — only linear
   reading. Fixed by changing `.section-label` from `<div>` to `<h2>`
   everywhere (in `index.html`'s seven static sections and `renderPanel()`'s
   dynamically-rendered ones) — a purely semantic change, zero visual risk,
   since every layout/typography property `.section-label` needs is already
   explicit in its own CSS rule. Also gave each `<section>` an accessible
   name via `aria-labelledby` pointing at its heading, and marked the
   decorative "01"/"02" numbering `aria-hidden="true"` (standard practice,
   though this session's own accessibility-tree inspection tool doesn't
   cleanly demonstrate the exclusion the spec calls for — noted as a tool
   limitation, not evidence the markup is wrong, the same kind of
   testing-environment nuance earlier passes in this avenue also hit and
   correctly reasoned through rather than took as a bug). Verified live
   that dynamically-updated headings (`#podium-label`/`#table-label`,
   rewritten by `app.js` on every telescope switch) still update correctly
   inside their new `<h2>` wrapper, and that a real panel (Jackson's
   CAPABILITY AREAS) renders its own heading/`aria-labelledby` pair
   correctly. A seventh pass checked the sequence a keyboard user has to Tab
   through before reaching anything, not the content itself: the header nav
   (5 tabs + REFRESH) plus the telescope switcher strip is 13 focusable
   stops on every single visit before reaching any real content — exactly
   the repeated-block problem WCAG 2.4.1 (Bypass Blocks) exists for, and no
   skip link existed at all. Added one as the very first element in `<body>`
   (`<a href="#main-content" class="skip-link">`), positioned off-screen by
   default and pulled into view only on `:focus` so it's invisible to sighted
   mouse users but reachable and visible to keyboard users, jumping to a
   `<main id="main-content" tabindex="-1">`. This session's browser pane hit
   a real tooling wall verifying it live — `document.visibilityState` got
   stuck `"hidden"` (survived a reload, a resize, and re-fronting the tab),
   which blocked genuine keyboard-driven `Tab`/`Enter` dispatch entirely, a
   different failure mode from the scripted-`.focus()` quirks earlier passes
   hit. Routed around it with a still-legitimate test: dispatched a real
   `.click()` on the anchor itself (exercising the browser's actual,
   spec-defined fragment-navigation behavior, not a workaround) and
   confirmed it moved both scroll position and `document.activeElement` to
   `#main-content` — and, unexpectedly useful, confirmed `:focus-visible`
   *does* match and apply the intended outline in this case, since browsers
   specifically treat focus-following-a-link as visible-worthy regardless of
   input device, unlike a bare scripted `element.focus()` call. Seven passes
   so far; worth a re-check if a new custom control, color, animation, or
   top-level section is ever added.

`static/app.js`'s pure/DOM-free logic has real coverage — `tests_js/`, using
Node's built-in `node:test`/`node:vm` (already on the machine, zero npm
install, zero package.json) to sandbox-extract the actual functions out of
the shipped file rather than a hand-copied duplicate. Covers the format
helpers, the panel sort comparator (`sortPanelRows`), the score-breakdown
tooltip builder (`buildTip`/`fmtRaw`), the watchlist persistence functions
(`loadWatchlist`/`saveWatchlist`/`isWatched`/`toggleWatch`, via a tiny
`FakeStorage` stand-in for `localStorage` — not a DOM shim, just its
four-method interface), and the main table's filter+sort pipeline
(`filtered()`). Every top-level function in `app.js` has been read and
classified at this point: what's DOM-free is tested; the rest (`render*`,
event wiring, `positionTip`/`hideTip`/`wireTipDelegation`, `podium()` —
anything touching `document`/`fetch`/`window`) genuinely needs a live
document and stays the dependency-adding decision this loop shouldn't make
unilaterally — surface a real DOM-shim framework as an option rather than
starting it, if it ever comes to that. Two vm-testing gotchas are
documented inline (in `panel_and_tip.test.js` and `watchlist_and_filter.
test.js`) for whoever extends this next: values built by code running
*inside* a vm context belong to that context's own realm, so `assert.
deepEqual`/`deepStrictEqual` rejects them against same-realm literals even
when every element matches — read arrays out with `Array.from(x, mapper)`
and compare objects via `JSON.stringify` instead of comparing vm-realm
values directly.

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
