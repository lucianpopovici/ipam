"""

pytestmark = pytest.mark.unit

Unit tests for ipam.py helper functions.
All Redis I/O is intercepted by the fake_redis fixture in conftest.py.
"""
import pytest
import ipaddress
from ipam import (
    parse_labels,
    carve_next_subnet,
    pool_by_label_set,
    resolve_template_rules,
    _validate_rules,
    project_pool_summary,
    global_pool_summary,
    get_project, save_project,
    get_network, save_network,
    get_ip, save_ip,
    add_labels_to_network, get_network_labels,
    remove_labels_from_network,
    label_scope,
    add_global_label, remove_global_label,
    add_project_label, remove_project_label,
    available_labels_for_project,
    get_template, save_template, delete_template,
    global_templates, project_templates,
    available_templates_for_project, template_scope,
    set_pending_slots, confirm_slot, confirm_all_slots,
    dismiss_slot, dismiss_all_slots,
    net_stats, project_networks, used_subnets_in_project,
    new_id, project_nets_key,
)
from db import r as _r


# ══════════════════════════════════════════════════════════════════════════════
# parse_labels
# ══════════════════════════════════════════════════════════════════════════════

class TestParseLabels:
    def test_empty_string(self):
        assert parse_labels('') == []

    def test_single_label(self):
        assert parse_labels('PROD') == ['PROD']

    def test_multiple_labels(self):
        assert parse_labels('PROD,London,web') == ['PROD', 'London', 'web']

    def test_strips_whitespace(self):
        assert parse_labels(' PROD , London ') == ['PROD', 'London']

    def test_deduplicates(self):
        assert parse_labels('PROD,PROD,London') == ['PROD', 'London']

    def test_ignores_empty_segments(self):
        assert parse_labels('PROD,,London') == ['PROD', 'London']

    def test_none_input(self):
        assert parse_labels(None) == []


# ══════════════════════════════════════════════════════════════════════════════
# carve_next_subnet
# ══════════════════════════════════════════════════════════════════════════════

class TestCarveNextSubnet:
    def _make_project(self, supernet='10.0.0.0/16'):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': supernet, 'description': ''})
        return pid

    def test_first_carve(self):
        pid  = self._make_project()
        cidr = carve_next_subnet('10.0.0.0/16', 24, pid)
        assert str(cidr) == '10.0.0.0/24'

    def test_second_carve_no_overlap(self):
        pid = self._make_project()
        c1  = carve_next_subnet('10.0.0.0/16', 24, pid)
        net = {'id': new_id(), 'name': 'n1', 'cidr': str(c1),
               'description': '', 'vlan': '', 'project_id': pid}
        save_network(net)
        _r.sadd(project_nets_key(pid), net['id'])
        c2  = carve_next_subnet('10.0.0.0/16', 24, pid)
        assert str(c2) == '10.0.1.0/24'
        assert not ipaddress.ip_network(str(c1)).overlaps(ipaddress.ip_network(str(c2)))

    def test_prefix_too_large_raises(self):
        pid = self._make_project('10.0.0.0/24')
        with pytest.raises(ValueError, match='must be smaller'):
            carve_next_subnet('10.0.0.0/24', 24, pid)

    def test_exhausted_raises(self):
        pid = self._make_project('10.0.0.0/30')
        # Fill up all /31 blocks
        for _ in range(2):
            c = carve_next_subnet('10.0.0.0/30', 31, pid)
            net = {'id': new_id(), 'name': 'n', 'cidr': str(c),
                   'description': '', 'vlan': '', 'project_id': pid}
            save_network(net)
            _r.sadd(project_nets_key(pid), net['id'])
        with pytest.raises(ValueError, match='No free'):
            carve_next_subnet('10.0.0.0/30', 31, pid)

    def test_multiple_prefix_sizes(self):
        pid = self._make_project()
        c1  = carve_next_subnet('10.0.0.0/16', 24, pid)
        net = {'id': new_id(), 'name': 'n1', 'cidr': str(c1),
               'description': '', 'vlan': '', 'project_id': pid}
        save_network(net)
        _r.sadd(project_nets_key(pid), net['id'])
        c2 = carve_next_subnet('10.0.0.0/16', 28, pid)
        assert ipaddress.ip_network(str(c2)).prefixlen == 28
        assert not ipaddress.ip_network(str(c1)).overlaps(ipaddress.ip_network(str(c2)))


# ══════════════════════════════════════════════════════════════════════════════
# pool_by_label_set
# ══════════════════════════════════════════════════════════════════════════════

