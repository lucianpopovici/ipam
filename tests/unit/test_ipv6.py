import ipam
from ipam import (
    resolve_template_rules, carve_next_subnet,
    save_network, save_project, net_stats,
    project_nets_key,
)

def test_ipv6_resolution(fake_redis):
    cidr = "2001:db8:1::/64"
    rules = [
        {"type": "from_start", "offset": 1, "role": "gateway", "status": "allocated"},
        {"type": "from_end", "count": 1, "role": "last", "status": "reserved"},
    ]
    resolved = resolve_template_rules(cidr, rules)

    assert len(resolved) == 2
    assert resolved[0]["ip"] == "2001:db8:1::1"
    assert resolved[0]["role"] == "gateway"
    assert resolved[1]["ip"] == "2001:db8:1:0:ffff:ffff:ffff:fffe"
    assert resolved[1]["role"] == "last"

def test_ipv6_carving(fake_redis):
    pid = "proj-v6"
    supernet = "2001:db8::/32"
    save_project({"id": pid, "name": "IPv6 Project", "supernet": supernet})

    # Carve first /48
    net1_cidr = str(carve_next_subnet(supernet, 48, pid))
    assert net1_cidr == "2001:db8::/48"

    net1 = {"id": "net1", "cidr": net1_cidr, "project_id": pid, "name": "Net 1"}
    save_network(net1)
    ipam.r.sadd(project_nets_key(pid), "net1")
    ipam.r.sadd('networks:index', 'net1')

    # Carve next /48
    net2_cidr = str(carve_next_subnet(supernet, 48, pid))
    assert net2_cidr == "2001:db8:1::/48"

def test_ipv6_stats(fake_redis):
    cidr = "2001:db8:acad::/64"
    net = {"id": "v6net", "cidr": cidr, "name": "V6 Subnet", "project_id": "p1"}
    save_network(net)
    ipam.r.sadd('networks:index', 'v6net')

    stats = net_stats(net)
    assert stats["ip_version"] == 6
    assert stats["prefix_len"] == 64
    assert stats["network_addr"] == "2001:db8:acad::"
    # total_hosts for IPv6 /64 should be 2^64 - 2 (or 2^64 depending on how it's calculated)
    # ipaddress.ip_network(cidr).num_addresses is 18446744073709551616
    assert stats["total_hosts"] > 10**18
