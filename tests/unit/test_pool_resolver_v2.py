"""Unit tests for resolve_pool (VRF-aware pool resolver)."""
import pytest
import fakeredis
import ipaddress as _ip
import ipam as ipam_mod
from ipam import (
    resolve_pool, save_network, save_project, new_id,
    project_nets_key, add_labels_to_network,
)
import db as db_mod


@pytest.fixture(autouse=True)
def fake_redis_unit(monkeypatch):
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(db_mod, 'r', fake_r)
    monkeypatch.setattr(ipam_mod, 'r', fake_r)
    return fake_r


def _make_project(pid='p1'):
    proj = {'id': pid, 'name': 'Test', 'customer_id': 'c1'}
    save_project(proj)
    return proj


def _make_net(pid, cidr, vrf_id=None, site_id=None, pod_id=None, labels=None):
    nid = new_id()
    net = {
        'id':         nid,
        'name':       cidr,
        'cidr':       cidr,
        'family':     _ip.ip_network(cidr, strict=False).version,
        'vrf_id':     vrf_id,
        'site_id':    site_id,
        'pod_id':     pod_id,
        'project_id': pid,
    }
    save_network(net)
    import ipam as _ipam
    _ipam.r.sadd(project_nets_key(pid), nid)
    if labels:
        add_labels_to_network(nid, labels)
    return net


@pytest.mark.unit
class TestResolvePool:
    def test_exact_vrf_match(self):
        pid = 'p1'
        _make_project(pid)
        n1 = _make_net(pid, '10.0.1.0/24', vrf_id='vrf-mgmt')
        n2 = _make_net(pid, '10.0.2.0/24', vrf_id='vrf-internet')

        results = resolve_pool(4, 'vrf-mgmt', set(), pid)
        ids = {r['id'] for r in results}
        assert n1['id'] in ids
        assert n2['id'] not in ids

    def test_null_vrf_is_exact_match(self):
        pid = 'p2'
        _make_project(pid)
        n1 = _make_net(pid, '192.168.0.0/24', vrf_id=None)
        n2 = _make_net(pid, '192.168.1.0/24', vrf_id='vrf-x')

        results = resolve_pool(4, None, set(), pid)
        ids = {r['id'] for r in results}
        assert n1['id'] in ids
        assert n2['id'] not in ids

    def test_label_subset_required(self):
        pid = 'p3'
        _make_project(pid)
        n1 = _make_net(pid, '10.1.1.0/24', labels=['env:prod', 'role:mgmt'])
        n2 = _make_net(pid, '10.1.2.0/24', labels=['env:staging'])

        results = resolve_pool(4, None, {'env:prod'}, pid)
        ids = {r['id'] for r in results}
        assert n1['id'] in ids
        assert n2['id'] not in ids

    def test_family_filter(self):
        pid = 'p4'
        _make_project(pid)
        n4 = _make_net(pid, '10.2.0.0/24')
        n6 = _make_net(pid, '2001:db8::/32')

        v4_results = resolve_pool(4, None, set(), pid)
        v6_results = resolve_pool(6, None, set(), pid)

        assert any(r['id'] == n4['id'] for r in v4_results)
        assert not any(r['id'] == n4['id'] for r in v6_results)
        assert any(r['id'] == n6['id'] for r in v6_results)

    def test_site_filter_allows_none(self):
        """Subnet with site_id=None matches any site filter (more general)."""
        pid = 'p5'
        _make_project(pid)
        n_general  = _make_net(pid, '10.3.0.0/24', site_id=None)
        n_specific = _make_net(pid, '10.3.1.0/24', site_id='site-lon')
        n_other    = _make_net(pid, '10.3.2.0/24', site_id='site-par')

        results = resolve_pool(4, None, set(), pid, site_id='site-lon')
        ids = {r['id'] for r in results}
        assert n_general['id'] in ids
        assert n_specific['id'] in ids
        assert n_other['id'] not in ids
