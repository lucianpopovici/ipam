"""IPAM helpers and data model functions."""

import json
import ipaddress
from flask import Blueprint
from db import r, redis_get, redis_save, redis_delete, redis_all

ipam_bp = Blueprint('ipam', __name__, url_prefix='')

# Redis key constants
PROJECT_INDEX        = 'projects:index'
NETWORK_INDEX        = 'networks:index'
LABELS_GLOBAL_KEY    = 'labels:global'
TEMPLATES_GLOBAL_KEY = 'templates:global'


def _project_key(pid):
    return f'project:{pid}'

def project_labels_key(pid):
    return f'project:{pid}:labels'

def project_templates_key(pid):
    return f'project:{pid}:templates'

def _network_key(nid):
    return f'network:{nid}'

def _network_labels_key(nid):
    return f'network:{nid}:labels'

def ip_key(ip):
    return f'ip:{ip}'

def net_ips_key(nid):
    return f'network:{nid}:ips'

def _template_key(tid):
    return f'template:{tid}'


def project_nets_key(pid):
    return f'project:{pid}:networks'


# Project CRUD


def save_project(proj: dict):
    """Save project metadata to Redis and update project index."""
    pid = proj['id']
    return redis_save(_project_key(pid), proj, PROJECT_INDEX)


def get_project(pid):
    """Retrieve project metadata by ID."""
    return redis_get(_project_key(pid))


def all_projects():
    """Return all projects sorted by ID."""
    return redis_all(PROJECT_INDEX, get_project)


# Network CRUD


def save_network(net: dict):
    """Save network metadata, update index, and link to project."""
    nid = net['id']
    redis_save(_network_key(nid), net, NETWORK_INDEX)
    if 'project_id' in net and net['project_id']:
        r.sadd(project_nets_key(net['project_id']), nid)
    return net


def get_network(nid):
    """Retrieve network metadata by ID."""
    return redis_get(_network_key(nid))


def project_networks(pid):
    """Return all networks belonging to a project."""
    return redis_all(project_nets_key(pid), get_network)


# IP CRUD


def save_ip(iprec: dict):
    redis_save(ip_key(iprec['ip']), iprec)
    if 'network_id' in iprec and iprec['network_id']:
        r.sadd(net_ips_key(iprec['network_id']), iprec['ip'])
    return iprec


def get_ip(ip_addr):
    return redis_get(ip_key(ip_addr))


def delete_ip(ip_addr):
    rec = get_ip(ip_addr)
    if not rec:
        return
    if rec.get('network_id'):
        r.srem(net_ips_key(rec['network_id']), ip_addr)
    redis_delete(ip_key(ip_addr))


# Label helpers


def global_labels():
    return sorted(r.smembers(LABELS_GLOBAL_KEY))


def add_global_label(label):
    r.sadd(LABELS_GLOBAL_KEY, label)


def remove_global_label(label):
    r.srem(LABELS_GLOBAL_KEY, label)


def project_labels(pid):
    return sorted(r.smembers(project_labels_key(pid)))


def add_project_label(pid, label):
    r.sadd(project_labels_key(pid), label)


def remove_project_label(pid, label):
    r.srem(project_labels_key(pid), label)


def available_labels_for_project(pid):
    return {'global': global_labels(), 'project': project_labels(pid)}


def label_scope(label, pid):
    if label in global_labels():
        return 'global'
    if pid and label in project_labels(pid):
        return 'project'
    return 'unknown'


# Network label mappings

def add_labels_to_network(nid, labels):
    for label in labels:
        r.sadd(_network_labels_key(nid), label)
        r.sadd(f'label:{label}:nets', nid)


def get_network_labels(nid):
    return sorted(r.smembers(_network_labels_key(nid)))


def remove_labels_from_network(nid, labels):
    for label in labels:
        r.srem(_network_labels_key(nid), label)
        r.srem(f'label:{label}:nets', nid)


# Template helpers

def save_template(tmp):
    tid = tmp['id']
    r.set(_template_key(tid), json.dumps(tmp))
    if tmp.get('scope') == 'project' and tmp.get('project_id'):
        r.sadd(project_templates_key(tmp['project_id']), tid)
    else:
        r.sadd(TEMPLATES_GLOBAL_KEY, tid)
    return tmp


