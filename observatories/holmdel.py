"""📡 HOLMDEL — a telescope for ideas.

The Holmdel horn antenna had an annoying background hiss its operators spent
a year trying to eliminate. The hiss was the cosmic microwave background — the
biggest discovery of the era, sitting in what everyone had classified as noise.

That's the mission: catch an idea while it still looks like noise. Holmdel
watches a curated watchlist of topics across independent public surfaces and
ranks them by **velocity** — how fast attention is accelerating — rather than
by volume, because by the time something is big it isn't news.

Sources (all public, no keys):
  Hacker News (Algolia)   story velocity and peak score, windowed by date
  Wikipedia pageviews     general curiosity, 60-day window vs the prior 60
  npm registry            builder adoption, where a topic has a canonical package
  OpenAlex                paper velocity, 180-day window vs the prior 180
  GitHub search           new-repo velocity + peak stars, 180-day window vs prior

The cross-source **spread** signal is the honest core: an idea moving on one
surface is a rumour, an idea moving on three is a trend. Topics with only a
single surface reporting are dampened.

── A stated limitation ──────────────────────────────────────────────────────
OpenAlex works unauthenticated from a normal connection (it was rate-limited
from the previous sandboxed deployment's shared egress IP, not blocked in
general) and gives Holmdel its research signal: paper counts matched on the
same quoted topic phrase used against Hacker News, windowed the same way. It
counts works, not citations, so a topic can show volume without yet showing
influence — a citation-weighted signal is a further improvement, not this one.
Semantic Scholar's unauthenticated quota is a small pool shared globally by
every caller without a key, so it stays unusable here regardless of network;
Crossref's query API is an OR match (a quoted three-word topic returns
millions of rows), so its counts are not topic counts either.

GitHub's search API is also unauthenticated-reachable, but its *search*
endpoint carries a much stricter rate limit than the rest of GitHub's API —
10 requests/minute, not the ~60/hour a first check of the wrong bucket
suggested — so a full 32-topic sweep is paced at roughly one request every
6.5 seconds and takes several minutes. It matches on repo name and
description, not README contents, so a topic's actual codebase footprint is
undercounted. Holmdel still tracks a curated watchlist rather than
discovering topics on its own.
"""
import datetime as dt
import time
from dataclasses import dataclass
from urllib.parse import quote, quote_plus

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, CrossoverRule, DeltaRule, NewLeaderRule
from telescope.http import get_json, try_json
from telescope.registry import register

MIN_STORIES = 12        # below this, HN growth is noise and is not reported
WINDOW_DAYS = 90        # HN comparison window
WIKI_DAYS = 60          # Wikipedia comparison window
RESEARCH_DAYS = 180     # OpenAlex comparison window — papers are sparser than posts
MIN_PAPERS = 15         # below this combined count, paper growth is noise
GITHUB_DAYS = 180       # GitHub comparison window — matches the research cadence
MIN_REPOS = 6           # below this combined count, repo growth is noise


@dataclass(frozen=True)
class Topic:
    key: str
    name: str
    group: str
    query: str                 # HN search phrase
    wiki: str = ""             # exact en.wikipedia article title
    npm: str = ""              # canonical npm package, where one exists


