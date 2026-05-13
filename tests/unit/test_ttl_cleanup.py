"""
Unit tests for TTL lazy cleanup.
"""
import pytest
import time
from ipam import claim_ip_atomic, network_addresses, get_ip, find_next_free_ip, save_network, net_bitmap_key, net_ips_key

@pytest.mark.unit
def test_lazy_cleanup_expired_ttl(fake_redis):
    """Verify that expired TTL reservations are cleaned up lazily."""
    net_id = 'net_ttl'
    cidr = '10.0.0.0/24'
    save_network({'id': net_id, 'cidr': cidr})
    
    # 1. Claim an IP with a very short TTL (1 second)
    addr = {'ip': '10.0.0.1', 'network_id': net_id}
    assert claim_ip_atomic(addr, cidr, ttl=1) is True
    
    # 2. Verify it's there
    assert get_ip('10.0.0.1') is not None
    assert fake_redis.getbit(net_bitmap_key(net_id), 1) == 1
    
    # 3. Wait for expiration
    time.sleep(1.1)
    
    # 4. Trigger lazy cleanup via network_addresses
    # network_addresses should see that the key is gone and clean up the set and bitmap
    addrs = network_addresses(net_id)
    assert len(addrs) == 0
    
    # 5. Verify bitmap and set are clean
    assert fake_redis.getbit(net_bitmap_key(net_id), 1) == 0
    assert not fake_redis.sismember(net_ips_key(net_id), '10.0.0.1')

@pytest.mark.unit
def test_find_next_free_ip_triggers_cleanup(fake_redis):
    """Verify that find_next_free_ip triggers cleanup when it seems full."""
    net_id = 'net_full'
    cidr = '10.0.0.0/30' # 4 addresses: .0, .1, .2, .3
    # .0 and .3 are network/broadcast
    save_network({'id': net_id, 'cidr': cidr})
    from ipam import sync_net_bitmap
    sync_net_bitmap(net_id)
    
    # Fill up .1 and .2 with short TTLs
    claim_ip_atomic({'ip': '10.0.0.1', 'network_id': net_id}, cidr, ttl=1)
    claim_ip_atomic({'ip': '10.0.0.2', 'network_id': net_id}, cidr, ttl=1)
    
    # Verify full (0, 1, 2, 3 all marked 1)
    assert find_next_free_ip(net_id) is None
    
    # Wait for expiration
    time.sleep(1.1)
    
    # find_next_free_ip should trigger cleanup and find .1 or .2
    next_ip = find_next_free_ip(net_id)
    assert next_ip in ['10.0.0.1', '10.0.0.2']
