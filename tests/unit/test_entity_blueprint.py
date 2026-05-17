"""
Unit tests for core/entity_blueprint.py — the EntityBlueprint base class.
"""
import json
import pytest
import fakeredis


@pytest.fixture()
def fake_r(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    import db
    monkeypatch.setattr(db, 'r', fr)
    return fr


# ── A minimal concrete subclass used in tests ──────────────────────────────────

def _make_blueprint():
    from core.entity_blueprint import EntityBlueprint

    class ZoneBlueprint(EntityBlueprint):
        entity_prefix         = 'zone'
        global_index          = 'zones:index'
        project_index_pattern = 'project:{pid}:zones'

    return ZoneBlueprint()


def _zone(zid='z-001', name='LON', pid='proj-1'):
    return {'id': zid, 'name': name, 'project_id': pid}


# ── save / get / exists ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_save_persists_to_redis(fake_r):
    zones = _make_blueprint()
    zone  = _zone()
    zones.save(zone)
    raw = fake_r.get('zone:z-001')
    assert raw is not None
    assert json.loads(raw)['name'] == 'LON'


@pytest.mark.unit
def test_save_adds_to_global_index(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    assert fake_r.sismember('zones:index', 'z-001')


@pytest.mark.unit
def test_save_adds_to_project_index(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    assert fake_r.sismember('project:proj-1:zones', 'z-001')


@pytest.mark.unit
def test_get_returns_entity(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    result = zones.get('z-001')
    assert result is not None
    assert result['name'] == 'LON'


@pytest.mark.unit
def test_get_returns_none_for_missing(fake_r):
    zones = _make_blueprint()
    assert zones.get('no-such-id') is None


@pytest.mark.unit
def test_exists_true(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    assert zones.exists('z-001') is True


@pytest.mark.unit
def test_exists_false(fake_r):
    zones = _make_blueprint()
    assert zones.exists('missing') is False


# ── delete ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_delete_removes_entity(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    zones.delete('z-001')
    assert zones.get('z-001') is None


@pytest.mark.unit
def test_delete_removes_from_indices(fake_r):
    zones = _make_blueprint()
    zones.save(_zone())
    zones.delete('z-001')
    assert not fake_r.sismember('zones:index', 'z-001')
    assert not fake_r.sismember('project:proj-1:zones', 'z-001')


@pytest.mark.unit
def test_delete_nonexistent_is_noop(fake_r):
    zones = _make_blueprint()
    zones.delete('ghost')   # must not raise


# ── all / project_entities ─────────────────────────────────────────────────────

@pytest.mark.unit
def test_all_returns_all_saved(fake_r):
    zones = _make_blueprint()
    zones.save(_zone('z-001', 'LON'))
    zones.save(_zone('z-002', 'AMS'))
    result = zones.all()
    assert len(result) == 2
    assert [z['name'] for z in result] == ['AMS', 'LON']  # sorted by name


@pytest.mark.unit
def test_all_empty_when_no_entities(fake_r):
    zones = _make_blueprint()
    assert zones.all() == []


@pytest.mark.unit
def test_project_entities_filters_by_project(fake_r):
    zones = _make_blueprint()
    zones.save(_zone('z-001', 'LON', pid='proj-A'))
    zones.save(_zone('z-002', 'AMS', pid='proj-B'))
    result = zones.project_entities('proj-A')
    assert len(result) == 1
    assert result[0]['name'] == 'LON'


@pytest.mark.unit
def test_project_entities_empty_for_unknown_project(fake_r):
    zones = _make_blueprint()
    assert zones.project_entities('no-such-project') == []


# ── on_save hook ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_on_save_hook_can_enrich_entity(fake_r):
    from core.entity_blueprint import EntityBlueprint

    class TaggedBlueprint(EntityBlueprint):
        entity_prefix         = 'tagged'
        global_index          = 'tagged:index'
        project_index_pattern = 'project:{pid}:tagged'

        def on_save(self, entity):
            return {**entity, 'auto_tag': 'injected'}

    bp   = TaggedBlueprint()
    bp.save({'id': 't-1', 'name': 'x', 'project_id': 'p'})
    result = bp.get('t-1')
    assert result['auto_tag'] == 'injected'


# ── on_delete hook ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_on_delete_hook_is_called(fake_r):
    from core.entity_blueprint import EntityBlueprint

    deleted_ids = []

    class HookedBlueprint(EntityBlueprint):
        entity_prefix         = 'hooked'
        global_index          = 'hooked:index'
        project_index_pattern = 'project:{pid}:hooked'

        def on_delete(self, entity_id):
            deleted_ids.append(entity_id)

    bp = HookedBlueprint()
    bp.save({'id': 'h-1', 'name': 'test', 'project_id': 'p'})
    bp.delete('h-1')
    assert 'h-1' in deleted_ids


# ── key pattern override ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_custom_key_pattern(fake_r):
    from core.entity_blueprint import EntityBlueprint

    class PrefixedBlueprint(EntityBlueprint):
        entity_prefix         = 'ns:widget'
        global_index          = 'ns:widgets:index'
        project_index_pattern = 'project:{pid}:ns:widgets'

    bp = PrefixedBlueprint()
    bp.save({'id': 'w-1', 'name': 'gadget', 'project_id': 'p'})
    assert fake_r.exists('ns:widget:w-1')
    assert bp.get('w-1')['name'] == 'gadget'


# ── sorted_by override ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_sorted_by_override(fake_r):
    from core.entity_blueprint import EntityBlueprint

    class SortedBlueprint(EntityBlueprint):
        entity_prefix         = 'sortable'
        global_index          = 'sortables:index'
        project_index_pattern = 'project:{pid}:sortables'
        sorted_by             = 'code'

    bp = SortedBlueprint()
    bp.save({'id': 's-1', 'name': 'Z', 'code': 'A', 'project_id': 'p'})
    bp.save({'id': 's-2', 'name': 'A', 'code': 'C', 'project_id': 'p'})
    bp.save({'id': 's-3', 'name': 'M', 'code': 'B', 'project_id': 'p'})

    codes = [e['code'] for e in bp.all()]
    assert codes == ['A', 'B', 'C']
