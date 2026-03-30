"""
pytestmark = pytest.mark.api

API tests for vmware.py routes using the Flask test client.
Every test gets a fresh fakeredis via the autouse fixture in conftest.py.
"""
import json
import pytest
import ipam as _ipam
from ipam import save_project, save_network, new_id, project_nets_key
from vmware import enable_network, allocate_ip


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _make_project(supernet='10.0.0.0/16'):
    pid = new_id()
    save_project({'id': pid, 'name': 'Proj', 'supernet': supernet, 'description': ''})
    return pid


def _make_network(pid, cidr='10.0.1.0/24'):
    nid = new_id()
    net = {'id': nid, 'name': cidr, 'cidr': cidr, 'description': '',
           'vlan': '', 'project_id': pid, 'pending_slots': []}
    save_network(net)
    _ipam.r.sadd(project_nets_key(pid), nid)
    return nid


# ══════════════════════════════════════════════════════════════════════════════
# UI routes
# ══════════════════════════════════════════════════════════════════════════════

class TestVMwareUI:
    def test_index_200(self, client):
        resp = client.get('/vmware')
        assert resp.status_code == 200

    def test_index_shows_no_subnets_message(self, client):
        resp = client.get('/vmware')
        assert b'No subnets found' in resp.data

    def test_index_lists_subnet(self, client):
        pid = _make_project()
        _make_network(pid, '10.0.1.0/24')
        resp = client.get('/vmware')
        assert b'10.0.1.0/24' in resp.data

    def test_enable_network(self, client):
        pid = _make_project()
        nid = _make_network(pid)
        resp = client.post(f'/vmware/networks/{nid}/enable', follow_redirects=False)
        assert resp.status_code == 302
        from vmware import is_enabled
        assert is_enabled(nid)

    def test_disable_network(self, client):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        resp = client.post(f'/vmware/networks/{nid}/disable', follow_redirects=False)
        assert resp.status_code == 302
        from vmware import is_enabled
        assert not is_enabled(nid)

    def test_enable_nonexistent_returns_404(self, client):
        resp = client.post('/vmware/networks/ghost/enable', follow_redirects=False)
        assert resp.status_code == 404

    def test_disable_nonexistent_returns_404(self, client):
        resp = client.post('/vmware/networks/ghost/disable', follow_redirects=False)
        assert resp.status_code == 404

    def test_enabled_badge_shown(self, client):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        resp = client.get('/vmware')
        assert b'enabled' in resp.data


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/vmware/networks
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIListNetworks:
    def test_empty_when_none_enabled(self, client):
        resp = client.get('/api/vmware/networks')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['networks'] == []

    def test_lists_enabled_networks(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.get('/api/vmware/networks')
        data = json.loads(resp.data)
        assert len(data['networks']) == 1
        assert data['networks'][0]['cidr'] == '10.0.1.0/24'

    def test_does_not_list_disabled_networks(self, client):
        pid = _make_project()
        _make_network(pid, '10.0.1.0/24')
        resp = client.get('/api/vmware/networks')
        data = json.loads(resp.data)
        assert data['networks'] == []

    def test_response_includes_stats(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.get('/api/vmware/networks')
        net = json.loads(resp.data)['networks'][0]
        assert 'total_hosts' in net
        assert 'allocated' in net
        assert 'vmware_allocs' in net


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/vmware/networks/<net_id>/allocate
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIAllocate:
    def test_allocate_returns_201(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.post(f'/api/vmware/networks/{nid}/allocate',
                           data=json.dumps({'vm_name': 'vm-01', 'datacenter': 'DC1', 'cluster': 'C1'}),
                           content_type='application/json')
        assert resp.status_code == 201

    def test_allocate_returns_ip(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.post(f'/api/vmware/networks/{nid}/allocate',
                           data=json.dumps({}), content_type='application/json')
        data = json.loads(resp.data)
        assert 'ip' in data
        assert data['ip'] == '10.0.1.1'

    def test_allocate_includes_vm_metadata(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.post(f'/api/vmware/networks/{nid}/allocate',
                           data=json.dumps({'vm_name': 'prod-vm', 'datacenter': 'DC-East', 'cluster': 'Prod'}),
                           content_type='application/json')
        data = json.loads(resp.data)
        assert data['vm_name'] == 'prod-vm'
        assert data['datacenter'] == 'DC-East'
        assert data['cluster'] == 'Prod'

    def test_allocate_no_body_uses_defaults(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.post(f'/api/vmware/networks/{nid}/allocate')
        assert resp.status_code == 201

    def test_allocate_increments_ip(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        r1 = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        r2 = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        assert r1['ip'] != r2['ip']

    def test_allocate_disabled_returns_400(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        resp = client.post(f'/api/vmware/networks/{nid}/allocate')
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert 'error' in data

    def test_allocate_exhausted_returns_400(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/30')
        enable_network(nid)
        client.post(f'/api/vmware/networks/{nid}/allocate')
        client.post(f'/api/vmware/networks/{nid}/allocate')
        resp = client.post(f'/api/vmware/networks/{nid}/allocate')
        assert resp.status_code == 400

    def test_allocate_ip_appears_in_list(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        ips_resp = json.loads(client.get(f'/api/vmware/networks/{nid}/ips').data)
        allocated_ips = [a['ip'] for a in ips_resp['allocations']]
        assert alloc['ip'] in allocated_ips


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/vmware/ip/<ip>/release
# ══════════════════════════════════════════════════════════════════════════════

class TestAPIRelease:
    def test_release_returns_200(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        resp = client.delete(f'/api/vmware/ip/{alloc["ip"]}/release')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['released'] == alloc['ip']

    def test_release_nonexistent_returns_404(self, client):
        resp = client.delete('/api/vmware/ip/1.2.3.4/release')
        assert resp.status_code == 404

    def test_released_ip_not_in_list(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        client.delete(f'/api/vmware/ip/{alloc["ip"]}/release')
        ips_resp = json.loads(client.get(f'/api/vmware/networks/{nid}/ips').data)
        assert alloc['ip'] not in [a['ip'] for a in ips_resp['allocations']]

    def test_released_ip_removed_from_ipam(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = json.loads(client.post(f'/api/vmware/networks/{nid}/allocate').data)
        client.delete(f'/api/vmware/ip/{alloc["ip"]}/release')
        from ipam import get_ip
        assert get_ip(alloc['ip']) is None


# ══════════════════════════════════════════════════════════════════════════════
# GET /api/vmware/networks/<net_id>/ips
# ══════════════════════════════════════════════════════════════════════════════

class TestAPINetworkIPs:
    def test_returns_empty_list_when_no_allocs(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.get(f'/api/vmware/networks/{nid}/ips')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['allocations'] == []

    def test_returns_allocations(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        client.post(f'/api/vmware/networks/{nid}/allocate',
                    data=json.dumps({'vm_name': 'vm-01'}), content_type='application/json')
        resp = client.get(f'/api/vmware/networks/{nid}/ips')
        data = json.loads(resp.data)
        assert len(data['allocations']) == 1
        assert data['allocations'][0]['vm_name'] == 'vm-01'

    def test_returns_400_when_not_enabled(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        resp = client.get(f'/api/vmware/networks/{nid}/ips')
        assert resp.status_code == 400

    def test_returns_404_for_missing_network(self, client):
        resp = client.get('/api/vmware/networks/ghost/ips')
        assert resp.status_code == 404

    def test_response_includes_cidr(self, client):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        resp = client.get(f'/api/vmware/networks/{nid}/ips')
        data = json.loads(resp.data)
        assert data['cidr'] == '10.0.1.0/24'
