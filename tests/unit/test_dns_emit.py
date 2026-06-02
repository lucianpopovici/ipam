"""Unit tests for DNS zone emission logic."""
import ipaddress
import json
import pytest
from dns import (
    emit_zone_file, collect_zone_records,
    _ptr_absolute, _ptr_label, _strip_zone,
    _label_sets_match, _ip_in_cidrs,
    _emit_soa, _resolve_serial,
    save_zone, get_zone,
)
from ipam import new_id, save_network, save_ip, add_labels_to_network


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _fwd_zone(**kwargs):
    z = {
        'id': new_id(), 'kind': 'forward', 'name': 'corp.example.com',
        'enabled': True, 'primary_ns': 'ns1.corp.example.com.',
        'admin_email': 'hostmaster.corp.example.com.',
        'refresh': 3600, 'retry': 600, 'expire': 1209600,
        'minimum_ttl': 300, 'default_ttl': 300,
        'serial_policy': 'manual', 'serial_current': 2026010100,
        'nameservers': ['ns1.corp.example.com.'],
        'scope': {'include_label_sets': [], 'exclude_label_sets': [],
                  'include_cidrs': [], 'exclude_cidrs': []},
        'static_records': [],
    }
    z.update(kwargs)
    return z


def _rev_zone(include_cidrs, **kwargs):
    z = {
        'id': new_id(), 'kind': 'reverse', 'name': '10.in-addr.arpa',
        'enabled': True, 'primary_ns': 'ns1.corp.example.com.',
        'admin_email': 'hostmaster.corp.example.com.',
        'refresh': 3600, 'retry': 600, 'expire': 1209600,
        'minimum_ttl': 300, 'default_ttl': 300,
        'serial_policy': 'manual', 'serial_current': 2026010100,
        'nameservers': [],
        'scope': {'include_label_sets': [], 'exclude_label_sets': [],
                  'include_cidrs': include_cidrs, 'exclude_cidrs': []},
        'static_records': [],
    }
    z.update(kwargs)
    return z


def _make_network(pid, cidr, labels=None):
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr,
           'description': '', 'vlan': '', 'project_id': pid,
           'template_id': None, 'pending_slots': []}
    save_network(net)
    if labels:
        add_labels_to_network(net['id'], labels)
    return net


def _make_ip(net_id, ip_str, hostname):
    save_ip({'ip': ip_str, 'hostname': hostname, 'description': '',
             'status': 'allocated', 'network_id': net_id})


# ── PTR helpers ──────────────────────────────────────────────────────────────────

class TestPtrHelpers:
    def test_ptr_absolute_ipv4(self):
        ip = ipaddress.ip_address('10.0.0.1')
        assert _ptr_absolute(ip) == '1.0.0.10.in-addr.arpa'

    def test_ptr_absolute_ipv6(self):
        ip = ipaddress.ip_address('2001:db8::1')
        abs_name = _ptr_absolute(ip)
        assert abs_name.endswith('.ip6.arpa')
        assert '1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0' in abs_name

    def test_ptr_label_class_a_zone(self):
        ip   = ipaddress.ip_address('10.0.0.1')
        zone = '10.in-addr.arpa'
        assert _ptr_label(ip, zone) == '1.0.0'

    def test_ptr_label_class_c_zone(self):
        ip   = ipaddress.ip_address('10.0.0.1')
        zone = '0.0.10.in-addr.arpa'
        assert _ptr_label(ip, zone) == '1'

    def test_ptr_label_outside_zone(self):
        ip   = ipaddress.ip_address('192.168.1.5')
        zone = '10.in-addr.arpa'
        assert _ptr_label(ip, zone) is None

    def test_strip_zone_subdomain(self):
        assert _strip_zone('vm01.corp.example.com', 'corp.example.com') == 'vm01'

    def test_strip_zone_apex(self):
        assert _strip_zone('corp.example.com', 'corp.example.com') == '@'

    def test_strip_zone_outside(self):
        assert _strip_zone('vm01.other.com', 'corp.example.com') == 'vm01.other.com'