# The watchlist. Curated on purpose: ideas have no natural join key, so v1
# tracks a hand-picked universe rather than pretending to auto-discover one.
TOPICS = (
    # ── AI ───────────────────────────────────────────────────────────────
    Topic("agents", "AI Agents", "AI", "AI agents",
          "Intelligent_agent", "langchain"),
    Topic("rag", "Retrieval Augmented Generation", "AI",
          "retrieval augmented generation", "Retrieval-augmented_generation",
          "llamaindex"),
    Topic("ssm", "State Space Models", "AI", "state space model",
          "Mamba_(deep_learning_architecture)"),
    Topic("moe", "Mixture of Experts", "AI", "mixture of experts",
          "Mixture_of_experts"),
    Topic("diffusion", "Diffusion Models", "AI", "diffusion model",
          "Diffusion_model"),
    Topic("worldmodels", "World Models", "AI", "world model",
          "World_model"),
    Topic("mcp", "Model Context Protocol", "AI", "model context protocol"),
    Topic("localllm", "Local Inference", "AI", "llama.cpp",
          "Llama.cpp"),
    Topic("distill", "Distillation & Quantization", "AI",
          "quantization", "Knowledge_distillation"),
    Topic("interp", "Mechanistic Interpretability", "AI",
          "mechanistic interpretability", "Mechanistic_interpretability"),
    # ── BIO ──────────────────────────────────────────────────────────────
    Topic("glp1", "GLP-1", "BIO", "GLP-1",
          "Glucagon-like_peptide-1_receptor_agonist"),
    Topic("crispr", "CRISPR & Gene Editing", "BIO", "CRISPR",
          "CRISPR_gene_editing"),
    Topic("proteins", "Protein Folding & Design", "BIO",
          "AlphaFold", "AlphaFold"),
    Topic("mrna", "mRNA Platforms", "BIO", "mRNA vaccine",
          "MRNA_vaccine"),
    Topic("longevity", "Longevity", "BIO", "longevity",
          "Life_extension"),
    Topic("bci", "Brain-Computer Interfaces", "BIO",
          "brain computer interface", "Brain–computer_interface"),
    # ── ENERGY ───────────────────────────────────────────────────────────
    Topic("ssb", "Solid State Batteries", "ENERGY", "solid state battery",
          "Solid-state_battery"),
    Topic("fusion", "Fusion", "ENERGY", "nuclear fusion",
          "Fusion_power"),
    Topic("smr", "Small Modular Reactors", "ENERGY",
          "small modular reactor", "Small_modular_reactor"),
    Topic("geothermal", "Enhanced Geothermal", "ENERGY",
          "enhanced geothermal", "Enhanced_geothermal_system"),
    Topic("grid", "Grid Storage", "ENERGY", "grid storage",
          "Grid_energy_storage"),
    # ── COMPUTE ──────────────────────────────────────────────────────────
    Topic("quantum", "Quantum Error Correction", "COMPUTE",
          "quantum error correction", "Quantum_error_correction"),
    Topic("photonics", "Silicon Photonics", "COMPUTE", "silicon photonics",
          "Silicon_photonics"),
    Topic("neuromorphic", "Neuromorphic Computing", "COMPUTE",
          "neuromorphic", "Neuromorphic_computing"),
    Topic("riscv", "RISC-V", "COMPUTE", "RISC-V", "RISC-V"),
    Topic("chiplets", "Chiplets & Advanced Packaging", "COMPUTE",
          "chiplet", "Chiplet"),
    # ── SPACE & MATERIALS ────────────────────────────────────────────────
    Topic("smallsat", "Small Satellites", "SPACE", "cubesat",
          "CubeSat"),
    Topic("spacemfg", "In-Space Manufacturing", "SPACE",
          "in-space manufacturing", "Space_manufacturing"),
    Topic("superconductor", "Superconductors", "MATERIALS",
          "superconductor", "Room-temperature_superconductor"),
    Topic("carboncapture", "Carbon Capture", "MATERIALS",
          "direct air capture", "Direct_air_capture"),
    # ── SECURITY ─────────────────────────────────────────────────────────
    Topic("pqc", "Post-Quantum Cryptography", "SECURITY",
          "post-quantum cryptography", "Post-quantum_cryptography"),
    Topic("zk", "Zero-Knowledge Proofs", "SECURITY", "zero knowledge proof",
          "Zero-knowledge_proof"),
)


def _growth(recent, prior, floor=0):
    """Percent change, gated on having enough volume to mean anything.

    Velocity on tiny numbers is the classic trap: 1 -> 6 stories is +500% and
    would top any board, but it is one slow news week. Below `floor` combined
    observations we report no growth signal at all rather than a loud wrong
    one — the kernel renormalises around the gap.
    """
    if prior is None or recent is None:
        return None
    if (recent + prior) < floor:
        return None
    if prior == 0:
        return 100.0 if recent > 0 else 0.0
    return round((recent - prior) / prior * 100, 1)


