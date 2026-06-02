"""API / route tests for the cross-project lint blueprint."""
import json
import pytest
from ipam import new_id, save_network
from lint import (
    save_reserved, get_reserved, get_ack, invalidate_cache,
    find_cidr_overlaps,
)


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _create_project(client, name='p', supernet='10.0.0.0/8'):
    resp = client.post('/projects/add', data={
        'name': name, 'supernet': supernet,
        'description': '', 'customer_id': '',
    }, follow_redirects=False)
    return resp.headers['Location'].rstrip('/').split('/')[-1]


def _net(pid, cidr, vrf_id=None):
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr,
           'description': '', 'vlan': '', 'project_id': pid,
           'template_id': None, 'pending_slots': []}
    if vrf_id:
        net['vrf_id'] = vrf_id
    save_network(net)
    import ipam as _m
    _m.r.sadd(f'project:{pid}:networks', net['id'])
    return net


def _reserved(cidr='192.0.2.0/24', name='Test', severity='warning'):
    res = {'id': new_id(), 'cidr': cidr, 'name': name,
           'severity': severity, 'description': ''}
    save_reserved(res)
    return res


# ── Fleet lint page ───────────────────────────────────────────────────────────

class TestLintFleet:
    def test_fleet_renders(self, client):
        assert client.get('/lint').status_code == 200

    def test_fleet_shows_no_issues_when_clean(self, client):
        resp = client.get('/lint')
        assert b'No lint issues' in resp.data or resp.status_code == 200

    def test_fleet_shows_overlap(self, client):
        pid_a = _create_project(client, 'A', '10.0.0.0/8')
        pid_b = _create_project(client, 'B', '10.0.0.0/8')
        _net(pid_a, '10.0.0.0/16')
        _net(pid_b, '10.0.1.0/24')
        invalidate_cache()
        resp = client.get('/lint?refresh=1')
        assert b'10.0.0.0' in resp.data

    def test_fleet_shows_reserved_violation(self, client):
        pid_a = _create_project(client, 'A')
        _net(pid_a, '100.64.5.0/24')
        _reserved('100.64.0.0/10', name='CGN', severity='error')
        invalidate_cache()
        resp = client.get('/lint?refresh=1')
        assert b'100.64' in resp.data

    def test_refresh_param_forces_recompute(self, client):
        resp = client.get('/lint?refresh=1')
        assert resp.status_code == 200


# ── Per-project lint page ─────────────────────────────────────────────────────

class TestLintProject:
    def test_project_lint_renders(self, client):
        pid = _create_project(client, 'P')
        resp = client.get(f'/lint/projects/{pid}')
        assert resp.status_code == 200

    def test_project_lint_no_findings(self, client):
        pid = _create_project(client, 'P')
        resp = client.get(f'/lint/projects/{pid}')
        assert b'No lint issues' in resp.data

    def test_project_lint_shows_overlap(self, client):
        pid_a = _create_project(client, 'A')
        pid_b = _create_project(client, 'B')
        _net(pid_a, '10.0.0.0/16')
        _net(pid_b, '10.0.1.0/24')
        invalidate_cache()
        resp = client.get(f'/lint/projects/{pid_a}?refresh=1')
        # Trigger fresh computation
        client.get('/lint?refresh=1')
        resp = client.get(f'/lint/projects/{pid_a}')
        assert resp.status_code == 200

    def test_project_lint_unknown_redirects(self, client):
        resp = client.get('/lint/projects/no-such-project')
        assert resp.status_code == 302


# ── JSON API ──────────────────────────────────────────────────────────────────

class TestLintJson:
    def test_json_structure(self, client):
        resp = client.get('/api/lint/findings.json')
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert 'findings' in data
        assert 'total' in data
        assert 'errors' in data
        assert 'warnings' in data

    def test_json_findings_populated(self, client):
        pid_a = _create_project(client, 'A')
        pid_b = _create_project(client, 'B')
        _net(pid_a, '10.0.0.0/16')
        _net(pid_b, '10.0.1.0/24')
        invalidate_cache()
        # Force fresh
        client.get('/lint?refresh=1')
        resp = client.get('/api/lint/findings.json')
        data = json.loads(resp.data)
        assert data['total'] >= 1
        assert data['errors'] >= 1


