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


# ── peer_index computed variable ──────────────────────────────────────────────

@pytest.mark.unit
def test_peer_index_returns_1_for_sole_peer(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'idx': {
                'template': '{{ index }}',
                'resolve': {
                    'index': {
                        'computed': 'peer_index',
                        'index_set': 'project:{project_id}:ne_instances',
                        'peer_prefix': 'ne_inst',
                        'group_by': 'ne_type_id',
                        'sort_by': 'name',
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    entity = {'id': 'ne-1', 'name': 'pe-01', 'ne_type_id': 'nt-1', 'project_id': 'p1'}
    fake_r.set('ne_inst:ne-1', json.dumps(entity))
    fake_r.sadd('project:p1:ne_instances', 'ne-1')

    assert evaluate('ne_instance', entity, 'idx') == '1'


@pytest.mark.unit
def test_peer_index_orders_peers_by_sort_field(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'idx': {
                'template': '{{ index }}',
                'resolve': {
                    'index': {
                        'computed': 'peer_index',
                        'index_set': 'project:{project_id}:ne_instances',
                        'peer_prefix': 'ne_inst',
                        'group_by': 'ne_type_id',
                        'sort_by': 'name',
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    ne_a = {'id': 'ne-a', 'name': 'aaa', 'ne_type_id': 'nt-1', 'project_id': 'p1'}
    ne_b = {'id': 'ne-b', 'name': 'bbb', 'ne_type_id': 'nt-1', 'project_id': 'p1'}
    ne_c = {'id': 'ne-c', 'name': 'ccc', 'ne_type_id': 'nt-1', 'project_id': 'p1'}
    for e in (ne_a, ne_b, ne_c):
        fake_r.set(f'ne_inst:{e["id"]}', json.dumps(e))
        fake_r.sadd('project:p1:ne_instances', e['id'])

    cache = {}
    assert evaluate('ne_instance', ne_a, 'idx', cache) == '1'
    assert evaluate('ne_instance', ne_b, 'idx', cache) == '2'
    assert evaluate('ne_instance', ne_c, 'idx', cache) == '3'


@pytest.mark.unit
def test_peer_index_groups_by_ne_type(monkeypatch, fake_r):
    """Peers of different NE types have independent indices."""
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'idx': {
                'template': '{{ index }}',
                'resolve': {
                    'index': {
                        'computed': 'peer_index',
                        'index_set': 'project:{project_id}:ne_instances',
                        'peer_prefix': 'ne_inst',
                        'group_by': 'ne_type_id',
                        'sort_by': 'name',
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    # Two instances of type A, one of type B
    ne_a1 = {'id': 'ne-a1', 'name': 'aaa', 'ne_type_id': 'nt-A', 'project_id': 'p1'}
    ne_a2 = {'id': 'ne-a2', 'name': 'bbb', 'ne_type_id': 'nt-A', 'project_id': 'p1'}
    ne_b1 = {'id': 'ne-b1', 'name': 'ccc', 'ne_type_id': 'nt-B', 'project_id': 'p1'}
    for e in (ne_a1, ne_a2, ne_b1):
        fake_r.set(f'ne_inst:{e["id"]}', json.dumps(e))
        fake_r.sadd('project:p1:ne_instances', e['id'])

    cache = {}
    assert evaluate('ne_instance', ne_a1, 'idx', cache) == '1'
    assert evaluate('ne_instance', ne_a2, 'idx', cache) == '2'
    assert evaluate('ne_instance', ne_b1, 'idx', cache) == '1'  # resets for type B


@pytest.mark.unit
def test_peer_index_cache_shared_across_calls(monkeypatch, fake_r):
    """The peer list is computed once per (index_set, group_value) per cache."""
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'idx': {
                'template': '{{ index }}',
                'resolve': {
                    'index': {
                        'computed': 'peer_index',
                        'index_set': 'project:{project_id}:ne_instances',
                        'peer_prefix': 'ne_inst',
                        'group_by': 'ne_type_id',
                        'sort_by': 'name',
                    }
                },
            }
        }
    })
    from core.derivations import evaluate
    ne_1 = {'id': 'ne-1', 'name': 'alpha', 'ne_type_id': 'nt-1', 'project_id': 'p1'}
    ne_2 = {'id': 'ne-2', 'name': 'beta',  'ne_type_id': 'nt-1', 'project_id': 'p1'}
    for e in (ne_1, ne_2):
        fake_r.set(f'ne_inst:{e["id"]}', json.dumps(e))
        fake_r.sadd('project:p1:ne_instances', e['id'])

    shared_cache = {}
    evaluate('ne_instance', ne_1, 'idx', shared_cache)
    evaluate('ne_instance', ne_2, 'idx', shared_cache)
    # Exactly one peer-list cache entry for this (set, group) combination
    peer_keys = [k for k in shared_cache if k.startswith('__peer_index__')]
    assert len(peer_keys) == 1


# ── Chained lookup (from_var) ─────────────────────────────────────────────────

@pytest.mark.unit
def test_chained_lookup_resolves_via_intermediate(monkeypatch, fake_r):
    """from_var resolves a field on a previously resolved entity."""
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'site_name': {
                'template': '{{ site.name }}',
                'resolve': {
                    'pod': {
                        'field': 'pod_id',
                        'entity': 'pod',
                        'fallback': {},
                    },
                    'site': {
                        'from_var': 'pod',
                        'field': 'site_id',
                        'entity': 'site',
                        'fallback': {},
                    },
                },
            }
        }
    })
    pod  = {'id': 'pod-1', 'name': 'POD-A', 'site_id': 'site-1'}
    site = {'id': 'site-1', 'name': 'LON'}
    fake_r.set('pod:pod-1',   json.dumps(pod))
    fake_r.set('site:site-1', json.dumps(site))

    from core.derivations import evaluate
    entity = {'id': 'ne-1', 'name': 'x', 'pod_id': 'pod-1', 'project_id': 'p1'}
    result = evaluate('ne_instance', entity, 'site_name')
    assert result == 'LON'


@pytest.mark.unit
def test_chained_lookup_fallback_when_intermediate_missing(monkeypatch, fake_r):
    _patch_derivations(monkeypatch, {
        'ne_instance': {
            'site_name': {
                'template': '{{ site.name | default("unknown") }}',
                'resolve': {
                    'pod':  {'field': 'pod_id',  'entity': 'pod',  'fallback': {}},
                    'site': {'from_var': 'pod', 'field': 'site_id', 'entity': 'site',
                             'fallback': {'name': 'unknown'}},
                },
            }
        }
    })
    from core.derivations import evaluate
    entity = {'id': 'ne-1', 'name': 'x', 'pod_id': 'missing-pod', 'project_id': 'p1'}
    result = evaluate('ne_instance', entity, 'site_name')
    assert result == 'unknown'


# ── Integration: actual derivations.yaml ─────────────────────────────────────

@pytest.mark.unit
def test_hostname_derivation_from_real_yaml(fake_r):
    """
    Hostname follows spec pattern: kind-index (e.g. pnf-01).
    The entity is the first (and only) peer of its NE type.
    """
    ne_type = {'id': 'nt-real', 'kind': 'PNF', 'name': 'EdgeRouter'}
    fake_r.set('ne_type:nt-real', json.dumps(ne_type))

    entity = {'id': 'ne-1', 'name': 'pe-lon-01', 'ne_type_id': 'nt-real',
              'project_id': 'p1'}
    fake_r.set('ne_inst:ne-1', json.dumps(entity))
    fake_r.sadd('project:p1:ne_instances', 'ne-1')

    from core.derivations import evaluate
    result = evaluate('ne_instance', entity, 'hostname')
    assert result == 'pnf-01'


@pytest.mark.unit
def test_hostname_index_increments_per_peer(fake_r):
    """Two PNF instances get indices 01 and 02 in name order."""
    ne_type = {'id': 'nt-real', 'kind': 'PNF', 'name': 'EdgeRouter'}
    fake_r.set('ne_type:nt-real', json.dumps(ne_type))

    ne_a = {'id': 'ne-aaa', 'name': 'aaa', 'ne_type_id': 'nt-real', 'project_id': 'p1'}
    ne_b = {'id': 'ne-bbb', 'name': 'bbb', 'ne_type_id': 'nt-real', 'project_id': 'p1'}
    for e in (ne_a, ne_b):
        fake_r.set(f'ne_inst:{e["id"]}', json.dumps(e))
        fake_r.sadd('project:p1:ne_instances', e['id'])

    from core.derivations import evaluate
    cache = {}
    assert evaluate('ne_instance', ne_a, 'hostname', cache) == 'pnf-01'
    assert evaluate('ne_instance', ne_b, 'hostname', cache) == 'pnf-02'


@pytest.mark.unit
def test_display_label_derivation_from_real_yaml(fake_r):
    ne_type = {'id': 'nt-real', 'kind': 'VNF', 'name': 'vRouter'}
    fake_r.set('ne_type:nt-real', json.dumps(ne_type))

    from core.derivations import evaluate
    entity = {'id': 'ne-2', 'name': 'vpe-001', 'ne_type_id': 'nt-real',
              'project_id': 'p1'}
    result = evaluate('ne_instance', entity, 'display_label')
    assert result == '[VNF] vpe-001'
