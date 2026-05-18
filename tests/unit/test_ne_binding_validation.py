"""
Unit tests for NE-HW binding validation checks in hw_logic.validate_project().

Each test creates the minimum Redis state needed to trigger one specific code,
then asserts that validate_project() emits (or does not emit) that code.
"""
import json
import pytest
import fakeredis


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_r(monkeypatch):
    server = fakeredis.FakeServer()
    fr = fakeredis.FakeRedis(server=server, decode_responses=True)
    import db
    import hw_logic
    for mod in (db, hw_logic):
        monkeypatch.setattr(mod, 'r', fr)
    return fr


PID = 'proj-0001'


def _tmpl(fake_r, ports, category='server', tmpl_id='tmpl-01'):
    tmpl = {
        'id': tmpl_id, 'name': 'TestDev', 'vendor': '', 'model': '',
        'category': category, 'form_factor': '19"', 'u_size': 1,
        'power_w': 0, 'weight_kg': 0, 'max_power_w': 0, 'max_weight_kg': 0,
        'cable_type': '', 'description': '', 'scope': 'global', 'project_id': '',
        'ports': ports,
    }
    fake_r.set(f'hw:template:{tmpl_id}', json.dumps(tmpl))
    fake_r.sadd('hw:templates:index', tmpl_id)
    return tmpl


def _inst(fake_r, tmpl, asset_tag='srv-001', iid='inst-01', rack_id=None):
    loc = {'rack_id': rack_id} if rack_id else {}
    inst = {
        'id': iid, 'template_id': tmpl['id'], 'project_id': PID,
        'asset_tag': asset_tag, 'serial': '', 'status': 'deployed',
        'location': loc, 'port_overrides': {}, 'labels': [],
    }
    fake_r.set(f'hw:instance:{iid}', json.dumps(inst))
    fake_r.sadd('hw:instances:index', iid)
    fake_r.sadd(f'project:{PID}:hw:instances', iid)
    return inst


def _ne_type(fake_r, interfaces, tid='ne-type-01'):
    ne_type = {
        'id': tid, 'name': 'Router', 'kind': 'PNF',
        'description': '', 'labels': [], 'params': {},
        'interfaces': interfaces,
        'scope': 'global', 'project_id': '',
    }
    fake_r.set(f'ne_type:{tid}', json.dumps(ne_type))
    fake_r.sadd('ne_types:index', tid)
    return ne_type


def _ne_inst(fake_r, ne_type, iface_bindings, name='pe-01', nid='ne-inst-01'):
    inst = {
        'id': nid, 'name': name, 'ne_type_id': ne_type['id'],
        'project_id': PID, 'description': '', 'labels': [], 'params': {},
        'iface_bindings': iface_bindings,
    }
    fake_r.set(f'ne_inst:{nid}', json.dumps(inst))
    fake_r.sadd('ne_instances:index', nid)
    fake_r.sadd(f'project:{PID}:ne_instances', nid)
    return inst


def _codes(issues):
    return [i['code'] for i in issues]


