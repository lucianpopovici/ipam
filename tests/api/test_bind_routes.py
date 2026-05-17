"""
API tests for NE instance CRUD and binding routes.
Uses Flask test client + fakeredis.
"""
import json
import pytest
import fakeredis


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    import db
    import ipam
    import ne
    import hw_logic
    for mod in (db, ipam, ne, hw_logic):
        monkeypatch.setattr(mod, 'r', fake_r)
    from hw import seed_connectors
    seed_connectors()
    from app import app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    with app.test_client() as c:
        # Auto-login
        with c.session_transaction() as sess:
            sess['_user_id'] = 'admin'
        yield c


@pytest.fixture()
def project(client):
    client.post('/projects/add',
                data={'name': 'Test', 'supernet': '10.0.0.0/8'},
                follow_redirects=True)
    # Get PID from redirect URL
    import db as db_mod
    pid = db_mod.r.smembers('projects:index').pop()
    return pid


@pytest.fixture()
def ne_type(client):
    import db as db_mod
    from db import new_id
    ne_type = {
        'id': new_id(), 'name': 'TestRouter', 'kind': 'PNF',
        'description': '', 'labels': [], 'params': {},
        'interfaces': [
            {'id': 'iface-1', 'name': 'mgmt', 'labels': ['mgmt'],
             'sharing': 'ne', 'ipv4': {'prefix_len': 29}, 'ipv6': None, 'params': {}},
        ],
        'scope': 'global', 'project_id': '',
    }
    db_mod.r.set(f'ne_type:{ne_type["id"]}', json.dumps(ne_type))
    db_mod.r.sadd('ne_types:index', ne_type['id'])
    return ne_type


