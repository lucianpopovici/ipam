"""Unit tests for cross-project lint rules."""
import pytest
from lint import (
    find_cidr_overlaps, find_reserved_overlaps,
    save_reserved, get_reserved, delete_reserved_record,
    all_reserved, finding_id,
    get_ack, save_ack, delete_ack,
    get_findings, invalidate_cache,
    project_finding_counts,
)
from ipam import new_id, save_network, get_network
import ipam as _ipam_mod


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _net(cidr, pid=None, vrf_id=None):
    pid = pid or new_id()
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr,
           'description': '', 'vlan': '', 'project_id': pid,
           'template_id': None, 'pending_slots': []}
    if vrf_id:
        net['vrf_id'] = vrf_id
    save_network(net)
    # Register in global networks:index (save_network does this)
    return pid, net


def _reserved(cidr, severity='warning', name='test'):
    res = {'id': new_id(), 'cidr': cidr, 'name': name,
           'severity': severity, 'description': ''}
    save_reserved(res)
    return res


# ── finding_id ───────────────────────────────────────────────────────────────────

class TestFindingId:
    def test_deterministic(self):
        a = finding_id('rule', 'vrf', 'cidr1', 'cidr2')
        b = finding_id('rule', 'vrf', 'cidr1', 'cidr2')
        assert a == b

    def test_different_inputs_differ(self):
        a = finding_id('rule', 'cidr1')
        b = finding_id('rule', 'cidr2')
        assert a != b

    def test_length_16(self):
        assert len(finding_id('x', 'y')) == 16


# ── Rule 1 + 2: CIDR overlaps ─────────────────────────────────────────────────