class TestPoolByLabelSet:
    def _make_net(self, cidr, labels, used=0, pending=0):
        return {
            'id':            new_id(),
            'cidr':          cidr,
            'labels':        labels,
            'used_count':    used,
            'pending_slots': [{}] * pending,
        }

    def test_single_group(self):
        nets = [self._make_net('10.0.0.0/24', ['PROD'], 10)]
        pools = pool_by_label_set(nets)
        assert len(pools) == 1
        assert pools[0]['alloc_ips'] == 10
        assert pools[0]['total_ips'] == 256

    def test_two_distinct_groups(self):
        nets = [
            self._make_net('10.0.0.0/24', ['PROD']),
            self._make_net('10.0.1.0/24', ['DEV']),
        ]
        pools = pool_by_label_set(nets)
        assert len(pools) == 2

    def test_same_label_set_merged(self):
        nets = [
            self._make_net('10.0.0.0/24', ['PROD', 'London'], 5),
            self._make_net('10.0.1.0/24', ['PROD', 'London'], 3),
        ]
        pools = pool_by_label_set(nets)
        assert len(pools) == 1
        assert pools[0]['subnet_count'] == 2
        assert pools[0]['alloc_ips'] == 8

    def test_pending_counted(self):
        nets = [self._make_net('10.0.0.0/24', ['PROD'], pending=5)]
        pools = pool_by_label_set(nets)
        assert pools[0]['pending'] == 5

    def test_utilization_calculated(self):
        nets = [self._make_net('10.0.0.0/24', ['PROD'], used=128)]
        pools = pool_by_label_set(nets)
        assert pools[0]['utilization'] == 50.0

    def test_no_labels_group(self):
        nets = [self._make_net('10.0.0.0/24', [])]
        pools = pool_by_label_set(nets)
        assert pools[0]['label_set'] == []

    def test_sorted_by_total_ips_desc(self):
        nets = [
            self._make_net('10.0.0.0/28', ['A']),
            self._make_net('10.0.1.0/24', ['B']),
        ]
        pools = pool_by_label_set(nets)
        assert pools[0]['total_ips'] > pools[1]['total_ips']


# ══════════════════════════════════════════════════════════════════════════════
# resolve_template_rules
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveTemplateRules:
    def test_from_start_offset_1(self):
        rules   = [{'type': 'from_start', 'offset': 1, 'role': 'gateway', 'status': 'reserved'}]
        result  = resolve_template_rules('10.0.0.0/24', rules)
        assert len(result) == 1
        assert result[0]['ip'] == '10.0.0.1'
        assert result[0]['role'] == 'gateway'

    def test_from_end_count_2(self):
        rules  = [{'type': 'from_end', 'count': 2, 'role': 'reserved', 'status': 'reserved'}]
        result = resolve_template_rules('10.0.0.0/24', rules)
        assert len(result) == 2
        ips = [r['ip'] for r in result]
        assert '10.0.0.254' in ips
        assert '10.0.0.253' in ips

    def test_range(self):
        rules  = [{'type': 'range', 'from': 10, 'to': 12, 'role': 'dhcp-pool', 'status': 'dhcp'}]
        result = resolve_template_rules('10.0.0.0/24', rules)
        assert len(result) == 3
        assert result[0]['ip'] == '10.0.0.10'
        assert result[2]['ip'] == '10.0.0.12'
        assert all(r['status'] == 'dhcp' for r in result)

    def test_later_rule_wins_overlap(self):
        rules = [
            {'type': 'from_start', 'offset': 1, 'role': 'gateway',  'status': 'reserved'},
            {'type': 'from_start', 'offset': 1, 'role': 'overridden','status': 'allocated'},
        ]
        result = resolve_template_rules('10.0.0.0/24', rules)
        assert len(result) == 1
        assert result[0]['role'] == 'overridden'

    def test_empty_rules(self):
        assert resolve_template_rules('10.0.0.0/24', []) == []

    def test_slash_31(self):
        rules  = [{'type': 'from_start', 'offset': 1, 'role': 'r', 'status': 'reserved'}]
        result = resolve_template_rules('10.0.0.0/31', rules)
        assert len(result) == 1

    def test_slash_32_no_hosts(self):
        rules  = [{'type': 'from_start', 'offset': 1, 'role': 'r', 'status': 'reserved'}]
        result = resolve_template_rules('10.0.0.1/32', rules)
        assert result == []

    def test_combined_rules(self):
        rules = [
            {'type': 'from_start', 'offset': 1, 'role': 'gateway',  'status': 'reserved'},
            {'type': 'from_start', 'offset': 2, 'role': 'vrrp',     'status': 'reserved'},
            {'type': 'from_end',   'count':  3, 'role': 'reserved', 'status': 'reserved'},
        ]
        result = resolve_template_rules('10.0.0.0/24', rules)
        assert len(result) == 5


