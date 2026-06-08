# pylint: disable=cyclic-import
"""
Health and conflict detection engine.
Consolidates checks for subnets, hardware, and logical requirements.
"""
import ipaddress
from ipam import project_networks, net_stats
from ne import compute_requirements, load_requirements, project_pods, pod_slot_fill
from hw_logic import project_cables, validate_project

def check_project_health(pid: str) -> list:
    """
    Run all health checks for a project and return a list of issue dicts.
    Each issue: {'type': str, 'severity': 'error'|'warning', 'message': str, 'context': dict}
    """
    issues = []

    # 1. Subnet Utilization
    issues.extend(_check_subnet_utilization(pid))

    # 2. Requirement Gaps
    issues.extend(_check_requirement_gaps(pid))

    # 3. Hardware Validation (Power, Weight, Placement)
    # We leverage existing hw_logic validation
    hw_issues = validate_project(pid)
    issues.extend(hw_issues)

    # 4. Cable Integrity
    issues.extend(_check_cable_integrity(pid))

    # 5. Service field gaps
    issues.extend(_check_service_field_gaps(pid))

    # 6. POD composition gaps (assigned NE instances vs planned slots)
    issues.extend(_check_pod_composition_gaps(pid))

    return issues

def _check_pod_composition_gaps(pid: str) -> list:
    """Flag PODs whose assigned NE instances don't match their planned slots."""
    issues = []
    for pod in project_pods(pid):
        for row in pod_slot_fill(pod['id']):
            if row['gap']:
                issues.append({
                    'type': 'composition',
                    'severity': 'warning',
                    'message': (f"POD {pod['name']}: planned {row['needed']}x "
                                f"{row['name']}, {row['assigned']} assigned "
                                f"({row['gap']} missing)."),
                    'context': {'pod_id': pod['id'], 'ne_type_id': row['ne_type_id']},
                })
            elif row['surplus']:
                issues.append({
                    'type': 'composition',
                    'severity': 'warning',
                    'message': (f"POD {pod['name']}: {row['assigned']}x {row['name']} "
                                f"assigned but only {row['needed']} planned "
                                f"({row['surplus']} over)."),
                    'context': {'pod_id': pod['id'], 'ne_type_id': row['ne_type_id']},
                })
    return issues

def _check_subnet_utilization(pid: str) -> list:
    issues = []
    networks = project_networks(pid)
    for net in networks:
        stats = net_stats(net)
        util = stats['utilization']
        if util > 90:
            issues.append({
                'type': 'utilization',
                'severity': 'error' if util > 98 else 'warning',
                'message': f"Subnet {net['cidr']} is {util}% full.",
                'context': {'net_id': net['id'], 'cidr': net['cidr']}
            })
    return issues

def _check_requirement_gaps(pid: str) -> list:
    issues = []
    reqs = load_requirements(pid)
    if not reqs:
        # If no requirements cached, compute them
        reqs = compute_requirements(pid)

    networks = project_networks(pid)
    # Map of (prefix_len, labels_tuple) -> count
    existing_nets = {}
    for net in networks:
        key = (ipaddress.ip_network(net['cidr']).prefixlen, tuple(sorted(net.get('labels', []))))
        existing_nets[key] = existing_nets.get(key, 0) + 1

    for req in reqs:
        if req.get('kind', 'ip') != 'ip':
            continue
        if req.get('pushed'):
            continue

        key = (req['prefix_len'], tuple(sorted(req['labels'])))
        needed = req['count']
        found = existing_nets.get(key, 0)

        if found < needed:
            msg = (f"Requirement gap: Need {needed}x /{req['prefix_len']} "
                   f"with labels {req['labels']}, but only found {found}.")
            issues.append({
                'type': 'requirement',
                'severity': 'warning',
                'message': msg,
                'context': {'req_key': req['key']}
            })

    return issues

def _check_cable_integrity(pid: str) -> list:
    issues = []
    cables = project_cables(pid)
    for c in cables:
        end_a = c.get('end_a', {})
        end_b = c.get('end_b', {})

        if not end_a.get('instance_id') or not end_b.get('instance_id'):
            issues.append({
                'type': 'cable',
                'severity': 'warning',
                'message': f"Cable {c.get('asset_tag', c['id'])} is only partially connected.",
                'context': {'cable_id': c['id']}
            })

        # Mismatched connectors check is already in hw_logic.validate_project
    return issues


def _check_service_field_gaps(pid: str) -> list:
    import services_logic  # pylint: disable=import-outside-toplevel
    issues = []
    for gap in services_logic.service_field_gaps(pid):
        issues.append({
            'type':     'service_field_missing',
            'severity': gap['severity'],
            'message':  (
                f"Service field missing: {gap['missing']} value(s) needed for "
                f"'{gap['service']}.{gap['field']}' on {gap['ne_type']} / {gap['iface']}."
            ),
            'context':  gap,
        })
    return issues
