"""Snapshot history + change detection, namespaced per telescope.

Each sweep saves a slim timestamped snapshot and diffs it against the previous
one, running every rule the telescope declared. Events are appended to the
telescope's events.json (newest first) and are what every output channel — the
dashboard feed, the merged observatory feed, X, Discord — reads from.

Snapshots always score with the telescope's DEFAULT weights so diffs stay
apples-to-apples regardless of how a viewer has the sliders set.
"""
import json
import os
import threading
import time

from telescope.cache import ROOT

KEEP_SNAPSHOTS = 300
MAX_EVENTS = 500

# Fields worth persisting on every telescope; domain packs add their own via
# Telescope.snapshot_fields.
BASE_FIELDS = ("key", "name", "rank", "score", "noise", "sources", "link")


class SnapshotStore:
    def __init__(self, namespace, rules=(), extra_fields=()):
        self.ns = namespace
        self.rules = list(rules)
        self.fields = tuple(BASE_FIELDS) + tuple(extra_fields)
        self.dir = os.path.join(ROOT, namespace, "history")
        self.events_file = os.path.join(ROOT, namespace, "events.json")
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()

    # ── snapshot io ──────────────────────────────────────────────────────
    def _files(self):
        return sorted(
            f for f in os.listdir(self.dir)
            if f.startswith("snapshot-") and f.endswith(".json")
        )

    def count(self):
        return len(self._files())

    def latest(self):
        files = self._files()
        if not files:
            return None
        try:
            with open(os.path.join(self.dir, files[-1]), encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def _slim(self, row):
        return {k: row.get(k) for k in self.fields}

    def _save(self, rows, ts):
        snap = {"ts": ts, "rows": [self._slim(r) for r in rows]}
        path = os.path.join(self.dir, f"snapshot-{int(ts * 1000)}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        os.replace(tmp, path)
        for old in self._files()[:-KEEP_SNAPSHOTS]:
            try:
                os.remove(os.path.join(self.dir, old))
            except OSError:
                pass
        return snap

    # ── events io ────────────────────────────────────────────────────────
    def load_events(self, since=None, limit=50):
        if not os.path.exists(self.events_file):
            return []
        try:
            with open(self.events_file, encoding="utf-8") as fh:
                events = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return []
        if since is not None:
            events = [e for e in events if e.get("ts", 0) > since]
        return events[:limit]

    def _append_events(self, events):
        combined = (events + self.load_events(limit=MAX_EVENTS))[:MAX_EVENTS]
        tmp = self.events_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(combined, fh)
        os.replace(tmp, self.events_file)

    # ── diff ─────────────────────────────────────────────────────────────
    def diff(self, prev, curr_rows, ts):
        if not prev:
            return []
        prev_rows = prev.get("rows", [])
        prev_by = {r["key"]: r for r in prev_rows if r.get("key")}
        events = []

        for rule in self.rules:
            events.extend(rule.board(prev_rows, curr_rows, ts))

        for row in curr_rows:
            if row.get("noise"):
                continue
            p = prev_by.get(row.get("key"))
            for rule in self.rules:
                events.extend(rule.row(p, row, ts))

        for e in events:
            e.setdefault("telescope", self.ns)
        return events

    def _unchanged(self, prev, curr_rows):
        if not prev:
            return False
        a = {r.get("key"): (r.get("rank"), r.get("score")) for r in prev.get("rows", [])}
        b = {r.get("key"): (r.get("rank"), r.get("score")) for r in curr_rows}
        return a == b

    def record(self, rows):
        """Snapshot, diff against the previous, log events. Returns new events."""
        with self._lock:
            ts = time.time()
            prev = self.latest()
            if self._unchanged(prev, rows):
                return []
            events = self.diff(prev, rows, ts)
            self._save(rows, ts)
            if events:
                self._append_events(events)
            return events