# ══════════════════════════════════════════════════════════════════════════════
# _validate_rules
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateRules:
    def test_valid_from_start(self):
        _validate_rules([{'type': 'from_start', 'offset': 1, 'role': 'r', 'status': 'reserved'}])

    def test_valid_from_end(self):
        _validate_rules([{'type': 'from_end', 'count': 2, 'role': 'r', 'status': 'reserved'}])

    def test_valid_range(self):
        _validate_rules([{'type': 'range', 'from': 1, 'to': 5, 'role': 'r', 'status': 'reserved'}])

    def test_invalid_type(self):
        with pytest.raises(ValueError, match='unknown type'):
            _validate_rules([{'type': 'invalid', 'role': 'r', 'status': 'reserved'}])

    def test_from_start_bad_offset(self):
        with pytest.raises(ValueError, match='offset'):
            _validate_rules([{'type': 'from_start', 'offset': 0, 'role': 'r', 'status': 'reserved'}])

    def test_from_end_bad_count(self):
        with pytest.raises(ValueError, match='count'):
            _validate_rules([{'type': 'from_end', 'count': 0, 'role': 'r', 'status': 'reserved'}])

    def test_range_bad_order(self):
        with pytest.raises(ValueError, match='range'):
            _validate_rules([{'type': 'range', 'from': 5, 'to': 3, 'role': 'r', 'status': 'reserved'}])

    def test_invalid_status(self):
        with pytest.raises(ValueError, match='status'):
            _validate_rules([{'type': 'from_start', 'offset': 1, 'role': 'r', 'status': 'bad'}])


# ══════════════════════════════════════════════════════════════════════════════
# Label helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelHelpers:
    def test_add_remove_global(self):
        add_global_label('PROD')
        from ipam import global_labels
        assert 'PROD' in global_labels()
        remove_global_label('PROD')
        assert 'PROD' not in global_labels()

    def test_label_scope_global(self):
        add_global_label('PROD')
        assert label_scope('PROD', None) == 'global'

    def test_label_scope_project(self):
        pid = new_id()
        add_project_label(pid, 'rack-A')
        assert label_scope('rack-A', pid) == 'project'

    def test_label_scope_unknown(self):
        assert label_scope('nobody-knows', 'some-pid') == 'unknown'

    def test_add_labels_to_network(self):
        nid = new_id()
        add_labels_to_network(nid, ['PROD', 'London'])
        labels = get_network_labels(nid)
        assert sorted(labels) == ['London', 'PROD']

    def test_remove_labels_from_network(self):
        nid = new_id()
        add_labels_to_network(nid, ['PROD', 'London'])
        remove_labels_from_network(nid, ['London'])
        assert get_network_labels(nid) == ['PROD']

    def test_available_labels_for_project(self):
        pid = new_id()
        add_global_label('GLOBAL')
        add_project_label(pid, 'LOCAL')
        av = available_labels_for_project(pid)
        assert 'GLOBAL' in av['global']
        assert 'LOCAL' in av['project']

    def test_project_label_does_not_appear_in_global(self):
        pid = new_id()
        add_project_label(pid, 'ONLY-HERE')
        from ipam import global_labels
        assert 'ONLY-HERE' not in global_labels()


# ══════════════════════════════════════════════════════════════════════════════
# Subnet template helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSubnetTemplateHelpers:
    def _make_tmpl(self, scope='global', pid=''):
        tmpl = {
            'id':          new_id(),
            'name':        'T1',
            'description': '',
            'rules': [
                {'type': 'from_start', 'offset': 1, 'role': 'gateway', 'status': 'reserved'},
            ],
            'scope':      scope,
            'project_id': pid,
        }
        save_template(tmpl)
        return tmpl

    def test_save_and_get(self):
        t = self._make_tmpl()
        assert get_template(t['id'])['name'] == 'T1'

    def test_global_templates_list(self):
        t = self._make_tmpl()
        assert any(x['id'] == t['id'] for x in global_templates())

    def test_project_templates_list(self):
        pid = new_id()
        t   = self._make_tmpl(scope='project', pid=pid)
        assert any(x['id'] == t['id'] for x in project_templates(pid))

    def test_global_not_in_project_list(self):
        pid = new_id()
        t   = self._make_tmpl()
        assert not any(x['id'] == t['id'] for x in project_templates(pid))

    def test_delete(self):
        t = self._make_tmpl()
        delete_template(t['id'])
        assert get_template(t['id']) is None

    def test_scope_global(self):
        t = self._make_tmpl()
        assert template_scope(t['id'], None) == 'global'

    def test_scope_project(self):
        pid = new_id()
        t   = self._make_tmpl(scope='project', pid=pid)
        assert template_scope(t['id'], pid) == 'project'

    def test_available_templates_contains_both(self):
        pid = new_id()
        g   = self._make_tmpl()
        p   = self._make_tmpl(scope='project', pid=pid)
        av  = available_templates_for_project(pid)
        assert any(x['id'] == g['id'] for x in av['global'])
        assert any(x['id'] == p['id'] for x in av['project'])


