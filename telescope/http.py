"""Shared HTTP helpers for every telescope's fetchers.

One place to set the User-Agent, timeouts and retry/backoff policy so a flaky
source degrades politely instead of taking a whole sweep down.
"""
import time

import requests

# Several government sources (SEC, USAspending) require a descriptive UA with
# contact info and will 403 a generic one.
UA = {
    "User-Agent": "Observatory-Telescope/1.0 (+https://github.com/ThyFriendlyFox/Hubble)",
    "Accept-Encoding": "gzip, deflate",
}
TIMEOUT = 30
RETRIES = 3
BACKOFF = 1.5


class SourceError(RuntimeError):
    """A source failed in a way the caller should surface, not swallow."""


def _request(method, url, headers=None, **kw):
    h = dict(UA)
    if headers:
        h.update(headers)
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.request(method, url, headers=h, timeout=TIMEOUT, **kw)
            # 429/5xx are worth retrying; 4xx otherwise is a real answer.
            if r.status_code == 429 or r.status_code >= 500:
                last = SourceError(f"{r.status_code} from {url}")
                time.sleep(BACKOFF ** attempt)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(BACKOFF ** attempt)
    raise SourceError(f"{url} failed after {RETRIES} attempts: {last}")


def get_json(url, headers=None, **kw):
    return _request("GET", url, headers=headers, **kw).json()


def post_json(url, payload, headers=None, **kw):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return _request("POST", url, headers=h, json=payload, **kw).json()


def get_text(url, headers=None, **kw):
    return _request("GET", url, headers=headers, **kw).text


def try_json(url, default=None, **kw):
    """Best-effort fetch — returns `default` instead of raising.

    For optional sources that enrich a telescope but shouldn't break a sweep.
    """
    try:
        return get_json(url, **kw)
    except (SourceError, ValueError):
        return default
