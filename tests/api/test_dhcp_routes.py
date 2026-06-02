"""API / route tests for the DHCP scope export blueprint."""
import json
import pytest
from ipam import new_id, save_network, save_ip, get_network
from dhcp import build_dhcp_scope


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _make_net(cidr='10.0.0.0/24', dhcp_enabled=False):
    net = {
        'id': new_id(), 'name': cidr, 'cidr': cidr,
        'description': '', 'vlan': '', 'project_id': new_id(),
        'template_id': None, 'pending_slots': [],
        'dhcp_enabled': dhcp_enabled,
    }
    save_network(net)
    return net


def _make_ip(net_id, ip_str, status='allocated', hostname='', mac=None):
    addr = {'ip': ip_str, 'hostname': hostname, 'description': '',
            'status': status, 'network_id': net_id}
    if mac:
        addr['mac_address'] = mac
    save_ip(addr)
    return addr


# ── DHCP list ────────────────────────────────────────────────────────────────────

class TestDhcpList:
    def test_list_page_renders(self, client):
        assert client.get('/dhcp').status_code == 200

    def test_list_shows_enabled_subnet(self, client):
        net = _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp')
        assert net['cidr'].encode() in resp.data

    def test_list_empty_state(self, client):
        resp = client.get('/dhcp')
        assert resp.status_code == 200


# ── Scope detail ─────────────────────────────────────────────────────────────────

class TestScopeDetail:
    def test_detail_renders_disabled(self, client):
        net = _make_net(dhcp_enabled=False)
        resp = client.get(f'/dhcp/scopes/{net["id"]}')
        assert resp.status_code == 200
        assert b'DHCP disabled' in resp.data

    def test_detail_renders_enabled(self, client):
        net = _make_net(dhcp_enabled=True)
        resp = client.get(f'/dhcp/scopes/{net["id"]}')
        assert resp.status_code == 200
        assert b'DHCP enabled' in resp.data

    def test_detail_shows_pool_ranges(self, client):
        net = _make_net(dhcp_enabled=True)
        _make_ip(net['id'], '10.0.0.100', status='dhcp')
        _make_ip(net['id'], '10.0.0.101', status='dhcp')
        resp = client.get(f'/dhcp/scopes/{net["id"]}')
        assert b'10.0.0.100' in resp.data

    def test_detail_shows_static_hosts(self, client):
        net = _make_net(dhcp_enabled=True)
        _make_ip(net['id'], '10.0.0.10', status='allocated',
                 hostname='myhost', mac='aa:bb:cc:11:22:33')
        resp = client.get(f'/dhcp/scopes/{net["id"]}')
        assert b'aa:bb:cc:11:22:33' in resp.data

    def test_detail_redirects_unknown(self, client):
        resp = client.get('/dhcp/scopes/no-such-net')
        assert resp.status_code == 302


# ── Toggle ───────────────────────────────────────────────────────────────────────

class TestToggleDhcp:
    def test_toggle_enables(self, client):
        net = _make_net(dhcp_enabled=False)
        client.post(f'/dhcp/scopes/{net["id"]}/toggle', follow_redirects=False)
        assert get_network(net['id'])['dhcp_enabled'] is True

    def test_toggle_disables(self, client):
        net = _make_net(dhcp_enabled=True)
        client.post(f'/dhcp/scopes/{net["id"]}/toggle', follow_redirects=False)
        assert get_network(net['id'])['dhcp_enabled'] is False


# ── Options form ─────────────────────────────────────────────────────────────────

class TestDhcpOptions:
    def test_options_form_renders(self, client):
        net = _make_net()
        resp = client.get(f'/dhcp/scopes/{net["id"]}/options')
        assert resp.status_code == 200

    def test_options_post_saves(self, client):
        net = _make_net()
        client.post(f'/dhcp/scopes/{net["id"]}/options', data={
            'dhcp_enabled': '1',
            'gateway': '10.0.0.1',
            'domain_name': 'lab.example.net',
            'domain_name_servers': '8.8.8.8\n8.8.4.4',
            'ntp_servers': '',
            'interface_mtu': '0',
            'lease_default': '3600',
            'lease_max': '7200',
            'passthrough': '',
        }, follow_redirects=False)
        updated = get_network(net['id'])
        assert updated['dhcp_enabled'] is True
        assert updated['dhcp_options']['gateway'] == '10.0.0.1'
        assert '8.8.8.8' in updated['dhcp_options']['domain_name_servers']


# ── ISC conf export ───────────────────────────────────────────────────────────────

