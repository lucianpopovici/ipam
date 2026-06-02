"""Unit tests for DHCP scope building, serialization, and MAC validation."""
import ipaddress
import json
import pytest
from dhcp import (
    normalize_mac, build_dhcp_scope, emit_isc_conf, emit_kea_json,
    _collapse_ranges, _default_options,
)
from ipam import new_id, save_network, save_ip


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _net(cidr, dhcp_enabled=True, dhcp_options=None):
    net = {
        'id': new_id(), 'name': cidr, 'cidr': cidr,
        'description': 'test subnet', 'vlan': '', 'project_id': new_id(),
        'template_id': None, 'pending_slots': [],
        'dhcp_enabled': dhcp_enabled,
    }
    if dhcp_options:
        net['dhcp_options'] = dhcp_options
    save_network(net)
    return net


def _ip(net_id, ip_str, status='allocated', hostname='', mac=None):
    addr = {'ip': ip_str, 'hostname': hostname, 'description': '',
            'status': status, 'network_id': net_id}
    if mac:
        addr['mac_address'] = mac
    save_ip(addr)
    return addr


# ── MAC validation ───────────────────────────────────────────────────────────────

class TestNormalizeMac:
    def test_valid_colon(self):
        assert normalize_mac('AA:BB:CC:11:22:33') == 'aa:bb:cc:11:22:33'

    def test_valid_hyphen(self):
        assert normalize_mac('aa-bb-cc-11-22-33') == 'aa:bb:cc:11:22:33'

    def test_lowercase_passthrough(self):
        assert normalize_mac('de:ad:be:ef:00:01') == 'de:ad:be:ef:00:01'

    def test_invalid_too_short(self):
        assert normalize_mac('aa:bb:cc:11:22') is None

    def test_invalid_chars(self):
        assert normalize_mac('gg:bb:cc:11:22:33') is None

    def test_empty(self):
        assert normalize_mac('') is None


# ── Range collapsing ─────────────────────────────────────────────────────────────

class TestCollapseRanges:
    def test_single_ip(self):
        assert _collapse_ranges(['10.0.0.5']) == [('10.0.0.5', '10.0.0.5')]

    def test_contiguous(self):
        ips = ['10.0.0.1', '10.0.0.2', '10.0.0.3']
        assert _collapse_ranges(ips) == [('10.0.0.1', '10.0.0.3')]

    def test_with_gap(self):
        ips = ['10.0.0.1', '10.0.0.2', '10.0.0.5', '10.0.0.6']
        assert _collapse_ranges(ips) == [
            ('10.0.0.1', '10.0.0.2'),
            ('10.0.0.5', '10.0.0.6'),
        ]

    def test_out_of_order_sorted(self):
        ips = ['10.0.0.3', '10.0.0.1', '10.0.0.2']
        assert _collapse_ranges(ips) == [('10.0.0.1', '10.0.0.3')]

    def test_empty(self):
        assert _collapse_ranges([]) == []


# ── Scope building ───────────────────────────────────────────────────────────────

class TestBuildDhcpScope:
    def test_disabled_returns_none(self, fake_redis):
        net = _net('10.0.1.0/24', dhcp_enabled=False)
        assert build_dhcp_scope(net) is None

    def test_basic_scope(self, fake_redis):
        net = _net('10.0.0.0/24')
        scope = build_dhcp_scope(net)
        assert scope is not None
        assert scope['cidr']    == '10.0.0.0/24'
        assert scope['network'] == '10.0.0.0'
        assert scope['netmask'] == '255.255.255.0'

    def test_gateway_from_options(self, fake_redis):
        net = _net('10.0.0.0/24', dhcp_options={'gateway': '10.0.0.254'})
        scope = build_dhcp_scope(net)
        assert scope['gateway'] == '10.0.0.254'

    def test_gateway_auto_from_reserved(self, fake_redis):
        net = _net('10.0.0.0/24')
        _ip(net['id'], '10.0.0.1', status='reserved')
        scope = build_dhcp_scope(net)
        assert scope['gateway'] == '10.0.0.1'

    def test_pool_ranges_from_dhcp_ips(self, fake_redis):
        net = _net('10.0.0.0/24')
        for i in range(100, 106):
            _ip(net['id'], f'10.0.0.{i}', status='dhcp')
        scope = build_dhcp_scope(net)
        assert scope['pool_ranges'] == [('10.0.0.100', '10.0.0.105')]

    def test_pool_ranges_with_gap(self, fake_redis):
        net = _net('10.0.0.0/24')
        for i in [10, 11, 20, 21]:
            _ip(net['id'], f'10.0.0.{i}', status='dhcp')
        scope = build_dhcp_scope(net)
        assert len(scope['pool_ranges']) == 2

    def test_static_host_with_mac(self, fake_redis):
        net = _net('10.0.0.0/24')
        _ip(net['id'], '10.0.0.10', status='allocated',
            hostname='srv01', mac='aa:bb:cc:11:22:33')
        scope = build_dhcp_scope(net)
        assert len(scope['static_hosts']) == 1
        h = scope['static_hosts'][0]
        assert h['mac']      == 'aa:bb:cc:11:22:33'
        assert h['hostname'] == 'srv01'
        assert h['ip']       == '10.0.0.10'

    def test_allocated_without_mac_not_static(self, fake_redis):
        net = _net('10.0.0.0/24')
        _ip(net['id'], '10.0.0.10', status='allocated', hostname='srv02')
        scope = build_dhcp_scope(net)
        assert len(scope['static_hosts']) == 0

    def test_version_v4(self, fake_redis):
        net = _net('10.0.0.0/24')
        assert build_dhcp_scope(net)['version'] == 4

    def test_version_v6(self, fake_redis):
        net = _net('2001:db8::/64')
        assert build_dhcp_scope(net)['version'] == 6


