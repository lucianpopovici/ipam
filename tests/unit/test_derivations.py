"""
Unit tests for core/derivations.py — the declarative computed-field engine.

Tests are isolated from Redis (monkeypatch db.r) and from the actual
derivations.yaml (derivations are injected via monkeypatching _load_yaml).
"""
import json
import pytest
import fakeredis


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_r(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    import db
    monkeypatch.setattr(db, 'r', fr)
    return fr


@pytest.fixture(autouse=True)
def clear_derivation_cache():
    """Force _load_yaml to reload before every test."""
    from core import derivations
    derivations.reload()
    yield
    derivations.reload()


def _patch_derivations(monkeypatch, spec: dict):
    """Override the derivation definitions for the duration of a test."""
    from core import derivations
    monkeypatch.setattr(derivations, '_cache', spec)


# ── load / derivations_for ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_derivations_for_unknown_entity():
    from core.derivations import derivations_for
    assert derivations_for('does_not_exist') == {}


@pytest.mark.unit
def test_derivations_for_known_entity(monkeypatch):
    _patch_derivations(monkeypatch, {'widget': {'color': {'template': 'blue'}}})
    from core.derivations import derivations_for
    assert 'color' in derivations_for('widget')


# ── evaluate — simple template with no resolve ────────────────────────────────

@pytest.mark.unit
def test_evaluate_simple_template(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'label': {'template': '{{ name | upper }}', 'resolve': {}},
        }
    })
    from core.derivations import evaluate
    result = evaluate('ne_instance', {'name': 'pe-lon-01'}, 'label')
    assert result == 'PE-LON-01'


@pytest.mark.unit
def test_evaluate_unknown_field_returns_empty(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {'ne_instance': {}})
    from core.derivations import evaluate
    assert evaluate('ne_instance', {'name': 'x'}, 'no_such_field') == ''


@pytest.mark.unit
def test_evaluate_bad_jinja_returns_empty(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'bad': {'template': '{% for %}broken{%endfor%}', 'resolve': {}},
        }
    })
    from core.derivations import evaluate
    assert evaluate('ne_instance', {'name': 'x'}, 'bad') == ''


# ── evaluate — resolve with Redis lookup ──────────────────────────────────────

@pytest.mark.unit
def test_evaluate_resolves_linked_entity(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'hostname': {
                'template': '{{ ne_type.kind | lower }}-{{ name | lower }}',
                'resolve': {
                    'ne_type': {
                        'field': 'ne_type_id',
                        'entity': 'ne_type',
                        'fallback': {},
                    }
                },
            }
        }
    })
    # Seed the NE type in fake Redis
    ne_type = {'id': 'nt-001', 'name': 'Router', 'kind': 'PNF'}
    fake_r.set('ne_type:nt-001', json.dumps(ne_type))

    from core.derivations import evaluate
    entity = {'name': 'pe-lon-01', 'ne_type_id': 'nt-001'}
    result = evaluate('ne_instance', entity, 'hostname')
    assert result == 'pnf-pe-lon-01'