class TestExportIscConf:
    def test_export_returns_text(self, client):
        net = _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export.conf')
        assert resp.status_code == 200
        assert b'dhcpd' in resp.data.lower() or b'subnet' in resp.data or b'Generated' in resp.data

    def test_export_contains_subnet(self, client):
        net = _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export.conf')
        assert b'10.0.0.0' in resp.data

    def test_export_contains_range(self, client):
        net = _make_net(dhcp_enabled=True)
        for i in range(100, 103):
            _make_ip(net['id'], f'10.0.0.{i}', status='dhcp')
        resp = client.get('/dhcp/export.conf')
        assert b'range 10.0.0.100 10.0.0.102;' in resp.data

    def test_export_contains_host_block(self, client):
        net = _make_net(dhcp_enabled=True)
        _make_ip(net['id'], '10.0.0.10', status='allocated',
                 hostname='server01', mac='de:ad:be:ef:00:01')
        resp = client.get('/dhcp/export.conf')
        assert b'host server01' in resp.data
        assert b'de:ad:be:ef:00:01' in resp.data

    def test_export_skips_disabled(self, client):
        _make_net(cidr='10.1.0.0/24', dhcp_enabled=False)
        resp = client.get('/dhcp/export.conf')
        assert b'10.1.0.0' not in resp.data


# ── Kea JSON export ───────────────────────────────────────────────────────────────

class TestExportKeaJson:
    def test_export_valid_json(self, client):
        _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export.json')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'Dhcp4' in data

    def test_export_contains_subnet(self, client):
        _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export.json')
        data = json.loads(resp.data)
        subnets = [s['subnet'] for s in data['Dhcp4']['subnet4']]
        assert '10.0.0.0/24' in subnets


# ── Bundle export ─────────────────────────────────────────────────────────────────

class TestExportBundle:
    def test_bundle_returns_gzip(self, client):
        _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export/bundle.tar.gz')
        assert resp.status_code == 200
        assert resp.content_type == 'application/gzip'

    def test_bundle_contains_kea_json(self, client):
        import io, tarfile
        _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export/bundle.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(resp.data), mode='r:gz') as tf:
            assert 'kea-dhcp4.json' in tf.getnames()

    def test_bundle_contains_dhcpd_conf(self, client):
        import io, tarfile
        _make_net(dhcp_enabled=True)
        resp = client.get('/dhcp/export/bundle.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(resp.data), mode='r:gz') as tf:
            assert any(n.endswith('dhcpd.conf') for n in tf.getnames())


# ── MAC address on IP form ────────────────────────────────────────────────────────

class TestMacOnIpForm:
    def test_add_ip_with_mac(self, client, seeded_subnet):
        net_id = seeded_subnet['id']
        resp = client.post(f'/networks/{net_id}/ip/add', data={
            'ip': '10.0.1.50', 'hostname': 'host1',
            'description': '', 'status': 'allocated',
            'mac_address': 'AA:BB:CC:11:22:33',
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ipam import get_ip
        addr = get_ip('10.0.1.50')
        assert addr['mac_address'] == 'aa:bb:cc:11:22:33'

    def test_add_ip_invalid_mac_warns(self, client, seeded_subnet):
        net_id = seeded_subnet['id']
        resp = client.post(f'/networks/{net_id}/ip/add', data={
            'ip': '10.0.1.51', 'hostname': 'host2',
            'description': '', 'status': 'allocated',
            'mac_address': 'not-a-mac',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Invalid MAC' in resp.data

    def test_edit_ip_saves_mac(self, client, seeded_subnet):
        net_id = seeded_subnet['id']
        # Create the IP first
        client.post(f'/networks/{net_id}/ip/add', data={
            'ip': '10.0.1.52', 'hostname': 'host3',
            'description': '', 'status': 'allocated', 'mac_address': '',
        }, follow_redirects=False)
        # Edit it to add a MAC
        resp = client.post('/ip/10.0.1.52/edit', data={
            'hostname': 'host3', 'description': '', 'status': 'allocated',
            'mac_address': 'de:ad:be:ef:00:01',
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ipam import get_ip
        assert get_ip('10.0.1.52')['mac_address'] == 'de:ad:be:ef:00:01'

    def test_edit_ip_clears_mac(self, client, seeded_subnet):
        net_id = seeded_subnet['id']
        client.post(f'/networks/{net_id}/ip/add', data={
            'ip': '10.0.1.53', 'hostname': 'h4', 'description': '',
            'status': 'allocated', 'mac_address': 'aa:bb:cc:00:00:01',
        }, follow_redirects=False)
        client.post('/ip/10.0.1.53/edit', data={
            'hostname': 'h4', 'description': '', 'status': 'allocated',
            'mac_address': '',  # clear it
        }, follow_redirects=False)
        from ipam import get_ip
        addr = get_ip('10.0.1.53')
        assert 'mac_address' not in addr