# ── ISC serializer ───────────────────────────────────────────────────────────────

class TestEmitIscConf:
    def _scope(self, fake_redis, cidr='10.0.0.0/24', gateway='10.0.0.1',
               pool=None, hosts=None, opts=None):
        net = _net(cidr, dhcp_options={**_default_options(), **(opts or {})})
        if gateway:
            net['dhcp_options'] = {**_default_options(), 'gateway': gateway, **(opts or {})}
            save_network(net)
        _ip(net['id'], gateway, status='reserved') if gateway else None
        for ip in (pool or []):
            _ip(net['id'], ip, status='dhcp')
        for h in (hosts or []):
            _ip(net['id'], h['ip'], status='allocated', hostname=h['name'], mac=h['mac'])
        return build_dhcp_scope(net)

    def test_contains_subnet_line(self, fake_redis):
        scope = self._scope(fake_redis)
        conf  = emit_isc_conf([scope])
        assert 'subnet 10.0.0.0 netmask 255.255.255.0' in conf

    def test_routers_option(self, fake_redis):
        scope = self._scope(fake_redis)
        assert 'option routers 10.0.0.1;' in emit_isc_conf([scope])

    def test_range_emitted(self, fake_redis):
        scope = self._scope(fake_redis, pool=['10.0.0.100', '10.0.0.101'])
        conf  = emit_isc_conf([scope])
        assert 'range 10.0.0.100 10.0.0.101;' in conf

    def test_host_block(self, fake_redis):
        scope = self._scope(fake_redis,
                            hosts=[{'ip': '10.0.0.10', 'name': 'srv01', 'mac': 'aa:bb:cc:11:22:33'}])
        conf = emit_isc_conf([scope])
        assert 'host srv01' in conf
        assert 'hardware ethernet aa:bb:cc:11:22:33;' in conf
        assert 'fixed-address    10.0.0.10;' in conf

    def test_dns_servers_option(self, fake_redis):
        scope = self._scope(fake_redis, opts={'domain_name_servers': ['8.8.8.8', '8.8.4.4']})
        conf  = emit_isc_conf([scope])
        assert 'domain-name-servers 8.8.8.8, 8.8.4.4;' in conf

    def test_passthrough_appended(self, fake_redis):
        scope = self._scope(fake_redis, opts={'passthrough': 'option tftp-server-name "10.0.0.5";'})
        conf  = emit_isc_conf([scope])
        assert 'option tftp-server-name "10.0.0.5";' in conf

    def test_no_range_when_empty(self, fake_redis):
        scope = self._scope(fake_redis)
        conf  = emit_isc_conf([scope])
        assert 'range' not in conf

    def test_multiple_scopes(self, fake_redis):
        s1 = self._scope(fake_redis, cidr='10.0.1.0/24', gateway='10.0.1.1')
        s2 = self._scope(fake_redis, cidr='10.0.2.0/24', gateway='10.0.2.1')
        conf = emit_isc_conf([s1, s2])
        assert 'subnet 10.0.1.0' in conf
        assert 'subnet 10.0.2.0' in conf


# ── Kea serializer ───────────────────────────────────────────────────────────────

class TestEmitKeaJson:
    def _scope(self, fake_redis, cidr='10.0.0.0/24', gateway='10.0.0.1', pool=None, hosts=None):
        net = _net(cidr, dhcp_options={**_default_options(), 'gateway': gateway})
        _ip(net['id'], gateway, status='reserved')
        for ip in (pool or []):
            _ip(net['id'], ip, status='dhcp')
        for h in (hosts or []):
            _ip(net['id'], h['ip'], status='allocated', hostname=h['name'], mac=h['mac'])
        return build_dhcp_scope(net)

    def test_top_level_structure(self, fake_redis):
        kea = emit_kea_json([])
        assert 'Dhcp4' in kea
        assert 'subnet4' in kea['Dhcp4']

    def test_subnet_entry(self, fake_redis):
        scope = self._scope(fake_redis)
        kea   = emit_kea_json([scope])
        assert kea['Dhcp4']['subnet4'][0]['subnet'] == '10.0.0.0/24'

    def test_pool_entry(self, fake_redis):
        scope = self._scope(fake_redis, pool=['10.0.0.100', '10.0.0.101', '10.0.0.102'])
        kea   = emit_kea_json([scope])
        pools = kea['Dhcp4']['subnet4'][0]['pools']
        assert pools[0]['pool'] == '10.0.0.100 - 10.0.0.102'

    def test_reservation_entry(self, fake_redis):
        scope = self._scope(fake_redis,
                            hosts=[{'ip': '10.0.0.10', 'name': 'srv', 'mac': 'aa:bb:cc:00:00:01'}])
        kea   = emit_kea_json([scope])
        res   = kea['Dhcp4']['subnet4'][0]['reservations']
        assert res[0]['hw-address'] == 'aa:bb:cc:00:00:01'
        assert res[0]['ip-address'] == '10.0.0.10'

    def test_routers_option_data(self, fake_redis):
        scope = self._scope(fake_redis)
        kea   = emit_kea_json([scope])
        opts  = {o['name']: o['data'] for o in kea['Dhcp4']['subnet4'][0]['option-data']}
        assert opts['routers'] == '10.0.0.1'
