"""Namespaced TTL disk cache. Each telescope gets its own directory."""
import json
import os
import time

ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Cache:
    def __init__(self, namespace):
        self.dir = os.path.join(ROOT, namespace)
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, key):
        return os.path.join(self.dir, f"{key}.json")

    def get(self, key, ttl):
        """Cached value if present and younger than `ttl` seconds, else None."""
        p = self._path(key)
        if not os.path.exists(p):
            return None
        if ttl <= 0 or time.time() - os.path.getmtime(p) > ttl:
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key, value):
        p = self._path(key)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f)
        os.replace(tmp, p)
        return value

    def age(self, key):
        """Seconds since the cache file was written, or None if missing."""
        p = self._path(key)
        if not os.path.exists(p):
            return None
        return time.time() - os.path.getmtime(p)

    def cached(self, key, ttl, producer):
        """get-or-produce. `producer` is only called on a miss."""
        hit = self.get(key, ttl)
        if hit is not None:
            return hit
        return self.set(key, producer())
