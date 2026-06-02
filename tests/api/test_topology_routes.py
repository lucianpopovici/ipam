"""API / route tests for the Mermaid topology blueprint."""
import json
import pytest
from ipam import new_id, save_network, save_ip, get_project
import ipam as _ipam_mod
from hw_logic import save_hw_template, save_hw_instance, save_cable


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _create_project(client, name='topo-proj'):
    resp = client.post('/projects/add', data={
        'name': name, 'supernet': '10.0.0.0/8',
        'description': '', 'customer_id': '',
    }, follow_redirects=False)
    return resp.headers['Location'].rstrip('/').split('/')[-1]


def _add_subnet(pid, cidr='10.0.1.0/24'):
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr, 'description': '',
           'vlan': '', 'project_id': pid, 'template_id': None, 'pending_slots': []}
    save_network(net)
    _ipam_mod.r.sadd(f'project:{pid}:networks', net['id'])
    return net


def _add_ip(net_id, ip, hostname=''):
    save_ip({'ip': ip, 'hostname': hostname, 'description': '',
             'status': 'allocated', 'network_id': net_id})


def _add_instance(pid, tag='srv-01'):
    tmpl = {'id': new_id(), 'name': 'Srv', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': ''}
    save_hw_template(tmpl)
    inst = {'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
            'asset_tag': tag, 'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {}}
    save_hw_instance(inst)
    return inst


# ── Overview ──────────────────────────────────────────────────────────────────────

class TestTopologyOverview:
    def test_overview_renders(self, client):
        assert client.get('/topology').status_code == 200

    def test_overview_lists_project(self, client):
        pid = _create_project(client)
        resp = client.get('/topology')
        assert b'topo-proj' in resp.data

    def test_overview_links_to_l3(self, client):
        pid = _create_project(client)
        resp = client.get('/topology')
        assert b'L3 Logical' in resp.data


# ── Per-project topology page ────────────────────────────────────────────────────

class TestProjectTopology:
    def test_l3_page_renders(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}?view=l3')
        assert resp.status_code == 200
        assert b'mermaid' in resp.data.lower()

    def test_l1_page_renders(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}?view=l1')
        assert resp.status_code == 200

    def test_l3_shows_subnet(self, client):
        pid = _create_project(client)
        _add_subnet(pid, '10.0.1.0/24')
        resp = client.get(f'/topology/projects/{pid}?view=l3')
        assert b'10.0.1.0/24' in resp.data

    def test_l3_shows_hostname(self, client):
        pid = _create_project(client)
        net = _add_subnet(pid)
        _add_ip(net['id'], '10.0.1.5', 'myhost')
        resp = client.get(f'/topology/projects/{pid}?view=l3')
        assert b'myhost' in resp.data

    def test_l1_shows_device(self, client):
        pid = _create_project(client)
        _add_instance(pid, tag='leaf-01')
        resp = client.get(f'/topology/projects/{pid}?view=l1')
        assert b'leaf-01' in resp.data

    def test_unknown_project_404(self, client):
        assert client.get('/topology/projects/no-such-pid').status_code == 404

    def test_default_view_is_l3(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}')
        assert resp.status_code == 200
        assert b'L3 Logical' in resp.data

    def test_truncation_warning(self, client):
        pid = _create_project(client)
        net = _add_subnet(pid)
        for i in range(1, 20):
            _add_ip(net['id'], f'10.0.1.{i}', f'h{i}')
        resp = client.get(f'/topology/projects/{pid}?view=l3&max_nodes=5')
        assert b'truncated' in resp.data.lower()


# ── Raw export downloads ──────────────────────────────────────────────────────────

class TestTopologyDownloads:
    def test_mermaid_download_l3(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}/render.mermaid?view=l3')
        assert resp.status_code == 200
        assert b'graph LR' in resp.data

    def test_mermaid_download_l1(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}/render.mermaid?view=l1')
        assert resp.status_code == 200
        assert b'graph TB' in resp.data

    def test_dot_download_l3(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}/render.dot?view=l3')
        assert resp.status_code == 200
        assert b'digraph L3' in resp.data

    def test_dot_download_l1(self, client):
        pid = _create_project(client)
        resp = client.get(f'/topology/projects/{pid}/render.dot?view=l1')
        assert resp.status_code == 200
        assert b'graph L1' in resp.data

    def test_unknown_project_404(self, client):
        assert client.get('/topology/projects/nope/render.mermaid').status_code == 404


# ── Embed API ─────────────────────────────────────────────────────────────────────

class TestTopologyEmbedApi:
    def test_l3_api_returns_json(self, client):
        pid = _create_project(client)
        resp = client.get(f'/api/topology/projects/{pid}/l3.mermaid')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'graph' in data
        assert 'truncated' in data

    def test_l1_api_returns_json(self, client):
        pid = _create_project(client)
        resp = client.get(f'/api/topology/projects/{pid}/l1.mermaid')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'graph' in data

    def test_l3_api_includes_subnet(self, client):
        pid = _create_project(client)
        _add_subnet(pid, '10.0.3.0/24')
        resp = client.get(f'/api/topology/projects/{pid}/l3.mermaid')
        data = json.loads(resp.data)
        assert '10.0.3.0/24' in data['graph']

    def test_l3_api_label_filter(self, client):
        pid = _create_project(client)
        from ipam import add_labels_to_network
        net_in  = _add_subnet(pid, '10.0.1.0/24')
        net_out = _add_subnet(pid, '10.0.2.0/24')
        add_labels_to_network(net_in['id'], ['prod'])
        add_labels_to_network(net_out['id'], ['test'])
        resp = client.get(f'/api/topology/projects/{pid}/l3.mermaid?labels=prod')
        data = json.loads(resp.data)
        assert '10.0.1.0/24' in data['graph']
        assert '10.0.2.0/24' not in data['graph']
