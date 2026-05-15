from ipam import carve_next_subnet, save_project, save_network, project_nets_key
from db import r

r.flushall()

pid = "proj-v6"
supernet = "2001:db8::/32"
save_project({"id": pid, "name": "IPv6 Project", "supernet": supernet})

# Carve first /48
net1_cidr = str(carve_next_subnet(supernet, 48, pid))
print(f"Net 1: {net1_cidr}")

net1 = {"id": "net1", "cidr": net1_cidr, "project_id": pid, "name": "Net 1"}
save_network(net1)
r.sadd(project_nets_key(pid), "net1")

# Used subnets check
from ipam import used_subnets_in_project
used = used_subnets_in_project(pid)
print(f"Used: {used}")

# Carve next /48
net2_cidr = str(carve_next_subnet(supernet, 48, pid))
print(f"Net 2: {net2_cidr}")