# ══════════════════════════════════════════════════════════════════════════════
# Pending slots
# ══════════════════════════════════════════════════════════════════════════════

class TestPendingSlots:
    def _setup(self):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        nid = new_id()
        net = {'id': nid, 'name': 'n', 'cidr': '10.0.0.0/24',
               'description': '', 'vlan': '', 'project_id': pid}
        save_network(net)
        tmpl = {
            'id': new_id(), 'name': 'T', 'description': '',
            'rules': [
                {'type': 'from_start', 'offset': 1, 'role': 'gateway',  'status': 'reserved'},
                {'type': 'from_start', 'offset': 2, 'role': 'vrrp',     'status': 'reserved'},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_template(tmpl)
        return nid, tmpl['id']

    def test_set_pending_creates_slots(self):
        nid, tid = self._setup()
        slots = set_pending_slots(nid, tid)
        assert len(slots) == 2
        net = get_network(nid)
        assert len(net['pending_slots']) == 2

    def test_confirm_slot_creates_ip(self):
        nid, tid = self._setup()
        set_pending_slots(nid, tid)
        net = get_network(nid)
        ip  = net['pending_slots'][0]['ip']
        assert confirm_slot(nid, ip) is True
        assert get_ip(ip) is not None
        net2 = get_network(nid)
        assert all(s['ip'] != ip for s in net2['pending_slots'])

    def test_confirm_all(self):
        nid, tid = self._setup()
        set_pending_slots(nid, tid)
        result = confirm_all_slots(nid)
        assert result['created'] == 2
        assert result['skipped'] == 0
        assert get_network(nid)['pending_slots'] == []

    def test_dismiss_slot(self):
        nid, tid = self._setup()
        set_pending_slots(nid, tid)
        net = get_network(nid)
        ip  = net['pending_slots'][0]['ip']
        assert dismiss_slot(nid, ip) is True
        assert len(get_network(nid)['pending_slots']) == 1
        assert get_ip(ip) is None

    def test_dismiss_all(self):
        nid, tid = self._setup()
        set_pending_slots(nid, tid)
        dismiss_all_slots(nid)
        assert get_network(nid)['pending_slots'] == []

    def test_already_allocated_slot_skipped(self):
        nid, tid = self._setup()
        slots = set_pending_slots(nid, tid)
        ip = slots[0]['ip']
        # Manually allocate that IP first
        save_ip({'ip': ip, 'hostname': '', 'description': '', 'status': 'allocated', 'network_id': nid})
        result = confirm_all_slots(nid)
        assert result['skipped'] == 1
        assert result['created'] == 1


# ══════════════════════════════════════════════════════════════════════════════
# project_pool_summary
# ══════════════════════════════════════════════════════════════════════════════

class TestProjectPoolSummary:
    def _setup(self):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/16', 'description': ''})
        return pid

    def test_empty_project(self):
        pid     = self._setup()
        summary = project_pool_summary(pid)
        assert summary['total_ips']    == 65536
        assert summary['allocated_ips'] == 0
        assert summary['subnet_count']  == 0

    def test_with_subnets(self):
        pid = self._setup()
        for cidr in ('10.0.0.0/24', '10.0.1.0/24'):
            nid = new_id()
            net = {'id': nid, 'name': 'n', 'cidr': cidr,
                   'description': '', 'vlan': '', 'project_id': pid}
            save_network(net)
            _r.sadd(project_nets_key(pid), nid)
        summary = project_pool_summary(pid)
        assert summary['subnet_count'] == 2

    def test_pending_counted(self):
        pid = self._setup()
        nid = new_id()
        net = {'id': nid, 'name': 'n', 'cidr': '10.0.0.0/24',
               'description': '', 'vlan': '', 'project_id': pid,
               'pending_slots': [{'ip': '10.0.0.1', 'role': 'r', 'status': 'reserved'}]}
        save_network(net)
        _r.sadd(project_nets_key(pid), nid)
        assert project_pool_summary(pid)['pending'] == 1

    def test_nonexistent_project(self):
        assert project_pool_summary('no-such-id') == {}