def get_template(tid):
    raw = r.get(_template_key(tid))
    return json.loads(raw) if raw else None


def delete_template(tid):
    tmp = get_template(tid)
    if not tmp:
        return
    if tmp.get('scope') == 'project' and tmp.get('project_id'):
        r.srem(project_templates_key(tmp['project_id']), tid)
    else:
        r.srem(TEMPLATES_GLOBAL_KEY, tid)
    r.delete(_template_key(tid))


def global_templates():
    return [get_template(tid) for tid in sorted(r.smembers(TEMPLATES_GLOBAL_KEY)) if get_template(tid)]


def project_templates(pid):
    return [get_template(tid) for tid in sorted(r.smembers(project_templates_key(pid))) if get_template(tid)]


def available_templates_for_project(pid):
    return {'global': global_templates(), 'project': project_templates(pid)}


def template_scope(tid, pid):
    tmpl = get_template(tid)
    if not tmpl:
        return 'unknown'
    if tmpl.get('scope') == 'project' and tmpl.get('project_id') == pid:
        return 'project'
    if tmpl.get('scope') == 'global':
        return 'global'
    return 'unknown'


# Pending Slot helpers

def _validate_rules(rules):
    for rule in rules:
        t = rule.get('type')
        if t not in ('from_start', 'from_end', 'range'):
            raise ValueError('unknown type')
        if 'role' not in rule or 'status' not in rule:
            raise ValueError('missing role/status')
        if rule['status'] not in ('reserved', 'allocated', 'dhcp'):
            raise ValueError('invalid status')
        if t == 'from_start':
            offset = int(rule.get('offset', 0))
            if offset < 1:
                raise ValueError('offset must be >= 1')
        elif t == 'from_end':
            count = int(rule.get('count', 0))
            if count < 1:
                raise ValueError('count must be >= 1')
        elif t == 'range':
            start = int(rule.get('from', 0))
            end = int(rule.get('to', 0))
            if start > end:
                raise ValueError('range invalid')


def resolve_template_rules(cidr, rules):
    if not rules:
        return []
    _validate_rules(rules)
    net = ipaddress.ip_network(cidr, strict=False)
    if net.prefixlen >= 32:
        hosts = []
    elif net.prefixlen == 31:
        hosts = list(net.hosts())
    else:
        hosts = list(net.hosts())

    hosts = [str(h) for h in hosts]
    results = {}

    for rule in rules:
        if rule['type'] == 'from_start':
            offset = int(rule.get('offset', 0))
            index = offset - 1
            if 0 <= index < len(hosts):
                ip = hosts[index]
                results[ip] = {'ip': ip, 'role': rule['role'], 'status': rule['status']}
        elif rule['type'] == 'from_end':
            count = int(rule.get('count', 0))
            for ip in hosts[-count:]:
                results[ip] = {'ip': ip, 'role': rule['role'], 'status': rule['status']}
        elif rule['type'] == 'range':
            start = int(rule.get('from', 0))
            end = int(rule.get('to', 0))
            # 1-based indexing for range
            for ip in hosts[start-1:end]:
                results[ip] = {'ip': ip, 'role': rule['role'], 'status': rule['status']}

    return list(results.values())


def set_pending_slots(nid, tid):
    net = get_network(nid)
    tmpl = get_template(tid)
    if not net or not tmpl:
        return []
    slots = resolve_template_rules(net['cidr'], tmpl.get('rules', []))
    net['pending_slots'] = slots
    save_network(net)
    return slots


def confirm_slot(nid, ip):
    net = get_network(nid)
    if not net:
        return False
    pending = net.get('pending_slots', [])
    slot = next((s for s in pending if s.get('ip') == ip), None)
    if not slot:
        return False
    if get_ip(ip):
        return False
    save_ip({'ip': ip, 'hostname': '', 'description': '', 'status': slot.get('status', 'reserved'), 'network_id': nid})
    net['pending_slots'] = [s for s in pending if s.get('ip') != ip]
    save_network(net)
    return True


