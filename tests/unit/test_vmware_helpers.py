"""
pytestmark = pytest.mark.unit

Unit tests for vmware.py helper functions.
All Redis I/O is intercepted by the fake_redis fixture in conftest.py.
"""
import pytest
import ipaddress
import ipam as _ipam
from ipam import save_project, save_network, new_id, project_nets_key
from vmware import (
    enable_network, disable_network, is_enabled,
    enabled_network_ids, enabled_networks,
    save_vmware_alloc, get_vmware_alloc, delete_vmware_alloc,
    network_vmware_ips,
    _find_next_available,
    allocate_ip, release_ip,
    VMWARE_SUBNETS_KEY, _alloc_key, _net_ips_key,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

def _make_project(supernet='10.0.0.0/16'):
    pid = new_id()
    save_project({'id': pid, 'name': 'Test', 'supernet': supernet, 'description': ''})
    return pid


def _make_network(pid, cidr='10.0.1.0/24'):
    nid = new_id()
    net = {'id': nid, 'name': cidr, 'cidr': cidr, 'description': '',
           'vlan': '', 'project_id': pid, 'pending_slots': []}
    save_network(net)
    _ipam.r.sadd(project_nets_key(pid), nid)
    return nid


# ══════════════════════════════════════════════════════════════════════════════
# Network enable / disable
# ══════════════════════════════════════════════════════════════════════════════

class TestEnableDisable:
    def test_enable_adds_to_set(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        import vmware as _vmware
        assert _vmware.r.sismember(VMWARE_SUBNETS_KEY, nid)

    def test_is_enabled_true_after_enable(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        assert is_enabled(nid) is True

    def test_is_enabled_false_before_enable(self):
        pid = _make_project()
        nid = _make_network(pid)
        assert is_enabled(nid) is False

    def test_disable_removes_from_set(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        disable_network(nid)
        assert is_enabled(nid) is False

    def test_enabled_network_ids_returns_all(self):
        pid  = _make_project()
        nid1 = _make_network(pid, '10.0.1.0/24')
        nid2 = _make_network(pid, '10.0.2.0/24')
        enable_network(nid1)
        enable_network(nid2)
        ids = enabled_network_ids()
        assert nid1 in ids and nid2 in ids

    def test_enabled_networks_returns_sorted_list(self):
        pid  = _make_project()
        nid1 = _make_network(pid, '10.0.2.0/24')
        nid2 = _make_network(pid, '10.0.1.0/24')
        enable_network(nid1)
        enable_network(nid2)
        nets = enabled_networks()
        cidrs = [n['cidr'] for n in nets]
        assert cidrs == sorted(cidrs, key=lambda c: ipaddress.ip_network(c))


# ══════════════════════════════════════════════════════════════════════════════
# Allocation metadata CRUD
# ══════════════════════════════════════════════════════════════════════════════

class TestAllocCRUD:
    def _alloc(self, ip='10.0.1.5', net_id='net1'):
        return {
            'ip': ip, 'network_id': net_id, 'cidr': '10.0.1.0/24',
            'vm_name': 'vm-01', 'datacenter': 'DC1', 'cluster': 'C1',
            'allocated_at': '2026-01-01T00:00:00+00:00',
        }

    def test_save_and_get(self):
        a = self._alloc()
        save_vmware_alloc(a)
        assert get_vmware_alloc('10.0.1.5') == a

    def test_get_missing_returns_none(self):
        assert get_vmware_alloc('1.2.3.4') is None

    def test_save_adds_to_net_ips_set(self):
        a = self._alloc()
        save_vmware_alloc(a)
        import vmware as _vmware
        assert _vmware.r.sismember(_net_ips_key('net1'), '10.0.1.5')

    def test_delete_removes_key_and_set_member(self):
        a = self._alloc()
        save_vmware_alloc(a)
        delete_vmware_alloc('10.0.1.5', 'net1')
        assert get_vmware_alloc('10.0.1.5') is None
        import vmware as _vmware
        assert not _vmware.r.sismember(_net_ips_key('net1'), '10.0.1.5')

    def test_network_vmware_ips_sorted(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        for ip in ['10.0.1.10', '10.0.1.2', '10.0.1.5']:
            save_vmware_alloc({
                'ip': ip, 'network_id': nid, 'cidr': '10.0.1.0/24',
                'vm_name': '', 'datacenter': '', 'cluster': '', 'allocated_at': '',
            })
        ips = [a['ip'] for a in network_vmware_ips(nid)]
        assert ips == sorted(ips, key=lambda i: ipaddress.ip_address(i))


# ══════════════════════════════════════════════════════════════════════════════
# _find_next_available
# ══════════════════════════════════════════════════════════════════════════════

class TestFindNextAvailable:
    def test_returns_first_host(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        assert _find_next_available(nid) == '10.0.1.1'

    def test_skips_allocated_ips(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        from ipam import save_ip, net_ips_key
        save_ip({'ip': '10.0.1.1', 'hostname': '', 'description': '',
                 'status': 'allocated', 'network_id': nid})
        assert _find_next_available(nid) == '10.0.1.2'

    def test_skips_pending_slots(self):
        pid = _make_project()
        nid = new_id()
        net = {'id': nid, 'name': 'n', 'cidr': '10.0.1.0/24', 'description': '',
               'vlan': '', 'project_id': pid,
               'pending_slots': [{'ip': '10.0.1.1', 'role': 'gw', 'status': 'reserved'}]}
        save_network(net)
        assert _find_next_available(nid) == '10.0.1.2'

    def test_returns_none_when_exhausted(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/30')
        # /30 has 2 usable hosts: .1 and .2
        from ipam import save_ip
        for ip in ['10.0.1.1', '10.0.1.2']:
            save_ip({'ip': ip, 'hostname': '', 'description': '',
                     'status': 'allocated', 'network_id': nid})
        assert _find_next_available(nid) is None

    def test_returns_none_for_missing_network(self):
        assert _find_next_available('nonexistent') is None


# ══════════════════════════════════════════════════════════════════════════════
# allocate_ip
# ══════════════════════════════════════════════════════════════════════════════

class TestAllocateIP:
    def test_allocates_first_available(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = allocate_ip(nid, vm_name='vm-01', datacenter='DC1', cluster='C1')
        assert alloc['ip'] == '10.0.1.1'
        assert alloc['vm_name'] == 'vm-01'
        assert alloc['network_id'] == nid

    def test_allocate_registers_in_ipam(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = allocate_ip(nid)
        from ipam import get_ip
        ip_rec = get_ip(alloc['ip'])
        assert ip_rec is not None
        assert ip_rec['status'] == 'allocated'

    def test_allocate_stores_vmware_metadata(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        alloc = allocate_ip(nid, vm_name='my-vm')
        assert get_vmware_alloc(alloc['ip']) is not None

    def test_allocate_advances_pointer(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        enable_network(nid)
        a1 = allocate_ip(nid, vm_name='vm-01')
        a2 = allocate_ip(nid, vm_name='vm-02')
        assert a1['ip'] != a2['ip']
        assert ipaddress.ip_address(a1['ip']) < ipaddress.ip_address(a2['ip'])

    def test_raises_when_not_enabled(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/24')
        with pytest.raises(ValueError, match='not enabled'):
            allocate_ip(nid)

    def test_raises_when_network_missing(self):
        enable_network('ghost')
        with pytest.raises(ValueError, match='not found'):
            allocate_ip('ghost')

    def test_raises_when_exhausted(self):
        pid = _make_project()
        nid = _make_network(pid, '10.0.1.0/30')
        enable_network(nid)
        allocate_ip(nid, vm_name='vm-01')
        allocate_ip(nid, vm_name='vm-02')
        with pytest.raises(ValueError, match='No available'):
            allocate_ip(nid, vm_name='vm-03')

    def test_allocated_at_present(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        alloc = allocate_ip(nid)
        assert 'allocated_at' in alloc and alloc['allocated_at']

    def test_hostname_set_from_vm_name(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        alloc = allocate_ip(nid, vm_name='my-vm')
        from ipam import get_ip
        assert get_ip(alloc['ip'])['hostname'] == 'my-vm'


# ══════════════════════════════════════════════════════════════════════════════
# release_ip
# ══════════════════════════════════════════════════════════════════════════════

class TestReleaseIP:
    def test_release_removes_from_ipam(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        alloc = allocate_ip(nid, vm_name='vm-01')
        ip = alloc['ip']
        assert release_ip(ip) is True
        from ipam import get_ip
        assert get_ip(ip) is None

    def test_release_removes_vmware_metadata(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        alloc = allocate_ip(nid)
        ip = alloc['ip']
        release_ip(ip)
        assert get_vmware_alloc(ip) is None

    def test_release_returns_false_for_nonexistent(self):
        assert release_ip('1.2.3.4') is False

    def test_released_ip_can_be_reallocated(self):
        pid = _make_project()
        nid = _make_network(pid)
        enable_network(nid)
        alloc1 = allocate_ip(nid, vm_name='vm-01')
        release_ip(alloc1['ip'])
        alloc2 = allocate_ip(nid, vm_name='vm-02')
        assert alloc2['ip'] == alloc1['ip']
