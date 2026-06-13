"""Fetch and normalise LLM data from HuggingFace, OpenRouter and LMArena.

Each fetcher returns a list/dict of raw-ish records and is independently
cached.  ``build_unified`` joins them into one model-per-row dataset keyed on
the HuggingFace repo id where possible, falling back to the OpenRouter slug.
"""
import re
import requests

import cache

UA = {"User-Agent": "HF-Dash/1.0 (+local dashboard)"}
TIMEOUT = 30


def _get_json(url, headers=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# HuggingFace
# --------------------------------------------------------------------------- #
def fetch_huggingface(limit=300, ttl=3600):
    """Top text-generation models on the Hub by downloads."""
    cached = cache.get("hf", ttl)
    if cached is not None:
        return cached
    url = (
        "https://huggingface.co/api/models"
        f"?sort=downloads&direction=-1&limit={limit}&filter=text-generation"
    )
    data = _get_json(url)
    out = []
    for m in data:
        out.append(
            {
                "hf_id": m.get("id") or m.get("modelId"),
                "downloads": m.get("downloads") or 0,
                "likes": m.get("likes") or 0,
                "created_at": m.get("createdAt"),
                "tags": m.get("tags") or [],
            }
        )
    return cache.set("hf", out)


# --------------------------------------------------------------------------- #
# OpenRouter  (usage ranking + pricing + embedded benchmarks)
# --------------------------------------------------------------------------- #
def fetch_openrouter(ttl=3600):
    """Models ordered by weekly token usage. Array index == usage rank.

    OpenRouter embeds two benchmark families we surface:
      * artificial_analysis -> intelligence_index / coding_index / agentic_index
      * design_arena        -> per-category elo + win_rate
    """
    cached = cache.get("openrouter", ttl)
    if cached is not None:
        return cached
    data = _get_json("https://openrouter.ai/api/v1/models?order=top-weekly")["data"]
    out = []
    for rank, m in enumerate(data, start=1):
        pricing = m.get("pricing") or {}
        aa = (m.get("benchmarks") or {}).get("artificial_analysis") or {}
        arena = (m.get("benchmarks") or {}).get("design_arena") or []
        arena_elos = [a.get("elo") for a in arena if a.get("elo")]
        out.append(
            {
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
                "arena_elo": max(arena_elos) if arena_elos else None,
            }
        )
    return cache.set("openrouter", out)


# --------------------------------------------------------------------------- #
# LMArena (Chatbot Arena) — best-effort. Degrades to {} if the source moves.
# --------------------------------------------------------------------------- #
def fetch_lmarena(ttl=21600):
    """Map of lowered model name -> arena elo. Best effort; may be empty."""
    cached = cache.get("lmarena", ttl)
    if cached is not None:
        return cached
    result = {}
    try:
        # Community-maintained mirror of the Chatbot Arena leaderboard.
        url = (
            "https://raw.githubusercontent.com/lm-sys/FastChat/main/"
            "fastchat/serve/monitor/leaderboard.csv"
        )
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.ok and "," in r.text:
            for line in r.text.splitlines()[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    name, elo = parts[0], _to_float(parts[1])
                    if name and elo:
                        result[_norm(name)] = elo
    except requests.RequestException:
        pass
    return cache.set("lmarena", result)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clean_price(v):
    """OpenRouter uses negative sentinels (e.g. -1000000) for variable/router
    pricing. Treat those as unknown so they don't skew the price normalisation."""
    f = _to_float(v)
    return None if f is None or f < 0 else f


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Patterns for non-"best LLM" entries: quant repacks, embeddings, rerankers,
# OCR/TTS/image/audio models, base (non-chat) checkpoints, test stubs, routers.
_NOISE_RE = re.compile(
    r"(gguf|awq|gptq|fp8|nvfp4|bnb-?4bit|-mlx|int4|int8|w4a16|w8a8|quantized"
    r"|embedding|reranker|bge-|gte-|tiny-random|internal-testing|unit-test"
    r"|-ocr|hunyuanocr|readerlm|\btts\b|parler|lyria|nano banana|image|audio"
    r"|router|fusion|\bt5\b|-base\b|guard)",
    re.IGNORECASE,
)


def _is_noise(name, hf_id):
    text = f"{name or ''} {hf_id or ''}"
    return bool(_NOISE_RE.search(text))


_MOE_RE = re.compile(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)


def _parse_params(name, hf_id):
    """Best-effort total parameter count (in billions) parsed from the model
    name/repo id. Returns a float or None. We want TOTAL params (what must be
    loaded into memory), so for MoE like '235B-A22B' we take the larger number,
    and for 'Mixtral-8x22B' we expand to 176B."""
    text = f"{name or ''} {hf_id or ''}"
    moe = _MOE_RE.search(text)
    sizes = [float(n) for n in _SIZE_RE.findall(text)]
    if moe:
        sizes.append(int(moe.group(1)) * float(moe.group(2)))
    return max(sizes) if sizes else None


# --------------------------------------------------------------------------- #
# Join everything into one dataset
# --------------------------------------------------------------------------- #
def build_unified(ttl=3600, force=False):
    # force=True bypasses the cache by passing ttl=0 to each fetcher.
    hf = fetch_huggingface(ttl=0 if force else ttl)
    orr = fetch_openrouter(ttl=0 if force else ttl)
    arena = fetch_lmarena(ttl=0 if force else ttl)

    hf_by_id = {m["hf_id"]: m for m in hf if m.get("hf_id")}
    rows = {}

    # Seed from OpenRouter (has usage + benchmarks + the HF join key).
    for m in orr:
        key = m.get("hf_id") or m["or_id"]
        h = hf_by_id.get(m.get("hf_id")) if m.get("hf_id") else None
        rows[key] = {
            "key": key,
            "name": m.get("name") or m["or_id"],
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
            "arena_elo": m.get("arena_elo")
            or arena.get(_norm(m.get("name"))),
            "sources": ["openrouter"] + (["huggingface"] if h else []),
            "noise": _is_noise(m.get("name"), m.get("hf_id")),
            "params_b": _parse_params(m.get("name"), m.get("hf_id")),
            "local": bool(m.get("hf_id")),  # has an open-weight HF repo
        }

    # Add HF-only models that never showed up on OpenRouter.
    for m in hf:
        key = m["hf_id"]
        if key in rows:
            continue
        rows[key] = {
            "key": key,
            "name": key,
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