@pytest.mark.unit
def test_evaluate_uses_fallback_when_entity_missing(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'hostname': {
                'template': '{{ ne_type.kind | lower }}-{{ name | lower }}',
                'resolve': {
                    'ne_type': {
                        'field': 'ne_type_id',
                        'entity': 'ne_type',
                        'fallback': {'kind': 'unknown'},
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    # ne_type_id points to a non-existent record
    entity = {'name': 'orphan', 'ne_type_id': 'missing-id'}
    result = evaluate('ne_instance', entity, 'hostname')
    assert result == 'unknown-orphan'


@pytest.mark.unit
def test_evaluate_missing_id_field_uses_fallback(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'hostname': {
                'template': '{{ ne_type.kind | lower }}-{{ name | lower }}',
                'resolve': {
                    'ne_type': {
                        'field': 'ne_type_id',
                        'entity': 'ne_type',
                        'fallback': {'kind': 'x'},
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    entity = {'name': 'no-type'}   # ne_type_id absent
    result = evaluate('ne_instance', entity, 'hostname')
    assert result == 'x-no-type'


# ── evaluate_all ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_evaluate_all_returns_all_fields(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'a': {'template': 'A-{{ name }}', 'resolve': {}},
            'b': {'template': 'B-{{ name }}', 'resolve': {}},
        }
    })
    from core.derivations import evaluate_all
    result = evaluate_all('ne_instance', {'name': 'x'})
    assert result == {'a': 'A-x', 'b': 'B-x'}


@pytest.mark.unit
def test_evaluate_all_empty_for_unknown_entity(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {})
    from core.derivations import evaluate_all
    assert evaluate_all('nope', {'name': 'x'}) == {}


# ── Request-level cache memoizes Redis lookups ────────────────────────────────

@pytest.mark.unit
def test_evaluate_cache_avoids_duplicate_lookups(monkeypatch, fake_r):
    """Two evaluate() calls with the same cache hit Redis only once."""
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'f1': {
                'template': '{{ ne_type.kind }}',
                'resolve': {'ne_type': {'field': 'ne_type_id', 'entity': 'ne_type',
                                        'fallback': {}}},
            },
            'f2': {
                'template': '{{ ne_type.name }}',
                'resolve': {'ne_type': {'field': 'ne_type_id', 'entity': 'ne_type',
                                        'fallback': {}}},
            },
        }
    })
    ne_type = {'id': 'nt-x', 'kind': 'VNF', 'name': 'MyVNF'}
    fake_r.set('ne_type:nt-x', json.dumps(ne_type))

    from core.derivations import evaluate
    cache = {}
    entity = {'name': 'x', 'ne_type_id': 'nt-x'}
    r1 = evaluate('ne_instance', entity, 'f1', cache)
    r2 = evaluate('ne_instance', entity, 'f2', cache)

    assert r1 == 'VNF'
    assert r2 == 'MyVNF'
    # Only one Redis key should have been stored in the cache
    assert len(cache) == 1
    assert 'ne_type:nt-x' in cache


# ── Custom filters ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_pad_filter(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'idx': {'template': '{{ index | pad(3) }}', 'resolve': {}},
        }
    })
    from core.derivations import evaluate
    assert evaluate('ne_instance', {'index': 7}, 'idx') == '007'


@pytest.mark.unit
def test_slugify_filter(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'slug': {'template': '{{ name | slugify }}', 'resolve': {}},
        }
    })
    from core.derivations import evaluate
    assert evaluate('ne_instance', {'name': 'My Router 01'}, 'slug') == 'my-router-01'


# ── Integration: actual derivations.yaml ─────────────────────────────────────

@pytest.mark.unit
def test_hostname_derivation_from_real_yaml(fake_r):
    """
    The hostname derivation declared in config/derivations.yaml produces
    the expected string when a real NE type is present in Redis.
    """
    ne_type = {'id': 'nt-real', 'kind': 'PNF', 'name': 'EdgeRouter'}
    fake_r.set('ne_type:nt-real', json.dumps(ne_type))

    from core.derivations import evaluate
    entity = {'id': 'ne-1', 'name': 'PE-LON-01', 'ne_type_id': 'nt-real',
              'project_id': 'p1'}
    result = evaluate('ne_instance', entity, 'hostname')
    assert result == 'pnf-pe-lon-01'


@pytest.mark.unit
def test_display_label_derivation_from_real_yaml(fake_r):
    ne_type = {'id': 'nt-real', 'kind': 'VNF', 'name': 'vRouter'}
    fake_r.set('ne_type:nt-real', json.dumps(ne_type))

    from core.derivations import evaluate
    entity = {'id': 'ne-2', 'name': 'vpe-001', 'ne_type_id': 'nt-real',
              'project_id': 'p1'}
    result = evaluate('ne_instance', entity, 'display_label')
    assert result == '[VNF] vpe-001'
