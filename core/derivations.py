"""
Derivation engine — evaluates declarative computed fields at read time.

Usage
─────
    from core.derivations import evaluate_all

    # In a view, after loading an entity:
    derived = evaluate_all('ne_instance', inst)
    # derived == {'hostname': 'pnf-01', 'display_label': '[PNF] pe-lon-01'}

    return render_template('...', inst=inst, derived=derived)

Design decisions (resolving open questions from CLAUDE_schema_flexibility.md)
──────────────────────────────────────────────────────────────────────────────
1. Path syntax: structured dict per resolved variable (not ad-hoc "via X via Y"
   strings). Three resolve types are supported:

   a) Entity lookup (field + entity):
        ne:
          field: ne_type_id      # ID field on the entity
          entity: ne_type        # Redis key prefix; key = ne_type:{id}
          fallback: {}

   b) Chained lookup (from_var + field + entity) — looks up a field on
      a previously resolved variable (declared-order dependency):
        site:
          from_var: pod          # use the already-resolved 'pod' dict
          field: site_id         # field on that resolved entity
          entity: site
          fallback: {}

   c) Computed: peer_index — 1-based position of this entity among its peers:
        index:
          computed: peer_index
          index_set: "project:{project_id}:ne_instances"  # Redis set (fields interpolated)
          peer_prefix: ne_inst   # key = ne_inst:{id}
          group_by: ne_type_id   # peers must share this field value
          sort_by: name          # stable sort order

2. Per-request memoization: pass a shared `cache` dict across multiple
   evaluate_all() calls in the same request to avoid re-fetching the same
   entity repeatedly.

3. No write-time caching: derived values are always computed fresh.

4. SandboxedEnvironment only: templates cannot call Python builtins or
   access the filesystem.
"""
import json
import re as _re
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


def _redis_smembers(key: str) -> set:
    import db
    return db.r.smembers(key)


# ── Computed: peer_index ───────────────────────────────────────────────────────

def _interpolate(pattern: str, entity: dict) -> str:
    """Replace {field_name} placeholders in *pattern* with entity field values."""
    def _sub(m):
        return str(entity.get(m.group(1), ''))
    return _re.sub(r'\{(\w+)\}', _sub, pattern)


def _compute_peer_index(entity: dict, spec: dict, cache: dict) -> int:
    """
    Return the 1-based position of *entity* among its peers.

    Peers are identified by:
      - Reading all IDs from a Redis set (index_set, with {field} interpolation)
      - Loading each peer from Redis (peer_prefix:{id})
      - Keeping only those where peer[group_by] == entity[group_by]
      - Sorting by sort_by field

    Result is cached by (index_set_key, group_value) so repeated calls within
    one request do not re-scan Redis.
    """
    index_set_pattern = spec.get('index_set', '')
    peer_prefix       = spec.get('peer_prefix', '')
    group_by          = spec.get('group_by', '')
    sort_by           = spec.get('sort_by', 'name')

    if not index_set_pattern or not peer_prefix:
        return 1

    index_set_key = _interpolate(index_set_pattern, entity)
    group_value   = entity.get(group_by, '')
    cache_key     = f'__peer_index__:{index_set_key}:{group_value}'

    if cache_key not in cache:
        all_ids = _redis_smembers(index_set_key)
        peers   = []
        for pid in all_ids:
            raw = _redis_get(f'{peer_prefix}:{pid}')
            if raw and raw.get(group_by) == group_value:
                peers.append(raw)
        peers.sort(key=lambda p: p.get(sort_by, ''))
        # Store ordered list of IDs for O(1) lookup
        cache[cache_key] = [p['id'] for p in peers]

    ordered_ids = cache[cache_key]
    entity_id   = entity.get('id', '')
    try:
        return ordered_ids.index(entity_id) + 1
    except ValueError:
        return len(ordered_ids) + 1   # entity not yet in set — append position


# ── Core evaluation ────────────────────────────────────────────────────────────

def _resolve_context(entity: dict, resolve_spec: dict, cache: dict) -> dict:
    """
    Build extra template variables by resolving each entry in *resolve_spec*.

    Three resolve types:
      - Entity lookup (field + entity keys)
      - Chained lookup (from_var + field + entity keys)
      - Computed (computed: peer_index)

    Entries are processed in declaration order so later entries can reference
    earlier ones via ``from_var``.
    """
    ctx = {}
    for var_name, spec in resolve_spec.items():
        computed = spec.get('computed', '')

        # ── computed: peer_index ──────────────────────────────────────────────
        if computed == 'peer_index':
            ctx[var_name] = _compute_peer_index(entity, spec, cache)
            continue

        # ── chained lookup: use a previously resolved variable as the source ──
        from_var = spec.get('from_var', '')
        if from_var:
            source   = ctx.get(from_var, {})           # resolved earlier
            field    = spec.get('field', '')
            prefix   = spec.get('entity', '')
            fallback = spec.get('fallback', '')
            entity_id = source.get(field, '') if isinstance(source, dict) else ''
            if not entity_id or not prefix:
                ctx[var_name] = fallback
                continue
            cache_key = f'{prefix}:{entity_id}'
            if cache_key not in cache:
                cache[cache_key] = _redis_get(cache_key) or fallback
            ctx[var_name] = cache[cache_key]
            continue

        # ── direct entity lookup ──────────────────────────────────────────────
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
