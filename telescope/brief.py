"""One digest across every enabled telescope — the morning brief.

Composition here is pure and side-effect free: it reads each enabled
telescope's already-cached board and already-recorded events, never forcing
a fresh sweep, so building a brief is always cheap regardless of how
expensive an individual telescope's own collect() is. telescope/notifier.py's
dispatch_brief() is what actually pushes the rendered text anywhere.
"""
import time

from telescope import registry

DEFAULT_WINDOW_HOURS = 24
TOP_EVENTS_PER_SCOPE = 3


def build(since=None, hours=DEFAULT_WINDOW_HOURS):
    """{"generated_at", "since", "sections": [...]} across every enabled
    telescope. `since` overrides `hours` when given explicitly."""
    now = time.time()
    since = since if since is not None else now - hours * 3600
    sections = []
    for slug in registry.enabled_slugs():
        try:
            scope = registry.get(slug)
        except KeyError:
            continue
        try:
            rows = scope.rank(scope.collect(force=False))
        except Exception:
            rows = []
        leader = rows[0] if rows and rows[0].get("score") is not None else None
        events = [
            e for e in scope.store.load_events(limit=100)
            if (e.get("ts") or 0) >= since
        ]
        sections.append({
            "slug": slug,
            "name": scope.name,
            "glyph": scope.glyph,
            "tagline": scope.tagline,
            "entity_label": scope.entity_label,
            "leader": (
                {"name": leader["name"], "score": leader["score"]}
                if leader else None
            ),
            "event_count": len(events),
            "top_events": [
                {"headline": e["headline"], "type": e.get("type")}
                for e in events[:TOP_EVENTS_PER_SCOPE]
            ],
        })
    return {"generated_at": now, "since": since, "sections": sections}


def render_text(digest):
    """A plain-text rendering suitable for Discord, Slack, or stdout."""
    lines = ["🔭 THE OBSERVATORY — MORNING BRIEF"]
    if not digest["sections"]:
        lines.append("No telescopes enabled.")
        return "\n".join(lines)
    for s in digest["sections"]:
        lead = (
            f"{s['leader']['name']} ({s['leader']['score']})"
            if s["leader"] else "no leader yet"
        )
        lines.append(
            f"\n{s['glyph']} {s['name']} — {lead} · "
            f"{s['event_count']} new event(s)"
        )
        for e in s["top_events"]:
            lines.append(f"  • {e['headline']}")
    return "\n".join(lines)
