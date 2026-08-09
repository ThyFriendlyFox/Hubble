"""The Observatory — a Flask server hosting every enabled telescope.

Run:  python app.py   ->  http://127.0.0.1:5000

Each telescope is an independently toggleable instrument. Disabled ones are
never fetched and never polled; they show as dark in the dashboard switcher.

Env:
  OBSERVATORY_ENABLED   comma-separated slugs, or "all". Seeds the toggle
                        defaults on first run (default: "hubble").
  OBSERVATORY_POLL      set to "0" to disable background sweeps entirely.
  PORT                  server port (default 5000)
"""
import os
import threading
import time
import traceback

from flask import Flask, jsonify, render_template, request

import roadmap as roadmap_data
from telescope import notifier, registry

app = Flask(__name__)
registry.discover()

POLLING = os.environ.get("OBSERVATORY_POLL", "1") != "0"


# ── pages ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/roadmap")
def api_roadmap():
    return jsonify({"phases": roadmap_data.PHASES})


# ── observatory-level API ────────────────────────────────────────────────
@app.route("/api/observatory")
def api_observatory():
    """Every telescope's identity + toggle state. Never fetches."""
    return jsonify({
        "telescopes": registry.catalog(),
        "load_errors": registry.load_errors(),
        "polling": POLLING,
    })


@app.route("/api/observatory/<slug>/toggle", methods=["POST"])
def api_toggle(slug):
    body = request.get_json(silent=True) or {}
    want = body.get("enabled")
    try:
        if want is None:                       # no explicit state -> flip
            want = not registry.is_enabled(slug)
        now = registry.set_enabled(slug, want)
    except KeyError:
        return jsonify({"error": f"unknown telescope '{slug}'"}), 404
    return jsonify({"slug": slug, "enabled": now})


@app.route("/api/observatory/whats-new")
def api_observatory_feed():
    """One merged, newest-first feed across every enabled telescope."""
    since = request.args.get("since", type=float)
    limit = request.args.get("limit", default=60, type=int)
    events = []
    for slug in registry.enabled_slugs():
        try:
            scope = registry.get(slug)
        except KeyError:
            continue
        for e in scope.store.load_events(since=since, limit=limit):
            e.setdefault("telescope", slug)
            e["telescope_name"] = scope.name
            e["glyph"] = scope.glyph
            events.append(e)
    events.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return jsonify({"events": events[:limit]})


# ── per-telescope API ────────────────────────────────────────────────────
def _parse_weights(raw):
    """'signal:value,signal:value' -> dict."""
    if not raw:
        return None
    out = {}
    for part in raw.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out or None


def _resolve(slug):
    """(telescope, error_response). Enabled telescopes only."""
    if slug not in registry.all_slugs():
        return None, (jsonify({"error": f"unknown telescope '{slug}'"}), 404)
    if not registry.is_enabled(slug):
        return None, (jsonify({"error": f"telescope '{slug}' is disabled",
                               "disabled": True}), 409)
    return registry.get(slug), None


@app.route("/api/telescope/<slug>")
def api_telescope(slug):
    scope, err = _resolve(slug)
    if err:
        return err
    force = request.args.get("refresh") == "1"
    weights = _parse_weights(request.args.get("weights"))
    try:
        payload = scope.view(weights=weights, force=force)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "rows": [],
                        "telescope": scope.meta()}), 502
    if force:
        # A manual refresh is also a sweep: snapshot, diff, announce.
        threading.Thread(
            target=scope.safe_sweep, args=(notifier,), daemon=True
        ).start()
    return jsonify(payload)


@app.route("/api/telescope/<slug>/whats-new")
def api_telescope_feed(slug):
    scope, err = _resolve(slug)
    if err:
        return err
    since = request.args.get("since", type=float)
    limit = request.args.get("limit", default=50, type=int)
    return jsonify({
        "events": scope.store.load_events(since=since, limit=limit),
        "snapshots": scope.store.count(),
        "poll_seconds": scope.poll_seconds,
    })


@app.route("/api/telescope/<slug>/sweep", methods=["POST"])
def api_sweep(slug):
    scope, err = _resolve(slug)
    if err:
        return err
    events = scope.safe_sweep(notifier)
    return jsonify({"events": events, "count": len(events),
                    "error": scope._last_error})


# ── background polling ───────────────────────────────────────────────────
def _poller():
    """One thread; sweeps each enabled telescope when its interval is due.

    Respects per-telescope cadence — Hubble every 6h, Jackson daily — and
    re-reads the toggle state each pass so flipping a switch takes effect
    without a restart.
    """
    last = {}
    while True:
        for slug in registry.enabled_slugs():
            try:
                scope = registry.get(slug)
            except KeyError:
                continue
            due = last.get(slug, 0) + scope.poll_seconds
            if time.time() < due:
                continue
            events = scope.safe_sweep(notifier)
            last[slug] = time.time()
            print(f"[observatory] {slug} sweep · {len(events)} new event(s)")
        time.sleep(60)


def _start_poller():
    # Flask's debug reloader imports this module twice; only poll in the
    # active worker process (or when the reloader is off).
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    threading.Thread(target=_poller, daemon=True).start()


if __name__ == "__main__":
    if POLLING:
        _start_poller()
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
