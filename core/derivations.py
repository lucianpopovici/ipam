"""
Derivation engine — evaluates declarative computed fields at read time.

Usage
─────
    from core.derivations import evaluate_all

    # In a view, after loading an entity:
    derived = evaluate_all('ne_instance', inst)
    # derived == {'hostname': 'pnf-pe-lon-01', 'display_label': '[PNF] pe-lon-01'}

    return render_template('...', inst=inst, derived=derived)

Design decisions (resolving open questions from CLAUDE_schema_flexibility.md)
──────────────────────────────────────────────────────────────────────────────
1. Path syntax: structured dict per resolved variable (not ad-hoc "via X via Y"
   strings). Each resolve entry is a single Redis lookup; multi-hop paths are
   declared as chained entries in order.

2. Per-request memoization: pass a shared `cache` dict across multiple
   evaluate_all() calls in the same request to avoid re-fetching the same
   entity repeatedly.

3. No write-time caching: derived values are always computed fresh. Redis is
   fast enough for one extra GET per resolved variable.

4. SandboxedEnvironment only: templates cannot call Python builtins or
   access the filesystem.
"""
import json
import pathlib
from jinja2.sandbox import SandboxedEnvironment

# ── Load derivations.yaml once at import time ──────────────────────────────────

_YAML_PATH = pathlib.Path(__file__).parent.parent / 'config' / 'derivations.yaml'
_cache: dict | None = None


def _load_yaml() -> dict:
    """Load and cache derivations.yaml. Returns {} if file is absent."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        import yaml
        with open(_YAML_PATH, 'r', encoding='utf-8') as fh:
            _cache = yaml.safe_load(fh) or {}
    except (FileNotFoundError, ImportError):
        _cache = {}
    return _cache


def derivations_for(entity_type: str) -> dict:
    """Return the derivation definitions for one entity type."""
    return _load_yaml().get(entity_type, {})


# ── Jinja2 environment ─────────────────────────────────────────────────────────

def _pad(value, width):
    """Jinja2 filter: zero-pad an integer to *width* digits."""
    try:
        return str(int(value)).zfill(int(width))
    except (ValueError, TypeError):
        return str(value)


def _slugify(value):
    """Jinja2 filter: lowercase + replace spaces with hyphens."""
    return str(value).lower().replace(' ', '-')


_JINJA = SandboxedEnvironment(autoescape=False)
_JINJA.filters['pad']     = _pad
_JINJA.filters['slugify'] = _slugify


# ── Redis access (lazy import so tests can monkeypatch db.r freely) ────────────

def _redis_get(key: str):
    import db  # local import — db.r is monkeypatched in tests
    raw = db.r.get(key)
    return json.loads(raw) if raw else None


# ── Core evaluation ────────────────────────────────────────────────────────────

def _resolve_context(entity: dict, resolve_spec: dict, cache: dict) -> dict:
    """
    Build extra template variables by resolving each entry in *resolve_spec*.

    resolve_spec example::

        ne_type:
          field: ne_type_id      # ID field on the entity
          entity: ne_type        # Redis key prefix
          fallback: {}           # value if lookup misses

    Entries are resolved in declaration order; later entries can reference
    results of earlier ones (not implemented yet — would need mutual ordering).
    """
    ctx = {}
    for var_name, spec in resolve_spec.items():
        field    = spec.get('field', '')
        prefix   = spec.get('entity', '')
        fallback = spec.get('fallback', '')

        entity_id = entity.get(field, '') if field else ''
        if not entity_id or not prefix:
            ctx[var_name] = fallback
            continue

        cache_key = f'{prefix}:{entity_id}'
        if cache_key not in cache:
            cache[cache_key] = _redis_get(cache_key) or fallback
        ctx[var_name] = cache[cache_key]

    return ctx


def _render(template_str: str, ctx: dict) -> str:
    """Render a Jinja2 template string; return empty string on error."""
    try:
        return _JINJA.from_string(template_str).render(**ctx)
    except Exception:  # pylint: disable=broad-except
        return ''


def evaluate(entity_type: str, entity: dict, field_name: str,
             cache: dict | None = None) -> str:
    """
    Evaluate one derived field for *entity*.

    Returns the rendered string, or '' if the derivation is not defined
    or fails.

    :param cache:  Shared dict for memoizing Redis lookups within a request.
                   Pass the same dict to all evaluate() calls in one request.
    """
    if cache is None:
        cache = {}
    defs = derivations_for(entity_type)
    spec = defs.get(field_name)
    if not spec:
        return ''

    template_str = spec.get('template', '')
    resolve_spec = spec.get('resolve', {})

    extra_ctx = _resolve_context(entity, resolve_spec, cache)
    # Base context: all entity fields + resolved extras
    ctx = {**entity, **extra_ctx}
    return _render(template_str, ctx)


def evaluate_all(entity_type: str, entity: dict,
                 cache: dict | None = None) -> dict:
    """
    Evaluate every derived field defined for *entity_type*.

    Returns a dict ``{field_name: rendered_value}``.
    Fields that error silently return ''.
    """
    if cache is None:
        cache = {}
    defs = derivations_for(entity_type)
    return {
        field_name: evaluate(entity_type, entity, field_name, cache)
        for field_name in defs
    }


def reload():
    """Force a reload of derivations.yaml (useful in tests / hot-reload)."""
    global _cache
    _cache = None