# ──────────────────────────────────────────────────────────────────────────────
# NE_PORT_DOUBLE_BOUND
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_port_double_bound(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'wan', 'labels': ['data'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
        {'id': 'if-b', 'name': 'lan', 'labels': ['data'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    binding = {'bind_mode': 'single',
               'ports': [{'hw_instance_id': inst['id'], 'port_id': 'p1',
                          'role': 'primary', 'bucket': []}]}
    # Two NE instances both claim the same port
    _ne_inst(fake_r, ne_type, {'if-a': binding}, name='pe-01', nid='ne-01')
    _ne_inst(fake_r, ne_type, {'if-b': binding}, name='pe-02', nid='ne-02')

    issues = hw_logic.validate_project(PID)
    assert 'NE_PORT_DOUBLE_BOUND' in _codes(issues)
    assert any(i['severity'] == 'error' for i in issues
               if i['code'] == 'NE_PORT_DOUBLE_BOUND')


@pytest.mark.unit
def test_no_double_bound_when_different_ports(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
        {'id': 'p2', 'name': 'Eth1', 'port_type': 'data', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'wan', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
        {'id': 'if-b', 'name': 'lan', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type,
             {'if-a': {'bind_mode': 'single',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'p1',
                                  'role': 'primary', 'bucket': []}]}},
             name='pe-01', nid='ne-01')
    _ne_inst(fake_r, ne_type,
             {'if-b': {'bind_mode': 'single',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'p2',
                                  'role': 'primary', 'bucket': []}]}},
             name='pe-02', nid='ne-02')

    issues = hw_logic.validate_project(PID)
    assert 'NE_PORT_DOUBLE_BOUND' not in _codes(issues)


# ──────────────────────────────────────────────────────────────────────────────
# NE_PORT_NOT_FOUND
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_port_not_found(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'wan', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type,
             {'if-a': {'bind_mode': 'single',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'p-nonexistent',
                                  'role': 'primary', 'bucket': []}]}})

    issues = hw_logic.validate_project(PID)
    assert 'NE_PORT_NOT_FOUND' in _codes(issues)
    assert any(i['severity'] == 'error' for i in issues if i['code'] == 'NE_PORT_NOT_FOUND')


# ──────────────────────────────────────────────────────────────────────────────
# NE_PORT_TYPE_MISMATCH
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_port_type_mismatch_data_on_mgmt(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        # iface labeled 'data' bound to a 'mgmt' port — mismatch
        {'id': 'if-a', 'name': 'uplink', 'labels': ['data'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type,
             {'if-a': {'bind_mode': 'single',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                                  'role': 'primary', 'bucket': []}]}})

    issues = hw_logic.validate_project(PID)
    assert 'NE_PORT_TYPE_MISMATCH' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues
               if i['code'] == 'NE_PORT_TYPE_MISMATCH')


@pytest.mark.unit
def test_no_type_mismatch_when_labels_absent(fake_r):
    """No mismatch reported when iface carries no data/mgmt label."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'uplink', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type,
             {'if-a': {'bind_mode': 'single',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                                  'role': 'primary', 'bucket': []}]}})

    issues = hw_logic.validate_project(PID)
    assert 'NE_PORT_TYPE_MISMATCH' not in _codes(issues)


# ──────────────────────────────────────────────────────────────────────────────
# NE_LAG_SINGLE_PORT
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_lag_single_port(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'uplink', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type,
             {'if-a': {'bind_mode': 'lag',
                       'ports': [{'hw_instance_id': inst['id'], 'port_id': 'p1',
                                  'role': 'member', 'bucket': []}]}})

    issues = hw_logic.validate_project(PID)
    assert 'NE_LAG_SINGLE_PORT' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues if i['code'] == 'NE_LAG_SINGLE_PORT')


# ──────────────────────────────────────────────────────────────────────────────
# NE_LAG_SPEED_MISMATCH
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_lag_speed_mismatch(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
        {'id': 'p2', 'name': 'Eth1', 'port_type': 'data', 'connector': 'SFP+',
         'speed_gbps': 10, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'uplink', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'lag',
        'ports': [
            {'hw_instance_id': inst['id'], 'port_id': 'p1', 'role': 'member', 'bucket': []},
            {'hw_instance_id': inst['id'], 'port_id': 'p2', 'role': 'member', 'bucket': []},
        ],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_LAG_SPEED_MISMATCH' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues if i['code'] == 'NE_LAG_SPEED_MISMATCH')


@pytest.mark.unit
def test_no_lag_speed_mismatch_when_same(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
        {'id': 'p2', 'name': 'Eth1', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'uplink', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'lag',
        'ports': [
            {'hw_instance_id': inst['id'], 'port_id': 'p1', 'role': 'member', 'bucket': []},
            {'hw_instance_id': inst['id'], 'port_id': 'p2', 'role': 'member', 'bucket': []},
        ],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_LAG_SPEED_MISMATCH' not in _codes(issues)


# ──────────────────────────────────────────────────────────────────────────────
# NE_LAG_MIXED_HOSTS
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_lag_mixed_hosts(fake_r):
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'p1', 'name': 'Eth0', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
    ])
    inst_a = _inst(fake_r, tmpl, asset_tag='srv-001', iid='inst-a')
    inst_b = _inst(fake_r, tmpl, asset_tag='srv-002', iid='inst-b')
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'uplink', 'labels': [], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'lag',
        'ports': [
            {'hw_instance_id': inst_a['id'], 'port_id': 'p1', 'role': 'member', 'bucket': []},
            {'hw_instance_id': inst_b['id'], 'port_id': 'p1', 'role': 'member', 'bucket': []},
        ],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_LAG_MIXED_HOSTS' in _codes(issues)
    assert any(i['severity'] == 'info' for i in issues if i['code'] == 'NE_LAG_MIXED_HOSTS')


# ──────────────────────────────────────────────────────────────────────────────
# NE_RULE_NO_MATCH
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_rule_no_match(fake_r):
    """Auto-rule binding with zero ports[] emits NE_RULE_NO_MATCH."""
    import hw_logic
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': ['mgmt'], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '^iLO$', 'group_by': []},
        'rule_materialized_at': '2026-01-01T00:00:00+00:00',
        'ports': [],   # zero matches stored
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_NO_MATCH' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues if i['code'] == 'NE_RULE_NO_MATCH')


# ──────────────────────────────────────────────────────────────────────────────
# NE_RULE_UNRACKED_PORT
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_rule_unracked_port(fake_r):
    """Auto-rule with group_by=rack and a matched port on an unracked device."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    # rack_id absent → unracked
    inst = _inst(fake_r, tmpl, rack_id=None)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '.*', 'group_by': ['rack']},
        'rule_materialized_at': '2026-01-01T00:00:00+00:00',
        'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                   'role': 'primary', 'bucket': ['rack:unracked']}],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_UNRACKED_PORT' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues if i['code'] == 'NE_RULE_UNRACKED_PORT')


