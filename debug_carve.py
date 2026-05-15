"""Debug script for carving subnets."""
from db import r
from ipam import (
    carve_next_subnet, save_project, save_network,
    project_nets_key, used_subnets_in_project
)

r.flushall()

PID = "proj-v6"
SUPERNET = "2001:db8::/32"
save_project({"id": PID, "name": "IPv6 Project", "supernet": SUPERNET})

# Carve first /48
NET1_CIDR = str(carve_next_subnet(SUPERNET, 48, PID))
print(f"Net 1: {NET1_CIDR}")

net1 = {"id": "net1", "cidr": NET1_CIDR, "project_id": PID, "name": "Net 1"}
save_network(net1)
r.sadd(project_nets_key(PID), "net1")

# Used subnets check
used = used_subnets_in_project(PID)
print(f"Used: {used}")

# Carve next /48
NET2_CIDR = str(carve_next_subnet(SUPERNET, 48, PID))
print(f"Net 2: {NET2_CIDR}")