@register
class Holmdel(Telescope):
    slug = "holmdel"
    name = "HOLMDEL"
    domain = "IDEAS"
    glyph = "📡"
    tagline = "IDEA VELOCITY INDEX"
    entity_label = "TOPICS"
    sources_label = "HACKER NEWS · WIKIPEDIA · NPM · OPENALEX · GITHUB"
    caveat = ("Tracks a curated watchlist, so it can only see ideas someone "
              "already put on the list — it does not discover new topics yet. "
              "Research velocity is OpenAlex work counts on a quoted phrase "
              "match, not citations, so it shows volume, not influence; "
              "Semantic Scholar and arXiv's own APIs remain too rate-limited "
              "unauthenticated to add as a second scholarly surface, and "
              "Crossref's OR-match query API can't produce valid topic "
              "counts. Repo velocity matches on GitHub repo name and "
              "description only, not READMEs, and GitHub's search endpoint's "
              "strict 10-req/min unauthenticated limit means a full sweep "
              "takes several minutes.")

    cache_ttl = 12 * 3600
    poll_seconds = 24 * 3600

    signals = (
        Signal("velocity", "hn_growth", "ATTENTION VELOCITY"),
        Signal("curiosity", "wiki_growth", "CURIOSITY TREND"),
        Signal("research", "paper_growth", "RESEARCH VELOCITY"),
        Signal("traction", "repo_growth", "REPO VELOCITY"),
        Signal("volume", "hn_recent", "STORY VOLUME", log=True),
        Signal("peak", "hn_points", "PEAK STORY", log=True),
        Signal("reach", "wiki_recent", "PUBLIC REACH", log=True),
        Signal("adoption", "npm_downloads", "BUILDER ADOPTION", log=True),
        Signal("papers", "paper_recent", "PAPER VOLUME", log=True),
        Signal("repos", "repo_recent", "NEW REPOS", log=True),
        Signal("stars", "repo_stars", "PEAK REPO STARS", log=True),
        Signal("spread", "spread", "CROSS-SOURCE SPREAD"),
    )
    default_weights = {
        "velocity": 22, "curiosity": 13, "research": 13, "traction": 13,
        "volume": 6, "peak": 3, "reach": 6, "adoption": 4, "papers": 4,
        "repos": 4, "stars": 2, "spread": 10,
    }
    # An idea moving on one independent surface is a rumour. Topics with no
    # growth reading on any of these get dampened rather than dropped.
    quality_signals = ("velocity", "curiosity", "research", "traction")

    columns = (
        Column("name", "TOPIC", "text"),
        Column("group", "FIELD", "text"),
        Column("score", "VELOCITY", "score"),
        Column("hn_growth", "HN TREND", "pct"),
        Column("hn_recent", "HN 90D", "int"),
        Column("hn_points", "PEAK", "int"),
        Column("wiki_growth", "WIKI TREND", "pct"),
        Column("wiki_recent", "WIKI 60D", "int"),
        Column("paper_growth", "PAPER TREND", "pct"),
        Column("paper_recent", "PAPERS 180D", "int"),
        Column("repo_growth", "REPO TREND", "pct"),
        Column("repo_recent", "REPOS 180D", "int"),
        Column("repo_stars", "TOP STARS", "int"),
        Column("npm_downloads", "NPM/MO", "int"),
        Column("spread", "SPREAD", "int"),
    )

    rules = (
        NewLeaderRule(
            headline="📡 {name} is the fastest-moving idea on the board "
                     "(HN {hn_growth}%, Wikipedia {wiki_growth}%)."
        ),
        ClimberRule(
            rank_delta=6, score_delta=6.0,
            headline="📈 {name} is accelerating — up {rank_delta} to #{rank} "
                     "(HN {hn_growth}%, Wikipedia {wiki_growth}%).",
        ),
        DeltaRule(
            field="hn_recent", direction="up", frac=0.75, min_abs=5,
            type="faint_signal",
            headline="📡 Faint signal — {name} story volume jumped {pct}% "
                     "({old_fmt} → {new_fmt} in 90 days).",
        ),
        DeltaRule(
            field="wiki_recent", direction="up", frac=0.50, min_abs=5000,
            type="breakout",
            headline="🌍 {name} broke into public curiosity — Wikipedia views "
                     "up {pct}%.",
        ),
        DeltaRule(
            field="paper_recent", direction="up", frac=0.75, min_abs=MIN_PAPERS,
            type="research_signal",
            headline="🔬 {name} research is accelerating — papers up {pct}% "
                     "({old_fmt} → {new_fmt} in 180 days).",
        ),
        DeltaRule(
            field="repo_recent", direction="up", frac=0.75, min_abs=MIN_REPOS,
            type="repo_signal",
            headline="🛠 {name} is drawing builders — new repos up {pct}% "
                     "({old_fmt} → {new_fmt} in 180 days).",
        ),
        # The transition TELESCOPES.md calls out as the highest-value one:
        # a topic with real paper growth last sweep, followed by real repo
        # growth this sweep — research becoming builders, not simultaneously
        # loud on both (that's just a broadly hot topic, not a crossover).
        CrossoverRule(
            leading_field="paper_growth", leading_min=40,
            lagging_field="repo_growth", lagging_min=75,
            headline="🔀 {name} crossed over — research led (papers "
                     "+{leading_value}% last sweep), builders are now "
                     "following (repos +{lagging_value}% this sweep).",
        ),
    )
    snapshot_fields = (
        "hn_growth", "hn_recent", "hn_points", "wiki_growth", "wiki_recent",
        "paper_growth", "paper_recent", "repo_growth", "repo_recent",
        "repo_stars", "npm_downloads", "spread", "group",
    )

    def source_keys(self):
        return ["hn", "wikipedia", "npm", "openalex", "github"]

    # ── sources ──────────────────────────────────────────────────────────
    def _hn(self, ttl):
        """Story count and peak score per topic, for two adjacent windows."""
        def go():
            now = int(time.time())
            span = WINDOW_DAYS * 86400
            out = {}
            for t in TOPICS:
                # Algolia honours quoted phrases and the precision matters:
                # unquoted "small modular reactor" matches 148 stories, quoted
                # it matches 41 — the rest are incidental word hits.
                q = quote_plus(f'"{t.query}"')

                def window(lo, hi):
                    data = try_json(
                        "https://hn.algolia.com/api/v1/search"
                        f"?query={q}&tags=story&hitsPerPage=20"
                        f"&numericFilters=created_at_i>{lo},created_at_i<{hi}",
                        default={},
                    )
                    hits = data.get("hits") or []
                    return (
                        data.get("nbHits"),
                        max([h.get("points") or 0 for h in hits], default=0),
                    )

                recent, points = window(now - span, now)
                prior, _ = window(now - 2 * span, now - span)
                out[t.key] = {"recent": recent, "prior": prior, "points": points}
                time.sleep(0.2)      # Algolia is generous but not unlimited
            return out
        return self.cache.cached("hn", ttl, go)

    def _wiki(self, ttl):
        """Pageviews per topic for two adjacent windows."""
        def go():
            today = dt.date.today() - dt.timedelta(days=2)   # publish lag
            def total(article, start, end):
                url = (
                    "https://wikimedia.org/api/rest_v1/metrics/pageviews/"
                    f"per-article/en.wikipedia/all-access/all-agents/"
                    f"{quote(article, safe='')}/daily/"
                    f"{start:%Y%m%d}/{end:%Y%m%d}"
                )
                data = try_json(url, default=None)
                if not data:
                    return None      # wrong/renamed title -> no signal, not a crash
                return sum(i.get("views") or 0 for i in data.get("items", []))

            out = {}
            for t in TOPICS:
                if not t.wiki:
                    continue
                r_end = today
                r_start = today - dt.timedelta(days=WIKI_DAYS)
                p_start = today - dt.timedelta(days=2 * WIKI_DAYS)
                out[t.key] = {
                    "recent": total(t.wiki, r_start, r_end),
                    "prior": total(t.wiki, p_start, r_start - dt.timedelta(days=1)),
                }
                time.sleep(0.1)
            return out
        return self.cache.cached("wikipedia", ttl, go)

    def _openalex(self, ttl):
        """Paper count per topic, for two adjacent windows.

        OpenAlex honours quoted phrases the same way Algolia does — unquoted
        "state space model" matches 6.5M works (anything containing all three
        words anywhere), quoted it matches ~99K. `per_page=1` is enough
        because we only read `meta.count`, not the works themselves.
        """
        def go():
            today = dt.date.today()
            span = dt.timedelta(days=RESEARCH_DAYS)
            out = {}
            for t in TOPICS:
                q = quote_plus(f'"{t.query}"')

                def window(start, end):
                    data = try_json(
                        "https://api.openalex.org/works"
                        f"?search={q}&filter=from_publication_date:{start:%Y-%m-%d},"
                        f"to_publication_date:{end:%Y-%m-%d}&per_page=1",
                        default={},
                    )
                    return (data.get("meta") or {}).get("count")

                recent = window(today - span, today)
                prior = window(today - 2 * span, today - span)
                out[t.key] = {"recent": recent, "prior": prior}
                time.sleep(0.15)     # generous unauthenticated quota, still be polite
            return out

        def totally_failed(result):
            return not any(v.get("recent") is not None for v in result.values())

        return self.cache.cached("openalex", ttl, go, is_empty=totally_failed)

    def _github(self, ttl):
        """New-repo creation velocity per topic, for two adjacent windows.

        GitHub's *search* endpoint has a much stricter unauthenticated rate
        limit than the rest of its API — 10 requests/minute, not the ~60/hour
        a first check of the wrong rate-limit bucket suggested — so this
        paces at ~6.5s between calls. A full 32-topic sweep takes several
        minutes; it only ever runs from the (12h-cached) background poller or
        a manual refresh, the same tradeoff Kepler already makes for its SEC
        EDGAR walk. Reuses each query's own top hits for peak stars, so that
        signal costs zero requests beyond the count itself.
        """
        def go():
            today = dt.date.today()
            span = dt.timedelta(days=GITHUB_DAYS)
            out = {}
            for t in TOPICS:
                def window(start, end):
                    q = quote_plus(
                        f'"{t.query}" created:{start:%Y-%m-%d}..{end:%Y-%m-%d}'
                    )
                    data = try_json(
                        "https://api.github.com/search/repositories"
                        f"?q={q}&sort=stars&order=desc&per_page=5",
                        default={},
                    )
                    items = data.get("items") or []
                    stars = max(
                        [i.get("stargazers_count") or 0 for i in items], default=0
                    )
                    return data.get("total_count"), stars

                recent, stars = window(today - span, today)
                prior, _ = window(today - 2 * span, today - span)
                out[t.key] = {"recent": recent, "prior": prior, "stars": stars}
                time.sleep(6.5)     # GitHub search: 10 req/min unauthenticated
            return out

        def totally_failed(result):
            return not any(v.get("recent") is not None for v in result.values())

        return self.cache.cached("github", ttl, go, is_empty=totally_failed)

    def _npm(self, ttl):
        def go():
            out = {}
            for t in TOPICS:
                if not t.npm:
                    continue
                data = try_json(
                    f"https://api.npmjs.org/downloads/point/last-month/{t.npm}",
                    default=None,
                )
                if data and data.get("downloads"):
                    out[t.key] = data["downloads"]
                time.sleep(0.1)
            return out
        return self.cache.cached("npm", ttl, go)

    # ── join ─────────────────────────────────────────────────────────────
    def collect(self, force=False):
        ttl = self.ttl(force)
        hn = self._hn(ttl)
        wiki = self._wiki(ttl)
        npm = self._npm(ttl)
        openalex = self._openalex(ttl)
        github = self._github(ttl)

        rows = []
        for t in TOPICS:
            h = hn.get(t.key) or {}
            w = wiki.get(t.key) or {}
            p = openalex.get(t.key) or {}
            g = github.get(t.key) or {}
            downloads = npm.get(t.key)

            hn_recent, hn_prior = h.get("recent"), h.get("prior")
            wiki_recent, wiki_prior = w.get("recent"), w.get("prior")
            paper_recent, paper_prior = p.get("recent"), p.get("prior")
            repo_recent, repo_prior = g.get("recent"), g.get("prior")

            # Spread: how many independent surfaces actually report this topic.
            spread = sum([
                bool(hn_recent),
                bool(wiki_recent),
                bool(downloads),
                bool(paper_recent),
                bool(repo_recent),
            ])
            rows.append({
                "key": t.key,
                "name": t.name,
                "group": t.group,
                "link": (f"https://hn.algolia.com/?query={quote_plus(t.query)}"
                         "&sort=byPopularity&type=story"),
                "hn_recent": hn_recent,
                "hn_prior": hn_prior,
                "hn_growth": _growth(hn_recent, hn_prior, floor=MIN_STORIES),
                "hn_points": h.get("points") or None,
                "wiki_recent": wiki_recent,
                "wiki_prior": wiki_prior,
                "wiki_growth": _growth(wiki_recent, wiki_prior, floor=500),
                "paper_recent": paper_recent,
                "paper_prior": paper_prior,
                "paper_growth": _growth(paper_recent, paper_prior, floor=MIN_PAPERS),
                "repo_recent": repo_recent,
                "repo_prior": repo_prior,
                "repo_growth": _growth(repo_recent, repo_prior, floor=MIN_REPOS),
                "repo_stars": g.get("stars") or None,
                "npm_downloads": downloads,
                # Scaled 0-100 so it blends like every other signal.
                "spread": round(spread / 5 * 100, 1),
                "sources": (["hn"] if hn_recent else []) +
                           (["wikipedia"] if wiki_recent else []) +
                           (["npm"] if downloads else []) +
                           (["openalex"] if paper_recent else []) +
                           (["github"] if repo_recent else []) or ["hn"],
                # A topic no surface reports is not evidence of anything.
                "noise": spread == 0,
            })
        return rows

    # ── secondary panel: which fields are heating up ─────────────────────
    def context(self, force=False):
        rows = self.collect(force=force)
        agg = {}
        for r in rows:
            if r["noise"]:
                continue
            slot = agg.setdefault(r["group"], {
                "field": r["group"], "topics": 0,
                "_hn": [], "_wiki": [],
            })
            slot["topics"] += 1
            if r["hn_growth"] is not None:
                slot["_hn"].append(r["hn_growth"])
            if r["wiki_growth"] is not None:
                slot["_wiki"].append(r["wiki_growth"])

        out = []
        for slot in agg.values():
            hn = slot.pop("_hn")
            wiki = slot.pop("_wiki")
            slot["hn_growth"] = round(sum(hn) / len(hn), 1) if hn else None
            slot["wiki_growth"] = round(sum(wiki) / len(wiki), 1) if wiki else None
            out.append(slot)
        if not out:
            return None
        out.sort(key=lambda r: r["wiki_growth"] if r["wiki_growth"] is not None else -999,
                 reverse=True)
        return {
            "title": "FIELDS · WHERE CURIOSITY IS MOVING",
            "subtitle": "Mean growth across the watchlist topics in each field",
            "columns": [
                {"field": "field", "label": "FIELD", "fmt": "text"},
                {"field": "topics", "label": "TOPICS", "fmt": "int"},
                {"field": "hn_growth", "label": "HN TREND", "fmt": "pct"},
                {"field": "wiki_growth", "label": "WIKI TREND", "fmt": "pct"},
            ],
            "rows": out,
        }
