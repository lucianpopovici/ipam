"""

pytestmark = pytest.mark.api

API tests for ipam.py routes using Flask test client.
Every test gets a fresh fakeredis via the autouse fixture in conftest.py.
"""
import json
import pytest
from ipam import (
    save_project, save_network, save_ip, add_labels_to_network,
    add_global_label, save_template, new_id, project_nets_key,
    get_network, get_ip,
)
from db import r


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _create_project(client, name='Test Project', supernet='10.0.0.0/16'):
    resp = client.post('/projects/add', data={
        'name': name, 'supernet': supernet, 'description': '',
    }, follow_redirects=False)
    assert resp.status_code == 302
    return resp.headers['Location'].rstrip('/').split('/')[-1]


def _create_subnet(client, pid, cidr='10.0.0.0/24', labels='', name=''):
    return client.post(f'/projects/{pid}/subnet/add', data={
        'mode': 'manual', 'cidr': cidr, 'name': name or cidr,
        'description': '', 'vlan': '', 'labels': labels,
    }, follow_redirects=False)


def _get_first_subnet(pid):
    from ipam import project_networks
    nets = project_networks(pid)
    return nets[0] if nets else None


# ══════════════════════════════════════════════════════════════════════════════
# Dashboard / Index
# ══════════════════════════════════════════════════════════════════════════════

class TestIndex:
    def test_index_200(self, client):
        resp = client.get('/')
        assert resp.status_code == 200

    def test_index_shows_project(self, client):
        _create_project(client, 'MyProject')
        resp = client.get('/')
        assert b'MyProject' in resp.data

    def test_overview_200(self, client):
        assert client.get('/overview').status_code == 200

    def test_search_empty(self, client):
        resp = client.get('/search')
        assert resp.status_code == 200

    def test_search_with_query(self, client):
        resp = client.get('/search?q=10.0.0.1')
        assert resp.status_code == 200

    def test_pool_ui_empty(self, client):
        assert client.get('/pool').status_code == 200

    def test_pool_api_no_labels(self, client):
        resp = client.get('/api/pool')
        assert resp.status_code == 400

    def test_pool_api_with_labels(self, client):
        resp = client.get('/api/pool?labels=PROD')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'pools' in data


# ══════════════════════════════════════════════════════════════════════════════
# Projects
# ══════════════════════════════════════════════════════════════════════════════

