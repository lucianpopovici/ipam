"""
pytestmark = pytest.mark.unit

Unit tests for ipam.py helper functions.
All Redis I/O is intercepted by the fake_redis fixture in conftest.py.
"""
from ipam import (
    claim_ip_atomic,
    find_next_free_ip,
    get_ip,
    save_network,
)

def test_claim_ip_atomic_success(fake_redis):
    """Test successful atomic IP claim and bitmap update."""
    addr = {
        'ip': '10.0.0.1',
        'hostname': 'host1',
        'description': 'test',
        'status': 'allocated',
        'network_id': 'net123'
    }
    success = claim_ip_atomic(addr, '10.0.0.0/24')
    assert success is True
    stored = get_ip('10.0.0.1')
    assert stored['hostname'] == 'host1'
    assert fake_redis.sismember('network:net123:ips', '10.0.0.1')
    # 10.0.0.1 is offset 1 in 10.0.0.0/24
    assert fake_redis.getbit('network:net123:bitmap', 1) == 1

def test_claim_ip_atomic_fail_already_exists(fake_redis):
    """Test that claiming an already allocated IP fails."""
    addr1 = {'ip': '10.0.0.1', 'hostname': 'h1', 'network_id': 'n1'}
    addr2 = {'ip': '10.0.0.1', 'hostname': 'h2', 'network_id': 'n1'}
    assert claim_ip_atomic(addr1, '10.0.0.0/24') is True
    assert claim_ip_atomic(addr2, '10.0.0.0/24') is False
    assert get_ip('10.0.0.1')['hostname'] == 'h1'

def test_find_next_free_ip_with_bitmap(fake_redis):
    """Test that find_next_free_ip correctly identifies available IPs using bitmaps."""
    net = {'id': 'net1', 'cidr': '192.168.1.0/24'}
    save_network(net)

    # Manually mark .1 and .2 as used
    claim_ip_atomic({'ip': '192.168.1.1', 'network_id': 'net1'}, '192.168.1.0/24')
    claim_ip_atomic({'ip': '192.168.1.2', 'network_id': 'net1'}, '192.168.1.0/24')

    # 192.168.1.3 should be the next one (ignoring 0 which is network addr)
    # Actually, sync_net_bitmap marks 0 and 255 as used for /24.
    from ipam import sync_net_bitmap
    sync_net_bitmap('net1')

    next_ip = find_next_free_ip('net1')
    assert next_ip == '192.168.1.3'
