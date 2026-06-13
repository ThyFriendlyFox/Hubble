"""Hubble — consolidated 'best LLMs right now' dashboard.

Run:  python app.py   ->  http://127.0.0.1:5000
"""
import os
import threading
import time
import traceback

from flask import Flask, jsonify, render_template, request

import cache
import fetchers
import notifier
import ranking
import snapshots

app = Flask(__name__)
CACHE_TTL = 3600  # 1 hour
POLL_INTERVAL = int(os.environ.get("HUBBLE_POLL_SECONDS", 6 * 3600))  # 6h default


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/models")
def api_models():
    force = request.args.get("refresh") == "1"
    weights = _parse_weights(request.args.get("weights"))
    try:
        models = fetchers.build_unified(ttl=CACHE_TTL, force=force)
    except Exception as e:  # network/source failure — report, don't crash
        traceback.print_exc()
        return jsonify({"error": str(e), "models": []}), 502
    # On a manual refresh, also run change-detection (default-weighted, on a
    # copy so user-weighted scoring below isn't clobbered).
    if force:
        try:
            events = snapshots.record(ranking.score([dict(m) for m in models]))
            notifier.dispatch(events)
        except Exception:
            traceback.print_exc()
    ranked = ranking.score(models, weights)
    return jsonify(
        {
            "models": ranked,
            "count": len(ranked),
            "weights": {**ranking.DEFAULT_WEIGHTS, **(weights or {})},
            "ages": {
                "huggingface": _age("hf"),
                "openrouter": _age("openrouter"),
                "lmarena": _age("lmarena"),
            },
        }
    )


@app.route("/api/whats-new")
def api_whats_new():
    """Detected events, newest first. ?since=<epoch> & ?limit=N optional."""
    since = request.args.get("since", type=float)
    limit = request.args.get("limit", default=50, type=int)
    return jsonify(
        {
            "events": snapshots.load_events(since=since, limit=limit),
            "snapshots": snapshots.count_snapshots(),
            "poll_interval": POLL_INTERVAL,
        }
    )


def _age(key):
    a = cache.age(key)
    return round(a) if a is not None else None


def _parse_weights(raw):
    """weights query is 'sig:val,sig:val' -> dict."""
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


def _refresh_and_detect():
    """Force-fetch every source, score with default weights, detect & dispatch."""
    models = fetchers.build_unified(force=True)
    events = snapshots.record(ranking.score(models))
    notifier.dispatch(events)
    return events


def _poller():
    """Background sweep so detection happens even when nobody's watching."""
    while True:
        try:
            events = _refresh_and_detect()
            print(f"[hubble] sweep complete · {len(events)} new event(s)")
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_INTERVAL)


def _start_poller():
    # With Flask's debug reloader the module imports twice; only run the poller
    # in the active worker process (or when the reloader is off).
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    threading.Thread(target=_poller, daemon=True).start()


if __name__ == "__main__":
    _start_poller()
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
