"""Tiny disk cache with TTL. Each key is a JSON file under data/."""
import json
import os
import time

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def _path(key):
    return os.path.join(DATA_DIR, f"{key}.json")


def get(key, ttl):
    """Return cached value if it exists and is younger than ttl seconds, else None."""
    p = _path(key)
    if not os.path.exists(p):
        return None
    if time.time() - os.path.getmtime(p) > ttl:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def set(key, value):
    p = _path(key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f)
    os.replace(tmp, p)
    return value


def age(key):
    """Seconds since the cache file was written, or None if missing."""
    p = _path(key)
    if not os.path.exists(p):
        return None
    return time.time() - os.path.getmtime(p)
