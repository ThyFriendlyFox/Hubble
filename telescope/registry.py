"""The telescope registry and its on/off toggles.

Every telescope registers itself here. Each one can be independently toggled:
a disabled telescope is never polled, never fetched, and shows as dark in the
dashboard switcher — so you can run just Hubble on a laptop, or light up the
whole observatory on a server.

Toggle state persists to `data/observatory.json`. The `OBSERVATORY_ENABLED`
env var seeds the defaults on first run (comma-separated slugs, or "all").
"""
import json
import os
import threading

from telescope.cache import ROOT

STATE_FILE = os.path.join(ROOT, "observatory.json")
_lock = threading.Lock()

_classes = {}      # slug -> Telescope subclass
_instances = {}    # slug -> instance (built lazily)
_load_errors = {}  # slug -> import/instantiation error


def register(cls):
    """Class decorator: add a telescope to the observatory."""
    _classes[cls.slug] = cls
    return cls


def discover():
    """Import every module in observatories/ so registration side-effects run."""
    import importlib
    import pkgutil

    import observatories

    for mod in pkgutil.iter_modules(observatories.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"observatories.{mod.name}")
        except Exception as e:  # a broken pack shouldn't take down the rest
            _load_errors[mod.name] = str(e)
    return sorted(_classes)


# ── toggle state ─────────────────────────────────────────────────────────
def _default_state():
    raw = os.environ.get("OBSERVATORY_ENABLED", "hubble").strip()
    if raw.lower() == "all":
        return {slug: True for slug in _classes}
    wanted = {s.strip() for s in raw.split(",") if s.strip()}
    return {slug: (slug in wanted) for slug in _classes}


def _read_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("enabled", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(enabled):
    os.makedirs(ROOT, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"enabled": enabled}, fh, indent=2)
    os.replace(tmp, STATE_FILE)


def state():
    """{slug: bool} for every registered telescope."""
    with _lock:
        saved = _read_state()
        defaults = _default_state()
        # Registered-but-unknown telescopes inherit the env default, so adding
        # a new pack doesn't silently enable it on an existing install.
        return {slug: saved.get(slug, defaults.get(slug, False)) for slug in _classes}


def is_enabled(slug):
    return state().get(slug, False)


def set_enabled(slug, on):
    if slug not in _classes:
        raise KeyError(slug)
    with _lock:
        current = {s: _read_state().get(s, _default_state().get(s, False))
                   for s in _classes}
        current[slug] = bool(on)
        _write_state(current)
    return bool(on)


# ── access ───────────────────────────────────────────────────────────────
def get(slug):
    """Instantiate (and memoise) a telescope by slug."""
    if slug not in _classes:
        raise KeyError(slug)
    with _lock:
        if slug not in _instances:
            _instances[slug] = _classes[slug]()
        return _instances[slug]


def all_slugs():
    return sorted(_classes)


def enabled_slugs():
    st = state()
    return [s for s in sorted(_classes) if st.get(s)]


def catalog():
    """Metadata for every telescope, enabled or not — powers the switcher.

    Deliberately does NOT fetch: listing the observatory must stay instant.
    """
    st = state()
    out = []
    for slug in sorted(_classes):
        cls = _classes[slug]
        out.append({
            "slug": slug,
            "name": cls.name,
            "domain": cls.domain,
            "tagline": cls.tagline,
            "glyph": cls.glyph,
            "entity_label": cls.entity_label,
            "sources_label": cls.sources_label,
            "caveat": cls.caveat,
            "enabled": st.get(slug, False),
            "poll_seconds": cls.poll_seconds,
        })
    return out


def load_errors():
    return dict(_load_errors)
