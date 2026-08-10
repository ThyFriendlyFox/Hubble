"""The observatory's feature roadmap, as data.

Kept here rather than only in Markdown so the dashboard can render it live at
the ROADMAP tab — the roadmap is part of the product, not a side document.
`status` is one of: shipped | building | next | later.
"""

PHASES = [
    {
        "title": "PHASE 0 · THE KERNEL",
        "status": "shipped",
        "note": "Hubble's pipeline extracted into a domain-agnostic core. "
                "Adding a telescope now costs a source adapter plus a config "
                "block, not a rewrite.",
        "items": [
            {"name": "telescope/ kernel package", "done": True,
             "detail": "cache, http, ranking, events, snapshots, notifier, registry"},
            {"name": "Declarative signals", "done": True,
             "detail": "each telescope's sliders and score breakdown generate themselves"},
            {"name": "Declarative event rules", "done": True,
             "detail": "NewLeader / NewEntrant / Climber / Delta / Threshold shapes"},
            {"name": "Per-telescope toggles", "done": True,
             "detail": "persisted to data/observatory.json; disabled scopes never fetch or poll"},
            {"name": "Generic dashboard", "done": True,
             "detail": "table, podium and sliders rendered from telescope metadata"},
            {"name": "Merged observatory feed", "done": True,
             "detail": "one newest-first stream across every enabled telescope"},
            {"name": "Per-telescope poll cadence", "done": True,
             "detail": "Hubble 6h, Jackson 24h — slow data isn't swept hourly"},
        ],
    },
    {
        "title": "PHASE 1 · THE FIRST FOUR INSTRUMENTS",
        "status": "shipped",
        "note": "All four run on live public data with no API keys.",
        "items": [
            {"name": "🔭 Hubble · AI", "done": True,
             "detail": "HuggingFace + OpenRouter + Artificial Analysis + arena"},
            {"name": "🛡 Jackson · Defense", "done": True,
             "detail": "USAspending obligations, 12m vs prior 12m, + PSC capability panel"},
            {"name": "💰 Simons · Capital", "done": True,
             "detail": "FRED + Yahoo + CoinGecko, ranked by abnormality not opinion"},
            {"name": "🪐 Kepler · Startups", "done": True,
             "detail": "SEC Form D raise detection + HN attention + stealth flagging"},
            {"name": "Live smoke tests", "done": True,
             "detail": "tests/ hits every real source and asserts on shape, not fixtures"},
        ],
    },
    {
        "title": "PHASE 2 · THE REMAINING INSTRUMENTS",
        "status": "shipped",
        "note": "All six telescopes now run. Holmdel shipped without a research "
                "source: every free scholarly API is unusable from this "
                "deployment, so it reads attention and adoption and says so "
                "rather than faking scholarship.",
        "items": [
            {"name": "📡 Holmdel · Ideas", "done": True,
             "detail": "HN + Wikipedia + npm velocity over a 32-topic curated watchlist"},
            {"name": "🚛 Reddington · Logistics", "done": True,
             "detail": "FRED freight volume/cost + freight-sector equities, index level"},
            {"name": "Shared series kernel", "done": True,
             "detail": "telescope/series.py — Simons and Reddington share adapters and analytics"},
            {"name": "Small-denominator gate", "done": True,
             "detail": "Holmdel reports no growth below 12 stories rather than a loud +500%"},
        ],
    },
    {
        "title": "PHASE 2B · UNBLOCKING HOLMDEL",
        "status": "next",
        "note": "Holmdel's research blindness is an access problem, not a "
                "design one. Each item below restores a source that exists but "
                "is unreachable from this deployment.",
        "items": [
            {"name": "OpenAlex research velocity", "done": True,
             "detail": "no key needed — the prior 429s were the sandboxed deployment's "
                       "shared egress IP, not OpenAlex itself; restores paper_growth "
                       "and paper_recent to Holmdel on the same quoted-phrase, "
                       "two-window pattern as its HN signal"},
            {"name": "arXiv preprint velocity", "done": True,
             "detail": "shipped via the simple REST query API, not OAI-PMH — its "
                       "documented ~1-req/3s etiquette turned out to make the bulk-"
                       "harvest protocol unnecessary. But a second, undocumented "
                       "limit showed up under real sustained load that a short burst "
                       "test didn't reveal: a full 32-topic sweep started drawing "
                       "429s about twenty requests in and stayed throttled for the "
                       "rest of that sweep, clearing again a few minutes later. Topic "
                       "order now rotates by day so the same topics aren't always "
                       "the ones that land before the throttle. Kept as its own "
                       "signal rather than summed with OpenAlex's: OpenAlex already "
                       "indexes arXiv, so combining counts would double-count the "
                       "same papers. This was the highest-value item left standing "
                       "precisely because OpenAlex has been quota-exhausted for "
                       "several sweeps running — arXiv gives Holmdel a working "
                       "research-adjacent signal again right now, and a second "
                       "CrossoverRule (arxiv_growth -> repo_growth) keeps "
                       "crossing_over observable through either scholarly surface "
                       "being down independently of the other. Verifying this also "
                       "caught a real bug in the *first* CrossoverRule, latent since "
                       "it shipped: it never required the lagging signal to be "
                       "newly elevated, so once arxiv_growth supplied a real "
                       "non-None leading value, an unchanged snapshot compared "
                       "against itself started firing every time — fixed in "
                       "telescope/events.py, generic, not Holmdel-specific"},
            {"name": "GitHub repo velocity", "done": True,
             "detail": "new-repo creation velocity + peak stars, 180-day window vs "
                       "prior, on the same quoted-phrase pattern as HN/OpenAlex. The "
                       "real limit turned out stricter than first read: GitHub's "
                       "*search* endpoint caps at 10 req/min unauthenticated, not the "
                       "~60/hour of its other APIs — a full 32-topic sweep now takes "
                       "several minutes, paced accordingly. Matches repo name and "
                       "description only, not READMEs"},
            {"name": "Holmdel crossover event", "done": True,
             "detail": "new kernel rule shape, CrossoverRule in telescope/events.py — "
                       "generic, not Holmdel-specific: fires when a leading field was "
                       "elevated in the PREVIOUS snapshot and a different, lagging "
                       "field is elevated in the CURRENT one. Wired to paper_growth "
                       "(leading) -> repo_growth (lagging) for 'research becomes "
                       "builders'. Deliberately doesn't require the leading signal to "
                       "have cooled — a two-point diff can't honestly tell 'declining' "
                       "from 'still high'. Couldn't observe a real live firing this "
                       "iteration (paper_growth is still None while OpenAlex's quota "
                       "is exhausted), so verified end-to-end against a real Holmdel "
                       "row with realistic values patched in for the still-down signal"},
            {"name": "Topic auto-discovery", "done": False,
             "detail": "graduate from curated watchlist to embedding-cluster resolution"},
            {"name": "Semantic Scholar key", "done": False,
             "detail": "still blocked without one — its unauthenticated quota is a "
                       "small pool shared globally by every unkeyed caller, confirmed "
                       "not a proxy artifact by retrying locally with backoff"},
        ],
    },
    {
        "title": "PHASE 3 · MAKING THE SIGNAL SHARPER",
        "status": "next",
        "note": "Everything here improves telescopes that already exist.",
        "items": [
            {"name": "Kepler entity resolution", "done": True,
             "detail": "a guessed '.com' domain per issuer, verified against the "
                       "homepage's own <title> before being trusted — load-bearing, "
                       "not a formality: verifying this live, a random real filer's "
                       "guessed domain resolved to an unrelated squatted gambling "
                       "site. When a domain resolves, HN attention switches from "
                       "fuzzy title-text search to checking the story's own linked "
                       "URL — a precision/recall trade, not a strict upgrade: it "
                       "catches a company's own launch posts more reliably but "
                       "misses third-party news coverage linking to a news site "
                       "instead. Kepler takes that trade because a false 'no "
                       "attention' costs nothing (renormalises away) while a false "
                       "'attention found' is the exact failure this instrument "
                       "exists to avoid. Only 12/220 issuers resolved a domain this "
                       "sweep — most pre-launch filers simply have no live site yet"},
            {"name": "Kepler hiring signal", "done": True,
             "detail": "open-role counts from Greenhouse/Lever/Ashby, join key is a "
                       "guessed slug from the company name — not a real identifier "
                       "like Form D's CIK. Greenhouse hits are verified against the "
                       "board's own stated company name; Lever/Ashby have no such "
                       "check, so those rest on slug distinctiveness alone. Verified "
                       "live (MedRhythms, Inc. -> Lever, 8 open clinical roles, "
                       "confirmed genuine not a collision) and checked SAM.gov as an "
                       "alternative for the next item down — it 404s without a "
                       "registered API key, same blocked-without-a-key category as "
                       "Semantic Scholar. Also fixed a kernel bug found in the "
                       "process: telescope/http.py retried a plain 404 three times "
                       "with backoff before giving up, wasting ~2.5s per guess on "
                       "an expected-common case"},
            {"name": "Jackson solicitations", "done": False,
             "detail": "SAM.gov opportunities as a leading indicator ahead of "
                       "obligations — confirmed blocked without a registered API "
                       "key (404 on the public search path), same category as "
                       "Semantic Scholar, not attempted further without one"},
            {"name": "Jackson tech-area board", "done": True,
             "detail": "the CAPABILITY AREAS panel now has a real blended score "
                       "(scale + momentum, equally weighted, log-scaled scale), not "
                       "just a table sorted by raw obligations — reassessed the "
                       "item rather than carry forward the earlier 'needs a bigger "
                       "multi-board architecture' assumption: telescope.ranking."
                       "score() was never actually tied to Telescope.rank()'s one-"
                       "board-per-class assumption, it just takes rows/signals/"
                       "weights, so a panel can be genuinely scored by calling it "
                       "directly, no kernel change needed. Also generalised panel "
                       "sortability while here — every telescope's every panel can "
                       "now be sorted by clicking any column header, client-side, "
                       "independent per panel — since 'rankable' should mean more "
                       "than one fixed order. Verified against real, current data: "
                       "the new score re-ranks Guided Missiles ($26.4B, +53.2% "
                       "growth) above Aircraft, Fixed Wing ($34.5B, +16.7%), which "
                       "a raw-amount sort never would have; confirmed clicking a "
                       "column header re-sorts correctly and independently per "
                       "panel, and that switching telescopes resets sort state so "
                       "it can't bleed into a differently-shaped panel"},
            {"name": "Jackson · unmapped defense startups", "done": True,
             "detail": "cross-telescope join, not a new source — Jackson's obligations "
                       "board can only see companies that already hold a DoD contract; "
                       "a new panel reads Kepler's already-cached Form D feed (never "
                       "forces it to refresh) and keeps filings whose name or industry "
                       "reads defense/dual-use. Keyword-on-name only, so it misses "
                       "deliberately-named stealth companies (Anduril doesn't say "
                       "'defense' anywhere) and is often empty — obvious-by-name "
                       "defense filers are rare in any 12-day window. Needed a kernel "
                       "change: telescope.panels() so a telescope can return more than "
                       "one secondary board (Jackson now has two)"},
            {"name": "Simons 13F whale tracking", "done": True,
             "detail": "new WHALE MOVES panel — quarter-over-quarter position deltas "
                       "across a curated watchlist of 11 large filers (Berkshire, "
                       "Renaissance, Citadel, and 8 more, CIKs confirmed live against "
                       "EDGAR's company search, not from memory). Real complexity "
                       "assessed with a research pass before committing to it: the "
                       "informationTable XML filename is filer-chosen, not fixed "
                       "like Form D's primary_doc.xml, so each filing needs its own "
                       "index lookup first; a large filer can split one security "
                       "across several manager rows (Berkshire's subsidiary "
                       "'otherManager' breakdown, confirmed live, not hypothetical), "
                       "so positions are summed by CUSIP within a filing before ever "
                       "comparing quarters. 45-day filing lag stated plainly on the "
                       "panel itself. Verified against real, current data: Berkshire "
                       "trimming Apple and American Express while adding Alphabet "
                       "and Occidental, Citadel cutting Tesla/Nvidia while adding "
                       "Micron/SanDisk — all matching real, independently "
                       "corroborated portfolio moves, not just internally consistent "
                       "numbers. Lifted a `money()` formatter into telescope/events.py "
                       "along the way: Jackson, Kepler and now Simons each had an "
                       "identical private copy"},
            {"name": "Backfill history", "done": True,
             "detail": "new Telescope.historical_rows() hook: reconstructs a real "
                       "one-period-ago baseline from data a telescope already fetched "
                       "for its own growth math (a prior-window value, never "
                       "fabricated), so the very first sweep can diff against "
                       "something instead of announcing nothing until a second live "
                       "sweep — a full day away at a 24h poll cadence. Wired into "
                       "Holmdel and Jackson, which already carry the needed prior "
                       "fields; Kepler's Form D filings are discrete point-in-time "
                       "events with nothing to reconstruct, so it correctly gets "
                       "none — the honest default, not a gap to fill. Verified "
                       "against real cached data, not synthetic: simulating "
                       "Holmdel's and Jackson's first-ever sweep produced 28 and 41 "
                       "real events respectively (AI Agents repos +248%, Dynetics "
                       "obligations +127%, both matching numbers already visible on "
                       "the live boards). That verification also surfaced a real, "
                       "unrelated bug — a None field rendered as the literal text "
                       "'None' in a headline — fixed in telescope/events.py"},
            {"name": "Per-signal explanations", "done": True,
             "detail": "hover a score to see raw value, normalised value, weight "
                       "and point contribution per signal, plus why a row was "
                       "dampened if it was — generic across all six telescopes"},
        ],
    },
    {
        "title": "PHASE 4 · THE OBSERVATORY LAYER",
        "status": "later",
        "note": "Where a fleet beats a collection: cross-telescope joins.",
        "items": [
            {"name": "Cross-telescope joins · Jackson SBIR → Kepler candidate",
             "done": False,
             "detail": "still blocked — Jackson's own SBIR fetch has been "
                       "unreliable (429/403) since HANDOFF flagged it, worse than "
                       "'rate-limits hard'; not worth building a join on top of a "
                       "source that unreliable"},
            {"name": "Cross-telescope joins · Holmdel crossover → Kepler sector",
             "done": True,
             "detail": "new SECTOR HEAT panel on Kepler — reads Holmdel's already-"
                       "recorded crossing_over events (never forces a Holmdel "
                       "sweep) and surfaces Kepler issuers whose SEC industry maps "
                       "to that topic's field, via a hand-curated, stated-"
                       "approximate mapping (SEC's finite industry list has no "
                       "'aerospace' or 'security' category at all). Verified "
                       "against real fired events, not synthetic — crossing_over "
                       "had actually fired twice by this iteration (Mechanistic "
                       "Interpretability, World Models, both group=AI), and the "
                       "panel correctly surfaced 15 real Kepler issuers in "
                       "AI-adjacent industries. Caught and fixed a real attribution "
                       "bug during verification: when two topics map to the same "
                       "industry, the panel was crediting only whichever topic "
                       "happened to be inserted first instead of both"},
            {"name": "Saved views / theses", "done": True,
             "detail": "name the current weighting, click it later to reapply — "
                       "persisted client-side (localStorage), scoped per telescope, "
                       "not round-tripped through the server: a slider config is "
                       "meaningless without the browser that set it. Deliberately "
                       "picked as the next item over the two remaining Phase 3 "
                       "options (Jackson tech-area board needs a bigger multi-board "
                       "ranking architecture; Jackson solicitations is blocked on a "
                       "SAM.gov key) after a run of iterations spent on flaky "
                       "external APIs (OpenAlex still down, arXiv throttling, "
                       "GitHub's strict limit, SAM.gov and Semantic Scholar both "
                       "key-gated) — a purely internal feature with zero external "
                       "dependency and full in-browser verifiability. Verified via "
                       "direct DOM/JS inspection end-to-end: save, persist across a "
                       "real page reload, apply (weights update to the exact saved "
                       "values), scope isolation (a view saved on Holmdel does not "
                       "appear on Kepler), delete"},
            {"name": "Watchlists + alerts", "done": True,
             "detail": "star any row (any telescope) to watch it — persisted "
                       "client-side, same reasoning as saved views: no user "
                       "account for a server to attach it to, meaningless without "
                       "the browser that set it. 'Alerts' means surfacing what's "
                       "already in the merged event feed for starred entities, not "
                       "a push mechanism — there's no delivery channel to push "
                       "through when nobody's looking. A WATCHED ONLY filter on "
                       "the board and a separate one on the feed, watched feed "
                       "items get a left-border highlight and a star marker. "
                       "Picked over the two remaining Phase 3 items for the same "
                       "reason saved views was: zero external dependency, full "
                       "in-browser verifiability, after a run of iterations on "
                       "flaky external APIs. Verified end-to-end via direct DOM/JS "
                       "inspection: toggle a star, confirm persistence across a "
                       "real page reload, confirm the board's WATCHED ONLY filter "
                       "shows exactly the starred row, confirm the merged feed "
                       "correctly flags and filters to the one real event "
                       "matching a starred entity, confirm scope isolation (a "
                       "watch on Holmdel doesn't leak into Kepler)"},
            {"name": "Morning brief", "done": True,
             "detail": "new telescope/brief.py composes one digest across every "
                       "enabled telescope (current leader + recent events), reading "
                       "already-cached boards and already-recorded events only — "
                       "never forces a sweep, so it's always cheap regardless of "
                       "how expensive any one telescope's own collect() is. A new "
                       "background scheduler (OBSERVATORY_BRIEF_HOURS, default 24) "
                       "dispatches it through the same Discord/Slack channels "
                       "regular events already use (both genuinely wired — real "
                       "webhook POSTs gated on env vars, not stubs — confirmed by "
                       "reading notifier.py before building on it). No real webhook "
                       "is configured in this environment, so verified two ways "
                       "instead of live delivery: a new BRIEF tab renders the same "
                       "digest in-browser with a SEND NOW button hitting the same "
                       "dispatch path the schedule uses, and calling dispatch_brief() "
                       "directly (bypassing Flask's stdout buffering, which delayed "
                       "the print in the dev server's own log tail) confirmed the "
                       "exact digest text logs correctly. Verified against real, "
                       "current data across all six telescopes at once — including "
                       "Hubble's actual current #1 model and Holmdel's 39 real "
                       "accumulated events, not synthetic data"},
            {"name": "Channels", "done": False,
             "detail": "Discord and Slack are wired; X needs credentials"},
            {"name": "Signal-quality feedback", "done": True,
             "detail": "new kernel rule shape, FlagFlipRule in telescope/events.py — "
                       "generic, not Kepler-specific: fires when a boolean field "
                       "flips between two snapshots. Wired to Kepler's stealth flag "
                       "(True -> False only, the one direction that's possible: an "
                       "HN story appearing is permanent, a filing's economics never "
                       "change) as a GRADUATED event — the exact moment a detection "
                       "made with zero public footprint gets its first public "
                       "confirmation, which is what 'did Kepler beat the intro' is "
                       "actually asking. No real graduation has happened yet in this "
                       "deployment's own history — checked the accumulated snapshot "
                       "history first rather than assume (only 2 real snapshots "
                       "exist so far, zero flips) — so verified against a real "
                       "issuer row with the field patched to simulate the "
                       "transition, the same approach used for crossing_over before "
                       "it had ever fired for real. Confirmed the generic diff-"
                       "roundtrip test doesn't spuriously fire it on unrelated "
                       "changes"},
        ],
    },
]


def as_markdown():
    """Render the same data as Markdown for ROADMAP.md."""
    out = ["# 🔭 Observatory — feature roadmap", ""]
    out.append("_Generated from `roadmap.py`, which also backs the dashboard's "
               "ROADMAP tab._")
    out.append("")
    for p in PHASES:
        done = sum(1 for i in p["items"] if i["done"])
        out.append(f"## {p['title']}  ·  `{p['status']}`  ({done}/{len(p['items'])})")
        out.append("")
        if p.get("note"):
            out.append(f"> {p['note']}")
            out.append("")
        for i in p["items"]:
            box = "x" if i["done"] else " "
            detail = f" — {i['detail']}" if i.get("detail") else ""
            out.append(f"- [{box}] **{i['name']}**{detail}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    print(as_markdown())