# ── Scope matching ───────────────────────────────────────────────────────────────

class TestScopeMatching:
    def test_empty_include_matches_all(self):
        assert _label_sets_match(['prod', 'mgmt'], [], [])

    def test_include_set_matches(self):
        assert _label_sets_match(['prod', 'mgmt'], [['prod']], [])

    def test_include_set_no_match(self):
        assert not _label_sets_match(['mgmt'], [['prod']], [])

    def test_include_multi_set_or(self):
        # subnet has 'oob' — matches second set
        assert _label_sets_match(['oob'], [['prod'], ['oob']], [])

    def test_exclude_overrides_include(self):
        assert not _label_sets_match(['prod'], [], [['prod']])

    def test_ip_in_cidrs(self):
        ip = ipaddress.ip_address('10.0.0.1')
        assert _ip_in_cidrs(ip, ['10.0.0.0/8'])
        assert not _ip_in_cidrs(ip, ['192.168.0.0/16'])

    def test_ip_in_cidrs_empty(self):
        ip = ipaddress.ip_address('10.0.0.1')
        assert not _ip_in_cidrs(ip, [])


# ── SOA formatting ───────────────────────────────────────────────────────────────

class TestSOAFormatting:
    def test_soa_contains_serial(self):
        zone = _fwd_zone()
        soa  = _emit_soa(zone, 2026010142)
        assert '2026010142' in soa
        assert '$ORIGIN corp.example.com.' in soa
        assert 'SOA' in soa

    def test_soa_auto_trailing_dot(self):
        zone = _fwd_zone(primary_ns='ns1.corp.example.com',  # no trailing dot
                         admin_email='hostmaster.corp.example.com')
        soa = _emit_soa(zone, 1)
        assert 'ns1.corp.example.com.' in soa
        assert 'hostmaster.corp.example.com.' in soa

    def test_soa_default_ns_from_zone_name(self):
        zone = _fwd_zone(primary_ns='', admin_email='')
        soa  = _emit_soa(zone, 1)
        assert 'ns1.corp.example.com.' in soa


# ── Serial policies ──────────────────────────────────────────────────────────────

class TestSerialPolicies:
    def test_manual_policy_unchanged(self, fake_redis):
        zone = _fwd_zone(serial_policy='manual', serial_current=9999)
        save_zone(zone)
        serial, updated = _resolve_serial(zone, bump=False)
        assert serial == 9999

    def test_on_export_bumps_when_requested(self, fake_redis):
        zone = _fwd_zone(serial_policy='on_export', serial_current=5)
        save_zone(zone)
        serial, _ = _resolve_serial(zone, bump=True)
        assert serial == 6

    def test_on_export_no_bump_on_preview(self, fake_redis):
        zone = _fwd_zone(serial_policy='on_export', serial_current=5)
        save_zone(zone)
        serial, _ = _resolve_serial(zone, bump=False)
        assert serial == 5

    def test_date_based_format(self, fake_redis):
        zone = _fwd_zone(serial_policy='date_based')
        save_zone(zone)
        serial, _ = _resolve_serial(zone, bump=True)
        # Should be YYYYMMDDnn — at minimum 10 digits
        assert serial > 2020000000

    def test_date_based_increments_within_day(self, fake_redis):
        zone = _fwd_zone(serial_policy='date_based')
        save_zone(zone)
        s1, _ = _resolve_serial(zone, bump=True)
        s2, _ = _resolve_serial(zone, bump=True)
        assert s2 > s1


# ── Forward zone emission ────────────────────────────────────────────────────────

