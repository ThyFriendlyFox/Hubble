"""Shared HTTP helpers for every telescope's fetchers.

One place to set the User-Agent, timeouts and retry/backoff policy so a flaky
source degrades politely instead of taking a whole sweep down.
"""
import html
import re
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
        except requests.RequestException as e:
            last = e
            time.sleep(BACKOFF ** attempt)
            continue
        # 429/5xx are worth retrying; 4xx otherwise is a real answer — a 404
        # means "no such resource", not "try again", so it must not fall into
        # the same retry loop as a rate limit. (It used to: raise_for_status()
        # raising here was caught by the broad except below and retried three
        # times with backoff, which is pure waste for an expected-common case
        # like guessing a job-board slug that doesn't exist.)
        if r.status_code == 429 or r.status_code >= 500:
            last = SourceError(f"{r.status_code} from {url}")
            time.sleep(BACKOFF ** attempt)
            continue
        try:
            r.raise_for_status()
        except requests.RequestException as e:
            raise SourceError(f"{r.status_code} from {url}: {e}") from e
        return r
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


def try_text(url, default=None, **kw):
    """try_json's counterpart for sources that don't speak JSON — arXiv's
    Atom feed, SEC's XML. Still gets the full retry/backoff policy: unlike
    probe_text's speculative guesses, these are known-good APIs having an
    occasional bad moment, which is exactly what retrying is for."""
    try:
        return get_text(url, **kw)
    except SourceError:
        return default


def xml_tag(text, name):
    """First <name>...</name> match's text content, entity-decoded.

    Lifted out of kepler.py and simons.py, which each had their own copy of
    this exact regex extraction -- both parse SEC XML (Form D's
    primary_doc.xml, 13F's informationTable) that legitimately escapes
    special characters in text content (a raw "&" is illegal XML), so
    "AT&T" round-trips as "AT&amp;T". Neither original copy decoded that
    back, which only ever looked like a cosmetic "&amp;" instead of "&" --
    until the frontend started HTML-escaping free text correctly, at which
    point the un-decoded entity became a visible double-escaped glyph.
    """
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.S | re.I)
    return html.unescape(m.group(1).strip()) if m else None


def probe_text(url, timeout=6, headers=None):
    """A single-attempt, no-retry text fetch for speculative lookups where
    most guesses are expected to be wrong — e.g. guessing a company's domain
    name from its filed name. get_text/try_json retry 429/5xx three times
    with backoff because that's correct for a known-good API having a bad
    moment; applying that same policy to a guess that's dead 9 times out of
    10 (dead DNS, expired cert, connection timeout) would turn a batch of
    guesses into minutes of pure waste. Returns None on any failure."""
    h = dict(UA)
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None
