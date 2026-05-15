"""
Unit tests for NE instance model helpers and the port-bound index.
"""
import pytest
import fakeredis
import db


@pytest.fixture(autouse=True)
def _fake_r(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    import hw_logic, ne
    monkeypatch.setattr(db, 'r', fake)
    monkeypatch.setattr(hw_logic, 'r', fake)
    monkeypatch.setattr(ne, 'r', fake)
    return fake


def _inst(nid='ne-001', pid='proj-1', **kw):
    base = {
        'id': nid, 'ne_type_id': 'netype-1', 'project_id': pid,
        'name': 'pe-lon-01', 'description': '', 'labels': [],
        'params': {}, 'iface_bindings': {},
    }
    base.update(kw)
    return base


import ne
import hw_logic


# ── Port-bound index helpers ───────────────────────────────────────────────────

@pytest.mark.unit
def test_set_get_clear_port_bound():
    hw_logic.set_port_bound('iid-1', 'eth0', 'ne-1', 'iface-1', 'single')
    info = hw_logic.get_port_bound('iid-1', 'eth0')
    assert info['ne_instance_id'] == 'ne-1'
    assert info['iface_id']       == 'iface-1'
    assert info['bind_mode']      == 'single'

    hw_logic.clear_port_bound('iid-1', 'eth0')
    assert hw_logic.get_port_bound('iid-1', 'eth0') is None


@pytest.mark.unit
def test_hw_instance_bindings_empty():
    assert hw_logic.hw_instance_bindings('iid-x') == []


@pytest.mark.unit
def test_hw_instance_bindings_returns_all():
    hw_logic.set_port_bound('iid-2', 'eth0', 'ne-1', 'iface-1', 'single')
    hw_logic.set_port_bound('iid-2', 'eth1', 'ne-1', 'iface-2', 'lag')
    result = hw_logic.hw_instance_bindings('iid-2')
    port_ids = {b['port_id'] for b in result}
    assert 'eth0' in port_ids
    assert 'eth1' in port_ids


# ── NE instance model ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_save_and_get_ne_instance():
    inst = _inst()
    ne.save_ne_instance(inst)
    loaded = ne.get_ne_instance('ne-001')
    assert loaded is not None
    assert loaded['name'] == 'pe-lon-01'


@pytest.mark.unit
def test_save_ne_instance_appears_in_project_list():
    ne.save_ne_instance(_inst())
    insts = ne.project_ne_instances('proj-1')
    assert any(i['id'] == 'ne-001' for i in insts)


@pytest.mark.unit
def test_save_ne_instance_updates_port_bound_index():
    inst = _inst(iface_bindings={
        'iface-1': {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'eth0',
                       'role': 'primary', 'bucket': []}],
        }
    })
    ne.save_ne_instance(inst)
    info = hw_logic.get_port_bound('hw-1', 'eth0')
    assert info['ne_instance_id'] == 'ne-001'
    assert info['bind_mode'] == 'single'


@pytest.mark.unit
def test_save_ne_instance_releases_removed_port():
    inst = _inst(iface_bindings={
        'iface-1': {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'eth0',
                       'role': 'primary', 'bucket': []}],
        }
    })
    ne.save_ne_instance(inst)
    assert hw_logic.get_port_bound('hw-1', 'eth0') is not None

    # Now remove the binding
    inst2 = {**inst, 'iface_bindings': {}}
    ne.save_ne_instance(inst2)
    assert hw_logic.get_port_bound('hw-1', 'eth0') is None


@pytest.mark.unit
def test_delete_ne_instance_releases_port_bound():
    inst = _inst(iface_bindings={
        'iface-1': {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'eth0',
                       'role': 'primary', 'bucket': []}],
        }
    })
    ne.save_ne_instance(inst)
    ne.delete_ne_instance('ne-001')

    assert ne.get_ne_instance('ne-001') is None
    assert hw_logic.get_port_bound('hw-1', 'eth0') is None
    assert 'ne-001' not in db.r.smembers('project:proj-1:ne_instances')


@pytest.mark.unit
def test_delete_ne_instance_not_found_noop():
    # Should not raise
    ne.delete_ne_instance('does-not-exist')


@pytest.mark.unit
def test_collect_excluded_ports_skips_target():
    # Two NE instances in same project
    inst_a = _inst(nid='ne-a', iface_bindings={
        'iface-1': {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'eth0',
                       'role': 'primary', 'bucket': []}],
        }
    })
    inst_b = _inst(nid='ne-b', iface_bindings={
        'iface-1': {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'eth1',
                       'role': 'primary', 'bucket': []}],
        }
    })
    ne.save_ne_instance(inst_a)
    ne.save_ne_instance(inst_b)

    # Exclude ports of others when editing ne-b
    excluded = ne._collect_excluded_ports('proj-1', 'ne-b')
    assert ('hw-1', 'eth0') in excluded   # from ne-a
    assert ('hw-1', 'eth1') not in excluded  # ne-b's own ports are not excluded


@pytest.mark.unit
def test_ne_instance_with_type_enriches():
    import json
    # Save a minimal NE type
    ne_type = {'id': 'netype-1', 'name': 'Router', 'kind': 'PNF',
               'description': '', 'labels': [], 'params': {},
               'interfaces': [], 'scope': 'global', 'project_id': ''}
    db.r.set('ne_type:netype-1', json.dumps(ne_type))

    ne.save_ne_instance(_inst())
    enriched = ne.ne_instance_with_type('ne-001')
    assert enriched['ne_type']['name'] == 'Router'


@pytest.mark.unit
def test_ne_instance_lag_binding():
    inst = _inst(iface_bindings={
        'iface-1': {
            'bind_mode': 'lag',
            'lag_id': 1,
            'ports': [
                {'hw_instance_id': 'hw-1', 'port_id': 'eth0', 'role': 'member', 'bucket': []},
                {'hw_instance_id': 'hw-1', 'port_id': 'eth1', 'role': 'member', 'bucket': []},
            ],
        }
    })
    ne.save_ne_instance(inst)
    # Both ports should be indexed
    assert hw_logic.get_port_bound('hw-1', 'eth0') is not None
    assert hw_logic.get_port_bound('hw-1', 'eth1') is not None
    bindings = hw_logic.hw_instance_bindings('hw-1')
    assert len(bindings) == 2


@pytest.mark.unit
def test_auto_rule_binding_saved():
    rule = {'port_types': ['mgmt'], 'name_regex': '^iLO$',
            'categories': ['server'], 'group_by': ['rack']}
    inst = _inst(iface_bindings={
        'iface-1': {
            'bind_mode': 'auto-rule',
            'rule': rule,
            'rule_materialized_at': '2026-01-01T00:00:00',
            'ports': [{'hw_instance_id': 'hw-1', 'port_id': 'ilo',
                       'role': 'primary', 'bucket': ['rack:R-01']}],
        }
    })
    ne.save_ne_instance(inst)
    loaded = ne.get_ne_instance('ne-001')
    binding = loaded['iface_bindings']['iface-1']
    assert binding['bind_mode'] == 'auto-rule'
    assert binding['rule']['name_regex'] == '^iLO$'
    assert binding['ports'][0]['bucket'] == ['rack:R-01']
    # Auto-rule ports ARE indexed
    assert hw_logic.get_port_bound('hw-1', 'ilo') is not None