@pytest.fixture()
def hw_tmpl(client):
    import db as db_mod
    from db import new_id
    tmpl = {
        'id': new_id(), 'name': 'Srv', 'vendor': '', 'model': '',
        'category': 'server', 'form_factor': '19"', 'u_size': 1,
        'cable_type': '', 'description': '', 'scope': 'global', 'project_id': '',
        'ports': [
            {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
             'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
            {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
             'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
        ],
    }
    db_mod.r.set(f'hw:template:{tmpl["id"]}', json.dumps(tmpl))
    db_mod.r.sadd('hw:templates:index', tmpl['id'])
    return tmpl


@pytest.fixture()
def hw_instance(client, project, hw_tmpl):
    import db as db_mod
    from db import new_id
    inst = {
        'id': new_id(), 'template_id': hw_tmpl['id'], 'project_id': project,
        'asset_tag': 'srv-001', 'serial': '', 'status': 'deployed',
        'location': {}, 'port_overrides': {}, 'labels': [],
    }
    db_mod.r.set(f'hw:instance:{inst["id"]}', json.dumps(inst))
    db_mod.r.sadd('hw:instances:index', inst['id'])
    db_mod.r.sadd(f'project:{project}:hw:instances', inst['id'])
    return inst


# ──────────────────────────────────────────────────────────────────────────────
# NE Instance CRUD
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_list_ne_instances_empty(client, project):
    r = client.get(f'/projects/{project}/ne-instances')
    assert r.status_code == 200
    assert b'No NE instances yet' in r.data


@pytest.mark.api
def test_add_ne_instance(client, project, ne_type):
    r = client.post(f'/projects/{project}/ne-instances/add',
                    data={'name': 'pe-lon-01', 'ne_type_id': ne_type['id'],
                          'description': '', 'labels': ''},
                    follow_redirects=True)
    assert r.status_code == 200
    assert b'pe-lon-01' in r.data


@pytest.mark.api
def test_add_ne_instance_missing_name(client, project, ne_type):
    r = client.post(f'/projects/{project}/ne-instances/add',
                    data={'name': '', 'ne_type_id': ne_type['id']})
    assert r.status_code == 200
    assert b'is-invalid' in r.data


@pytest.mark.api
def test_add_ne_instance_missing_type(client, project):
    r = client.post(f'/projects/{project}/ne-instances/add',
                    data={'name': 'test', 'ne_type_id': ''})
    assert r.status_code == 200
    assert b'is-invalid' in r.data


@pytest.mark.api
def test_ne_instance_detail(client, project, ne_type):
    client.post(f'/projects/{project}/ne-instances/add',
                data={'name': 'pe-lon-01', 'ne_type_id': ne_type['id'],
                      'description': '', 'labels': ''},
                follow_redirects=True)
    # Find the NE instance ID
    import ne as ne_mod
    insts = ne_mod.project_ne_instances(project)
    nid = insts[0]['id']

    r = client.get(f'/projects/{project}/ne-instances/{nid}')
    assert r.status_code == 200
    assert b'pe-lon-01' in r.data
    assert b'mgmt' in r.data  # iface name


@pytest.mark.api
def test_edit_ne_instance(client, project, ne_type):
    client.post(f'/projects/{project}/ne-instances/add',
                data={'name': 'pe-lon-01', 'ne_type_id': ne_type['id'],
                      'description': '', 'labels': ''},
                follow_redirects=True)
    import ne as ne_mod
    nid = ne_mod.project_ne_instances(project)[0]['id']

    r = client.post(f'/projects/{project}/ne-instances/{nid}/edit',
                    data={'name': 'pe-lon-02', 'description': 'renamed'},
                    follow_redirects=True)
    assert r.status_code == 200
    assert b'pe-lon-02' in r.data


@pytest.mark.api
def test_delete_ne_instance(client, project, ne_type):
    client.post(f'/projects/{project}/ne-instances/add',
                data={'name': 'to-delete', 'ne_type_id': ne_type['id'],
                      'description': '', 'labels': ''},
                follow_redirects=True)
    import ne as ne_mod
    nid = ne_mod.project_ne_instances(project)[0]['id']

    r = client.post(f'/projects/{project}/ne-instances/{nid}/delete',
                    follow_redirects=True)
    assert r.status_code == 200
    assert ne_mod.get_ne_instance(nid) is None


# ──────────────────────────────────────────────────────────────────────────────
# Binding routes
# ──────────────────────────────────────────────────────────────────────────────

def _create_ne_inst(client, project, ne_type):
    client.post(f'/projects/{project}/ne-instances/add',
                data={'name': 'test-ne', 'ne_type_id': ne_type['id'],
                      'description': '', 'labels': ''},
                follow_redirects=True)
    import ne as ne_mod
    return ne_mod.project_ne_instances(project)[0]['id']


@pytest.mark.api
def test_bind_single_port(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    iface_id = 'iface-1'
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    r = client.post(f'/ne-instances/{nid}/bindings/{iface_id}',
                    data={'bind_mode': 'single', 'ports_json': ports_json,
                          'lag_id': ''},
                    follow_redirects=True)
    assert r.status_code == 200
    # Port is now indexed
    import hw_logic
    info = hw_logic.get_port_bound(hw_instance['id'], 'ilo')
    assert info is not None
    assert info['ne_instance_id'] == nid


@pytest.mark.api
def test_bind_lag(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    ports_json = json.dumps([
        {'hw_instance_id': hw_instance['id'], 'port_id': 'eth0', 'role': 'member'},
    ])
    r = client.post(f'/ne-instances/{nid}/bindings/iface-1',
                    data={'bind_mode': 'lag', 'ports_json': ports_json, 'lag_id': '1'},
                    follow_redirects=True)
    assert r.status_code == 200
    import ne as ne_mod
    inst = ne_mod.get_ne_instance(nid)
    binding = inst['iface_bindings'].get('iface-1', {})
    assert binding['bind_mode'] == 'lag'


@pytest.mark.api
def test_unbind_iface(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    # Bind first
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    client.post(f'/ne-instances/{nid}/bindings/iface-1',
                data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                follow_redirects=True)
    # Unbind
    r = client.post(f'/ne-instances/{nid}/bindings/iface-1/delete',
                    follow_redirects=True)
    assert r.status_code == 200
    import hw_logic
    assert hw_logic.get_port_bound(hw_instance['id'], 'ilo') is None


@pytest.mark.api
def test_bind_auto_rule(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    rule = {'port_types': ['mgmt'], 'name_regex': '.*', 'categories': ['server'],
            'group_by': []}
    r = client.post(f'/ne-instances/{nid}/bindings/iface-1',
                    data={'bind_mode': 'auto-rule',
                          'rule_json': json.dumps(rule)},
                    follow_redirects=True)
    assert r.status_code == 200
    import ne as ne_mod
    inst = ne_mod.get_ne_instance(nid)
    binding = inst['iface_bindings'].get('iface-1', {})
    assert binding['bind_mode'] == 'auto-rule'
    # 'ilo' matched (port_type=mgmt, category=server)
    assert any(p['port_id'] == 'ilo' for p in binding['ports'])


@pytest.mark.api
def test_rematerialize_auto_rule(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    rule = {'port_types': ['mgmt'], 'name_regex': '.*', 'categories': ['server'],
            'group_by': []}
    client.post(f'/ne-instances/{nid}/bindings/iface-1',
                data={'bind_mode': 'auto-rule', 'rule_json': json.dumps(rule)},
                follow_redirects=True)
    # Rematerialize
    r = client.post(f'/ne-instances/{nid}/bindings/iface-1/rematerialize',
                    follow_redirects=True)
    assert r.status_code == 200


@pytest.mark.api
def test_port_conflict_blocked(client, project, ne_type, hw_instance):
    """Two NE instances cannot share the same port (explicit binding)."""
    nid_a = _create_ne_inst(client, project, ne_type)

    import ne as ne_mod
    from db import new_id
    # Create second NE instance directly
    inst_b = {
        'id': new_id(), 'ne_type_id': ne_type['id'], 'project_id': project,
        'name': 'ne-b', 'description': '', 'labels': [], 'params': {},
        'iface_bindings': {},
    }
    ne_mod.save_ne_instance(inst_b)
    nid_b = inst_b['id']

    # Bind nid_a to 'ilo'
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    client.post(f'/ne-instances/{nid_a}/bindings/iface-1',
                data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                follow_redirects=True)

    # Attempt to bind nid_b to the same port — should be blocked
    r = client.post(f'/ne-instances/{nid_b}/bindings/iface-1',
                    data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                    follow_redirects=True)
    assert r.status_code == 200
    assert b'already bound' in r.data.lower() or b'Port(s) already bound' in r.data


# ──────────────────────────────────────────────────────────────────────────────
# API endpoints
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_api_free_ports(client, project, hw_instance):
    r = client.get(f'/api/projects/{project}/hw/{hw_instance["id"]}/free-ports')
    assert r.status_code == 200
    data = r.get_json()
    assert 'ports' in data
    assert any(p['port_id'] == 'ilo' for p in data['ports'])


@pytest.mark.api
def test_api_free_ports_type_filter(client, project, hw_instance):
    r = client.get(f'/api/projects/{project}/hw/{hw_instance["id"]}/free-ports?type=mgmt')
    data = r.get_json()
    assert all(p['port_type'] == 'mgmt' for p in data['ports'])


@pytest.mark.api
def test_api_free_ports_shows_bound(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    client.post(f'/ne-instances/{nid}/bindings/iface-1',
                data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                follow_redirects=True)

    r = client.get(f'/api/projects/{project}/hw/{hw_instance["id"]}/free-ports')
    data = r.get_json()
    ilo = next(p for p in data['ports'] if p['port_id'] == 'ilo')
    assert ilo['is_bound'] is True


@pytest.mark.api
def test_api_hw_bindings(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    client.post(f'/ne-instances/{nid}/bindings/iface-1',
                data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                follow_redirects=True)

    r = client.get(f'/api/projects/{project}/hw/{hw_instance["id"]}/bindings')
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['bindings']) == 1
    assert data['bindings'][0]['port_id'] == 'ilo'


@pytest.mark.api
def test_api_rules_preview(client, project, hw_instance):
    rule = {'port_types': ['mgmt'], 'name_regex': '.*', 'categories': ['server'],
            'group_by': []}
    r = client.post(f'/api/projects/{project}/rules/preview',
                    json={'rule': rule})
    assert r.status_code == 200
    data = r.get_json()
    assert 'total_ports' in data
    assert data['total_ports'] == 1


@pytest.mark.api
def test_api_rules_preview_bad_rule(client, project):
    r = client.post(f'/api/projects/{project}/rules/preview',
                    json={'rule': 'not-a-dict'})
    assert r.status_code == 400


@pytest.mark.api
def test_api_ne_instance_impact(client, project, ne_type, hw_instance):
    nid = _create_ne_inst(client, project, ne_type)
    ports_json = json.dumps([{
        'hw_instance_id': hw_instance['id'], 'port_id': 'ilo', 'role': 'primary'
    }])
    client.post(f'/ne-instances/{nid}/bindings/iface-1',
                data={'bind_mode': 'single', 'ports_json': ports_json, 'lag_id': ''},
                follow_redirects=True)

    r = client.get(f'/api/ne-instances/{nid}/impact')
    assert r.status_code == 200
    data = r.get_json()
    assert data['label'] == 'test-ne'
    assert len(data['cascades']) == 1


@pytest.mark.api
def test_hw_instance_detail_page(client, project, hw_instance):
    r = client.get(f'/projects/{project}/hw/instances/{hw_instance["id"]}')
    assert r.status_code == 200
    assert b'srv-001' in r.data
    assert b'iLO' in r.data  # port name
    assert b'free' in r.data  # all ports free initially
