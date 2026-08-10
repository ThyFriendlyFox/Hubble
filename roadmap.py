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
            {"name": "arXiv via OAI-PMH", "done": False,
             "detail": "arXiv's query API works fine unauthenticated with normal "
                       "pacing (~1 req/3s) — bulk OAI-PMH harvest is still worth it "
                       "as a second scholarly surface, not a fix for a block"},
            {"name": "GitHub repo velocity", "done": False,
             "detail": "unblocked — GitHub's unauthenticated search API returns 200 "
                       "locally (60 req/hour ceiling) — add as Holmdel's fourth surface"},
            {"name": "Holmdel crossover event", "done": False,
             "detail": "'research → builders' — papers source now exists; needs a new "
                       "declarative rule shape in telescope/events.py that compares a "
                       "leading signal in the prior snapshot against a lagging one in "
                       "the current snapshot, not just a same-snapshot delta"},
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
            {"name": "Kepler entity resolution", "done": False,
             "detail": "resolve issuers to domains; replaces fuzzy HN name matching"},
            {"name": "Kepler hiring signal", "done": False,
             "detail": "Greenhouse/Lever/Ashby board endpoints — the honest traction metric"},
            {"name": "Jackson solicitations", "done": False,
             "detail": "SAM.gov opportunities as a leading indicator ahead of obligations"},
            {"name": "Jackson tech-area board", "done": False,
             "detail": "promote the PSC panel into a rankable second board"},
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
            {"name": "Simons 13F whale tracking", "done": False,
             "detail": "EDGAR 13F position deltas; 45-day lag stated on the row"},
            {"name": "Backfill history", "done": False,
             "detail": "seed snapshots from historical data so events fire on day one"},
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
            {"name": "Cross-telescope joins", "done": False,
             "detail": "Jackson SBIR award → Kepler candidate; Holmdel crossover → Kepler sector"},
            {"name": "Saved views / theses", "done": False,
             "detail": "name a slider configuration and return to it"},
            {"name": "Watchlists + alerts", "done": False,
             "detail": "per-entity subscriptions rather than board-level events"},
            {"name": "Morning brief", "done": False,
             "detail": "one digest across every telescope, pushed on a schedule"},
            {"name": "Channels", "done": False,
             "detail": "Discord and Slack are wired; X needs credentials"},
            {"name": "Signal-quality feedback", "done": False,
             "detail": "track which detections proved out — did Kepler beat the intro?"},
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
