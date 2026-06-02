"""API / route tests for the DNS zone export blueprint."""
import json
import pytest
from ipam import new_id, save_network, save_ip, add_labels_to_network
from dns import save_zone, get_zone, all_zones


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _zone_payload(kind='forward', name='corp.example.com', **kwargs):
    data = {
        'name': name, 'kind': kind, 'enabled': '1',
        'primary_ns': 'ns1.corp.example.com.',
        'admin_email': 'hostmaster.corp.example.com.',
        'refresh': '3600', 'retry': '600', 'expire': '1209600',
        'minimum_ttl': '300', 'default_ttl': '300',
        'serial_policy': 'manual', 'serial_current': '0',
        'nameservers': 'ns1.corp.example.com.',
        'include_label_sets': '', 'exclude_label_sets': '',
        'include_cidrs': '', 'exclude_cidrs': '',
        'static_records': '[]',
    }
    data.update(kwargs)
    return data


def _seed_ip(net_cidr, ip_str, hostname):
    pid = new_id()
    net = {'id': new_id(), 'name': net_cidr, 'cidr': net_cidr,
           'description': '', 'vlan': '', 'project_id': pid,
           'template_id': None, 'pending_slots': []}
    save_network(net)
    save_ip({'ip': ip_str, 'hostname': hostname, 'description': '',
             'status': 'allocated', 'network_id': net['id']})
    return net


# ── Zone list ────────────────────────────────────────────────────────────────────

class TestDnsZoneList:
    def test_list_page_renders(self, client):
        assert client.get('/dns').status_code == 200

    def test_list_shows_zone_name(self, client):
        zone = {'id': new_id(), 'kind': 'forward', 'name': 'test.example.com',
                'enabled': True, 'primary_ns': '', 'admin_email': '',
                'refresh': 3600, 'retry': 600, 'expire': 1209600,
                'minimum_ttl': 300, 'default_ttl': 300,
                'serial_policy': 'manual', 'serial_current': 0,
                'nameservers': [], 'scope': {'include_label_sets': [],
                'exclude_label_sets': [], 'include_cidrs': [], 'exclude_cidrs': []},
                'static_records': []}
        save_zone(zone)
        resp = client.get('/dns')
        assert b'test.example.com' in resp.data

    def test_list_empty_state(self, client):
        resp = client.get('/dns')
        assert resp.status_code == 200


# ── Zone CRUD ────────────────────────────────────────────────────────────────────

class TestDnsZoneCrud:
    def test_add_zone_get(self, client):
        assert client.get('/dns/zones/add').status_code == 200

    def test_add_zone_post(self, client):
        resp = client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        assert resp.status_code == 302
        zones = all_zones()
        assert any(z['name'] == 'corp.example.com' for z in zones)

    def test_add_zone_missing_name(self, client):
        data = _zone_payload(name='')
        resp = client.post('/dns/zones/add', data=data, follow_redirects=False)
        assert resp.status_code == 200
        assert b'required' in resp.data.lower()

    def test_edit_zone(self, client):
        client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        zid = all_zones()[0]['id']
        resp = client.get(f'/dns/zones/{zid}/edit')
        assert resp.status_code == 200
        resp2 = client.post(f'/dns/zones/{zid}/edit',
                             data=_zone_payload(name='updated.example.com'),
                             follow_redirects=False)
        assert resp2.status_code == 302
        assert get_zone(zid)['name'] == 'updated.example.com'

    def test_delete_zone(self, client):
        client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        zid = all_zones()[0]['id']
        resp = client.post(f'/dns/zones/{zid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_zone(zid) is None

    def test_toggle_zone(self, client):
        client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        zid   = all_zones()[0]['id']
        was   = get_zone(zid)['enabled']
        client.post(f'/dns/zones/{zid}/toggle', follow_redirects=False)
        assert get_zone(zid)['enabled'] == (not was)

    def test_zone_detail_page(self, client):
        client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        zid  = all_zones()[0]['id']
        resp = client.get(f'/dns/zones/{zid}')
        assert resp.status_code == 200
        assert b'corp.example.com' in resp.data

    def test_unknown_zone_redirects(self, client):
        resp = client.get('/dns/zones/no-such-zone')
        assert resp.status_code == 302


# ── Zone export ──────────────────────────────────────────────────────────────────

class TestDnsZoneExport:
    def _make_forward_zone(self, client):
        client.post('/dns/zones/add', data=_zone_payload(), follow_redirects=False)
        return all_zones()[0]

    def test_export_zone_file(self, client):
        zone = self._make_forward_zone(client)
        resp = client.get(f'/dns/zones/{zone["id"]}/export.zone')
        assert resp.status_code == 200
        assert b'SOA' in resp.data
        assert b'$ORIGIN' in resp.data

    def test_export_zone_file_contains_records(self, client):
        _seed_ip('10.0.1.0/24', '10.0.1.5', 'myhost.corp.example.com')
        zone = self._make_forward_zone(client)
        resp = client.get(f'/dns/zones/{zone["id"]}/export.zone')
        assert b'10.0.1.5' in resp.data
        assert b'myhost' in resp.data

    def test_export_zone_json(self, client):
        zone = self._make_forward_zone(client)
        resp = client.get(f'/dns/zones/{zone["id"]}/export.json')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['zone'] == 'corp.example.com'
        assert 'records' in data

    def test_export_json_contains_records(self, client):
        _seed_ip('10.0.1.0/24', '10.0.1.7', 'srv.corp.example.com')
        zone = self._make_forward_zone(client)
        resp = client.get(f'/dns/zones/{zone["id"]}/export.json')
        data = json.loads(resp.data)
        vals = [r['value'] for r in data['records']]
        assert '10.0.1.7' in vals

    def test_export_tarball(self, client):
        self._make_forward_zone(client)
        resp = client.get('/dns/export.tar.gz')
        assert resp.status_code == 200
        assert resp.content_type == 'application/gzip'

    def test_export_tarball_includes_named_conf(self, client):
        import gzip, tarfile, io
        self._make_forward_zone(client)
        resp = client.get('/dns/export.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(resp.data), mode='r:gz') as tf:
            names = tf.getnames()
        assert 'named.conf.local' in names
        assert any(n.endswith('.zone') for n in names)

    def test_export_unknown_zone_404(self, client):
        resp = client.get('/dns/zones/no-such/export.zone')
        assert resp.status_code == 404

    def test_export_tarball_skips_disabled_zones(self, client):
        import gzip, tarfile, io
        self._make_forward_zone(client)
        zid = all_zones()[0]['id']
        client.post(f'/dns/zones/{zid}/toggle')  # disable it
        resp = client.get('/dns/export.tar.gz')
        with tarfile.open(fileobj=io.BytesIO(resp.data), mode='r:gz') as tf:
            names = tf.getnames()
        assert not any(n.endswith('.zone') for n in names)


# ── Reverse zone ─────────────────────────────────────────────────────────────────

class TestReverseZoneRoutes:
    def test_reverse_zone_export_contains_ptr(self, client):
        _seed_ip('10.0.0.0/24', '10.0.0.1', 'gw.corp.example.com')
        client.post('/dns/zones/add', data=_zone_payload(
            kind='reverse', name='10.in-addr.arpa',
            include_cidrs='10.0.0.0/8',
        ), follow_redirects=False)
        zid  = all_zones()[0]['id']
        resp = client.get(f'/dns/zones/{zid}/export.zone')
        assert b'PTR' in resp.data
        assert b'gw.corp.example.com.' in resp.data