class TestProjects:
    def test_add_project_form_200(self, client):
        assert client.get('/projects/add').status_code == 200

    def test_add_project_creates_and_redirects(self, client):
        resp = client.post('/projects/add', data={
            'name': 'My Project', 'supernet': '192.168.0.0/16', 'description': 'test',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert '/projects/' in resp.headers['Location']

    def test_project_detail_200(self, client):
        pid  = _create_project(client)
        resp = client.get(f'/projects/{pid}')
        assert resp.status_code == 200
        assert b'Test Project' in resp.data

    def test_project_detail_404_unknown(self, client):
        assert client.get('/projects/no-such-pid').status_code == 404

    def test_invalid_supernet_rejected(self, client):
        resp = client.post('/projects/add', data={
            'name': 'Bad', 'supernet': 'not-a-cidr', 'description': '',
        }, follow_redirects=True)
        assert b'Invalid' in resp.data

    def test_delete_project(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert client.get(f'/projects/{pid}').status_code == 404

    def test_delete_project_removes_subnets(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        nid = net['id']
        client.post(f'/projects/{pid}/delete')
        assert get_network(nid) is None


# ══════════════════════════════════════════════════════════════════════════════
# Labels
# ══════════════════════════════════════════════════════════════════════════════

class TestLabels:
    def test_global_labels_page_200(self, client):
        assert client.get('/labels').status_code == 200

    def test_add_global_label(self, client):
        client.post('/labels', data={'action': 'add', 'label': 'PROD'})
        resp = client.get('/labels')
        assert b'PROD' in resp.data

    def test_delete_global_label(self, client):
        client.post('/labels', data={'action': 'add', 'label': 'TEMP'})
        client.post('/labels', data={'action': 'delete', 'label': 'TEMP'})
        resp = client.get('/labels')
        # Should not appear as a standalone label
        from ipam import global_labels
        assert 'TEMP' not in global_labels()

    def test_empty_label_rejected(self, client):
        resp = client.post('/labels', data={'action': 'add', 'label': ''}, follow_redirects=True)
        assert b'empty' in resp.data.lower()

    def test_project_labels_page_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/labels').status_code == 200

    def test_add_project_label(self, client):
        pid = _create_project(client)
        client.post(f'/projects/{pid}/labels', data={'action': 'add', 'label': 'rack-A'})
        from ipam import project_labels
        assert 'rack-A' in project_labels(pid)

    def test_global_label_rejected_as_project_label(self, client):
        pid = _create_project(client)
        add_global_label('GLOBAL-LBL')
        resp = client.post(f'/projects/{pid}/labels',
                           data={'action': 'add', 'label': 'GLOBAL-LBL'},
                           follow_redirects=True)
        assert b'global' in resp.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Subnets
# ══════════════════════════════════════════════════════════════════════════════

class TestSubnets:
    def test_add_subnet_form_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/subnet/add').status_code == 200

    def test_add_subnet_manual(self, client):
        pid  = _create_project(client)
        resp = _create_subnet(client, pid, cidr='10.0.0.0/24')
        assert resp.status_code == 302
        net = _get_first_subnet(pid)
        assert net is not None
        assert net['cidr'] == '10.0.0.0/24'

    def test_add_subnet_auto(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'auto', 'prefix_len': '24', 'name': '',
            'description': '', 'vlan': '', 'labels': '',
        }, follow_redirects=False)
        assert resp.status_code == 302
        net = _get_first_subnet(pid)
        assert net['cidr'] == '10.0.0.0/24'

    def test_add_subnet_outside_supernet_rejected(self, client):
        pid  = _create_project(client, supernet='10.0.0.0/24')
        resp = client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'manual', 'cidr': '192.168.1.0/24',
            'name': '', 'description': '', 'vlan': '', 'labels': '',
        }, follow_redirects=True)
        assert b'not within' in resp.data.lower()

    def test_add_overlapping_subnet_rejected(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid, cidr='10.0.0.0/24')
        resp = client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'manual', 'cidr': '10.0.0.0/25',
            'name': '', 'description': '', 'vlan': '', 'labels': '',
        }, follow_redirects=True)
        assert b'overlap' in resp.data.lower()

    def test_add_subnet_with_labels(self, client):
        pid = _create_project(client)
        add_global_label('PROD')
        _create_subnet(client, pid, cidr='10.0.0.0/24', labels='PROD')
        net = _get_first_subnet(pid)
        from ipam import get_network_labels
        assert 'PROD' in get_network_labels(net['id'])

    def test_network_detail_200(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        resp = client.get(f'/networks/{net["id"]}')
        assert resp.status_code == 200

    def test_network_detail_404(self, client):
        assert client.get('/networks/no-such-id').status_code == 404

    def test_edit_network(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        resp = client.post(f'/networks/{net["id"]}/edit', data={
            'name': 'renamed', 'description': 'new desc', 'vlan': '100', 'labels': '',
        }, follow_redirects=False)
        assert resp.status_code == 302
        updated = get_network(net['id'])
        assert updated['name'] == 'renamed'
        assert updated['vlan'] == '100'

    def test_delete_network(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        nid  = net['id']
        resp = client.post(f'/networks/{nid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_network(nid) is None

    def test_auto_prefix_too_large_rejected(self, client):
        pid  = _create_project(client, supernet='10.0.0.0/24')
        resp = client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'auto', 'prefix_len': '24',
            'name': '', 'description': '', 'vlan': '', 'labels': '',
        }, follow_redirects=True)
        assert b'must be smaller' in resp.data.lower()

    def test_bulk_add_subnets(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/subnet/bulk',
                           json={'subnets': [
                               {'prefix_len': 24, 'name': 'net1', 'labels': [], 'description': '', 'vlan': ''},
                               {'prefix_len': 24, 'name': 'net2', 'labels': [], 'description': '', 'vlan': ''},
                           ]})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data['allocated']) == 2
        assert data['errors'] == []

    def test_bulk_add_non_json_rejected(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/subnet/bulk', data='not json',
                           content_type='text/plain')
        assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# IPs
# ══════════════════════════════════════════════════════════════════════════════

class TestIPs:
    def test_add_ip(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        resp = client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.5', 'hostname': 'host1', 'description': '', 'status': 'allocated',
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert get_ip('10.0.0.5') is not None

    def test_add_ip_outside_subnet_rejected(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid, cidr='10.0.0.0/24')
        net  = _get_first_subnet(pid)
        resp = client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '192.168.1.1', 'hostname': '', 'description': '', 'status': 'allocated',
        }, follow_redirects=True)
        assert b'not within' in resp.data.lower()

    def test_add_duplicate_ip_rejected(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.5', 'hostname': '', 'description': '', 'status': 'allocated',
        })
        resp = client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.5', 'hostname': '', 'description': '', 'status': 'allocated',
        }, follow_redirects=True)
        assert b'already allocated' in resp.data.lower()

    def test_edit_ip(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.10', 'hostname': 'original', 'description': '', 'status': 'allocated',
        })
        resp = client.post('/ip/10.0.0.10/edit', data={
            'hostname': 'updated', 'description': 'changed', 'status': 'reserved',
        }, follow_redirects=False)
        assert resp.status_code == 302
        addr = get_ip('10.0.0.10')
        assert addr['hostname']    == 'updated'
        assert addr['status']      == 'reserved'
        assert addr['description'] == 'changed'

    def test_delete_ip(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.20', 'hostname': '', 'description': '', 'status': 'allocated',
        })
        resp = client.post('/ip/10.0.0.20/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_ip('10.0.0.20') is None

    def test_next_available_api(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        resp = client.get(f'/api/networks/{net["id"]}/next')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'next_available' in data
        assert data['next_available'] == '10.0.0.1'

    def test_next_available_skips_allocated(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': '10.0.0.1', 'hostname': '', 'description': '', 'status': 'allocated',
        })
        resp = client.get(f'/api/networks/{net["id"]}/next')
        data = json.loads(resp.data)
        assert data['next_available'] == '10.0.0.2'

    def test_next_available_skips_pending(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        # Manually inject a pending slot at .1
        nid  = net['id']
        n    = get_network(nid)
        n['pending_slots'] = [{'ip': '10.0.0.1', 'role': 'r', 'status': 'reserved'}]
        save_network(n)
        resp = client.get(f'/api/networks/{nid}/next')
        data = json.loads(resp.data)
        assert data['next_available'] == '10.0.0.2'

    def test_add_ip_invalid_address(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net  = _get_first_subnet(pid)
        resp = client.post(f'/networks/{net["id"]}/ip/add', data={
            'ip': 'not-an-ip', 'hostname': '', 'description': '', 'status': 'allocated',
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Subnet Templates
# ══════════════════════════════════════════════════════════════════════════════

class TestSubnetTemplates:
    def _valid_rules_json(self):
        return json.dumps([
            {'type': 'from_start', 'offset': 1, 'role': 'gateway', 'status': 'reserved'},
            {'type': 'from_end',   'count': 2,  'role': 'reserved','status': 'reserved'},
        ])

    def test_list_templates_200(self, client):
        assert client.get('/templates').status_code == 200

    def test_add_template_form_200(self, client):
        assert client.get('/templates/add').status_code == 200

    def test_add_global_template(self, client):
        resp = client.post('/templates/add', data={
            'name': 'Standard /24', 'description': '',
            'rules_json': self._valid_rules_json(),
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ipam import global_templates
        assert any(t['name'] == 'Standard /24' for t in global_templates())

    def test_add_project_template(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/templates/add', data={
            'name': 'Project Tmpl', 'description': '',
            'rules_json': self._valid_rules_json(),
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ipam import project_templates
        assert any(t['name'] == 'Project Tmpl' for t in project_templates(pid))

    def test_add_template_invalid_rules_rejected(self, client):
        resp = client.post('/templates/add', data={
            'name': 'Bad', 'description': '',
            'rules_json': json.dumps([{'type': 'from_start', 'offset': 0, 'role': 'r', 'status': 'reserved'}]),
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower()

    def test_edit_template(self, client):
        client.post('/templates/add', data={
            'name': 'Before', 'description': '',
            'rules_json': self._valid_rules_json(),
        })
        from ipam import global_templates
        tid  = global_templates()[0]['id']
        resp = client.post(f'/templates/{tid}/edit', data={
            'name': 'After', 'description': 'updated',
            'rules_json': self._valid_rules_json(),
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ipam import get_template
        assert get_template(tid)['name'] == 'After'

    def test_delete_template(self, client):
        client.post('/templates/add', data={
            'name': 'DeleteMe', 'description': '',
            'rules_json': self._valid_rules_json(),
        })
        from ipam import global_templates, get_template
        tid  = global_templates()[0]['id']
        resp = client.post(f'/templates/{tid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_template(tid) is None

    def test_preview_template_api(self, client):
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': self._valid_rules_json(),
        })
        from ipam import global_templates
        tid  = global_templates()[0]['id']
        resp = client.get(f'/api/templates/{tid}/preview?cidr=10.0.0.0/24')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'resolved' in data
        assert len(data['resolved']) == 3   # 1 from_start + 2 from_end

    def test_preview_inline_api(self, client):
        resp = client.post('/api/templates/preview_inline',
                           json={'cidr': '10.0.0.0/24', 'rules': [
                               {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
                           ]})
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['resolved'][0]['ip'] == '10.0.0.1'

    def test_apply_template_and_pending_slots(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        # Create template
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': json.dumps([
                {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
            ]),
        })
        from ipam import global_templates
        tid = global_templates()[0]['id']
        resp = client.post(f'/networks/{net["id"]}/template', data={
            'template_id': tid,
        }, follow_redirects=False)
        assert resp.status_code == 302
        n = get_network(net['id'])
        assert len(n['pending_slots']) == 1
        assert n['pending_slots'][0]['ip'] == '10.0.0.1'

    def test_confirm_slot(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': json.dumps([
                {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
            ]),
        })
        from ipam import global_templates
        tid = global_templates()[0]['id']
        client.post(f'/networks/{net["id"]}/template', data={'template_id': tid})
        resp = client.post(f'/networks/{net["id"]}/slots/confirm', data={'ip': '10.0.0.1'})
        assert resp.status_code == 302
        assert get_ip('10.0.0.1') is not None
        assert get_network(net['id'])['pending_slots'] == []

    def test_confirm_all_slots(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': json.dumps([
                {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
                {'type': 'from_start', 'offset': 2, 'role': 'vrrp', 'status': 'reserved'},
            ]),
        })
        from ipam import global_templates
        tid = global_templates()[0]['id']
        client.post(f'/networks/{net["id"]}/template', data={'template_id': tid})
        resp = client.post(f'/networks/{net["id"]}/slots/confirm_all')
        assert resp.status_code == 302
        assert get_ip('10.0.0.1') is not None
        assert get_ip('10.0.0.2') is not None

    def test_dismiss_slot(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': json.dumps([
                {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
            ]),
        })
        from ipam import global_templates
        tid = global_templates()[0]['id']
        client.post(f'/networks/{net["id"]}/template', data={'template_id': tid})
        client.post(f'/networks/{net["id"]}/slots/dismiss', data={'ip': '10.0.0.1'})
        assert get_ip('10.0.0.1') is None
        assert get_network(net['id'])['pending_slots'] == []

    def test_dismiss_all_slots(self, client):
        pid = _create_project(client)
        _create_subnet(client, pid)
        net = _get_first_subnet(pid)
        client.post('/templates/add', data={
            'name': 'T', 'description': '',
            'rules_json': json.dumps([
                {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
                {'type': 'from_start', 'offset': 2, 'role': 'vrrp', 'status': 'reserved'},
            ]),
        })
        from ipam import global_templates
        tid = global_templates()[0]['id']
        client.post(f'/networks/{net["id"]}/template', data={'template_id': tid})
        client.post(f'/networks/{net["id"]}/slots/dismiss_all')
        assert get_network(net['id'])['pending_slots'] == []

    def test_manage_project_templates_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/templates').status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Pool API
# ══════════════════════════════════════════════════════════════════════════════

class TestPoolAPI:
    def test_pool_api_returns_matching_subnets(self, client):
        pid = _create_project(client)
        add_global_label('PROD')
        _create_subnet(client, pid, cidr='10.0.0.0/24', labels='PROD')
        resp = client.get('/api/pool?labels=PROD')
        data = json.loads(resp.data)
        assert data['subnet_count'] == 1
        assert any(s['cidr'] == '10.0.0.0/24' for s in data['subnets'])

    def test_pool_api_intersection(self, client):
        pid = _create_project(client)
        add_global_label('PROD')
        add_global_label('London')
        _create_subnet(client, pid, cidr='10.0.0.0/24', labels='PROD,London')
        _create_subnet(client, pid, cidr='10.0.1.0/24', labels='PROD')
        resp = client.get('/api/pool?labels=PROD,London')
        data = json.loads(resp.data)
        assert data['subnet_count'] == 1

    def test_pool_api_no_match(self, client):
        resp = client.get('/api/pool?labels=NONEXISTENT')
        data = json.loads(resp.data)
        assert data.get('subnet_count', 0) == 0 or data == {}

    def test_pool_ui_with_labels(self, client):
        pid = _create_project(client)
        add_global_label('PROD')
        _create_subnet(client, pid, labels='PROD')
        resp = client.get('/pool?labels=PROD')
        assert resp.status_code == 200
        assert b'PROD' in resp.data