@pytest.mark.unit
def test_no_unracked_warning_when_not_grouping_by_rack(fake_r):
    """NE_RULE_UNRACKED_PORT is not emitted when group_by does not include 'rack'."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl, rack_id=None)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '.*', 'group_by': []},  # no 'rack'
        'rule_materialized_at': '2026-01-01T00:00:00+00:00',
        'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                   'role': 'primary', 'bucket': []}],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_UNRACKED_PORT' not in _codes(issues)


# ──────────────────────────────────────────────────────────────────────────────
# NE_RULE_STALE
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_rule_stale_new_instance(fake_r):
    """Stored ports[] disagrees with fresh materialization → NE_RULE_STALE."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    # Stored ports is empty, but the rule would now match inst's iLO port
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '.*', 'group_by': []},
        'rule_materialized_at': '2026-01-01T00:00:00+00:00',
        'ports': [],  # stale: should be inst/ilo
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_STALE' in _codes(issues)
    assert any(i['severity'] == 'warning' for i in issues if i['code'] == 'NE_RULE_STALE')


@pytest.mark.unit
def test_ne_rule_not_stale_when_fresh(fake_r):
    """Stored ports[] matches fresh materialization → no NE_RULE_STALE."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '.*', 'group_by': []},
        'rule_materialized_at': '2026-05-01T00:00:00+00:00',
        'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                   'role': 'primary', 'bucket': []}],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_STALE' not in _codes(issues)


@pytest.mark.unit
def test_ne_rule_stale_not_emitted_without_materialized_at(fake_r):
    """If rule_materialized_at is absent the rule has never run — no STALE."""
    import hw_logic
    ne_type = _ne_type(fake_r, [
        {'id': 'if-a', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {'if-a': {
        'bind_mode': 'auto-rule',
        'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                 'name_regex': '.*', 'group_by': []},
        # no rule_materialized_at
        'ports': [],
    }})

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_STALE' not in _codes(issues)


# ──────────────────────────────────────────────────────────────────────────────
# NE_RULE_EXPLICIT_OVERLAP
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_ne_rule_explicit_overlap(fake_r):
    """Auto-rule would match a port that is already explicitly bound elsewhere."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)

    # NE type for the auto-rule NE
    ne_type_rule = _ne_type(fake_r, [
        {'id': 'if-oob', 'name': 'oob', 'labels': [], 'sharing': 'project',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ], tid='ne-type-rule')

    # NE type for the explicit-binding NE
    ne_type_exp = _ne_type(fake_r, [
        {'id': 'if-mgmt', 'name': 'mgmt', 'labels': ['mgmt'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ], tid='ne-type-exp')

    # Explicit binding on inst/ilo by a different NE instance
    _ne_inst(fake_r, ne_type_exp,
             {'if-mgmt': {'bind_mode': 'single',
                          'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                                     'role': 'primary', 'bucket': []}]}},
             name='explicit-ne', nid='ne-explicit')

    # Auto-rule NE whose rule would match inst/ilo but it's excluded by the explicit binding
    _ne_inst(fake_r, ne_type_rule,
             {'if-oob': {
                 'bind_mode': 'auto-rule',
                 'rule': {'port_types': ['mgmt'], 'categories': ['server'],
                          'name_regex': '.*', 'group_by': []},
                 'rule_materialized_at': '2026-01-01T00:00:00+00:00',
                 'ports': [],  # empty because the explicit binding excluded it
             }},
             name='oob-ne', nid='ne-rule')

    issues = hw_logic.validate_project(PID)
    assert 'NE_RULE_EXPLICIT_OVERLAP' in _codes(issues)
    assert any(i['severity'] == 'info' for i in issues if i['code'] == 'NE_RULE_EXPLICIT_OVERLAP')


# ──────────────────────────────────────────────────────────────────────────────
# Clean project — no false positives
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_no_binding_issues_when_project_is_clean(fake_r):
    """A well-configured single binding emits no NE binding codes."""
    import hw_logic
    tmpl = _tmpl(fake_r, [
        {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt', 'connector': 'RJ45',
         'speed_gbps': 1, 'count': 1, 'notes': ''},
        {'id': 'eth0', 'name': 'eth0', 'port_type': 'data', 'connector': 'SFP28',
         'speed_gbps': 25, 'count': 1, 'notes': ''},
    ])
    inst = _inst(fake_r, tmpl)
    ne_type = _ne_type(fake_r, [
        {'id': 'if-mgmt', 'name': 'mgmt', 'labels': ['mgmt'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
        {'id': 'if-data', 'name': 'uplink', 'labels': ['data'], 'sharing': 'ne',
         'ipv4': None, 'ipv6': None, 'params': {}},
    ])
    _ne_inst(fake_r, ne_type, {
        'if-mgmt': {'bind_mode': 'single',
                    'ports': [{'hw_instance_id': inst['id'], 'port_id': 'ilo',
                               'role': 'primary', 'bucket': []}]},
        'if-data': {'bind_mode': 'single',
                    'ports': [{'hw_instance_id': inst['id'], 'port_id': 'eth0',
                               'role': 'primary', 'bucket': []}]},
    })

    ne_binding_codes = {
        'NE_PORT_DOUBLE_BOUND', 'NE_PORT_NOT_FOUND', 'NE_PORT_TYPE_MISMATCH',
        'NE_LAG_SINGLE_PORT', 'NE_LAG_SPEED_MISMATCH', 'NE_LAG_MIXED_HOSTS',
        'NE_RULE_NO_MATCH', 'NE_RULE_UNRACKED_PORT', 'NE_RULE_STALE',
        'NE_RULE_EXPLICIT_OVERLAP',
    }
    issues = hw_logic.validate_project(PID)
    emitted = set(_codes(issues)) & ne_binding_codes
    assert emitted == set(), f'Unexpected binding issues on clean project: {emitted}'
