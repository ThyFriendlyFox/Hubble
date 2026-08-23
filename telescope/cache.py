"""Namespaced TTL disk cache. Each telescope gets its own directory."""
import json
import os
import threading
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
        # One lock per cache key, not one for the whole instance -- see
        # _lock_for()'s docstring for why a single instance-wide lock was a
        # real bug, found live, not just a theoretical concern. `_locks_guard`
        # only protects creating a new per-key Lock object; it's never held
        # while a per-key lock itself is held, so it can't become a second
        # instance-wide bottleneck.
        self._locks = {}
        self._locks_guard = threading.Lock()

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
        # tmp includes pid+thread id: a background poller sweep and a
        # manual refresh can legitimately race on the same key (e.g. a
        # ?refresh=1 request's announce-sweep thread landing mid-poll), and
        # a shared ".tmp" name meant the loser's os.replace() found its own
        # tmp file already consumed by the winner -- FileNotFoundError, not
        # a stale-cache problem this class otherwise guards against.
        os.makedirs(self.dir, exist_ok=True)
        p = self._path(key)
        tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
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

    def _lock_for(self, key):
        """The Lock guarding `key` specifically, creating it on first use.

        Used to be one `self._lock` shared by every key in the namespace --
        found live, not in a test, to be a real bug: Holmdel's OpenAlex
        source has a 12h TTL and, independently, is known to sometimes hang
        for tens of seconds mid-response rather than fail fast with a 429/503
        (both already handled gracefully elsewhere). The instant openalex's
        cache aged past 12h, the next request for the Holmdel board called
        cached("openalex", ...), which held the *whole-instance* lock for as
        long as that hung fetch took -- and every other source's cached()
        call for Holmdel, even ones with a perfectly fresh, instant cache
        hit (github, arxiv, hn, wikipedia, npm, events), blocked behind it
        too, since they all shared the same lock. A real user's board load
        hung for minutes waiting on data it didn't even need. Per-key
        locking keeps the actual protection this class exists for (below)
        while letting unrelated keys proceed independently -- a cache hit on
        one key should never wait on a cold, slow miss on another.
        """
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
        return lock

    def cached(self, key, ttl, producer, is_empty=None):
        """get-or-produce. `producer` is only called on a miss.

        A transient total outage (every request in the batch failed) should
        not overwrite a still-usable stale cache with a blackout that then
        gets served as truth for the rest of `ttl` — up to 12h for some
        telescopes. Pass `is_empty(fresh) -> bool` to opt in: if the fresh
        result looks like a total failure and an older file still exists on
        disk, that stale-but-real value is served instead and the miss is
        retried on the next call rather than written over.

        Whole-method lock (per key -- see _lock_for()), not just around the
        write: found live, not in a test — two concurrent requests hitting
        the same telescope with a cold cache (a real scenario: the poller
        and a page load, or two browser tabs, landing close together) each
        independently ran the full producer, in parallel, for every source.
        For Holmdel that means two entire 6-source sweeps racing each other
        against GitHub's strict 10 req/min limit and arXiv's own
        undocumented throttle at once — each one making the other's
        rate-limiting worse, not just wasting the redundant requests. The
        second caller now blocks on the lock instead of piling on;
        re-checking get() after acquiring it (not just once at the top) is
        what makes that block turn into a cache hit instead of a second
        redundant producer() call once the first caller's result has landed.
        """
        with self._lock_for(key):
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