class TestCidrOverlapDetection:
    def test_no_findings_empty(self, fake_redis):
        assert find_cidr_overlaps() == []

    def test_no_overlap_disjoint_cidrs(self, fake_redis):
        pid_a, _ = _net('10.0.1.0/24')
        pid_b, _ = _net('10.0.2.0/24')
        assert find_cidr_overlaps() == []

    def test_overlap_different_projects(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/16')
        pid_b, _ = _net('10.0.1.0/24')
        findings = find_cidr_overlaps()
        assert len(findings) == 1
        assert findings[0]['rule'] == 'cross_project_overlap'
        assert findings[0]['severity'] == 'error'

    def test_same_cidr_collision(self, fake_redis):
        pid_a, _ = _net('10.0.1.0/24')
        pid_b, _ = _net('10.0.1.0/24')
        findings = find_cidr_overlaps()
        assert len(findings) == 1
        assert findings[0]['rule'] == 'same_cidr_collision'

    def test_within_project_not_flagged(self, fake_redis):
        # Within-project overlaps are the existing validator's job
        pid = new_id()
        _net('10.0.0.0/16', pid=pid)
        _net('10.0.1.0/24', pid=pid)
        assert find_cidr_overlaps() == []

    def test_different_vrf_no_flag(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/24', vrf_id='vrf-a')
        pid_b, _ = _net('10.0.0.0/24', vrf_id='vrf-b')
        assert find_cidr_overlaps() == []

    def test_same_vrf_flagged(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/24', vrf_id='vrf-x')
        pid_b, _ = _net('10.0.0.0/24', vrf_id='vrf-x')
        findings = find_cidr_overlaps()
        assert len(findings) == 1
        assert findings[0]['vrf'] == 'vrf-x'

    def test_finding_has_both_subnets(self, fake_redis):
        pid_a, net_a = _net('10.0.0.0/16')
        pid_b, net_b = _net('10.0.1.0/24')
        f = find_cidr_overlaps()[0]
        cidrs = {f['subnet_a']['cidr'], f['subnet_b']['cidr']}
        assert cidrs == {'10.0.0.0/16', '10.0.1.0/24'}

    def test_finding_project_ids_correct(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/16')
        pid_b, _ = _net('10.0.1.0/24')
        f = find_cidr_overlaps()[0]
        pids = {f['subnet_a']['project_id'], f['subnet_b']['project_id']}
        assert pids == {pid_a, pid_b}

    def test_multiple_overlapping_pairs(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/8')
        pid_b, _ = _net('10.1.0.0/16')
        pid_c, _ = _net('10.2.0.0/16')
        findings = find_cidr_overlaps()
        assert len(findings) >= 2

    def test_default_vrf_used_when_none(self, fake_redis):
        pid_a, _ = _net('192.168.1.0/24')
        pid_b, _ = _net('192.168.1.0/24')
        findings = find_cidr_overlaps()
        assert findings[0]['vrf'] == '_def'


# ── Rule 5: Reserved ranges ───────────────────────────────────────────────────

class TestReservedRanges:
    def test_no_findings_no_reservations(self, fake_redis):
        _net('10.0.1.0/24')
        assert find_reserved_overlaps() == []

    def test_subnet_in_reserved_flagged(self, fake_redis):
        _net('100.64.1.0/24')
        _reserved('100.64.0.0/10', severity='error', name='CGN')
        findings = find_reserved_overlaps()
        assert len(findings) == 1
        assert findings[0]['rule'] == 'reserved_range_overlap'
        assert findings[0]['severity'] == 'error'

    def test_subnet_outside_reserved_not_flagged(self, fake_redis):
        _net('10.0.1.0/24')
        _reserved('192.168.0.0/16', name='home')
        assert find_reserved_overlaps() == []

    def test_finding_includes_reserved_info(self, fake_redis):
        _net('192.0.2.1/32')
        res = _reserved('192.0.2.0/24', name='TEST-NET-1')
        findings = find_reserved_overlaps()
        assert findings[0]['reserved']['cidr'] == '192.0.2.0/24'
        assert findings[0]['reserved']['name'] == 'TEST-NET-1'

    def test_save_and_retrieve_reserved(self, fake_redis):
        res = _reserved('10.99.0.0/16', name='My range')
        assert get_reserved(res['id'])['cidr'] == '10.99.0.0/16'

    def test_delete_reserved(self, fake_redis):
        res = _reserved('10.99.0.0/16', name='r')
        delete_reserved_record(res['id'])
        assert get_reserved(res['id']) is None

    def test_all_reserved_sorted(self, fake_redis):
        _reserved('10.0.0.0/8', name='A')
        _reserved('192.168.0.0/16', name='B')
        _reserved('100.64.0.0/10', name='C')
        cidrs = [r['cidr'] for r in all_reserved()]
        assert cidrs == sorted(cidrs)


# ── Cache ─────────────────────────────────────────────────────────────────────

class TestFindingsCache:
    def test_get_findings_empty(self, fake_redis):
        assert get_findings() == []

    def test_cache_populated(self, fake_redis):
        import lint as _lint_mod
        get_findings()
        assert _lint_mod.r.get('lint:findings:cache') is not None

    def test_invalidate_clears_cache(self, fake_redis):
        import lint as _lint_mod
        get_findings()
        invalidate_cache()
        assert _lint_mod.r.get('lint:findings:cache') is None

    def test_force_recomputes(self, fake_redis):
        get_findings()
        pid_a, _ = _net('10.0.0.0/16')
        pid_b, _ = _net('10.0.1.0/24')
        # Without force, still gets cached empty result
        cached = get_findings(force=False)
        fresh  = get_findings(force=True)
        assert len(fresh) == 1


# ── Acknowledgements ───────────────────────────────────────────────────────────

class TestAcknowledgements:
    def test_ack_persists(self, fake_redis):
        fid = finding_id('test', 'abc')
        save_ack(fid, 'deliberate')
        ack = get_ack(fid)
        assert ack is not None
        assert ack['reason'] == 'deliberate'
        assert ack['finding_id'] == fid

    def test_unack_removes(self, fake_redis):
        fid = finding_id('test', 'xyz')
        save_ack(fid, 'reason')
        delete_ack(fid)
        assert get_ack(fid) is None

    def test_ack_has_timestamp(self, fake_redis):
        fid = finding_id('test', 'ts')
        save_ack(fid)
        assert 'acked_at' in get_ack(fid)


# ── project_finding_counts ────────────────────────────────────────────────────

class TestProjectFindingCounts:
    def test_empty_findings(self, fake_redis):
        assert project_finding_counts([]) == {}

    def test_counts_errors(self, fake_redis):
        pid_a, net_a = _net('10.0.0.0/16')
        pid_b, net_b = _net('10.0.1.0/24')
        findings = find_cidr_overlaps()
        counts = project_finding_counts(findings)
        assert counts[pid_a]['errors'] == 1
        assert counts[pid_b]['errors'] == 1

    def test_counts_total(self, fake_redis):
        pid_a, _ = _net('10.0.0.0/16')
        pid_b, _ = _net('10.0.1.0/24')
        counts = project_finding_counts(find_cidr_overlaps())
        assert counts[pid_a]['total'] == 1