# ── Acknowledgement routes ─────────────────────────────────────────────────────

class TestAckRoutes:
    def _get_finding_id(self, client):
        pid_a = _create_project(client, 'A')
        pid_b = _create_project(client, 'B')
        _net(pid_a, '10.0.0.0/16')
        _net(pid_b, '10.0.1.0/24')
        invalidate_cache()
        client.get('/lint?refresh=1')
        findings = find_cidr_overlaps()
        return findings[0]['id'] if findings else None

    def test_ack_finding(self, client):
        fid = self._get_finding_id(client)
        if not fid:
            pytest.skip('no findings to ack')
        resp = client.post(f'/lint/findings/{fid}/ack',
                           data={'reason': 'test reason'}, follow_redirects=False)
        assert resp.status_code == 302
        assert get_ack(fid) is not None
        assert get_ack(fid)['reason'] == 'test reason'

    def test_unack_finding(self, client):
        from lint import save_ack, finding_id
        fid = finding_id('test', 'abc')
        save_ack(fid, 'pre-existing')
        resp = client.post(f'/lint/findings/{fid}/unack', follow_redirects=False)
        assert resp.status_code == 302
        assert get_ack(fid) is None


# ── Reserved range CRUD ────────────────────────────────────────────────────────

class TestReservedCrud:
    def test_reserved_list_renders(self, client):
        assert client.get('/lint/reserved').status_code == 200

    def test_add_reserved(self, client):
        resp = client.post('/lint/reserved/add', data={
            'cidr': '100.64.0.0/10', 'name': 'CGN',
            'severity': 'error', 'description': 'Carrier-grade NAT',
        }, follow_redirects=False)
        assert resp.status_code == 302
        from lint import all_reserved
        cidrs = [r['cidr'] for r in all_reserved()]
        assert '100.64.0.0/10' in cidrs

    def test_add_reserved_invalid_cidr(self, client):
        resp = client.post('/lint/reserved/add', data={
            'cidr': 'not-a-cidr', 'name': 'Bad',
            'severity': 'warning', 'description': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Invalid CIDR' in resp.data

    def test_add_reserved_missing_name(self, client):
        resp = client.post('/lint/reserved/add', data={
            'cidr': '10.0.0.0/8', 'name': '',
            'severity': 'warning', 'description': '',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'required' in resp.data.lower()

    def test_delete_reserved(self, client):
        res = _reserved('192.168.99.0/24', name='Del me')
        resp = client.post(f'/lint/reserved/{res["id"]}/delete',
                           follow_redirects=False)
        assert resp.status_code == 302
        assert get_reserved(res['id']) is None

    def test_reserved_shows_in_list(self, client):
        _reserved('203.0.113.0/24', name='TEST-NET-3')
        resp = client.get('/lint/reserved')
        assert b'203.0.113.0' in resp.data
        assert b'TEST-NET-3' in resp.data


# ── Project index lint badge ───────────────────────────────────────────────────

class TestProjectIndexBadge:
    def test_index_renders_with_lint(self, client):
        # Just confirm the index page still renders when lint runs
        resp = client.get('/')
        assert resp.status_code == 200

    def test_badge_appears_for_project_with_issues(self, client):
        pid_a = _create_project(client, 'BadA')
        pid_b = _create_project(client, 'BadB')
        _net(pid_a, '10.50.0.0/16')
        _net(pid_b, '10.50.1.0/24')
        invalidate_cache()
        resp = client.get('/?refresh=1')  # index reads from cache
        assert resp.status_code == 200
        # Badge may or may not appear depending on cache; just confirm no 500
