"""A small, polite, bounded web crawler — the one kernel primitive genuinely
new to this project rather than lifted from an existing telescope.

Every other source in this codebase is a known API endpoint; a crawler is
different in kind; it visits pages nobody asked it to visit by name, so it
has to be trustworthy about *not* doing that carelessly. Three rules, all
non-negotiable regardless of what a domain pack passes in:

  1. Every fetch is checked against that domain's robots.txt first.
  2. Every fetch after the first to the same run sleeps `delay` seconds.
  3. The crawl hard-stops at `max_pages` fetches, full stop — no domain
     pack gets to accidentally request an unbounded crawl.

Uses `telescope.http.try_text()` for the actual fetches, so a crawl inherits
the same UA string, timeout and retry/backoff policy as every other source
in this project — robots.txt is checked against that same UA, not a
generic one, so what's fetched and what's declared are the same identity.
"""
import time
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from telescope.http import UA, try_text

_robots_cache = {}


def _robots_url(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/robots.txt"


def robots_allow(url):
    """Whether this project's own user-agent may fetch `url`, per that
    domain's robots.txt. Fails open only on a missing/unreadable robots.txt
    (no rules to violate); never fails open on a rule that explicitly denies."""
    domain = urlparse(url).netloc
    parser = _robots_cache.get(domain)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        text = try_text(_robots_url(url), default=None)
        if text is None:
            parser.allow_all = True
        else:
            parser.parse(text.splitlines())
        _robots_cache[domain] = parser
    return parser.allow_all or parser.can_fetch(UA["User-Agent"], url)


class _LinkExtractor(HTMLParser):
    """Collects every `<a href>` target, resolved to an absolute URL."""

    def __init__(self, base_url):
        super().__init__()
        self.base_url = base_url
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(urljoin(self.base_url, href))


def _extract_links(html_text, base_url):
    parser = _LinkExtractor(base_url)
    try:
        parser.feed(html_text)
    except Exception:
        # Malformed HTML shouldn't crash a crawl -- whatever links were
        # collected before the parser choked are still real.
        pass
    return parser.links


def crawl(seed_urls, *, max_pages=40, delay=1.0, same_domain_only=True, link_filter=None):
    """Breadth-first crawl from `seed_urls`, returning the directed link
    graph discovered: `{page_url: [linked_url, ...]}`.

    `link_filter(url) -> bool`, if given, decides whether a *discovered*
    link is worth adding to the crawl frontier (queued for its own fetch) --
    it does not affect which links get recorded as edges of an already-
    fetched page, only which of those links get visited next. This is what
    keeps a crawl seeded from a handful of relevant pages from wandering
    into the rest of a large site: every recorded edge is real, but only
    the relevant ones get followed further.

    A page whose own domain's robots.txt disallows it is skipped entirely --
    no fetch, no edges, not counted against `max_pages`.
    """
    seeds = list(dict.fromkeys(seed_urls))  # de-dup, preserve order
    seed_domains = {urlparse(u).netloc for u in seeds}
    queue = list(seeds)
    visited = set()
    graph = {}
    fetched = 0

    while queue and fetched < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if not robots_allow(url):
            continue
        if fetched > 0:
            time.sleep(delay)
        text = try_text(url, default=None)
        fetched += 1
        if text is None:
            graph[url] = []
            continue
        links = _extract_links(text, url)
        graph[url] = links
        for link in links:
            if link in visited or link in queue:
                continue
            if same_domain_only and urlparse(link).netloc not in seed_domains:
                continue
            if link_filter and not link_filter(link):
                continue
            queue.append(link)

    return graph
