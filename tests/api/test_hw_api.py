"""
API tests for hw.py routes using Flask test client.
"""
import json
import pytest
from db import new_id
from hw import (
    save_hw_template, save_hw_instance, save_bom, save_cable,
    get_hw_template, get_hw_instance, get_cable,
    project_instances, project_cables, get_rack_slots,
    seed_connectors,
    load_validation,
)

pytestmark = pytest.mark.api


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _create_project(client):
    """Create a test project and return its ID."""
    resp = client.post('/projects/add', data={
        'name': 'HW Test Project', 'supernet': '10.0.0.0/8', 'description': '',
    }, follow_redirects=False)
    return resp.headers['Location'].rstrip('/').split('/')[-1]


def _server_tmpl():
    """Return a standard server template dict."""
    return {
        'id': new_id(), 'name': 'Server-1U', 'vendor': 'Dell', 'model': 'R650',
        'category': 'server', 'form_factor': '19"', 'u_size': 1,
        'cable_type': '', 'description': '',
        'ports': [
            {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
             'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
            {'id': 'sfp0', 'name': 'sfp0', 'port_type': 'data',
             'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
        ],
        'scope': 'global', 'project_id': '',
    }


def _rack_tmpl():
    """Return a standard rack template dict."""
    return {
        'id': new_id(), 'name': 'Rack-42U', 'vendor': 'APC', 'model': 'AR3000',
        'category': 'rack', 'form_factor': '19"', 'u_size': 42,
        'cable_type': '', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }


def _cable_tmpl():
    """Return a standard cable template dict."""
    return {
        'id': new_id(), 'name': 'DAC-25G', 'vendor': 'ACME', 'model': 'D25',
        'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
        'cable_type': 'DAC', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }


def _make_site(pid, name='Site-A'):
    """Create and save a site in the project, return its ID."""
    from ne import save_site
    site = {'id': new_id(), 'project_id': pid, 'name': name, 'params': {}}
    save_site(site)
    return site['id']


def _make_instance(pid, tmpl):
    """Create and save a hardware instance."""
    inst = {
        'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
        'asset_tag': f'{tmpl["name"][:4]}-{new_id()}',
        'serial': '', 'status': 'in-stock',
        'location': {}, 'port_overrides': {},
    }
    save_hw_instance(inst)
    return inst


# ══════════════════════════════════════════════════════════════════════════════
# Connector management
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectorRoutes:
    """Test suite for connector management routes."""

    def test_connectors_page_200(self, client):
        """Verify connectors page loads."""
        assert client.get('/admin/hw/connectors').status_code == 200

    def test_add_connector(self, client):
        """Verify adding a connector."""
        resp = client.post('/admin/hw/connectors', data={
            'action': 'add', 'name': 'MY-CONN',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert client.get('/admin/hw/connectors').status_code == 200
        from hw import all_connectors
        assert 'MY-CONN' in all_connectors()

    def test_delete_connector(self, client):
        """Verify deleting a connector."""
        seed_connectors()
        client.post('/admin/hw/connectors', data={'action': 'add', 'name': 'TEMP-X'})
        client.post('/admin/hw/connectors', data={'action': 'delete', 'name': 'TEMP-X'})
        from hw import all_connectors
        assert 'TEMP-X' not in all_connectors()

    def test_set_compat(self, client):
        """Verify setting connector compatibility."""
        seed_connectors()
        client.post('/admin/hw/connectors', data={'action': 'add', 'name': 'CA'})
        client.post('/admin/hw/connectors', data={'action': 'add', 'name': 'CB'})
        resp = client.post('/admin/hw/connectors', data={
            'action': 'compat', 'conn_a': 'CA', 'conn_b': 'CB', 'compatible': '1',
        }, follow_redirects=False)
        assert resp.status_code == 302
        from hw_logic import connectors_compatible
        assert connectors_compatible('CA', 'CB')

    def test_unset_compat(self, client):
        """Verify unsetting connector compatibility."""
        seed_connectors()
        client.post('/admin/hw/connectors', data={'action': 'add', 'name': 'CX'})
        client.post('/admin/hw/connectors', data={'action': 'add', 'name': 'CY'})
        client.post('/admin/hw/connectors', data={
            'action': 'compat', 'conn_a': 'CX', 'conn_b': 'CY', 'compatible': '1',
        })
        client.post('/admin/hw/connectors', data={
            'action': 'compat', 'conn_a': 'CX', 'conn_b': 'CY', 'compatible': '0',
        })
        from hw_logic import connectors_compatible
        assert not connectors_compatible('CX', 'CY')


# ══════════════════════════════════════════════════════════════════════════════
# Hardware templates
# ══════════════════════════════════════════════════════════════════════════════

class TestHWTemplateRoutes:
    """Test suite for hardware template routes."""

    def test_hw_templates_list_200(self, client):
        """Verify hardware templates list loads."""
        assert client.get('/hw/templates').status_code == 200

    def test_add_hw_template_form_200(self, client):
        """Verify add hardware template form loads."""
        assert client.get('/hw/templates/add').status_code == 200

    def test_add_global_template(self, client):
        """Verify adding a global hardware template."""
        seed_connectors()
        resp = client.post('/hw/templates/add', data={
            'name':        'My Server',
            'vendor':      'Dell',
            'model':       'R650',
            'category':    'server',
            'form_factor': '19"',
            'u_size':      '1',
            'cable_type':  '',
            'description': '',
            'ports_json':  json.dumps([]),
        }, follow_redirects=False)
        assert resp.status_code == 302
        from hw import global_hw_templates
        assert any(t['name'] == 'My Server' for t in global_hw_templates())

    def test_add_template_with_ports(self, client):
        """Verify adding a template with ports (count=1 — no expansion)."""
        seed_connectors()
        ports = json.dumps([{
            'id': 'p1', 'name': 'eth0', 'port_type': 'data',
            'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
            'breakout_fan_out': 1, 'notes': '',
        }])
        resp = client.post('/hw/templates/add', data={
            'name': 'Server-Ports', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': '1',
            'cable_type': '', 'description': '', 'ports_json': ports,
        }, follow_redirects=False)
        assert resp.status_code == 302
        from hw import global_hw_templates
        tmpl = next(t for t in global_hw_templates() if t['name'] == 'Server-Ports')
        assert len(tmpl['ports']) == 1

    def test_add_template_count_expansion(self, client):
        """Verify saving a template via route expands count>1 port rows."""
        seed_connectors()
        ports = json.dumps([{
            'id': 'p1', 'name': 'eth', 'port_type': 'data',
            'connector': 'RJ45', 'speed_gbps': 1, 'count': 4,
            'breakout_fan_out': 1, 'notes': '',
        }])
        resp = client.post('/hw/templates/add', data={
            'name': 'Count-Expand', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': '1',
            'cable_type': '', 'description': '', 'ports_json': ports,
        }, follow_redirects=False)
        assert resp.status_code == 302
        from hw import global_hw_templates
        tmpl = next(t for t in global_hw_templates() if t['name'] == 'Count-Expand')
        assert len(tmpl['ports']) == 4
        assert [p['name'] for p in tmpl['ports']] == ['eth0', 'eth1', 'eth2', 'eth3']

    def test_edit_hw_template(self, client):
        """Verify editing a hardware template."""
        seed_connectors()
        t = _server_tmpl()
        save_hw_template(t)
        resp = client.post(f'/hw/templates/{t["id"]}/edit', data={
            'name': 'Renamed', 'vendor': 'HP', 'model': 'DL380',
            'category': 'server', 'form_factor': '19"', 'u_size': '2',
            'cable_type': '', 'description': 'updated', 'ports_json': json.dumps([]),
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert get_hw_template(t['id'])['name'] == 'Renamed'
        assert get_hw_template(t['id'])['u_size'] == 2

    def test_delete_hw_template(self, client):
        """Verify deleting a hardware template."""
        t = _server_tmpl()
        save_hw_template(t)
        resp = client.post(f'/hw/templates/{t["id"]}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_hw_template(t['id']) is None

    def test_project_templates_page_200(self, client):
        """Verify project hardware templates page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/templates').status_code == 200

    def test_add_project_template(self, client):
        """Verify adding a project-specific hardware template."""
        seed_connectors()
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/hw/templates/add', data={
            'name': 'Custom Switch', 'vendor': 'Cisco', 'model': 'N9K',
            'category': 'switch', 'form_factor': '19"', 'u_size': '1',
            'cable_type': '', 'description': '', 'ports_json': json.dumps([]),
        }, follow_redirects=False)
        assert resp.status_code == 302
        from hw import project_hw_templates
        assert any(t['name'] == 'Custom Switch' for t in project_hw_templates(pid))

    def test_add_template_invalid_ports_rejected(self, client):
        """Verify invalid ports JSON is rejected."""
        seed_connectors()
        resp = client.post('/hw/templates/add', data={
            'name': 'Bad', 'vendor': '', 'model': '', 'category': 'server',
            'form_factor': '19"', 'u_size': '1', 'cable_type': '',
            'description': '', 'ports_json': 'NOT JSON',
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower()

    def test_add_template_missing_name_rejected(self, client):
        """Verify template without name is rejected."""
        seed_connectors()
        resp = client.post('/hw/templates/add', data={
            'name': '', 'vendor': '', 'model': '', 'category': 'server',
            'form_factor': '19"', 'u_size': '1', 'cable_type': '',
            'description': '', 'ports_json': '[]',
        }, follow_redirects=True)
        assert b'required' in resp.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Bill of Materials
# ══════════════════════════════════════════════════════════════════════════════

class TestBOMRoutes:
    """Test suite for Bill of Materials routes."""

    def test_bom_page_200(self, client):
        """Verify BOM page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/bom').status_code == 200

    def test_save_bom(self, client):
        """Verify saving a BOM."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        bom = json.dumps([{
            'id': new_id(), 'template_id': t['id'],
            'qty': 3, 'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3,
            'description': '',
        }])
        resp = client.post(f'/projects/{pid}/bom', data={'bom_json': bom},
                           follow_redirects=False)
        assert resp.status_code == 302
        from hw import get_bom
        loaded = get_bom(pid)
        assert len(loaded) == 1
        assert loaded[0]['qty'] == 3

    def test_save_empty_bom(self, client):
        """Verify saving an empty BOM."""
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/bom', data={'bom_json': '[]'},
                           follow_redirects=False)
        assert resp.status_code == 302

    def test_save_bom_invalid_json(self, client):
        """Verify invalid BOM JSON is rejected."""
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/bom', data={'bom_json': 'NOT JSON'},
                           follow_redirects=True)
        assert b'invalid' in resp.data.lower()

    def test_generate_from_bom_line(self, client):
        """Verify generating instances from a BOM line."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        item_id = new_id()
        save_bom(pid, [{
            'id': item_id, 'template_id': t['id'],
            'qty': 4, 'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3,
            'description': '',
        }])
        resp = client.post(f'/projects/{pid}/bom/generate',
                           data={'item_id': item_id}, follow_redirects=False)
        assert resp.status_code == 302
        instances = project_instances(pid, category='server')
        assert len(instances) == 4

    def test_generate_all_from_bom(self, client):
        """Verify generating all instances from BOM."""
        pid = _create_project(client)
        t1  = _server_tmpl()
        t2  = _rack_tmpl()
        save_hw_template(t1)
        save_hw_template(t2)
        save_bom(pid, [
            {'id': new_id(), 'template_id': t1['id'], 'qty': 3,
             'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
            {'id': new_id(), 'template_id': t2['id'], 'qty': 2,
             'tag_prefix': 'rack', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
        ])
        resp = client.post(f'/projects/{pid}/bom/generate-all', follow_redirects=False)
        assert resp.status_code == 302
        instances = project_instances(pid)
        assert len(instances) == 5

    def test_generate_with_missing_item_id(self, client):
        """Verify generation with missing item ID fails gracefully."""
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/bom/generate',
                           data={'item_id': 'no-such-id'}, follow_redirects=True)
        assert b'not found' in resp.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Inventory
# ══════════════════════════════════════════════════════════════════════════════

class TestInventoryRoutes:
    """Test suite for inventory management routes."""

    def test_inventory_page_200(self, client):
        """Verify inventory page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/inventory').status_code == 200

    def test_inventory_category_filter(self, client):
        """Verify inventory category filtering."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        _make_instance(pid, t)
        resp = client.get(f'/projects/{pid}/hw/inventory?category=server')
        assert resp.status_code == 200
        assert b'Server-1U' in resp.data

    def test_add_instance_form_200(self, client):
        """Verify add instance form loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/instances/add').status_code == 200

    def test_add_instance_manually(self, client):
        """Verify adding an instance manually."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        sid = _make_site(pid)
        resp = client.post(f'/projects/{pid}/hw/instances/add', data={
            'template_id': t['id'], 'asset_tag': 'MANUAL-001',
            'serial': 'SN123', 'status': 'in-stock', 'site_id': sid,
        }, follow_redirects=False)
        assert resp.status_code == 302
        instances = project_instances(pid)
        added = next((i for i in instances if i['asset_tag'] == 'MANUAL-001'), None)
        assert added is not None
        assert added['site_id'] == sid

    def test_edit_instance(self, client):
        """Verify editing an instance."""
        pid  = _create_project(client)
        t    = _server_tmpl()
        save_hw_template(t)
        inst = _make_instance(pid, t)
        sid  = _make_site(pid)
        resp = client.post(f'/projects/{pid}/hw/instances/{inst["id"]}/edit', data={
            'asset_tag': 'UPDATED-TAG', 'serial': 'SN-NEW', 'status': 'deployed',
            'site_id': sid,
        }, follow_redirects=False)
        assert resp.status_code == 302
        updated = get_hw_instance(inst['id'])
        assert updated['asset_tag'] == 'UPDATED-TAG'
        assert updated['status']    == 'deployed'

    def test_delete_instance(self, client):
        """Verify deleting an instance."""
        pid  = _create_project(client)
        t    = _server_tmpl()
        save_hw_template(t)
        inst = _make_instance(pid, t)
        resp = client.post(f'/projects/{pid}/hw/instances/{inst["id"]}/delete',
                           follow_redirects=False)
        assert resp.status_code == 302
        assert get_hw_instance(inst['id']) is None

    def test_add_instance_no_template_rejected(self, client):
        """Verify instance without template is rejected."""
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/hw/instances/add', data={
            'template_id': '', 'asset_tag': 'X', 'serial': '', 'status': 'in-stock',
        }, follow_redirects=True)
        assert b'select' in resp.data.lower()

    def test_add_instance_no_site_rejected(self, client):
        """Instance without a site is rejected (re-render, not saved)."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        resp = client.post(f'/projects/{pid}/hw/instances/add', data={
            'template_id': t['id'], 'asset_tag': 'NO-SITE',
            'serial': '', 'status': 'in-stock', 'site_id': '',
        }, follow_redirects=False)
        assert resp.status_code == 200
        assert not any(i['asset_tag'] == 'NO-SITE' for i in project_instances(pid))

    def test_inventory_shows_totals_by_type(self, client):
        """Inventory page renders a totals-by-type summary."""
        pid = _create_project(client)
        t   = _server_tmpl()
        save_hw_template(t)
        _make_instance(pid, t)
        _make_instance(pid, t)
        resp = client.get(f'/projects/{pid}/hw/inventory')
        assert resp.status_code == 200
        assert b'Totals by hardware type' in resp.data
        assert t['name'].encode() in resp.data


# ══════════════════════════════════════════════════════════════════════════════
# Rack layout
# ══════════════════════════════════════════════════════════════════════════════

class TestRackRoutes:
    """Test suite for rack management routes."""

    def _setup_rack_and_device(self, client):
        """Helper to set up a rack and a device instance."""
        pid   = _create_project(client)
        rt    = _rack_tmpl()
        save_hw_template(rt)
        dt    = _server_tmpl()
        save_hw_template(dt)
        rack  = _make_instance(pid, rt)
        dev   = _make_instance(pid, dt)
        return pid, rack, dev

    def test_rack_list_200(self, client):
        """Verify rack list loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/racks').status_code == 200

    def test_rack_detail_200(self, client):
        """Verify rack detail page loads."""
        pid, rack, _ = self._setup_rack_and_device(client)
        assert client.get(f'/projects/{pid}/hw/racks/{rack["id"]}').status_code == 200

    def test_place_device_success(self, client):
        """Verify placing a device in a rack."""
        pid, rack, dev = self._setup_rack_and_device(client)
        resp = client.post(f'/projects/{pid}/hw/racks/{rack["id"]}/place', data={
            'instance_id': dev['id'], 'u_pos': '10',
        }, follow_redirects=False)
        assert resp.status_code == 302
        slots = get_rack_slots(rack['id'])
        assert any(s['instance_id'] == dev['id'] and s['u_pos'] == 10 for s in slots)

    def test_place_device_u_overflow(self, client):
        """Verify placing a device beyond rack height is rejected."""
        pid, rack, dev = self._setup_rack_and_device(client)
        resp = client.post(f'/projects/{pid}/hw/racks/{rack["id"]}/place', data={
            'instance_id': dev['id'], 'u_pos': '43',  # > 42U rack
        }, follow_redirects=True)
        assert b'overflow' in resp.data.lower() or b'exceeds' in resp.data.lower()

    def test_remove_device_from_rack(self, client):
        """Verify removing a device from a rack."""
        pid, rack, dev = self._setup_rack_and_device(client)
        client.post(f'/projects/{pid}/hw/racks/{rack["id"]}/place',
                    data={'instance_id': dev['id'], 'u_pos': '5'})
        resp = client.post(f'/projects/{pid}/hw/racks/{rack["id"]}/remove',
                           data={'instance_id': dev['id']}, follow_redirects=False)
        assert resp.status_code == 302
        assert get_rack_slots(rack['id']) == []
        assert get_hw_instance(dev['id'])['location'] == {}

    def test_api_place_device_json(self, client):
        """Verify placing a device via JSON API."""
        pid, rack, dev = self._setup_rack_and_device(client)
        resp = client.post(f'/api/projects/{pid}/hw/racks/{rack["id"]}/place',
                           json={'instance_id': dev['id'], 'u_pos': 5})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['ok'] is True

    def test_api_place_device_overlap_fails(self, client):
        """Verify overlapping placement is rejected."""
        pid   = _create_project(client)
        rt    = _rack_tmpl()
        save_hw_template(rt)
        dt    = _server_tmpl()
        save_hw_template(dt)
        rack  = _make_instance(pid, rt)
        dev1  = _make_instance(pid, dt)
        dev2  = _make_instance(pid, dt)
        client.post(f'/api/projects/{pid}/hw/racks/{rack["id"]}/place',
                    json={'instance_id': dev1['id'], 'u_pos': 5})
        resp = client.post(f'/api/projects/{pid}/hw/racks/{rack["id"]}/place',
                           json={'instance_id': dev2['id'], 'u_pos': 5})
        data = json.loads(resp.data)
        assert data['ok'] is False
        assert any(i['code'] == 'U_OCCUPIED' for i in data['issues'])

    def test_rack_table_200(self, client):
        """Verify rack table page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/rack-table').status_code == 200

    def test_rack_table_bulk_post(self, client):
        """Verify bulk placement via rack table."""
        pid, rack, dev = self._setup_rack_and_device(client)
        resp = client.post(f'/projects/{pid}/hw/rack-table',
                           json=[{'rack_id': rack['id'], 'instance_id': dev['id'], 'u_pos': 3}])
        assert resp.status_code == 200
        results = json.loads(resp.data)
        assert results[0]['ok'] is True

    def test_rack_table_bulk_error_reported(self, client):
        """Verify bulk placement errors are reported."""
        pid, rack, dev = self._setup_rack_and_device(client)
        resp = client.post(f'/projects/{pid}/hw/rack-table',
                           json=[{'rack_id': rack['id'], 'instance_id': dev['id'], 'u_pos': 99}])
        results = json.loads(resp.data)
        assert results[0]['ok'] is False


# ══════════════════════════════════════════════════════════════════════════════
# Cable plant
# ══════════════════════════════════════════════════════════════════════════════

class TestCableRoutes:
    """Test suite for cable management routes."""

    def test_cable_list_200(self, client):
        """Verify cable list page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/cables').status_code == 200

    def test_add_cable_form_200(self, client):
        """Verify add cable form loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/cables/add').status_code == 200

    def test_add_cable(self, client):
        """Verify adding a cable."""
        pid  = _create_project(client)
        ct   = _cable_tmpl()
        save_hw_template(ct)
        dt   = _server_tmpl()
        save_hw_template(dt)
        dev1 = _make_instance(pid, dt)
        dev2 = _make_instance(pid, dt)
        resp = client.post(f'/projects/{pid}/hw/cables/add', data={
            'template_id':    ct['id'],
            'asset_tag':      'CAB-001',
            'label':          'test link',
            'length_m':       '1.0',
            'end_a_instance': dev1['id'],
            'end_a_port':     'sfp0',
            'end_b_instance': dev2['id'],
            'end_b_port':     'sfp0',
            'breakout':       '',
            'breakout_fan_out': '1',
        }, follow_redirects=False)
        assert resp.status_code == 302
        cables = project_cables(pid)
        assert any(c['asset_tag'] == 'CAB-001' for c in cables)

    def test_edit_cable(self, client):
        """Verify editing a cable."""
        pid  = _create_project(client)
        ct   = _cable_tmpl()
        save_hw_template(ct)
        dt   = _server_tmpl()
        save_hw_template(dt)
        dev1 = _make_instance(pid, dt)
        dev2 = _make_instance(pid, dt)
        c = {
            'id': new_id(), 'template_id': ct['id'], 'project_id': pid,
            'asset_tag': 'CAB-EDIT', 'label': '', 'length_m': '',
            'end_a': {'instance_id': dev1['id'], 'port_id': 'sfp0'},
            'end_b': {'instance_id': dev2['id'], 'port_id': 'sfp0'},
            'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c)
        resp = client.post(f'/projects/{pid}/hw/cables/{c["id"]}/edit', data={
            'template_id':    ct['id'],
            'asset_tag':      'CAB-UPDATED',
            'label':          'renamed',
            'length_m':       '2.0',
            'end_a_instance': dev1['id'],
            'end_a_port':     'sfp0',
            'end_b_instance': dev2['id'],
            'end_b_port':     'sfp0',
            'breakout':       '',
            'breakout_fan_out': '1',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert get_cable(c['id'])['asset_tag'] == 'CAB-UPDATED'
        assert get_cable(c['id'])['length_m']   == '2.0'

    def test_delete_cable(self, client):
        """Verify deleting a cable."""
        pid = _create_project(client)
        c = {
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'DEL-CAB', 'label': '', 'length_m': '',
            'end_a': {}, 'end_b': {}, 'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c)
        resp = client.post(f'/projects/{pid}/hw/cables/{c["id"]}/delete',
                           follow_redirects=False)
        assert resp.status_code == 302
        assert get_cable(c['id']) is None

    def test_instance_ports_api(self, client):
        """Verify instance ports API."""
        pid  = _create_project(client)
        dt   = _server_tmpl()
        save_hw_template(dt)
        inst = _make_instance(pid, dt)
        resp = client.get(f'/api/projects/{pid}/hw/instance-ports/{inst["id"]}')
        assert resp.status_code == 200
        ports = json.loads(resp.data)
        assert len(ports) == 2
        names = [p['name'] for p in ports]
        assert 'eth0' in names
        assert 'sfp0' in names

    def test_instance_ports_api_marks_used(self, client):
        """Verify instance ports API marks ports in use."""
        pid  = _create_project(client)
        dt   = _server_tmpl()
        save_hw_template(dt)
        dev1 = _make_instance(pid, dt)
        dev2 = _make_instance(pid, dt)
        c = {
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'C1', 'label': '', 'length_m': '',
            'end_a': {'instance_id': dev1['id'], 'port_id': 'sfp0'},
            'end_b': {'instance_id': dev2['id'], 'port_id': 'sfp0'},
            'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c)
        resp  = client.get(f'/api/projects/{pid}/hw/instance-ports/{dev1["id"]}')
        ports = json.loads(resp.data)
        sfp   = next(p for p in ports if p['id'] == 'sfp0')
        assert sfp['in_use'] is True

    def test_instance_ports_api_unknown_instance(self, client):
        """Verify instance ports API handles unknown instance."""
        pid  = _create_project(client)
        resp = client.get(f'/api/projects/{pid}/hw/instance-ports/no-such-id')
        assert resp.status_code == 200
        assert not json.loads(resp.data)

    def test_instance_ports_api_returns_all_subports(self, client):
        """48-port switch must surface 48 entries in the cable-form picker."""
        seed_connectors()
        pid  = _create_project(client)
        tmpl = {
            'id': new_id(), 'name': 'ToR-48', 'vendor': 'Cisco', 'model': 'N9K',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'p1', 'name': 'Eth1/{0..47}', 'port_type': 'data',
                       'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = _make_instance(pid, tmpl)
        resp  = client.get(f'/api/projects/{pid}/hw/instance-ports/{inst["id"]}')
        ports = json.loads(resp.data)
        assert len(ports) == 48
        assert {p['name'] for p in ports} == {f'Eth1/{i}' for i in range(48)}
        assert len({p['id'] for p in ports}) == 48

    def test_cable_can_bind_to_specific_subport(self, client):
        """Cables must distinguish between p1-3 and p1-4 on the same template."""
        seed_connectors()
        pid  = _create_project(client)
        tmpl = {
            'id': new_id(), 'name': 'ToR-8', 'vendor': 'Cisco', 'model': 'N9K',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'p1', 'name': 'Eth1/{0..7}', 'port_type': 'data',
                       'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = _make_instance(pid, tmpl)

        # Bind two different cables to p1-3 and p1-4 (distinct sub-ports)
        c1 = {
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'C-P3', 'label': '', 'length_m': '',
            'end_a': {'instance_id': inst['id'], 'port_id': 'p1-3'},
            'end_b': {'instance_id': '', 'port_id': ''},
            'breakout': False, 'breakout_fan_out': 1,
        }
        c2 = {
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'C-P4', 'label': '', 'length_m': '',
            'end_a': {'instance_id': inst['id'], 'port_id': 'p1-4'},
            'end_b': {'instance_id': '', 'port_id': ''},
            'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c1)
        save_cable(c2)

        resp  = client.get(f'/api/projects/{pid}/hw/instance-ports/{inst["id"]}')
        ports = json.loads(resp.data)
        # p1-3 and p1-4 are distinct; both show as in_use, others are free
        p3 = next(p for p in ports if p['id'] == 'p1-3')
        p4 = next(p for p in ports if p['id'] == 'p1-4')
        p0 = next(p for p in ports if p['id'] == 'p1')
        assert p3['in_use'] is True
        assert p4['in_use'] is True
        assert p0['in_use'] is False


# ══════════════════════════════════════════════════════════════════════════════
# Validation routes
# ══════════════════════════════════════════════════════════════════════════════

class TestValidationRoutes:
    """Test suite for validation routes."""

    def test_validate_page_200(self, client):
        """Verify validation page loads."""
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/hw/validate').status_code == 200

    def test_validate_empty_project_no_issues(self, client):
        """Verify empty project has no issues."""
        pid  = _create_project(client)
        resp = client.get(f'/projects/{pid}/hw/validate')
        assert b'No issues' in resp.data

    def test_api_validate_returns_json(self, client):
        """Verify validation API returns JSON."""
        pid  = _create_project(client)
        resp = client.get(f'/api/projects/{pid}/hw/validate')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'issues' in data
        assert 'errors' in data
        assert 'warnings' in data

    def test_validate_detects_connector_mismatch(self, client):
        """Verify validation detects connector mismatch."""
        seed_connectors()
        pid = _create_project(client)
        # Server with RJ45 eth0, switch with SFP28 swp0
        srv_t = {
            'id': new_id(), 'name': 'Srv', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                       'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        sw_t = {
            'id': new_id(), 'name': 'Sw', 'vendor': '', 'model': '',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'swp0', 'name': 'swp0', 'port_type': 'data',
                       'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(srv_t)
        save_hw_template(sw_t)
        srv  = _make_instance(pid, srv_t)
        sw   = _make_instance(pid, sw_t)
        ct   = _cable_tmpl()
        save_hw_template(ct)
        c = {
            'id': new_id(), 'template_id': ct['id'], 'project_id': pid,
            'asset_tag': 'BAD-CAB', 'label': '', 'length_m': '',
            'end_a': {'instance_id': srv['id'], 'port_id': 'eth0'},
            'end_b': {'instance_id': sw['id'],  'port_id': 'swp0'},
            'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c)
        resp = client.get(f'/api/projects/{pid}/hw/validate')
        data = json.loads(resp.data)
        codes = {i['code'] for i in data['issues']}
        assert 'CONNECTOR_MISMATCH' in codes

    def test_validate_caches_result(self, client):
        """Verify validation results are cached."""
        pid = _create_project(client)
        client.get(f'/projects/{pid}/hw/validate')
        cached = load_validation(pid)
        assert isinstance(cached, list)

    def test_api_validate_structure(self, client):
        """Verify validation API response structure."""
        pid  = _create_project(client)
        resp = client.get(f'/api/projects/{pid}/hw/validate')
        data = json.loads(resp.data)
        assert 'total' in data
        assert data['total'] == data['errors'] + data['warnings'] + sum(
            1 for i in data['issues']
            if i['severity'] not in ('error', 'warning')
        )
