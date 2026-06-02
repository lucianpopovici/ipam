"""
IPAM blueprint — projects, subnets, IPs, labels, subnet templates, pool, search, overview.
"""
import ipaddress
import json
import uuid
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort, make_response
from auth import editor_required
from db import r
from hw_logic import (
    HW_INST_INDEX, get_hw_instance, get_hw_template, project_instances, get_rack_slots
)
from core.forms import form_errors

ipam_bp = Blueprint('ipam', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Validation helpers
# ══════════════════════════════════════════════════════════════════════════════

def validate_cidr(cidr: str) -> bool:
    """Validate a CIDR string using ip_network."""
    try:
        ipaddress.ip_network(cidr, strict=False)
        return True
    except (ValueError, TypeError):
        return False

def validate_ip(ip: str) -> bool:
    """Validate an IP address string using ip_address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except (ValueError, TypeError):
        return False

def validate_ip_interface(val: str) -> bool:
    """Strict validation using ip_interface (IP/Prefix or just IP)."""
    try:
        ipaddress.ip_interface(val)
        return True
    except (ValueError, TypeError):
        return False

# ── Address-count / minimum-prefix helpers ─────────────────────────────────────

def _ceil_log2(n: int) -> int:
    """Smallest k such that 2^k >= n."""
    if n <= 1:
        return 0
    k = 0
    while (1 << k) < n:
        k += 1
    return k

def min_prefix_v4(address_count: int) -> int:
    """Smallest IPv4 prefix that accommodates address_count host addresses."""
    if address_count <= 1:
        return 32
    if address_count == 2:
        return 31   # RFC 3021 P2P
    # +2 for network + broadcast; next power-of-2 >= count+2
    return 32 - _ceil_log2(address_count + 2)

def min_prefix_v6(address_count: int) -> int:
    """Smallest IPv6 prefix that accommodates address_count addresses."""
    if address_count <= 1:
        return 128
    return 128 - _ceil_log2(address_count)

def address_count_from_prefix(prefix_len: int, family: int) -> int:
    """Usable host count for a prefix (legacy conversion)."""
    if family == 6:
        if prefix_len >= 128:
            return 1
        return 2 ** (128 - prefix_len)
    # IPv4
    if prefix_len >= 32:
        return 1
    if prefix_len == 31:
        return 2
    return max(1, 2 ** (32 - prefix_len) - 2)

# ── Bitmap Helpers ─────────────────────────────────────────────────────────────

def get_ip_offset(net_cidr, ip_str):
    """Calculate the bit offset of an IP address within its network."""
    net_obj = ipaddress.ip_network(net_cidr, strict=False)
    ip_obj = ipaddress.ip_address(ip_str)
    return int(ip_obj) - int(net_obj.network_address)

def get_ip_at_offset(net_cidr, offset):
    """Return the IP address string for a given bit offset in a network."""
    net_obj = ipaddress.ip_network(net_cidr, strict=False)
    return str(net_obj.network_address + offset)

def sync_net_bitmap(net_id):
    """Rebuild the bitmap for a network based on currently allocated IPs."""
    net = get_network(net_id)
    if not net:
        return
    bkey = net_bitmap_key(net_id)
    r.delete(bkey)
    for ip_str in r.smembers(net_ips_key(net_id)):
        offset = get_ip_offset(net['cidr'], ip_str)
        r.setbit(bkey, offset, 1)
    # Also mark network and broadcast as "used" in the bitmap if they shouldn't be assigned
    net_obj = ipaddress.ip_network(net['cidr'], strict=False)
    if net_obj.version == 4 and net_obj.prefixlen <= 30:
        r.setbit(bkey, 0, 1) # Network
        r.setbit(bkey, net_obj.num_addresses - 1, 1) # Broadcast

# ══════════════════════════════════════════════════════════════════════════════
# Key helpers
# ══════════════════════════════════════════════════════════════════════════════

def project_key(pid):
    """Return the Redis key for a project."""
    return f'project:{pid}'

def project_nets_key(pid):
    """Return the Redis key for project networks."""
    return f'project:{pid}:networks'

def project_labels_key(pid):
    """Return the Redis key for project labels."""
    return f'project:{pid}:labels'

def project_templates_key(pid):
    """Return the Redis key for project templates."""
    return f'project:{pid}:templates'

def net_key(nid):
    """Return the Redis key for a network."""
    return f'network:{nid}'

def net_ips_key(nid):
    """Return the Redis key for network IPs."""
    return f'network:{nid}:ips'

def net_labels_key(nid):
    """Return the Redis key for network labels."""
    return f'network:{nid}:labels'

def net_bitmap_key(nid):
    """Return the Redis key for the network's availability bitmap."""
    return f'network:{nid}:bitmap'

def publish_ip_update(action: str, addr: dict):
    """Publish an IP update message via Redis Pub/Sub."""
    msg = json.dumps({'action': action, 'data': addr})
    r.publish('ipam:updates', msg)

def label_nets_key(label):
    """Return the Redis key for label-to-networks mapping."""
    return f'label:{label}:nets'

def template_key(tid):
    """Return the Redis key for a template."""
    return f'template:{tid}'

def ip_key(ip):
    """Return the Redis key for an IP address."""
    return f'ip:{ip}'

PROJECTS_INDEX   = 'projects:index'
NETWORKS_INDEX   = 'networks:index'
GLOBAL_LABELS    = 'labels:global'
GLOBAL_TEMPLATES = 'templates:global'

def new_id() -> str:
    """Generate a random 8-character ID."""
    return str(uuid.uuid4())[:8]

# ══════════════════════════════════════════════════════════════════════════════
# Label helpers
# ══════════════════════════════════════════════════════════════════════════════

def parse_labels(form_value: str) -> list:
    """Parse comma-separated labels into a list of unique strings."""
    if not form_value:
        return []
    seen, result = set(), []
    for label in form_value.split(','):
        label = label.strip()
        if label and label not in seen:
            seen.add(label)
            result.append(label)
    return result

def global_labels() -> list:
    """Return all global labels."""
    return sorted(r.smembers(GLOBAL_LABELS))

def project_labels(pid: str) -> list:
    """Return all labels for a specific project."""
    return sorted(r.smembers(project_labels_key(pid)))

def available_labels_for_project(pid: str) -> dict:
    """Return both global and project-specific labels."""
    return {'global': global_labels(), 'project': project_labels(pid)}

def add_global_label(label):
    """Add a label to the global set."""
    r.sadd(GLOBAL_LABELS, label)

def remove_global_label(label):
    """Remove a label from the global set."""
    r.srem(GLOBAL_LABELS, label)

def add_project_label(pid, label):
    """Add a label to a project."""
    r.sadd(project_labels_key(pid), label)

def remove_project_label(pid, label):
    """Remove a label from a project."""
    r.srem(project_labels_key(pid), label)

def add_labels_to_network(net_id, labels):
    """Associate labels with a network."""
    for label in labels:
        r.sadd(net_labels_key(net_id), label)
        r.sadd(label_nets_key(label), net_id)

def remove_labels_from_network(net_id, labels):
    """Disassociate labels from a network."""
    for label in labels:
        r.srem(net_labels_key(net_id), label)
        r.srem(label_nets_key(label), net_id)

def get_network_labels(net_id: str) -> list:
    """Return labels associated with a network."""
    return sorted(r.smembers(net_labels_key(net_id)))

def label_scope(label: str, pid: str) -> str:
    """Determine if a label is global or project-specific."""
    if r.sismember(GLOBAL_LABELS, label):
        return 'global'
    if pid and r.sismember(project_labels_key(pid), label):
        return 'project'
    return 'unknown'

# ══════════════════════════════════════════════════════════════════════════════
# Subnet template helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_template(tid):
    """Retrieve a template by ID."""
    raw = r.get(template_key(tid))
    return json.loads(raw) if raw else None

def save_template(tmpl):
    """Save a template to Redis."""
    r.set(template_key(tmpl['id']), json.dumps(tmpl))
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.sadd(project_templates_key(tmpl['project_id']), tmpl['id'])
    else:
        r.sadd(GLOBAL_TEMPLATES, tmpl['id'])

def delete_template(tid):
    """Delete a template and its index references."""
    tmpl = get_template(tid)
    if not tmpl:
        return
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.srem(project_templates_key(tmpl['project_id']), tid)
    else:
        r.srem(GLOBAL_TEMPLATES, tid)
    r.delete(template_key(tid))

def global_templates() -> list:
    """Return all global subnet templates."""
    return sorted(
        [t for t in (get_template(tid) for tid in r.smembers(GLOBAL_TEMPLATES)) if t],
        key=lambda t: t['name'])

def project_templates(pid: str) -> list:
    """Return all templates for a specific project."""
    return sorted(
        [t for t in (get_template(tid) for tid in r.smembers(project_templates_key(pid))) if t],
        key=lambda t: t['name'])

def available_templates_for_project(pid: str) -> dict:
    """Return both global and project-specific templates."""
    return {'global': global_templates(),
            'project': project_templates(pid) if pid else []}

def template_scope(tid: str, pid: str) -> str:
    """Determine if a template is global or project-specific."""
    if r.sismember(GLOBAL_TEMPLATES, tid):
        return 'global'
    if pid and r.sismember(project_templates_key(pid), tid):
        return 'project'
    return 'unknown'

# ══════════════════════════════════════════════════════════════════════════════
# Rule engine
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_offset(offset_obj, prev_end: int) -> int:
    """Resolve an offset object to an absolute host index (0-based)."""
    if isinstance(offset_obj, int):
        return offset_obj - 1  # legacy: 1-based
    if not isinstance(offset_obj, dict):
        return 0
    kind = offset_obj.get('kind', 'fixed')
    if kind == 'fixed':
        return int(offset_obj.get('value', 0))
    if kind == 'after_previous':
        return prev_end + int(offset_obj.get('gap', 0))
    return 0


def resolve_template_rules(cidr: str, rules: list, pid: str = None) -> list:
    """Resolve subnet rules into a list of specific IP slots.

    *pid* is used by per_hw/per_ne rules to look up live inventory counts.
    When None, per_hw/per_ne rules fall back to max_count.
    """
    network_obj = ipaddress.ip_network(cidr, strict=False)
    num_hosts   = network_obj.num_addresses
    if num_hosts <= 2:
        host_list = [network_obj[i] for i in range(num_hosts)]
    else:
        num_hosts -= 2
        host_list = None  # index network_obj[idx + 1]

    def _ip_at(idx):
        return str(host_list[idx] if host_list else network_obj[idx + 1])

    slot_map = {}
    prev_end = -1  # track last index used by previous rule (for after_previous)

    for rule in rules:
        rtype  = rule.get('type')
        role   = rule.get('role', 'reserved')
        status = rule.get('status', 'reserved')

        if rtype == 'from_start':
            offset_obj = rule.get('offset', 1)
            idx = _resolve_offset(offset_obj, prev_end)
            if 0 <= idx < num_hosts:
                slot_map[idx] = {'ip': _ip_at(idx), 'role': role, 'status': status}
                prev_end = max(prev_end, idx)

        elif rtype == 'from_end':
            count = int(rule.get('count', 1))
            for i in range(count):
                idx = num_hosts - 1 - i
                if idx >= 0:
                    slot_map[idx] = {'ip': _ip_at(idx), 'role': role, 'status': status}
            prev_end = max(prev_end, num_hosts - 1)

        elif rtype == 'range':
            frm = int(rule.get('from', 1)) - 1
            to  = int(rule.get('to',  1)) - 1
            for idx in range(frm, min(to + 1, num_hosts)):
                if idx >= 0:
                    if len(slot_map) > 1000:
                        break
                    slot_map[idx] = {'ip': _ip_at(idx), 'role': role, 'status': status}
            prev_end = max(prev_end, min(to, num_hosts - 1))

        elif rtype in ('per_hw', 'per_ne'):
            # Determine how many slots to reserve
            max_count = int(rule.get('max_count', 0))
            actual_count = _resolve_dynamic_count(rtype, rule, pid) if pid else 0
            count = actual_count if pid else max_count
            offset_obj = rule.get('offset', {'kind': 'fixed', 'value': 0})
            start_idx  = _resolve_offset(offset_obj, prev_end)
            for i in range(count):
                idx = start_idx + i
                if idx >= num_hosts:
                    break
                if len(slot_map) > 1000:
                    break
                slot_map[idx] = {
                    'ip':     _ip_at(idx),
                    'role':   f"{role}:{i}" if count > 1 else role,
                    'status': status,
                    'phantom_source': f'rule:{rtype}:{i}',
                }
            if count > 0:
                prev_end = max(prev_end, start_idx + count - 1)

    return [info for idx, info in sorted(slot_map.items())]


def _resolve_dynamic_count(rtype: str, rule: dict, pid: str) -> int:
    """Count actual inventory items matching a per_hw or per_ne rule."""
    max_count = int(rule.get('max_count', 0))
    try:
        if rtype == 'per_hw':
            count = _count_per_hw(rule, pid)
        else:
            count = _count_per_ne(rule, pid)
        return min(count, max_count) if max_count else count
    except Exception:  # pylint: disable=broad-except
        return 0


def _count_per_hw(rule: dict, pid: str) -> int:
    """Count HW instances matching per_hw selectors in the project."""
    insts = project_instances(pid)
    total = 0
    for sel in rule.get('selectors', []):
        required_labels = set(sel.get('labels_any', []))
        for inst in insts:
            tmpl = get_hw_template(inst.get('template_id', '')) or {}
            eff_labels = set(tmpl.get('labels', [])) | set(inst.get('labels', []))
            if required_labels and not required_labels.intersection(eff_labels):
                continue
            total += 1
    return total


def _count_per_ne(rule: dict, pid: str) -> int:
    """Count NE instances matching per_ne filter in the project."""
    from ne import project_ne_instances as _pi  # pylint: disable=import-outside-toplevel
    ne_filter    = rule.get('ne_filter', {})
    ne_type_id   = ne_filter.get('ne_type_id')
    labels_any   = set(ne_filter.get('labels_any', []))
    per_ne_count = int(rule.get('per_ne_count', 1))
    max_count    = int(rule.get('max_count', 0))

    matching = 0
    for inst in _pi(pid):
        if ne_type_id and inst.get('ne_type_id') != ne_type_id:
            continue
        if labels_any and not labels_any.intersection(set(inst.get('labels', []))):
            continue
        matching += 1
        if max_count and matching >= max_count:
            break
    return matching * per_ne_count


def _validate_offset(offset_obj, rule_index: int, allow_relative: bool):
    """Validate a rule offset object ({kind, value} or {kind, gap})."""
    if not isinstance(offset_obj, dict):
        return  # legacy integer offset; accepted silently
    kind = offset_obj.get('kind')
    if kind == 'fixed':
        if not isinstance(offset_obj.get('value'), int) or offset_obj['value'] < 0:
            raise ValueError(f'Rule {rule_index}: fixed offset needs integer value >= 0')
    elif kind == 'after_previous':
        if not allow_relative:
            raise ValueError(f'Rule {rule_index}: TMPL_FIRST_RULE_RELATIVE — '
                             f'first rule cannot use after_previous offset')
        if not isinstance(offset_obj.get('gap', 0), int):
            raise ValueError(f'Rule {rule_index}: after_previous needs integer gap')
    elif kind is not None:
        raise ValueError(f'Rule {rule_index}: unknown offset kind "{kind}"')


def _validate_rules(rules: list):
    """Validate template rules for correctness."""
    valid_types    = {'from_start', 'from_end', 'range', 'per_hw', 'per_ne'}
    valid_statuses = {'reserved', 'allocated', 'dhcp'}
    for i, rule in enumerate(rules):
        rtype = rule.get('type')
        if rtype not in valid_types:
            raise ValueError(f'Rule {i}: unknown type "{rtype}"')
        if rtype == 'from_start':
            offset = rule.get('offset', 1)
            if isinstance(offset, dict):
                _validate_offset(offset, i, allow_relative=(i > 0))
            elif not isinstance(offset, int) or offset < 1:
                raise ValueError(f'Rule {i}: from_start needs integer offset >= 1')
        elif rtype == 'from_end':
            if not isinstance(rule.get('count'), int) or rule['count'] < 1:
                raise ValueError(f'Rule {i}: from_end needs integer count >= 1')
        elif rtype == 'range':
            frm, to = rule.get('from'), rule.get('to')
            if not isinstance(frm, int) or not isinstance(to, int) or frm < 1 or to < frm:
                raise ValueError(f'Rule {i}: range needs integer from >= 1, to >= from')
        elif rtype in ('per_hw', 'per_ne'):
            _validate_offset(rule.get('offset', {'kind': 'fixed', 'value': 0}), i, allow_relative=(i > 0))
            max_count = rule.get('max_count', 0)
            if not isinstance(max_count, int) or max_count < 0:
                raise ValueError(f'Rule {i}: {rtype} needs integer max_count >= 0')
        if rule.get('status', 'reserved') not in valid_statuses:
            raise ValueError(f'Rule {i}: unknown status "{rule["status"]}"')

# ══════════════════════════════════════════════════════════════════════════════
# Pending slots
# ══════════════════════════════════════════════════════════════════════════════

def set_pending_slots(net_id: str, tid: str):
    """Apply a template to a network, generating pending IP slots."""
    net  = get_network(net_id)
    tmpl = get_template(tid)
    if not net or not tmpl:
        raise ValueError('Network or template not found')
    resolved = resolve_template_rules(net['cidr'], tmpl['rules'])
    pending  = [s for s in resolved if not get_ip(s['ip'])]
    net['template_id']   = tid
    net['pending_slots'] = pending
    save_network(net)
    return pending

def confirm_slot(net_id: str, ip_str: str) -> bool:
    """Confirm a pending IP slot, creating a real IP record."""
    net = get_network(net_id)
    if not net:
        return False
    pending = net.get('pending_slots', [])
    slot = next((s for s in pending if s['ip'] == ip_str), None)
    if not slot:
        return False

    success = claim_ip_atomic({
        'ip': ip_str, 'hostname': '', 'description': slot['role'],
        'status': slot['status'], 'network_id': net_id,
        'from_template': net.get('template_id', '')
    }, net['cidr'])

    if success:
        net['pending_slots'] = [s for s in pending if s['ip'] != ip_str]
        save_network(net)
        return True
    return False

def confirm_all_slots(net_id: str) -> dict:
    """Confirm all pending IP slots for a network."""
    net = get_network(net_id)
    if not net:
        return {'created': 0, 'skipped': 0}
    pending = net.get('pending_slots', [])
    created = skipped = 0
    tid = net.get('template_id', '')
    for slot in pending:
        success = claim_ip_atomic({
            'ip': slot['ip'], 'hostname': '', 'description': slot['role'],
            'status': slot['status'], 'network_id': net_id, 'from_template': tid
        }, net['cidr'])
        if success:
            created += 1
        else:
            skipped += 1
    net['pending_slots'] = []
    save_network(net)
    return {'created': created, 'skipped': skipped}

def dismiss_slot(net_id: str, ip_str: str) -> bool:
    """Remove a single pending IP slot."""
    net = get_network(net_id)
    if not net:
        return False
    before = len(net.get('pending_slots', []))
    net['pending_slots'] = [s for s in net.get('pending_slots', []) if s['ip'] != ip_str]
    if len(net['pending_slots']) < before:
        save_network(net)
        return True
    return False

def dismiss_all_slots(net_id: str):
    """Remove all pending IP slots for a network."""
    net = get_network(net_id)
    if not net:
        return
    net['pending_slots'] = []
    save_network(net)

# ══════════════════════════════════════════════════════════════════════════════
# Core data helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_project(pid):
    """Retrieve a project by ID."""
    raw = r.get(project_key(pid))
    return json.loads(raw) if raw else None

def save_project(proj):
    """Save a project and its index."""
    r.set(project_key(proj['id']), json.dumps(proj))
    r.sadd(PROJECTS_INDEX, proj['id'])

def all_projects():
    """Return all projects."""
    return [p for p in (get_project(pid) for pid in r.smembers(PROJECTS_INDEX)) if p]

def get_network(nid):
    """Retrieve a network by ID."""
    raw = r.get(net_key(nid))
    return json.loads(raw) if raw else None

def save_network(net):
    """Save a network and its index."""
    r.set(net_key(net['id']), json.dumps(net))
    r.sadd(NETWORKS_INDEX, net['id'])

def get_ip(ip_str):
    """Retrieve an IP record."""
    raw = r.get(ip_key(ip_str))
    return json.loads(raw) if raw else None

def save_ip(addr):
    """Save an IP record and update network-to-IP index."""
    r.set(ip_key(addr['ip']), json.dumps(addr))
    r.sadd(net_ips_key(addr['network_id']), addr['ip'])

def claim_ip_atomic(addr, net_cidr, ttl=None) -> bool:
    """
    Atomically claims an IP address using a Lua script to prevent race conditions.
    Also updates the subnet availability bitmap.
    Supports an optional TTL (in seconds) for temporary reservations.
    Returns True if successful, False if already taken.
    """
    script = """
    if redis.call('EXISTS', KEYS[1]) == 0 then
        if ARGV[4] ~= '' then
            redis.call('SETEX', KEYS[1], ARGV[4], ARGV[1])
        else
            redis.call('SET', KEYS[1], ARGV[1])
        end
        redis.call('SADD', KEYS[2], ARGV[2])
        redis.call('SETBIT', KEYS[3], ARGV[3], 1)
        return 1
    else
        return 0
    end
    """
    key = ip_key(addr['ip'])
    idx_key = net_ips_key(addr['network_id'])
    bkey = net_bitmap_key(addr['network_id'])
    offset = get_ip_offset(net_cidr, addr['ip'])
    ttl_val = str(ttl) if ttl else ''
    res = r.eval(script, 3, key, idx_key, bkey, json.dumps(addr), addr['ip'], offset, ttl_val)
    success = bool(res)
    if success:
        publish_ip_update('allocate', addr)
    return success

def all_networks():
    """Return all networks."""
    return [n for n in (get_network(nid) for nid in r.smembers(NETWORKS_INDEX)) if n]

def find_network_by_cidr(cidr: str):
    """Find a network record by its CIDR string."""
    for net in all_networks():
        if net['cidr'] == cidr:
            return net
    return None

def network_addresses(net_id):
    """Return all IP records for a network, sorted by IP address. Performs lazy cleanup of expired TTLs."""
    net = get_network(net_id)
    if not net:
        return []

    raw_ips = r.smembers(net_ips_key(net_id))
    valid_addrs = []
    expired_ips = []

    for ip_str in raw_ips:
        addr = get_ip(ip_str)
        if addr:
            valid_addrs.append(addr)
        else:
            expired_ips.append(ip_str)

    # Lazy cleanup of expired TTL reservations
    if expired_ips:
        bkey = net_bitmap_key(net_id)
        idx_key = net_ips_key(net_id)
        for ip_str in expired_ips:
            r.srem(idx_key, ip_str)
            try:
                offset = get_ip_offset(net['cidr'], ip_str)
                r.setbit(bkey, offset, 0)
            except (ValueError, TypeError):
                pass # Should not happen if data is consistent

    return sorted(valid_addrs, key=lambda a: ipaddress.ip_address(a['ip']))

def project_networks(pid):
    """Return all networks for a project with stats."""
    return [net_stats(n) for n in (get_network(nid) for nid in r.smembers(project_nets_key(pid))) if n]

def net_stats(net):
    """Calculate and return network statistics and metadata."""
    network_obj = ipaddress.ip_network(net['cidr'], strict=False)
    total = max(network_obj.num_addresses - 2, 1)
    used  = r.scard(net_ips_key(net['id']))
    net   = dict(net)
    net['total_hosts']   = total
    net['used_count']    = used
    net['utilization']   = round((used / total) * 100, 1)
    net['network_addr']  = str(network_obj.network_address)
    net['broadcast']     = str(network_obj.broadcast_address)
    net['netmask']       = str(network_obj.netmask)
    net['prefix_len']    = network_obj.prefixlen
    net['ip_version']    = network_obj.version
    net['labels']        = get_network_labels(net['id'])
    tid = net.get('template_id')
    net['template']      = get_template(tid) if tid else None
    net['pending_slots'] = net.get('pending_slots', [])
    return net

# ── Subnet carving ─────────────────────────────────────────────────────────────

def used_subnets_in_project(pid) -> list:
    """Return a list of ip_network objects for subnets in a project."""
    result = []
    for nid in r.smembers(project_nets_key(pid)):
        net = get_network(nid)
        if net:
            result.append(ipaddress.ip_network(net['cidr'], strict=False))
    return result

def carve_next_subnet(parent_cidr: str, prefix_len: int, pid: str):
    """Find the next available subnet of a certain size within a parent CIDR."""
    parent = ipaddress.ip_network(parent_cidr, strict=False)
    if prefix_len <= parent.prefixlen:
        raise ValueError(f'/{prefix_len} must be smaller than parent /{parent.prefixlen}')
    used = used_subnets_in_project(pid)
    for candidate in parent.subnets(new_prefix=prefix_len):
        if not any(candidate.overlaps(u) for u in used):
            return candidate
    raise ValueError(f'No free /{prefix_len} block available in {parent_cidr}')

# ── Pool calculations ───────────────────────────────────────────────────────────

def pool_by_label_set(networks: list) -> list:
    """Group networks by their label sets and calculate aggregate stats."""
    groups = {}
    for net in networks:
        labels  = frozenset(net.get('labels', []))
        key     = ' | '.join(sorted(labels))
        size    = ipaddress.ip_network(net['cidr']).num_addresses
        used    = net.get('used_count', 0)
        pending = len(net.get('pending_slots', []))
        if key not in groups:
            groups[key] = {'label_set': sorted(labels), 'label_key': key,
                           'total_ips': 0, 'alloc_ips': 0, 'pending': 0,
                           'subnet_count': 0, 'subnets': []}
        groups[key]['total_ips']    += size
        groups[key]['alloc_ips']    += used
        groups[key]['pending']      += pending
        groups[key]['subnet_count'] += 1
        groups[key]['subnets'].append(net['cidr'])
    for g in groups.values():
        g['free_ips']    = g['total_ips'] - g['alloc_ips']
        g['utilization'] = round((g['alloc_ips'] / g['total_ips']) * 100, 1) if g['total_ips'] else 0
    return sorted(groups.values(), key=lambda g: g['total_ips'], reverse=True)

def project_pool_summary(pid):
    """Return a summary of IP usage and pools for a specific project."""
    proj = get_project(pid)
    if not proj:
        return {}
    nets    = project_networks(pid)
    alloc   = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
    pending = sum(len(n.get('pending_slots', [])) for n in nets)
    # Capacity: if legacy_supernet exists use that, otherwise sum subnets
    legacy = proj.get('legacy_supernet') or proj.get('supernet')
    if legacy:
        try:
            total = ipaddress.ip_network(legacy, strict=False).num_addresses
        except ValueError:
            total = alloc
    else:
        total = alloc
    return {
        'supernet': legacy or '', 'total_ips': total,
        'allocated_ips': alloc, 'free_ips': max(0, total - alloc),
        'pending': pending,
        'utilization': round((alloc / total) * 100, 1) if total else 0,
        'subnet_count': len(nets), 'pools': pool_by_label_set(nets),
    }

def global_pool_summary() -> dict:
    """Return a global summary of IP usage across all projects."""
    projects = all_projects()
    grand_total = grand_alloc = grand_pending = 0
    project_rows = []
    all_nets = []

    for proj in sorted(projects, key=lambda p: p['name']):
        nets   = project_networks(proj['id'])
        all_nets.extend(nets)
        proj_alloc   = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
        proj_pending = sum(len(n.get('pending_slots', [])) for n in nets)
        # Capacity: legacy_supernet if present, else sum of subnets
        legacy = proj.get('legacy_supernet') or proj.get('supernet')
        if legacy:
            try:
                proj_total = ipaddress.ip_network(legacy, strict=False).num_addresses
            except ValueError:
                proj_total = proj_alloc
        else:
            proj_total = proj_alloc
        grand_total   += proj_total
        grand_alloc   += proj_alloc
        grand_pending += proj_pending
        project_rows.append({
            'id': proj['id'], 'name': proj['name'],
            'supernet': legacy or '',
            'total_ips': proj_total, 'alloc_ips': proj_alloc,
            'free_ips': max(0, proj_total - proj_alloc), 'pending': proj_pending,
            'utilization': round((proj_alloc / proj_total) * 100, 1) if proj_total else 0,
            'subnet_count': len(nets),
        })

    return {
        'total_ips': grand_total, 'alloc_ips': grand_alloc,
        'free_ips': max(0, grand_total - grand_alloc), 'pending': grand_pending,
        'utilization': round((grand_alloc / grand_total) * 100, 1) if grand_total else 0,
        'project_count': len(projects),
        'projects': project_rows,
        'label_pool': pool_by_label_set(all_nets),
    }

def resolve_pool(family: int, vrf_id, label_set: set, pid: str,
                site_id: str = None, pod_id: str = None) -> list:
    """
    Return subnets in project *pid* that match the given criteria.

    Match conditions (all must hold):
      1. subnet.family == family  (4 or 6)
      2. subnet.vrf_id == vrf_id  (null == null — not a wildcard)
      3. label_set ⊆ subnet.labels
      4. site_id: subnet.site_id == site_id OR subnet.site_id is None
      5. pod_id:  subnet.pod_id  == pod_id  OR subnet.pod_id  is None
    """
    nets = project_networks(pid)
    results = []
    for net in nets:
        net_family = net.get('family')
        if net_family is None:
            # Legacy: infer family from CIDR
            try:
                net_family = ipaddress.ip_network(net['cidr'], strict=False).version
            except ValueError:
                continue
        if net_family != family:
            continue
        # VRF must match exactly (null == null is a valid match)
        if net.get('vrf_id') != vrf_id:
            continue
        # All required labels must be present
        net_labels = set(net.get('labels', get_network_labels(net['id'])))
        if not label_set.issubset(net_labels):
            continue
        # Site/pod: subnet's value must equal the filter OR be None (more general)
        if site_id is not None and net.get('site_id') not in (site_id, None):
            continue
        if pod_id is not None and net.get('pod_id') not in (pod_id, None):
            continue
        results.append(net)
    return results


def _delete_network_data(nid):
    """Delete all data associated with a network, including its IP records and label associations."""
    for ip_str in r.smembers(net_ips_key(nid)):
        r.delete(ip_key(ip_str))
    for label in r.smembers(net_labels_key(nid)):
        r.srem(label_nets_key(label), nid)
    r.delete(net_ips_key(nid))
    r.delete(net_labels_key(nid))
    r.delete(net_bitmap_key(nid))
    r.delete(net_key(nid))
    r.srem(NETWORKS_INDEX, nid)

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Dashboard & Projects
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/dashboard')
def dashboard():
    """Render the Visual Dashboard & Analytics."""
    # 1. Global IP Utilization
    summary = global_pool_summary()
    ip_stats = {
        'total_capacity': summary['total_ips'],
        'allocated': summary['alloc_ips'],
        'pending': summary['pending'],
        'free': summary['free_ips']
    }

    # 2. Top 5 Projects by Subnets
    projects = all_projects()
    project_list = []
    for p in projects:
        nets = project_networks(p['id'])
        project_list.append({
            'name': p['name'],
            'subnet_count': len(nets),
            'id': p['id']
        })
    top_projects = sorted(project_list, key=lambda x: x['subnet_count'], reverse=True)[:5]

    # 3. Rack Occupancy & Heavy/Power Racks
    all_racks = []
    for p in projects:
        racks = project_instances(p['id'], category='rack')
        for r_inst in racks:
            slots = get_rack_slots(r_inst['id'])
            tmpl = r_inst.get('template')
            rack_u = int(tmpl['u_size']) if tmpl else 42

            used_u = 0
            total_power = 0.0
            total_weight = 0.0
            for slot in slots:
                inst = get_hw_instance(slot['instance_id'])
                if inst:
                    t = get_hw_template(inst['template_id'])
                    if t:
                        u_size = int(t.get('u_size', 1))
                        used_u += u_size
                        total_power += float(t.get('power_w', 0) or 0)
                        total_weight += float(t.get('weight_kg', 0) or 0)

            all_racks.append({
                'asset_tag': r_inst.get('asset_tag') or r_inst['id'],
                'used_u': used_u,
                'total_u': rack_u,
                'power_w': total_power,
                'max_power_w': float(tmpl.get('max_power_w', 0) or 0) if tmpl else 0,
                'weight_kg': total_weight,
                'max_weight_kg': float(tmpl.get('max_weight_kg', 0) or 0) if tmpl else 0,
                'project_name': p['name']
            })

    total_u_capacity = sum(r['total_u'] for r in all_racks)
    total_u_used = sum(r['used_u'] for r in all_racks)

    total_power_capacity = sum(r['max_power_w'] for r in all_racks)
    total_power_used = sum(r['power_w'] for r in all_racks)

    rack_stats = {
        'total_u': total_u_capacity,
        'used_u': total_u_used,
        'free_u': total_u_capacity - total_u_used,
        'total_power_cap': total_power_capacity,
        'used_power': total_power_used
    }

    top_power_racks = sorted(all_racks, key=lambda x: x['power_w'], reverse=True)[:5]
    top_heavy_racks = sorted(all_racks, key=lambda x: x['weight_kg'], reverse=True)[:5]

    # 4. Global Health Alerts
    from health_logic import check_project_health
    all_health_issues = []
    for p in projects:
        project_issues = check_project_health(p['id'])
        for issue in project_issues:
            issue['project_name'] = p['name']
            issue['project_id'] = p['id']
            all_health_issues.append(issue)
    all_health_issues.sort(key=lambda x: 0 if x['severity'] == 'error' else 1)

    # 5. Summary Cards
    hw_count = r.scard(HW_INST_INDEX)
    total_subnets = r.scard(NETWORKS_INDEX)

    # 6. Pending checklists across all projects
    from checks_logic import project_checklists as _proj_cls
    pending_checklists = []
    for proj in projects:
        for cl in _proj_cls(proj['id']):
            if cl.get('status') not in ('draft', 'in-progress'):
                continue
            n = sum(1 for c in cl.get('checks', []) if c['status'] == 'pending')
            if n:
                pending_checklists.append({
                    'cid':              cl['id'],
                    'project_name':     proj['name'],
                    'deployment_label': cl['deployment_label'],
                    'phase':            cl['phase'],
                    'pending':          n,
                })

    return render_template('dashboard.html',
                           ip_stats=ip_stats,
                           top_projects=top_projects,
                           rack_stats=rack_stats,
                           top_power_racks=top_power_racks,
                           top_heavy_racks=top_heavy_racks,
                           active_projects=len(projects),
                           total_subnets=total_subnets,
                           total_hw_instances=hw_count,
                           health_issues=all_health_issues[:20],
                           pending_checklists=pending_checklists)

@ipam_bp.route('/')
def index():
    """Render the IPAM dashboard with project summaries and global stats."""
    projects = sorted(all_projects(), key=lambda p: p['name'])
    for p in projects:
        nets = project_networks(p['id'])
        p['subnet_count'] = len(nets)
        try:
            legacy = p.get('legacy_supernet') or p.get('supernet')
            alloc  = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
            if legacy:
                parent = ipaddress.ip_network(legacy, strict=False)
                p['utilization'] = round((alloc / parent.num_addresses) * 100, 1)
            else:
                p['utilization'] = 0
        except (ValueError, TypeError):
            p['utilization'] = 0
    return render_template('index.html', projects=projects,
                           global_labels=global_labels(),
                           global_summary=global_pool_summary())


@ipam_bp.route('/overview')
def overview():
    """Render a detailed global overview of IP usage and pools."""
    return render_template('overview.html', summary=global_pool_summary())


@ipam_bp.route('/labels', methods=['GET', 'POST'])
@editor_required
def manage_global_labels():
    """Manage global labels via web interface."""
    if request.method == 'POST':
        action = request.form.get('action')
        label  = request.form.get('label', '').strip()
        if not label:
            flash('Label cannot be empty.', 'danger')
        elif action == 'add':
            add_global_label(label)
            flash(f'Global label "{label}" added.', 'success')
        elif action == 'delete':
            remove_global_label(label)
            flash(f'Global label "{label}" removed.', 'info')
        return redirect(url_for('ipam.manage_global_labels'))
    return render_template('global_labels.html', labels=global_labels())


@ipam_bp.route('/projects/add', methods=['GET', 'POST'])
@editor_required
def add_project():
    """Add a new project. Customer is required; supernet is optional (stored as legacy_supernet)."""
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        customer_id = request.form.get('customer_id', '').strip()
        supernet    = request.form.get('supernet', '').strip()

        supernet_ok = (not supernet) or validate_ip_interface(supernet)
        errors = form_errors(
            ('name',     bool(name),     'Project name is required.'),
            ('supernet', supernet_ok,    'Invalid supernet CIDR.'),
        )
        if errors:
            from customer import all_customers
            return render_template('project_form.html', errors=errors,
                                   form_values=request.form,
                                   customers=all_customers())
        # Warn (don't block) when no customer is assigned
        if not customer_id:
            flash('No customer assigned. Assign a customer to use VRFs and service definitions.', 'warning')

        proj = {
            'id':          new_id(),
            'name':        name,
            'description': request.form.get('description', ''),
            'customer_id': customer_id,
        }
        if supernet:
            proj['legacy_supernet'] = supernet

        save_project(proj)
        r.sadd(f'customer:{customer_id}:projects', proj['id'])
        flash(f'Project "{proj["name"]}" created.', 'success')
        return redirect(url_for('ipam.project_detail', pid=proj['id']))

    from customer import all_customers
    return render_template('project_form.html', errors={}, form_values={},
                           customers=all_customers())


@ipam_bp.route('/projects/<pid>')
def project_detail(pid):
    """Render the detail page for a specific project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    nets      = sorted(project_networks(pid), key=lambda n: ipaddress.ip_network(n['cidr']))
    summary   = project_pool_summary(pid)
    labels    = available_labels_for_project(pid)
    templates = available_templates_for_project(pid)
    return render_template('project_detail.html', proj=proj, nets=nets,
                           summary=summary, labels=labels, templates=templates)


@ipam_bp.route('/projects/<pid>/delete', methods=['POST'])
@editor_required
def delete_project(pid):
    """Delete a project and all its associated networks and data."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    for nid in list(r.smembers(project_nets_key(pid))):
        _delete_network_data(nid)
    r.delete(project_nets_key(pid))
    r.delete(project_labels_key(pid))
    for tid in list(r.smembers(project_templates_key(pid))):
        delete_template(tid)
    r.delete(project_templates_key(pid))
    r.delete(project_key(pid))
    r.srem(PROJECTS_INDEX, pid)
    flash(f'Project "{proj["name"]}" deleted.', 'info')
    return redirect(url_for('ipam.index'))


@ipam_bp.route('/projects/<pid>/labels', methods=['GET', 'POST'])
@editor_required
def manage_project_labels(pid):
    """Manage labels specific to a project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        action = request.form.get('action')
        label  = request.form.get('label', '').strip()
        if not label:
            flash('Label cannot be empty.', 'danger')
        elif action == 'add':
            if r.sismember(GLOBAL_LABELS, label):
                flash(f'"{label}" already exists as a global label.', 'warning')
            else:
                add_project_label(pid, label)
                flash(f'Project label "{label}" added.', 'success')
        elif action == 'delete':
            remove_project_label(pid, label)
            flash(f'Project label "{label}" removed.', 'info')
        return redirect(url_for('ipam.manage_project_labels', pid=pid))
    return render_template('project_labels.html', proj=proj,
                           proj_labels=project_labels(pid), global_labels=global_labels())

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Subnet templates
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/templates')
def list_templates():
    """List all global subnet templates."""
    return render_template('templates_list.html',
                           global_tmpl=global_templates(), project_tmpl=[])


@ipam_bp.route('/templates/add',                    methods=['GET', 'POST'])
@ipam_bp.route('/projects/<pid>/templates/add',     methods=['GET', 'POST'])
@editor_required
def add_template(pid=None):
    """Add a new subnet template, optionally scoped to a project."""
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Template name is required.', 'danger')
            return redirect(request.url)
        rules_json = request.form.get('rules_json', '[]').strip()
        try:
            rules = json.loads(rules_json)
            if not isinstance(rules, list):
                raise ValueError('rules must be a JSON array')
            _validate_rules(rules)
        except (json.JSONDecodeError, ValueError) as e:
            flash(f'Invalid rules: {e}', 'danger')
            return redirect(request.url)
        tmpl = {'id': new_id(), 'name': name,
                'description': request.form.get('description', ''),
                'rules': rules,
                'scope': 'project' if pid else 'global',
                'project_id': pid or ''}
        save_template(tmpl)
        flash(f'Template "{name}" saved.', 'success')
        return redirect(url_for('ipam.manage_project_templates', pid=pid) if pid
                        else url_for('ipam.list_templates'))
    return render_template('template_form.html', tmpl=None, proj=proj)


@ipam_bp.route('/templates/<tid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_template(tid):
    """Edit an existing subnet template."""
    tmpl = get_template(tid)
    if not tmpl:
        abort(404)
    pid  = tmpl.get('project_id') or None
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        rules_json = request.form.get('rules_json', '[]').strip()
        try:
            rules = json.loads(rules_json)
            _validate_rules(rules)
        except (json.JSONDecodeError, ValueError) as e:
            flash(f'Invalid rules: {e}', 'danger')
            return redirect(url_for('ipam.edit_template', tid=tid))
        tmpl['name']        = request.form.get('name', tmpl['name']).strip()
        tmpl['description'] = request.form.get('description', '')
        tmpl['rules']       = rules
        save_template(tmpl)
        flash(f'Template "{tmpl["name"]}" updated.', 'success')
        return redirect(url_for('ipam.manage_project_templates', pid=pid) if pid
                        else url_for('ipam.list_templates'))
    return render_template('template_form.html', tmpl=tmpl, proj=proj)


@ipam_bp.route('/templates/<tid>/delete', methods=['POST'])
@editor_required
def delete_template_route(tid):
    """Delete a subnet template."""
    tmpl = get_template(tid)
    if not tmpl:
        abort(404)
    pid = tmpl.get('project_id') or None
    delete_template(tid)
    flash(f'Template "{tmpl["name"]}" deleted.', 'info')
    return redirect(url_for('ipam.manage_project_templates', pid=pid) if pid
                    else url_for('ipam.list_templates'))


@ipam_bp.route('/projects/<pid>/templates')
def manage_project_templates(pid):
    """Manage templates for a specific project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    return render_template('project_templates.html', proj=proj,
                           proj_tmpl=project_templates(pid),
                           global_tmpl=global_templates())


@ipam_bp.route('/api/templates/<tid>/preview')
def preview_template(tid):
    """Preview how a template would resolve for a given CIDR."""
    cidr = request.args.get('cidr', '').strip()
    if not cidr:
        return jsonify({'error': 'Provide ?cidr=...'}), 400
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return jsonify({'error': 'Invalid CIDR'}), 400
    tmpl = get_template(tid)
    if not tmpl:
        abort(404)
    resolved = resolve_template_rules(cidr, tmpl['rules'])
    for slot in resolved:
        slot['already_allocated'] = bool(get_ip(slot['ip']))
    return jsonify({'cidr': cidr, 'resolved': resolved})


@ipam_bp.route('/api/templates/preview_inline', methods=['POST'])
def preview_template_inline():
    """Preview a template's rules against a CIDR without saving the template."""
    data  = request.get_json(force=True)
    cidr  = data.get('cidr', '').strip()
    rules = data.get('rules', [])
    if not cidr:
        return jsonify({'error': 'Provide cidr'}), 400
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return jsonify({'error': 'Invalid CIDR'}), 400
    try:
        _validate_rules(rules)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    resolved = resolve_template_rules(cidr, rules)
    for slot in resolved:
        slot['already_allocated'] = bool(get_ip(slot['ip']))
    return jsonify({'cidr': cidr, 'resolved': resolved})


@ipam_bp.route('/networks/<net_id>/template', methods=['GET', 'POST'])
@editor_required
def apply_template(net_id):
    """Apply a subnet template to an existing network."""
    net = get_network(net_id)
    if not net:
        abort(404)
    proj      = get_project(net.get('project_id')) if net.get('project_id') else None
    pid       = net.get('project_id', '')
    templates = available_templates_for_project(pid)
    if request.method == 'POST':
        tid = request.form.get('template_id', '').strip()
        if not tid:
            flash('Please select a template.', 'danger')
            return redirect(url_for('ipam.apply_template', net_id=net_id))
        try:
            pending = set_pending_slots(net_id, tid)
            flash(f'Template assigned. {len(pending)} slot(s) pending confirmation'
                  + (' — review below.' if pending else ' (all IPs already allocated).'),
                  'success' if pending else 'info')
        except ValueError as e:
            flash(str(e), 'danger')
        return redirect(url_for('ipam.network_detail', net_id=net_id))
    selected_tid = request.args.get('tid') or net.get('template_id') or \
                   (templates['global'][0]['id'] if templates['global'] else None)
    preview = []
    if selected_tid:
        tmpl = get_template(selected_tid)
        if tmpl:
            preview = resolve_template_rules(net['cidr'], tmpl['rules'])
            for slot in preview:
                slot['already_allocated'] = bool(get_ip(slot['ip']))
    return render_template('apply_template.html',
                           net=net_stats(net), proj=proj,
                           templates=templates,
                           selected_tid=selected_tid, preview=preview)


@ipam_bp.route('/networks/<net_id>/slots/confirm',     methods=['POST'])
@editor_required
def confirm_slot_route(net_id):
    """Confirm a pending IP slot and allocate it."""
    ip_str = request.form.get('ip', '').strip()
    if ip_str:
        confirm_slot(net_id, ip_str)
        flash(f'{ip_str} confirmed and allocated.', 'success')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/confirm_all', methods=['POST'])
@editor_required
def confirm_all_slots_route(net_id):
    """Confirm all pending IP slots for a network."""
    result = confirm_all_slots(net_id)
    flash(f'{result["created"]} slot(s) confirmed, {result["skipped"]} skipped.', 'success')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/dismiss',     methods=['POST'])
@editor_required
def dismiss_slot_route(net_id):
    """Dismiss a pending IP slot without allocating it."""
    ip_str = request.form.get('ip', '').strip()
    if ip_str:
        dismiss_slot(net_id, ip_str)
        flash(f'{ip_str} slot dismissed.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/dismiss_all', methods=['POST'])
@editor_required
def dismiss_all_slots_route(net_id):
    """Dismiss all pending IP slots for a network."""
    dismiss_all_slots(net_id)
    flash('All pending slots dismissed.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Subnets
# ══════════════════════════════════════════════════════════════════════════════

def _handle_auto_subnet(proj, pid):
    """Handle auto-carving logic for a new subnet (requires legacy_supernet)."""
    supernet = proj.get('legacy_supernet') or proj.get('supernet')
    if not supernet:
        return None, 'Auto-carve requires a supernet. Enter the CIDR manually or assign a supernet to this project.'
    prefix_len = request.form.get('prefix_len', '').strip()
    if not prefix_len or not prefix_len.isdigit():
        return None, 'Please enter a valid prefix length.'
    try:
        return str(carve_next_subnet(supernet, int(prefix_len), pid)), None
    except ValueError as e:
        return None, str(e)

def _handle_manual_subnet(proj, pid):
    """Handle manual CIDR validation for a new subnet."""
    cidr = request.form.get('cidr', '').strip()
    if not validate_ip_interface(cidr):
        return None, f'Invalid CIDR: {cidr}'

    supernet = proj.get('legacy_supernet') or proj.get('supernet')
    try:
        subnet_obj = ipaddress.ip_network(cidr, strict=False)
        if supernet:
            sup_obj = ipaddress.ip_network(supernet, strict=False)
            if not subnet_obj.subnet_of(sup_obj):
                return None, f'{cidr} is not within {supernet}.'
    except (ValueError, TypeError) as e:
        return None, f'Invalid CIDR: {e}'
    for used in used_subnets_in_project(pid):
        if ipaddress.ip_network(cidr, strict=False).overlaps(used):
            return None, f'{cidr} overlaps with {used}.'
    return cidr, None

@ipam_bp.route('/projects/<pid>/subnet/add', methods=['GET', 'POST'])
@editor_required
def add_subnet(pid):
    """Add a new subnet to a project (manual or auto-carve)."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        mode   = request.form.get('mode', 'manual')
        labels = parse_labels(request.form.get('labels', ''))
        name   = request.form.get('name', '').strip()
        tid    = request.form.get('template_id', '').strip() or None
        if mode == 'auto':
            cidr, err = _handle_auto_subnet(proj, pid)
        else:
            cidr, err = _handle_manual_subnet(proj, pid)

        if err:
            flash(err, 'danger')
            return redirect(url_for('ipam.add_subnet', pid=pid))

        try:
            net_family = ipaddress.ip_network(cidr, strict=False).version
        except ValueError:
            net_family = 4
        net = {
            'id':          new_id(),
            'name':        name or cidr,
            'cidr':        cidr,
            'family':      net_family,
            'description': request.form.get('description', ''),
            'vlan':        request.form.get('vlan') or '',
            'project_id':  pid,
            'vrf_id':      request.form.get('vrf_id', '').strip() or None,
            'site_id':     request.form.get('site_id', '').strip() or None,
            'pod_id':      request.form.get('pod_id', '').strip() or None,
        }
        save_network(net)
        r.sadd(project_nets_key(pid), net['id'])
        add_labels_to_network(net['id'], labels)
        sync_net_bitmap(net['id'])
        if tid:
            try:
                pending = set_pending_slots(net['id'], tid)
                flash(f'Subnet {cidr} added. Template assigned — '
                      f'{len(pending)} slot(s) pending confirmation.', 'success')
            except ValueError as e:
                flash(f'Subnet added but template assignment failed: {e}', 'warning')
        else:
            flash(f'Subnet {cidr} added.', 'success')
        return redirect(url_for('ipam.project_detail', pid=pid))
    from vrf import vrfs_for_project  # pylint: disable=import-outside-toplevel
    from ne import project_sites, project_pods  # pylint: disable=import-outside-toplevel
    return render_template('subnet_form.html', proj=proj,
                           labels=available_labels_for_project(pid),
                           templates=available_templates_for_project(pid),
                           vrfs=vrfs_for_project(pid),
                           sites=project_sites(pid),
                           pods=project_pods(pid))


@ipam_bp.route('/projects/<pid>/subnet/bulk', methods=['GET', 'POST'])
@editor_required
def bulk_add_subnets(pid):
    """Bulk create multiple subnets in a project (auto-carve)."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        try:
            subnet_requests = request.get_json(force=True).get('subnets', [])
        except Exception:
            return jsonify({'error': 'Invalid JSON'}), 400
        results, errors = [], []
        for req in subnet_requests:
            labels = [l.strip() for l in req.get('labels', []) if str(l).strip()]
            tid    = req.get('template_id') or None
            try:
                bulk_supernet = proj.get('legacy_supernet') or proj.get('supernet')
                if not bulk_supernet:
                    raise ValueError('No supernet on project; add subnets manually.')
                cidr = str(carve_next_subnet(bulk_supernet, int(req['prefix_len']), pid))
                try:
                    net_family = ipaddress.ip_network(cidr, strict=False).version
                except ValueError:
                    net_family = 4
                net  = {'id': new_id(), 'name': req.get('name', '') or cidr, 'cidr': cidr,
                        'family': net_family,
                        'description': req.get('description', ''),
                        'vlan': req.get('vlan', ''), 'project_id': pid}
                save_network(net)
                r.sadd(project_nets_key(pid), net['id'])
                add_labels_to_network(net['id'], labels)
                sync_net_bitmap(net['id'])
                pending_count = 0
                if tid:
                    pending = set_pending_slots(net['id'], tid)
                    pending_count = len(pending)
                results.append({'cidr': cidr, 'name': net['name'],
                                'labels': labels, 'pending_slots': pending_count})
            except (ValueError, KeyError) as e:
                errors.append({'request': req, 'error': str(e)})
        return jsonify({'allocated': results, 'errors': errors})
    return render_template('bulk_form.html', proj=proj,
                           labels=available_labels_for_project(pid),
                           templates=available_templates_for_project(pid))


@ipam_bp.route('/networks/<net_id>/edit', methods=['GET', 'POST'])
@editor_required
def edit_network(net_id):
    """Edit subnet metadata."""
    net = get_network(net_id)
    if not net:
        abort(404)
    pid = net.get('project_id')
    if request.method == 'POST':
        net['name']        = request.form.get('name', net['name']).strip()
        net['description'] = request.form.get('description', '')
        net['vlan']        = request.form.get('vlan') or ''
        net['vrf_id']      = request.form.get('vrf_id', '').strip() or None
        net['site_id']     = request.form.get('site_id', '').strip() or None
        net['pod_id']      = request.form.get('pod_id', '').strip() or None
        # Ensure family is stored if not already set
        if 'family' not in net:
            try:
                net['family'] = ipaddress.ip_network(net['cidr'], strict=False).version
            except ValueError:
                pass
        new_labels = parse_labels(request.form.get('labels', ''))
        old_labels = get_network_labels(net_id)
        remove_labels_from_network(net_id, [l for l in old_labels if l not in new_labels])
        add_labels_to_network(net_id,      [l for l in new_labels  if l not in old_labels])
        save_network(net)
        flash('Subnet updated.', 'success')
        return redirect(url_for('ipam.project_detail', pid=pid) if pid else url_for('ipam.index'))
    return render_template('subnet_edit_form.html', net=net_stats(net),
                           current_labels=','.join(get_network_labels(net_id)),
                           labels=available_labels_for_project(pid) if pid
                                  else {'global': global_labels(), 'project': []},
                           templates=available_templates_for_project(pid) if pid
                                     else {'global': global_templates(), 'project': []})


@ipam_bp.route('/networks/<net_id>/delete', methods=['POST'])
@editor_required
def delete_network(net_id):
    """Delete a subnet."""
    net = get_network(net_id)
    if not net:
        abort(404)
    pid = net.get('project_id')
    if pid:
        r.srem(project_nets_key(pid), net_id)
    _delete_network_data(net_id)
    flash(f'Subnet {net["cidr"]} deleted.', 'info')
    return redirect(url_for('ipam.project_detail', pid=pid) if pid else url_for('ipam.index'))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Network / IP detail
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/networks/<net_id>')
def network_detail(net_id):
    """View subnet details and allocated IPs."""
    net = get_network(net_id)
    if not net:
        abort(404)
    net  = net_stats(net)
    proj = get_project(net.get('project_id')) if net.get('project_id') else None
    pid  = net.get('project_id', '')
    net['labelled'] = [{'name': l, 'scope': label_scope(l, pid)} for l in net['labels']]
    real_addrs    = network_addresses(net_id)
    pending_slots = net.get('pending_slots', [])
    for a in real_addrs:
        a['phantom'] = False
    phantoms = [
        {'ip': s['ip'], 'hostname': '', 'description': s['role'],
         'status': s['status'], 'phantom': True, 'from_template': True,
         'network_id': net_id}
        for s in pending_slots
        if s['ip'] not in {a['ip'] for a in real_addrs}
    ]
    all_addrs = sorted(real_addrs + phantoms,
                       key=lambda a: ipaddress.ip_address(a['ip']))
    return render_template('network_detail.html', net=net, addresses=all_addrs,
                           proj=proj, pending_count=len(pending_slots))


@ipam_bp.route('/networks/<net_id>/ip/add', methods=['GET', 'POST'])
@editor_required
def add_ip(net_id):
    """Manually allocate an IP address in a subnet."""
    net = get_network(net_id)
    if not net:
        abort(404)
    if request.method == 'POST':
        ip_str = request.form['ip'].strip()
        if not validate_ip_interface(ip_str):
            flash(f'Invalid IP address: {ip_str}', 'danger')
            return redirect(url_for('ipam.add_ip', net_id=net_id))

        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            flash('Invalid IP address.', 'danger')
            return redirect(url_for('ipam.add_ip', net_id=net_id))
        if ip_obj not in ipaddress.ip_network(net['cidr'], strict=False):
            flash(f'{ip_str} is not within {net["cidr"]}.', 'danger')
            return redirect(url_for('ipam.add_ip', net_id=net_id))

        addr_data = {
            'ip': ip_str,
            'hostname': request.form.get('hostname', ''),
            'description': request.form.get('description', ''),
            'status': request.form.get('status', 'allocated'),
            'network_id': net_id
        }

        if not claim_ip_atomic(addr_data, net['cidr']):
            flash(f'{ip_str} is already allocated or was just snatched.', 'warning')
            return redirect(url_for('ipam.add_ip', net_id=net_id))

        net_rec = get_network(net_id)
        is_pending = any(s['ip'] == ip_str for s in net_rec.get('pending_slots', []))
        if is_pending:
            dismiss_slot(net_id, ip_str)

        flash(f'{ip_str} allocated.', 'success')
        return redirect(url_for('ipam.network_detail', net_id=net_id))
    return render_template('ip_form.html', net=net)


@ipam_bp.route('/ip/<path:ip_str>/edit', methods=['GET', 'POST'])
@editor_required
def edit_ip(ip_str):
    """Edit IP address metadata."""
    addr = get_ip(ip_str)
    if not addr:
        abort(404)
    net = get_network(addr['network_id'])
    if request.method == 'POST':
        if not validate_ip_interface(ip_str):
            flash(f'Invalid IP address record: {ip_str}', 'danger')
            return redirect(url_for('ipam.network_detail', net_id=addr['network_id']))

        addr['hostname']    = request.form.get('hostname', '')
        addr['description'] = request.form.get('description', '')
        addr['status']      = request.form.get('status', 'allocated')
        r.set(ip_key(ip_str), json.dumps(addr))
        flash(f'{ip_str} updated.', 'success')
        return redirect(url_for('ipam.network_detail', net_id=addr['network_id']))
    return render_template('ip_form.html', net=net, addr=addr)


@ipam_bp.route('/ip/<path:ip_str>/delete', methods=['POST'])
@editor_required
def delete_ip(ip_str):
    """Release an allocated IP address."""
    addr = get_ip(ip_str)
    if not addr:
        abort(404)
    net_id = addr['network_id']
    net = get_network(net_id)
    if net:
        offset = get_ip_offset(net['cidr'], ip_str)
        r.setbit(net_bitmap_key(net_id), offset, 0)

    r.delete(ip_key(ip_str))
    r.srem(net_ips_key(net_id), ip_str)
    publish_ip_update('release', addr)
    flash(f'{ip_str} released.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))


def find_next_free_ip(net_id: str) -> str:
    """
    High-performance search for the next available IP in a network using Bitmaps.
    Returns the IP address string or None if full.
    """
    net = get_network(net_id)
    if not net:
        return None

    bkey = net_bitmap_key(net_id)
    if not r.exists(bkey):
        sync_net_bitmap(net_id)

    offset = r.bitpos(bkey, 0)
    net_obj = ipaddress.ip_network(net['cidr'], strict=False)

    if offset < 0 or offset >= net_obj.num_addresses:
        # Subnet seems full. Trigger lazy cleanup of expired TTLs and retry.
        network_addresses(net_id)
        offset = r.bitpos(bkey, 0)
        if offset < 0 or offset >= net_obj.num_addresses:
            return None

    ip_str = get_ip_at_offset(net['cidr'], offset)
    pending = {s['ip'] for s in net.get('pending_slots', [])}

    if ip_str in pending:
        # Fallback to BITPOS with bit-offset if pending slots interfere
        for _ in range(100):
            offset = r.bitpos(bkey, 0, offset + 1, -1, "BIT")
            if offset < 0 or offset >= net_obj.num_addresses:
                return None
            ip_str = get_ip_at_offset(net['cidr'], offset)
            if ip_str not in pending:
                return ip_str
        return None

    return ip_str

@ipam_bp.route('/api/networks/<net_id>/next')
def next_available(net_id):
    """API endpoint to find the next available IP in a network using Bitmaps."""
    ip_str = find_next_free_ip(net_id)
    if ip_str:
        return jsonify({'next_available': ip_str})
    return jsonify({'error': 'No available addresses'}), 404

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Pool query & Search
# ══════════════════════════════════════════════════════════════════════════════

def _pool_query(query_labels: list) -> dict:
    """Find all networks matching a set of labels and return summary stats."""
    if not query_labels:
        return {}
    label_keys   = [label_nets_key(l) for l in query_labels]
    matching_ids = r.smembers(label_keys[0]) if len(label_keys) == 1 else r.sinter(*label_keys)
    nets = sorted(
        [net_stats(n) for n in (get_network(nid) for nid in matching_ids) if n],
        key=lambda n: ipaddress.ip_network(n['cidr']))
    return {
        'query_labels': query_labels,
        'subnet_count': len(nets),
        'total_ips':    sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets),
        'pools':        pool_by_label_set(nets),
        'subnets':      nets,
    }


@ipam_bp.route('/api/pool')
def pool_api():
    """API endpoint to query IP pools by labels."""
    labels_param = request.args.get('labels', '')
    if not labels_param:
        return jsonify({'error': 'Provide ?labels=LabelA,LabelB'}), 400
    return jsonify(_pool_query([l.strip() for l in labels_param.split(',') if l.strip()]))


@ipam_bp.route('/pool')
def pool_ui():
    """Render the pool query tool."""
    labels_param = request.args.get('labels', '')
    query_labels = [l.strip() for l in labels_param.split(',') if l.strip()]
    result = _pool_query(query_labels) if query_labels else None
    return render_template('pool.html', global_labels=global_labels(),
                           result=result, labels_param=labels_param)


@ipam_bp.route('/api/networks/export')
def export_networks():
    """Export all subnets to a CSV file."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'CIDR', 'VLAN', 'Project', 'Template'])

    for nid in r.smembers(NETWORKS_INDEX):
        net = get_network(nid)
        if not net:
            continue
        proj = get_project(net.get('project_id'))
        tmpl = get_template(net.get('template_id'))
        writer.writerow([
            net['id'],
            net['name'],
            net['cidr'],
            net.get('vlan', ''),
            proj['name'] if proj else 'Unknown',
            tmpl['name'] if tmpl else ''
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=subnets.csv'
    response.headers['Content-type'] = 'text/csv'
    return response


def _redisearch_ips(q):
    """Perform a high-performance search using RediSearch (FT.SEARCH)."""
    try:
        # Check if index exists, if not create it
        try:
            r.ft("idx:ip").info()
        except Exception:  # RediSearch index may not exist yet
            try:
                from redis.commands.search.field import TextField  # type: ignore[import]
                from redis.commands.search.indexDefinition import IndexDefinition, IndexType  # type: ignore[import]
            except ImportError:
                return None
            schema = (
                TextField("$.ip", as_name="ip"),
                TextField("$.hostname", as_name="hostname"),
                TextField("$.description", as_name="description"),
            )
            r.ft("idx:ip").create_index(schema, definition=IndexDefinition(prefix=["ip:"], index_type=IndexType.JSON))

        # Search
        # ft().search returns a Result object
        search_res = r.ft("idx:ip").search(q)
        results = []
        for doc in search_res.docs:
            # RediSearch with JSON returns the JSON string in the 'json' field
            addr = json.loads(doc.json)
            addr['network'] = get_network(addr['network_id'])
            results.append(addr)
        return results
    except Exception:  # pylint: disable=broad-except
        # Fallback to standard scan if RediSearch is not available
        return None

@ipam_bp.route('/search')
def search():
    """Search for IP records, HW instances, and Projects."""
    q = request.args.get('q', '').strip().lower()
    results = {'ips': [], 'hw': [], 'projects': []}
    if not q:
        return render_template('search.html', results=results, q=q)

    # 1. Search IPs (Try RediSearch first, then fallback)
    ip_results = _redisearch_ips(q)
    if ip_results is not None:
        results['ips'] = ip_results
    else:
        for key in r.scan_iter('ip:*'):
            raw = r.get(key)
            if not raw:
                continue
            addr = json.loads(raw)
            if (q in addr.get('ip', '').lower() or
                q in addr.get('hostname', '').lower() or
                q in addr.get('description', '').lower()):
                addr['network'] = get_network(addr['network_id'])
                results['ips'].append(addr)
    results['ips'].sort(key=lambda a: ipaddress.ip_address(a['ip']))

    # 2. Search HW Instances
    for iid in r.smembers(HW_INST_INDEX):
        inst = get_hw_instance(iid)
        if not inst:
            continue
        tmpl = get_hw_template(inst['template_id'])
        tmpl_name = tmpl['name'].lower() if tmpl else ''
        if (q in inst.get('asset_tag', '').lower() or
            q in inst.get('serial', '').lower() or
            q in tmpl_name):
            inst['template'] = tmpl
            results['hw'].append(inst)
    results['hw'].sort(key=lambda i: i.get('asset_tag', ''))

    # 3. Search Projects
    for pid in r.smembers(PROJECTS_INDEX):
        proj = get_project(pid)
        if not proj:
            continue
        if (q in proj.get('name', '').lower() or
            q in proj.get('description', '').lower() or
            q in proj.get('id', '').lower()):
            results['projects'].append(proj)
    results['projects'].sort(key=lambda p: p.get('name', ''))

    return render_template('search.html', results=results, q=q)


# ══════════════════════════════════════════════════════════════════════════════
# Validation API — /api/validate/<type>
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/api/validate/cidr')
def api_validate_cidr():
    """Validate a CIDR string via API."""
    val = request.args.get('v', '').strip()
    return jsonify({'ok': True} if validate_cidr(val) else {'ok': False, 'error': 'Invalid CIDR'})


@ipam_bp.route('/api/validate/ip-interface')
def api_validate_ip_interface():
    """Validate an IP interface string via API."""
    val = request.args.get('v', '').strip()
    return jsonify({'ok': True} if validate_ip_interface(val) else {'ok': False, 'error': 'Invalid IP/prefix'})


@ipam_bp.route('/api/validate/prefix-in-supernet')
def api_validate_prefix_in_supernet():
    """Validate if a prefix is within a supernet via API."""
    val      = request.args.get('v', '').strip()
    supernet = request.args.get('supernet', '').strip()
    if not validate_cidr(val):
        return jsonify({'ok': False, 'error': 'Invalid CIDR'})
    if supernet and validate_cidr(supernet):
        try:
            net = ipaddress.ip_network(val, strict=False)
            sup = ipaddress.ip_network(supernet, strict=False)
            if not net.subnet_of(sup):
                return jsonify({'ok': False, 'error': f'Not within {supernet}'})
        except (ValueError, TypeError):
            pass
    return jsonify({'ok': True})


# ══════════════════════════════════════════════════════════════════════════════
# Jobs polling API — /api/jobs/<job_id>
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/api/jobs/<job_id>')
def api_get_job(job_id):
    """Get job status and result via API."""
    from core.jobs import get_job
    job = get_job(job_id)
    if not job:
        abort(404)
    return jsonify(job)


# ══════════════════════════════════════════════════════════════════════════════
# Project impact + subnet bulk delete
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/api/projects/<pid>/impact')
def project_impact(pid):
    """Calculate the impact of deleting a project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    nets = project_networks(pid)
    cascades = []
    if nets:
        total_ips = sum(r.scard(net_ips_key(nid)) for nid in (n['id'] for n in nets))
        n = len(nets)
        cascades.append({'kind': 'delete', 'count': n,
                         'what': f'{n} subnet{"s" if n != 1 else ""} ({total_ips} IPs) will be deleted'})
    return jsonify({'label': proj['name'], 'cascades': cascades})


@ipam_bp.route('/projects/<pid>/subnets/bulk-delete', methods=['POST'])
@editor_required
def bulk_delete_subnets(pid):
    """Bulk delete subnets from a project."""
    ids = (request.get_json(silent=True, force=True) or {}).get('ids', [])
    for nid in ids:
        net = get_network(nid)
        if net and net.get('project_id') == pid:
            _delete_network_data(nid)
            r.srem(project_nets_key(pid), nid)
    return jsonify({'deleted': len(ids)})