def confirm_all_slots(nid):
    net = get_network(nid)
    if not net:
        return {'created': 0, 'skipped': 0}
    pending = list(net.get('pending_slots', []))
    created = 0
    skipped = 0
    for slot in pending:
        if confirm_slot(nid, slot['ip']):
            created += 1
        else:
            skipped += 1
    return {'created': created, 'skipped': skipped}


def dismiss_slot(nid, ip):
    net = get_network(nid)
    if not net:
        return False
    pending = net.get('pending_slots', [])
    if not any(s for s in pending if s.get('ip') == ip):
        return False
    net['pending_slots'] = [s for s in pending if s.get('ip') != ip]
    save_network(net)
    return True


def dismiss_all_slots(nid):
    net = get_network(nid)
    if not net:
        return []
    net['pending_slots'] = []
    save_network(net)
    return []


# Pool and carving


def used_subnets_in_project(pid):
    return [get_network(nid) for nid in r.smembers(project_nets_key(pid)) if get_network(nid)]


def carve_next_subnet(supernet, prefix_len, pid):
    net = ipaddress.ip_network(supernet, strict=False)
    if prefix_len <= net.prefixlen:
        raise ValueError('must be smaller')
    used = [ipaddress.ip_network(n['cidr'], strict=False) for n in used_subnets_in_project(pid) if n.get('cidr')]
    for candidate in net.subnets(new_prefix=prefix_len):
        if any(candidate.overlaps(u) for u in used):
            continue
        return candidate
    raise ValueError('No free')


def pool_by_label_set(networks):
    """
    Groups subnets by their label sets and computes aggregate utilization for each pool.
    Returns a list of pool summary dicts, sorted by total capacity descending.
    """
    grouped = {}
    for n in networks:
        labels = tuple(sorted(n.get('labels', [])))
        if labels not in grouped:
            grouped[labels] = {
                'label_set': list(labels),
                'subnet_count': 0,
                'alloc_ips': 0,
                'total_ips': 0,
                'pending': 0,
                'utilization': 0.0
            }
        group = grouped[labels]
        group['subnet_count'] += 1
        used = n.get('used_count', 0)
        pending = len(n.get('pending_slots', []))
        total = int(ipaddress.ip_network(n['cidr'], strict=False).num_addresses) if n.get('cidr') else 0
        group['alloc_ips'] += used
        group['pending'] += pending
        group['total_ips'] += total
    for g in grouped.values():
        g['utilization'] = (g['alloc_ips'] / g['total_ips'] * 100) if g['total_ips'] else 0
    pools = sorted(grouped.values(), key=lambda x: x['total_ips'], reverse=True)
    return pools


def net_stats(nid):
    net = get_network(nid)
    if not net:
        return {}
    allocated = r.scard(net_ips_key(nid))
    pending = len(net.get('pending_slots', []))
    total = int(ipaddress.ip_network(net['cidr'], strict=False).num_addresses) if net.get('cidr') else 0
    return {'allocated': allocated, 'pending': pending, 'total': total}


def project_pool_summary(pid):
    proj = get_project(pid)
    if not proj:
        return {}
    nets = project_networks(pid)
    total_ips = int(ipaddress.ip_network(proj['supernet'], strict=False).num_addresses) if proj.get('supernet') else 0
    allocated = 0
    pending = 0
    for net in nets:
        allocated += r.scard(net_ips_key(net['id']))
        pending += len(net.get('pending_slots', []))
    return {
        'total_ips': total_ips,
        'allocated_ips': allocated,
        'pending': pending,
        'subnet_count': len(nets),
    }


def global_pool_summary():
    summary = {'total_ips':0, 'allocated_ips':0, 'pending':0, 'subnet_count':0}
    for pid in r.smembers(PROJECT_INDEX):
        ps = project_pool_summary(pid)
        summary['total_ips'] += ps.get('total_ips',0)
        summary['allocated_ips'] += ps.get('allocated_ips',0)
        summary['pending'] += ps.get('pending',0)
        summary['subnet_count'] += ps.get('subnet_count',0)
    return summary
