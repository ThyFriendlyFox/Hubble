"""🔭 HUBBLE — a telescope for AI.

The original instrument. Consolidates what people actually use, like and rank
highest across the LLM landscape into one "best models right now" board.

Sources (all public, no keys):
  HuggingFace Hub   downloads, likes
  OpenRouter        real-world weekly usage rank, pricing, context, the HF join key
  Artificial Analysis (embedded in OpenRouter)  intelligence / coding / agentic
  Design Arena      (embedded in OpenRouter)    elo
  LMArena           best-effort community mirror
"""
import re

from telescope import Column, Signal, Telescope
from telescope.events import ClimberRule, DeltaRule, NewEntrantRule, NewLeaderRule
from telescope.http import get_text, try_json
from telescope.registry import register


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _clean_price(v):
    """OpenRouter uses negative sentinels (e.g. -1000000) for variable/router
    pricing. Treat those as unknown so they don't skew price normalisation."""
    f = _to_float(v)
    return None if f is None or f < 0 else f


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Non-"best LLM" entries: quant repacks, embeddings, rerankers, OCR/TTS/image
# models, base (non-chat) checkpoints, test stubs, routers.
_NOISE_RE = re.compile(
    r"(gguf|awq|gptq|fp8|nvfp4|bnb-?4bit|-mlx|int4|int8|w4a16|w8a8|quantized"
    r"|embedding|reranker|bge-|gte-|tiny-random|internal-testing|unit-test"
    r"|-ocr|hunyuanocr|readerlm|\btts\b|parler|lyria|nano banana|image|audio"
    r"|router|fusion|\bt5\b|-base\b|guard)",
    re.IGNORECASE,
)

_MOE_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _is_noise(name, hf_id):
    return bool(_NOISE_RE.search(f"{name or ''} {hf_id or ''}"))


def _parse_params(name, hf_id):
    """Best-effort TOTAL parameter count in billions, parsed from the name.

    Total (not active) params is what must be loaded into memory, so MoE like
    '235B-A22B' takes the larger number and 'Mixtral-8x22B' expands to 176B.
    """
    text = f"{name or ''} {hf_id or ''}"
    moe = _MOE_RE.search(text)
    sizes = [float(n) for n in _SIZE_RE.findall(text)]
    if moe:
        sizes.append(int(moe.group(1)) * float(moe.group(2)))
    return max(sizes) if sizes else None


def _price_str(p):
    return f"${(p or 0) * 1e6:.2f}"