class TestForwardZoneEmission:
    def test_a_record_emitted(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '10.0.1.0/24')
        _make_ip(net['id'], '10.0.1.10', 'vm01.corp.example.com')
        zone    = _fwd_zone()
        content = emit_zone_file(zone)
        assert 'A' in content
        assert '10.0.1.10' in content
        assert 'vm01' in content

    def test_aaaa_record_for_ipv6(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '2001:db8::/64')
        _make_ip(net['id'], '2001:db8::1', 'v6host.corp.example.com')
        zone    = _fwd_zone()
        content = emit_zone_file(zone)
        assert 'AAAA' in content
        assert '2001:db8::1' in content

    def test_no_record_without_hostname(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '10.0.2.0/24')
        _make_ip(net['id'], '10.0.2.5', '')
        zone    = _fwd_zone()
        records = collect_zone_records(zone)
        assert not any(r['value'] == '10.0.2.5' for r in records)

    def test_label_scope_filters(self, fake_redis):
        pid    = new_id()
        net_in = _make_network(pid, '10.0.1.0/24', labels=['prod'])
        net_out = _make_network(pid, '10.0.2.0/24', labels=['test'])
        _make_ip(net_in['id'],  '10.0.1.1', 'in.corp.example.com')
        _make_ip(net_out['id'], '10.0.2.1', 'out.corp.example.com')
        zone = _fwd_zone(scope={
            'include_label_sets': [['prod']],
            'exclude_label_sets': [], 'include_cidrs': [], 'exclude_cidrs': [],
        })
        records = collect_zone_records(zone)
        ips = {r['value'] for r in records if r['type'] == 'A'}
        assert '10.0.1.1' in ips
        assert '10.0.2.1' not in ips

    def test_static_records_appended(self, fake_redis):
        zone = _fwd_zone(static_records=[
            {'name': '@', 'type': 'MX', 'value': '10 mail.corp.example.com.', 'ttl': 3600},
        ])
        content = emit_zone_file(zone)
        assert 'MX' in content
        assert 'mail.corp.example.com.' in content

    def test_ns_records_emitted(self, fake_redis):
        zone = _fwd_zone(nameservers=['ns1.corp.example.com.', 'ns2.corp.example.com.'])
        content = emit_zone_file(zone)
        assert 'NS' in content
        assert 'ns1.corp.example.com.' in content
        assert 'ns2.corp.example.com.' in content


# ── Reverse zone emission ────────────────────────────────────────────────────────

class TestReverseZoneEmission:
    def test_ptr_record_emitted(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '10.0.0.0/24')
        _make_ip(net['id'], '10.0.0.1', 'gw.corp.example.com')
        zone    = _rev_zone(['10.0.0.0/8'])
        content = emit_zone_file(zone)
        assert 'PTR' in content
        assert 'gw.corp.example.com.' in content

    def test_ptr_label_relative_to_zone(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '10.0.0.0/24')
        _make_ip(net['id'], '10.0.0.5', 'host5.corp.example.com')
        zone    = _rev_zone(['10.0.0.0/24'], name='0.0.10.in-addr.arpa')
        records = collect_zone_records(zone)
        ptr = next((r for r in records if r['type'] == 'PTR'), None)
        assert ptr is not None
        assert ptr['name'] == '5'  # relative label in 0.0.10.in-addr.arpa

    def test_ip_outside_include_cidr_excluded(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '192.168.0.0/24')
        _make_ip(net['id'], '192.168.0.1', 'other.corp.example.com')
        zone    = _rev_zone(['10.0.0.0/8'])
        records = collect_zone_records(zone)
        assert not any(r['type'] == 'PTR' for r in records)

    def test_exclude_cidr_removes_ip(self, fake_redis):
        pid = new_id()
        net = _make_network(pid, '10.0.0.0/24')
        _make_ip(net['id'], '10.0.0.99', 'excluded.corp.example.com')
        zone = _rev_zone(
            ['10.0.0.0/8'],
            scope={
                'include_label_sets': [], 'exclude_label_sets': [],
                'include_cidrs': ['10.0.0.0/8'],
                'exclude_cidrs': ['10.0.0.99/32'],
            },
        )
        records = collect_zone_records(zone)
        assert not any('excluded' in r.get('value', '') for r in records)
