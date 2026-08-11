"""Namespaced TTL disk cache. Each telescope gets its own directory."""
import json
import os
import time

ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Cache:
    def __init__(self, namespace):
        # Directory creation is deferred to set() rather than done here:
        # every telescope constructs a Cache on import (via Telescope.__init__),
        # so an eager makedirs() here means merely instantiating one — even a
        # short-lived test double whose .dir gets redirected before any real
        # read or write — leaves a stray empty namespace folder in the real
        # data/ directory. get()/age() already tolerate a missing directory
        # (os.path.exists on a path under it just returns False), so only
        # set() actually needs it to exist.
        self.dir = os.path.join(ROOT, namespace)

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
        os.makedirs(self.dir, exist_ok=True)
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

    def cached(self, key, ttl, producer, is_empty=None):
        """get-or-produce. `producer` is only called on a miss.

        A transient total outage (every request in the batch failed) should
        not overwrite a still-usable stale cache with a blackout that then
        gets served as truth for the rest of `ttl` — up to 12h for some
        telescopes. Pass `is_empty(fresh) -> bool` to opt in: if the fresh
        result looks like a total failure and an older file still exists on
        disk, that stale-but-real value is served instead and the miss is
        retried on the next call rather than written over.
        """
        hit = self.get(key, ttl)
        if hit is not None:
            return hit
        fresh = producer()
        if is_empty and is_empty(fresh) and os.path.exists(self._path(key)):
            try:
                with open(self._path(key), encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self.set(key, fresh)
