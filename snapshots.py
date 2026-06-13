"""Snapshot history + change detection for Hubble.

Each sweep we save a slim, timestamped snapshot of the ranked index to
``data/history/`` and diff it against the previous one to emit *events* — the
things worth announcing ("new #1", "new model entered", "big climber", "price
drop"). Events are appended to ``data/events.json`` (newest first) and are what
any output channel (the dashboard feed, an X bot, Discord, RSS) reads from.

Scoring for snapshots always uses Hubble's DEFAULT weights so diffs are
apples-to-apples regardless of how a viewer has the sliders set.
"""
import json
import os
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HIST_DIR = os.path.join(DATA_DIR, "history")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")
os.makedirs(HIST_DIR, exist_ok=True)

_lock = threading.Lock()

# ── tuning ────────────────────────────────────────────────────────────────
NEW_MODEL_MAX_RANK = 150   # only announce new arrivals this high or better
CLIMB_RANK_DELTA = 15      # places gained to count as a "climber"
CLIMB_SCORE_DELTA = 2.5    # or this much blended-score gained
PRICE_DROP_FRAC = 0.25     # completion price must fall by >=25%
KEEP_SNAPSHOTS = 300       # prune older history beyond this
MAX_EVENTS = 500           # cap the events log

SNAP_FIELDS = (
    "key", "name", "rank", "score", "intelligence_index", "coding_index",
    "usage_rank", "arena_elo", "price_completion", "noise", "sources",
)


def _slim(m):
    return {k: m.get(k) for k in SNAP_FIELDS}


# ── snapshot io ───────────────────────────────────────────────────────────
def _snapshot_files():
    return sorted(
        f for f in os.listdir(HIST_DIR)
        if f.startswith("snapshot-") and f.endswith(".json")
    )


def latest_snapshot():
    files = _snapshot_files()
    if not files:
        return None
    try:
        with open(os.path.join(HIST_DIR, files[-1]), encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def count_snapshots():
    return len(_snapshot_files())


def _save_snapshot(models, ts):
    snap = {"ts": ts, "models": [_slim(m) for m in models]}
    path = os.path.join(HIST_DIR, f"snapshot-{int(ts * 1000)}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snap, fh)
    os.replace(tmp, path)
    # prune old history
    files = _snapshot_files()
    for old in files[:-KEEP_SNAPSHOTS]:
        try:
            os.remove(os.path.join(HIST_DIR, old))
        except OSError:
            pass
    return snap


# ── events io ─────────────────────────────────────────────────────────────
def load_events(since=None, limit=50):
    if not os.path.exists(EVENTS_FILE):
        return []
    try:
        with open(EVENTS_FILE, encoding="utf-8") as fh:
            events = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    if since is not None:
        events = [e for e in events if e.get("ts", 0) > since]
    return events[:limit]


def _append_events(events):
    existing = load_events(limit=MAX_EVENTS)
    combined = events + existing            # newest first
    combined = combined[:MAX_EVENTS]
    tmp = EVENTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(combined, fh)
    os.replace(tmp, EVENTS_FILE)


# ── diff engine ───────────────────────────────────────────────────────────
def _ev(kind, m, ts, headline, **extra):
    e = {
        "type": kind,
        "key": m.get("key"),
        "name": m.get("name"),
        "rank": m.get("rank"),
        "score": m.get("score"),
        "ts": ts,
        "headline": headline,
    }
    e.update(extra)
    return e


def _price_str(p):
    return f"${(p or 0) * 1e6:.2f}"


def diff(prev, curr_models, ts):
    """Compare a previous snapshot dict against the current ranked models."""
    if not prev:
        return []
    events = []
    prev_by = {m["key"]: m for m in prev.get("models", [])}

    prev_leader = next((m for m in prev.get("models", []) if m.get("rank") == 1), None)
    curr_leader = next((m for m in curr_models if m.get("rank") == 1), None)
    if curr_leader and (not prev_leader or prev_leader["key"] != curr_leader["key"]):
        events.append(_ev(
            "new_leader", curr_leader, ts,
            f"🔭 New #1 — {curr_leader['name']} takes the top spot "
            f"(score {curr_leader['score']}).",
        ))

    for m in curr_models:
        if m.get("noise"):
            continue
        p = prev_by.get(m["key"])

        # brand-new arrival
        if p is None:
            meaningful = m.get("intelligence_index") is not None or m.get("usage_rank")
            if meaningful and m.get("rank") and m["rank"] <= NEW_MODEL_MAX_RANK:
                events.append(_ev(
                    "new_model", m, ts,
                    f"🔭 New intelligence detected — {m['name']} enters the "
                    f"index at #{m['rank']} (score {m['score']}).",
                ))
            continue

        # big climber (by rank gained or score gained)
        if p.get("rank") and m.get("rank"):
            rank_delta = p["rank"] - m["rank"]
            score_delta = (m.get("score") or 0) - (p.get("score") or 0)
            if rank_delta >= CLIMB_RANK_DELTA or score_delta >= CLIMB_SCORE_DELTA:
                events.append(_ev(
                    "big_climber", m, ts,
                    f"📈 {m['name']} is climbing — up {rank_delta} to "
                    f"#{m['rank']} (score {m['score']}).",
                    rank_delta=rank_delta, score_delta=round(score_delta, 1),
                ))

        # price drop on the completion side
        op, np_ = p.get("price_completion"), m.get("price_completion")
        if op and np_ and np_ < op * (1 - PRICE_DROP_FRAC):
            events.append(_ev(
                "price_drop", m, ts,
                f"💸 {m['name']} got cheaper — {_price_str(op)} → "
                f"{_price_str(np_)} /Mtok.",
                old_price=op, new_price=np_,
            ))

    return events


def _unchanged(prev, curr_models):
    """Skip writing a redundant snapshot if nothing material moved."""
    if not prev:
        return False
    a = {m["key"]: (m.get("rank"), m.get("score")) for m in prev.get("models", [])}
    b = {m["key"]: (m.get("rank"), m.get("score")) for m in curr_models}
    return a == b


def record(models):
    """Snapshot the current ranked models, diff vs the last one, log events.

    Returns the list of newly detected events (possibly empty)."""
    with _lock:
        ts = time.time()
        prev = latest_snapshot()
        if _unchanged(prev, models):
            return []
        events = diff(prev, models, ts)
        _save_snapshot(models, ts)
        if events:
            _append_events(events)
        return events