@register
class Hubble(Telescope):
    slug = "hubble"
    name = "HUBBLE"
    domain = "AI"
    glyph = "🔭"
    tagline = "CONSOLIDATED LLM INDEX"
    entity_label = "MODELS"
    sources_label = "HUGGINGFACE · OPENROUTER · BENCHMARKS · ARENA"
    caveat = ("Usage rank is OpenRouter traffic only — it under-counts models "
              "served direct from a lab's own API.")

    signals = (
        Signal("intelligence", "intelligence_index", "INTELLIGENCE"),
        Signal("coding", "coding_index", "CODING"),
        Signal("agentic", "agentic_index", "AGENTIC"),
        Signal("arena", "arena_elo", "ARENA ELO"),
        Signal("usage", "usage_rank", "REAL USAGE", higher=False),
        Signal("downloads", "downloads", "DOWNLOADS", log=True),
        Signal("likes", "likes", "LIKES"),
        Signal("price", "price_completion", "CHEAP PRICE", higher=False),
    )
    default_weights = {
        "intelligence": 30, "coding": 15, "agentic": 10, "arena": 10,
        "usage": 20, "downloads": 5, "likes": 5, "price": 5,
    }
    quality_signals = ("intelligence", "coding", "agentic", "arena")

    columns = (
        Column("name", "MODEL", "text"),
        Column("score", "SCORE", "score"),
        Column("intelligence_index", "INTL"),
        Column("coding_index", "CODE"),
        Column("agentic_index", "AGENT"),
        Column("arena_elo", "ARENA", "int"),
        Column("usage_rank", "USAGE", "rank"),
        Column("downloads", "DOWNLOADS", "int"),
        Column("likes", "LIKES", "int"),
        Column("price_completion", "$/MTOK", "price"),
    )

    rules = (
        NewLeaderRule(
            headline="🔭 New #1 — {name} takes the top spot (score {score})."
        ),
        NewEntrantRule(
            max_rank=150,
            require_any=("intelligence_index", "usage_rank"),
            type="new_model",
            headline="🔭 New intelligence detected — {name} enters the index "
                     "at #{rank} (score {score}).",
        ),
        ClimberRule(rank_delta=15, score_delta=2.5),
        DeltaRule(
            field="price_completion", direction="down", frac=0.25,
            type="price_drop",
            headline="💸 {name} got cheaper — {old_fmt} → {new_fmt} /Mtok.",
            formatter=_price_str,
        ),
    )
    snapshot_fields = (
        "intelligence_index", "coding_index", "usage_rank", "arena_elo",
        "price_completion", "params_b", "local",
    )

    def source_keys(self):
        return ["hf", "openrouter", "lmarena"]

    # ── sources ──────────────────────────────────────────────────────────
    def fetch_huggingface(self, ttl, limit=300):
        def go():
            url = ("https://huggingface.co/api/models"
                   f"?sort=downloads&direction=-1&limit={limit}&filter=text-generation")
            return [
                {
                    "hf_id": m.get("id") or m.get("modelId"),
                    "downloads": m.get("downloads") or 0,
                    "likes": m.get("likes") or 0,
                    "created_at": m.get("createdAt"),
                }
                for m in try_json(url, default=[])
            ]
        # HuggingFace's text-generation models sorted by downloads is never
        # legitimately empty, so an empty result is a fetch failure worth
        # falling back to stale-but-real data for, not the new truth.
        return self.cache.cached("hf", ttl, go, is_empty=lambda r: not r)

    def fetch_openrouter(self, ttl):
        """Models ordered by weekly token usage — array index IS the rank."""
        def go():
            data = try_json(
                "https://openrouter.ai/api/v1/models?order=top-weekly", default={}
            ).get("data") or []
            out = []
            for rank, m in enumerate(data, start=1):
                pricing = m.get("pricing") or {}
                bench = m.get("benchmarks") or {}
                aa = bench.get("artificial_analysis") or {}
                arena = bench.get("design_arena") or []
                elos = [a.get("elo") for a in arena if a.get("elo")]
                out.append({
                    "or_id": m.get("id"),
                    "name": m.get("name"),
                    "hf_id": m.get("hugging_face_id") or None,
                    "usage_rank": rank,
                    "context_length": m.get("context_length"),
                    "price_prompt": _clean_price(pricing.get("prompt")),
                    "price_completion": _clean_price(pricing.get("completion")),
                    "intelligence_index": aa.get("intelligence_index"),
                    "coding_index": aa.get("coding_index"),
                    "agentic_index": aa.get("agentic_index"),
                    "arena_elo": max(elos) if elos else None,
                })
            return out
        # OpenRouter's weekly usage ranking is never legitimately empty
        # either, same reasoning as fetch_huggingface().
        return self.cache.cached("openrouter", ttl, go, is_empty=lambda r: not r)

    def fetch_lmarena(self, ttl):
        """Lowered model name -> arena elo. Best effort; may be empty."""
        def go():
            result = {}
            try:
                text = get_text(
                    "https://raw.githubusercontent.com/lm-sys/FastChat/main/"
                    "fastchat/serve/monitor/leaderboard.csv"
                )
            except Exception:
                return result
            for line in text.splitlines()[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    name, elo = parts[0], _to_float(parts[1])
                    if name and elo:
                        result[_norm(name)] = elo
            return result
        return self.cache.cached("lmarena", ttl, go)

    # ── join ─────────────────────────────────────────────────────────────
    def collect(self, force=False):
        ttl = self.ttl(force)
        hf = self.fetch_huggingface(ttl)
        orr = self.fetch_openrouter(ttl)
        arena = self.fetch_lmarena(ttl)

        hf_by_id = {m["hf_id"]: m for m in hf if m.get("hf_id")}
        rows = {}

        # Seed from OpenRouter — it has usage + benchmarks + the HF join key.
        for m in orr:
            key = m.get("hf_id") or m["or_id"]
            h = hf_by_id.get(m.get("hf_id")) if m.get("hf_id") else None
            rows[key] = {
                "key": key,
                "name": m.get("name") or m["or_id"],
                "link": (f"https://huggingface.co/{m['hf_id']}" if m.get("hf_id")
                         else f"https://openrouter.ai/{m['or_id']}"),
                "hf_id": m.get("hf_id"),
                "or_id": m["or_id"],
                "downloads": h["downloads"] if h else None,
                "likes": h["likes"] if h else None,
                "usage_rank": m.get("usage_rank"),
                "context_length": m.get("context_length"),
                "price_prompt": m.get("price_prompt"),
                "price_completion": m.get("price_completion"),
                "intelligence_index": m.get("intelligence_index"),
                "coding_index": m.get("coding_index"),
                "agentic_index": m.get("agentic_index"),
                "arena_elo": m.get("arena_elo") or arena.get(_norm(m.get("name"))),
                "sources": ["openrouter"] + (["huggingface"] if h else []),
                "noise": _is_noise(m.get("name"), m.get("hf_id")),
                "params_b": _parse_params(m.get("name"), m.get("hf_id")),
                "local": bool(m.get("hf_id")),
            }

        # Add HF-only models that never showed up on OpenRouter.
        for m in hf:
            key = m["hf_id"]
            if key in rows:
                continue
            rows[key] = {
                "key": key,
                "name": key,
                "link": f"https://huggingface.co/{key}",
                "hf_id": key,
                "or_id": None,
                "downloads": m["downloads"],
                "likes": m["likes"],
                "usage_rank": None,
                "context_length": None,
                "price_prompt": None,
                "price_completion": None,
                "intelligence_index": None,
                "coding_index": None,
                "agentic_index": None,
                "arena_elo": arena.get(_norm(key.split("/")[-1])),
                "sources": ["huggingface"],
                "noise": _is_noise(key, key),
                "params_b": _parse_params(key, key),
                "local": True,
            }

        return list(rows.values())
