"""
Hardware Management logic and data access helpers.
"""

import hashlib
import json
import re as _re
from db import r, new_id, redis_get, redis_save, redis_delete, redis_all

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

CATEGORIES = ('server', 'switch', 'router', 'pdu', 'rack', 'cable', 'other')
FORM_FACTORS = ('19"', '21"', 'OCP', 'desktop', 'tower', '0U', 'N/A')
PORT_TYPES = ('data', 'mgmt', 'power', 'console', 'usb')
CABLE_TYPES = ('DAC', 'AOC', 'fiber-patch', 'copper-patch', 'power', 'console', 'other')
SEVERITIES = ('error', 'warning', 'info')

PORT_EXPAND_CAP = 1024


def expand_port_pattern(name: str, count: int) -> tuple[list[str] | None, str | None]:
    """
    Expand a port-name pattern into a list of concrete names.

    Rules (must match templates/hw/template_form.html::expandPattern):
      - 'eth{0..7}'        → eth0, eth1, …, eth7 (range wins, count ignored,
                              zero-padding preserved from the literal digits)
      - 'gi-{N}/0', count=3 → gi-0/0, gi-1/0, gi-2/0
      - 'eth', count=4     → eth0, eth1, eth2, eth3 (no pattern: index appended)
      - 'iLO', count=1     → iLO (no expansion)

    Returns (names, None) on success or (None, error_message) on failure.
    Caps result at PORT_EXPAND_CAP.
    """
    name = (name or '').strip()
    if not name:
        return None, 'Port name is required'

    m = _re.search(r'\{(\d+)\.\.(\d+)\}', name)
    if m:
        start_s, end_s = m.group(1), m.group(2)
        start, end = int(start_s), int(end_s)
        if end < start:
            return None, 'Range end must be >= start'
        if end - start + 1 > PORT_EXPAND_CAP:
            return None, f'Range too large (max {PORT_EXPAND_CAP})'
        width  = len(start_s)
        prefix = name[:m.start()]
        suffix = name[m.end():]
        return [f'{prefix}{str(i).zfill(width)}{suffix}'
                for i in range(start, end + 1)], None

    n = int(count) if count else 1
    if n < 1:
        n = 1
    if n > PORT_EXPAND_CAP:
        return None, f'Count too large (max {PORT_EXPAND_CAP})'
    if '{N}' in name:
        return [name.replace('{N}', str(i)) for i in range(n)], None
    if n == 1:
        return [name], None
    return [f'{name}{i}' for i in range(n)], None


def expand_template_ports(tmpl: dict) -> dict:
    """
    Expand any port rows with count>1 or pattern syntax into individual rows.

    Idempotent: a template whose rows already all have count==1 and no '{' in
    any name is returned unchanged.

    Per-row id rule:
      - count==1 and no range: row passes through unchanged.
      - otherwise: first sub-port keeps the original id; sub-ports n>=1 get
        f'{original_id}-{n}'.

    Raises ValueError on invalid patterns so save_hw_template can surface a
    flash error.
    """
    rows = tmpl.get('ports') or []
    out = []
    for row in rows:
        name = (row.get('name') or '').strip()
        count = int(row.get('count') or 1)
        if count <= 1 and '{' not in name:
            out.append({**row, 'count': 1})
            continue

        names, err = expand_port_pattern(name, count)
        if err:
            raise ValueError(f'Port "{name}": {err}')
        orig_id = row.get('id') or ''
        for i, n in enumerate(names):
            sub_id = orig_id if i == 0 else f'{orig_id}-{i}'
            out.append({**row, 'id': sub_id, 'name': n, 'count': 1})
    return {**tmpl, 'ports': out}


# Default connectors seeded on first run
DEFAULT_CONNECTORS = [
    'RJ45', 'SFP', 'SFP+', 'SFP28', 'QSFP28', 'QSFP-DD',
    'IEC-C13', 'IEC-C14', 'IEC-C19', 'IEC-C20',
    'NEMA-515', 'USB-A', 'USB-C', 'RJ11', 'DB9',
]

# Default compatibility matrix  {connector → set of compatible connectors}
# Symmetric: if A compat B then B compat A (enforced in get_compat)
DEFAULT_COMPAT = {
    'RJ45': {'RJ45'},
    'SFP': {'SFP', 'SFP+', 'SFP28'},
    'SFP+': {'SFP+', 'SFP', 'SFP28'},
    'SFP28': {'SFP28', 'SFP+', 'SFP'},
    'QSFP28': {'QSFP28', 'QSFP-DD'},
    'QSFP-DD': {'QSFP-DD', 'QSFP28'},
    'IEC-C13': {'IEC-C13', 'IEC-C14', 'IEC-C19', 'IEC-C20'},
    'IEC-C14': {'IEC-C14', 'IEC-C13'},
    'IEC-C19': {'IEC-C19', 'IEC-C20', 'IEC-C13'},
    'IEC-C20': {'IEC-C20', 'IEC-C19'},
    'NEMA-515': {'NEMA-515'},
    'USB-A': {'USB-A', 'USB-C'},
    'USB-C': {'USB-C', 'USB-A'},
    'RJ11': {'RJ11'},
    'DB9': {'DB9'},
}

# ══════════════════════════════════════════════════════════════════════════════
# Redis key helpers
# ══════════════════════════════════════════════════════════════════════════════

HW_CONNECTORS = 'hw:connectors'
HW_TMPL_INDEX = 'hw:templates:index'
HW_INST_INDEX = 'hw:instances:index'
HW_CABLE_INDEX = 'hw:cables:index'


def _compat_key(conn):
    """Key for connector compatibility set."""
    return f'hw:compat:{conn}'


def _tmpl_key(tid):
    """Key for hardware template JSON."""
    return f'hw:template:{tid}'


def _inst_key(iid):
    """Key for hardware instance JSON."""
    return f'hw:instance:{iid}'


def _rack_slots_key(iid):
    """Key for rack occupancy list."""
    return f'hw:rack:{iid}:slots'


def _cable_key(cid):
    """Key for cable instance JSON."""
    return f'hw:cable:{cid}'


def _bom_key(pid):
    """Key for project Bill of Materials."""
    return f'project:{pid}:bom'


def _validation_key(pid):
    """Key for cached hardware validation results."""
    return f'project:{pid}:hw:validation'

# ══════════════════════════════════════════════════════════════════════════════
# Connector & compatibility helpers
# ══════════════════════════════════════════════════════════════════════════════

def seed_connectors():
    """Seed default connectors and compat matrix if not yet present."""
    if r.scard(HW_CONNECTORS) == 0:
        for c in DEFAULT_CONNECTORS:
            r.sadd(HW_CONNECTORS, c)
        for conn, compat_set in DEFAULT_COMPAT.items():
            for other in compat_set:
                r.sadd(_compat_key(conn), other)


def all_connectors() -> list:
    """Return all defined connector names, sorted."""
    seed_connectors()
    return sorted(r.smembers(HW_CONNECTORS))


def add_connector(name: str):
    """Define a new connector type."""
    r.sadd(HW_CONNECTORS, name)
    # Self-compatible by default
    r.sadd(_compat_key(name), name)


def remove_connector(name: str):
    """Delete a connector type and its compatibility rules."""
    r.srem(HW_CONNECTORS, name)
    r.delete(_compat_key(name))
    # Remove from all other compat sets
    for conn in r.smembers(HW_CONNECTORS):
        r.srem(_compat_key(conn), name)


def get_compat(conn: str) -> set:
    """Return set of connectors compatible with the given one."""
    return r.smembers(_compat_key(conn))


def set_compat(conn_a: str, conn_b: str, compatible: bool):
    """Update symmetric compatibility between two connectors."""
    if compatible:
        r.sadd(_compat_key(conn_a), conn_b)
        r.sadd(_compat_key(conn_b), conn_a)
    else:
        r.srem(_compat_key(conn_a), conn_b)
        r.srem(_compat_key(conn_b), conn_a)


def connectors_compatible(a: str, b: str) -> bool:
    """Check if two connectors can be mated."""
    return bool(r.sismember(_compat_key(a), b))


def full_compat_matrix() -> dict:
    """Return {conn: [list of compatible connectors]} for all connectors."""
    conns = all_connectors()
    return {c: sorted(get_compat(c)) for c in conns}


# ══════════════════════════════════════════════════════════════════════════════
# Hardware template helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_hw_template(tid):
    """Retrieve a hardware template by ID."""
    return redis_get(_tmpl_key(tid))


def save_hw_template(tmpl):
    """Save hardware template; expand port-name patterns + count before persisting."""
    tmpl = expand_template_ports(tmpl)
    redis_save(_tmpl_key(tmpl['id']), tmpl)
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.sadd(f'project:{tmpl["project_id"]}:hw:templates', tmpl['id'])
    else:
        r.sadd(HW_TMPL_INDEX, tmpl['id'])
    return tmpl


def delete_hw_template(tid):
    """Delete template and remove from indices."""
    tmpl = get_hw_template(tid)
    if not tmpl:
        return
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.srem(f'project:{tmpl["project_id"]}:hw:templates', tid)
    else:
        r.srem(HW_TMPL_INDEX, tid)
    redis_delete(_tmpl_key(tid))


def global_hw_templates(category=None) -> list:
    """List all global hardware templates, optionally filtered by category."""
    tmpls = redis_all(HW_TMPL_INDEX, get_hw_template)
    if category:
        tmpls = [t for t in tmpls if t['category'] == category]
    return sorted(tmpls, key=lambda t: (t['category'], t['name']))


def project_hw_templates(pid, category=None) -> list:
    """List all project-specific hardware templates, optionally filtered by category."""
    key = f'project:{pid}:hw:templates'
    tmpls = redis_all(key, get_hw_template)
    if category:
        tmpls = [t for t in tmpls if t['category'] == category]
    return sorted(tmpls, key=lambda t: (t['category'], t['name']))


def available_hw_templates(pid, category=None) -> dict:
    """Return dict with both global and project-specific templates."""
    return {
        'global': global_hw_templates(category),
        'project': project_hw_templates(pid, category),
    }


def all_hw_templates_for_project(pid, category=None) -> list:
    """Flat list: global + project templates available to a project."""
    av = available_hw_templates(pid, category)
    return av['global'] + av['project']


# ══════════════════════════════════════════════════════════════════════════════
# Bill of Materials helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_bom(pid) -> list:
    """Retrieve project Bill of Materials."""
    return redis_get(_bom_key(pid)) or []


def save_bom(pid, bom: list):
    """Save project Bill of Materials."""
    redis_save(_bom_key(pid), bom)


def bom_with_templates(pid) -> list:
    """Return BoM lines enriched with template data."""
    lines = []
    for item in get_bom(pid):
        tmpl = get_hw_template(item['template_id'])
        lines.append({**item, 'template': tmpl})
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# Hardware instance helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_hw_instance(iid):
    """Retrieve hardware instance by ID."""
    return redis_get(_inst_key(iid))


def save_hw_instance(inst):
    """Save hardware instance and update project index."""
    redis_save(_inst_key(inst['id']), inst)
    r.sadd(HW_INST_INDEX, inst['id'])
    r.sadd(f'project:{inst["project_id"]}:hw:instances', inst['id'])


def write_binding_ip_to_ports(inst: dict, iface_id: str, ip_str: str) -> None:
    """
    Write ip_str into port_overrides for every port in the given iface binding.

    For LAG / active-passive bindings the same IP is shared across all ports;
    each port's entry records shared_with as the list of sibling port_ids on the
    same HW instance, and lag_id when present.  For single-port bindings
    shared_with is empty.  Idempotent: re-running with the same IP just
    overwrites.
    """
    binding = inst.get('iface_bindings', {}).get(iface_id, {})
    ports   = binding.get('ports', [])
    if not ports:
        return
    lag_id  = binding.get('lag_id')

    # Group port_ids by hw_instance_id so shared_with is per-device
    by_iid: dict = {}
    for p in ports:
        by_iid.setdefault(p['hw_instance_id'], []).append(p['port_id'])

    for hw_iid, port_ids in by_iid.items():
        hw_inst = get_hw_instance(hw_iid)
        if not hw_inst:
            continue
        overrides = dict(hw_inst.get('port_overrides', {}))
        for pid in port_ids:
            siblings = [p for p in port_ids if p != pid]
            entry = dict(overrides.get(pid, {}))
            entry['ip']          = ip_str
            entry['shared_with'] = siblings
            if lag_id is not None:
                entry['lag_id'] = lag_id
            overrides[pid] = entry
        save_hw_instance({**hw_inst, 'port_overrides': overrides})


def delete_hw_instance(iid):
    """Delete instance, remove from indices and rack placement."""
    inst = get_hw_instance(iid)
    if not inst:
        return
    r.srem(HW_INST_INDEX, iid)
    r.srem(f'project:{inst["project_id"]}:hw:instances', iid)
    # Remove from rack if placed
    if inst.get('location', {}).get('rack_id'):
        _remove_from_rack(inst['location']['rack_id'], iid)
    # Clean up NE-HW port-bound index for this instance
    bound = r.hgetall(_bound_ports_key(iid))
    for port_id in bound:
        r.delete(_port_bound_key(iid, port_id))
    r.delete(_bound_ports_key(iid))
    redis_delete(_inst_key(iid))


# ── NE-HW port-bound index ────────────────────────────────────────────────────

def _port_bound_key(iid: str, port_id: str) -> str:
    return f'hw:port_bound:{iid}:{port_id}'


def _bound_ports_key(iid: str) -> str:
    return f'hw:instance:{iid}:bound_ports'


def set_port_bound(iid: str, port_id: str,
                   ne_instance_id: str, iface_id: str, bind_mode: str):
    """Record that (iid, port_id) is bound to an NE iface."""
    payload = json.dumps({
        'ne_instance_id': ne_instance_id,
        'iface_id':       iface_id,
        'bind_mode':      bind_mode,
    })
    r.set(_port_bound_key(iid, port_id), ne_instance_id)
    r.hset(_bound_ports_key(iid), port_id, payload)


def clear_port_bound(iid: str, port_id: str):
    """Remove a port-bound index entry."""
    r.delete(_port_bound_key(iid, port_id))
    r.hdel(_bound_ports_key(iid), port_id)


def get_port_bound(iid: str, port_id: str) -> dict | None:
    """Return binding info dict for (iid, port_id) or None if free."""
    raw = r.hget(_bound_ports_key(iid), port_id)
    return json.loads(raw) if raw else None


def hw_instance_bindings(iid: str) -> list:
    """
    Return all NE iface bindings covering ports on this HW instance.
    Each item: {port_id, ne_instance_id, iface_id, bind_mode}.
    Uses the cached hw:instance:{iid}:bound_ports hash — O(1).
    """
    result = []
    for port_id, payload in r.hgetall(_bound_ports_key(iid)).items():
        try:
            info = json.loads(payload)
        except Exception:
            continue
        result.append({
            'port_id':        port_id,
            'ne_instance_id': info.get('ne_instance_id', ''),
            'iface_id':       info.get('iface_id', ''),
            'bind_mode':      info.get('bind_mode', 'single'),
        })
    return result


def project_instances(pid, category=None) -> list:
    """List all instances in a project, optionally filtered by category."""
    key = f'project:{pid}:hw:instances'
    insts = []
    for inst in redis_all(key, get_hw_instance):
        tmpl = get_hw_template(inst['template_id'])
        inst = {**inst, 'template': tmpl}
        if category and tmpl and tmpl['category'] != category:
            continue
        insts.append(inst)
    return sorted(insts, key=lambda i: i.get('asset_tag', ''))


def generate_instances_from_bom_line(pid: str, item: dict) -> list:
    """
    Generate hardware instances for a BoM line item.
    Returns list of created instance records.
    """
    tmpl = get_hw_template(item['template_id'])
    if not tmpl:
        raise ValueError(f'Template {item["template_id"]} not found')
    prefix = item.get('tag_prefix', tmpl['name'][:8].replace(' ', '-'))
    start = int(item.get('tag_start', 1))
    pad = int(item.get('tag_pad', 3))
    qty = int(item.get('qty', 1))
    # Only create the delta: skip asset tags that already exist in the project,
    # so re-running generation is idempotent rather than duplicating instances.
    existing_tags = {inst.get('asset_tag') for inst in project_instances(pid)}
    created = []
    for i in range(qty):
        tag = f'{prefix}-{str(start + i).zfill(pad)}'
        if tag in existing_tags:
            continue
        inst = {
            'id': new_id(),
            'template_id': item['template_id'],
            'project_id': pid,
            'asset_tag': tag,
            'serial': '',
            'status': 'in-stock',
            'site_id': item.get('site_id', ''),
            'location': {},
            'port_overrides': {},  # port_id → {notes, mac, ip}
        }
        save_hw_instance(inst)
        created.append(inst)
    return created


# ══════════════════════════════════════════════════════════════════════════════
# Rack layout helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_rack_slots(rack_iid) -> list:
    """Retrieve occupancy list for a rack."""
    raw = r.get(_rack_slots_key(rack_iid))
    return json.loads(raw) if raw else []


def save_rack_slots(rack_iid, slots: list):
    """Save occupancy list for a rack."""
    r.set(_rack_slots_key(rack_iid), json.dumps(slots))


def _remove_from_rack(rack_iid, instance_iid):
    """Remove an instance from a rack's slot list."""
    slots = [s for s in get_rack_slots(rack_iid) if s['instance_id'] != instance_iid]
    save_rack_slots(rack_iid, slots)


def place_in_rack(rack_iid: str, instance_iid: str, u_pos: int) -> list:
    """
    Place an instance in a rack at u_pos.
    Returns list of validation issues (may be empty).
    """
    issues = []
    rack_inst = get_hw_instance(rack_iid)
    inst = get_hw_instance(instance_iid)
    if not rack_inst or not inst:
        return [{'severity': 'error', 'code': 'NOT_FOUND',
                 'message': 'Rack or instance not found'}]

    rack_tmpl = get_hw_template(rack_inst['template_id'])
    dev_tmpl = get_hw_template(inst['template_id'])
    if not rack_tmpl or not dev_tmpl:
        return [{'severity': 'error', 'code': 'TEMPLATE_NOT_FOUND',
                 'message': 'Template missing'}]

    # Form factor check
    ff_issues = _check_form_factor(rack_tmpl, dev_tmpl, instance_iid)
    issues.extend(ff_issues)

    # U space check
    rack_u = int(rack_tmpl.get('u_size', 42))
    dev_u = int(dev_tmpl.get('u_size', 1))
    if u_pos < 1 or (u_pos + dev_u - 1) > rack_u:
        issues.append({'severity': 'error', 'code': 'U_OVERFLOW',
                       'message': f'Device ({dev_u}U) at U{u_pos} exceeds rack height ({rack_u}U)',
                       'context': {'rack': rack_iid, 'device': instance_iid}})

    # Overlap check
    slots = get_rack_slots(rack_iid)
    occupied = set()
    for slot in slots:
        if slot['instance_id'] == instance_iid:
            continue
        other_tmpl = get_hw_template(get_hw_instance(slot['instance_id'])['template_id'])
        other_u = int(other_tmpl.get('u_size', 1)) if other_tmpl else 1
        for u in range(slot['u_pos'], slot['u_pos'] + other_u):
            occupied.add(u)
    for u in range(u_pos, u_pos + dev_u):
        if u in occupied:
            issues.append({'severity': 'error', 'code': 'U_OCCUPIED',
                           'message': f'U{u} is already occupied',
                           'context': {'rack': rack_iid, 'device': instance_iid}})
            break

    if not any(i['severity'] == 'error' for i in issues):
        # Remove old placement if re-placing
        slots = [s for s in slots if s['instance_id'] != instance_iid]
        slots.append({'u_pos': u_pos, 'instance_id': instance_iid})
        save_rack_slots(rack_iid, slots)
        # Update instance location
        inst['location'] = {'rack_id': rack_iid, 'u_pos': u_pos}
        # Inherit the rack's site if the device has none of its own.
        if not inst.get('site_id') and rack_inst.get('site_id'):
            inst['site_id'] = rack_inst['site_id']
        save_hw_instance(inst)

    return issues


def propagate_rack_site(rack_iid: str) -> int:
    """
    Push a rack's site_id onto every placed instance that has none of its own.
    Returns the number of instances updated.
    """
    rack_inst = get_hw_instance(rack_iid)
    if not rack_inst or not rack_inst.get('site_id'):
        return 0
    site_id = rack_inst['site_id']
    updated = 0
    for slot in get_rack_slots(rack_iid):
        inst = get_hw_instance(slot['instance_id'])
        if inst and not inst.get('site_id'):
            inst['site_id'] = site_id
            save_hw_instance(inst)
            updated += 1
    return updated


def _check_form_factor(rack_tmpl: dict, dev_tmpl: dict, instance_iid: str) -> list:
    """Validate that device form factor is compatible with rack form factor."""
    rack_ff = rack_tmpl.get('form_factor', '19"')
    dev_ff = dev_tmpl.get('form_factor', '19"')
    # OCP rack only accepts OCP devices
    if rack_ff == 'OCP' and dev_ff != 'OCP':
        return [{'severity': 'error', 'code': 'FORM_FACTOR_MISMATCH',
                 'message': f'OCP rack cannot accept {dev_ff} device "{dev_tmpl["name"]}"',
                 'context': {'device': instance_iid}}]
    # 19" rack cannot accept OCP devices
    if rack_ff == '19"' and dev_ff == 'OCP':
        return [{'severity': 'error', 'code': 'FORM_FACTOR_MISMATCH',
                 'message': f'19" rack cannot accept OCP device "{dev_tmpl["name"]}"',
                 'context': {'device': instance_iid}}]
    # 21" rack accepts both 19" and 21"
    return []


def rack_layout_view(rack_iid: str) -> dict:
    """
    Return a structured view of the rack for rendering.
    Includes empty U slots and placed devices, sorted top-to-bottom.
    """
    rack_inst = get_hw_instance(rack_iid)
    if not rack_inst:
        return {}
    rack_tmpl = get_hw_template(rack_inst['template_id'])
    rack_u = int(rack_tmpl.get('u_size', 42)) if rack_tmpl else 42

    slots = get_rack_slots(rack_iid)
    # Build u → slot map
    u_map = {}
    total_power = 0.0
    total_weight = 0.0
    for slot in slots:
        inst = get_hw_instance(slot['instance_id'])
        dev_tmpl = get_hw_template(inst['template_id']) if inst else None
        dev_u = int(dev_tmpl.get('u_size', 1)) if dev_tmpl else 1
        if dev_tmpl:
            total_power += float(dev_tmpl.get('power_w', 0) or 0)
            total_weight += float(dev_tmpl.get('weight_kg', 0) or 0)
        for u in range(slot['u_pos'], slot['u_pos'] + dev_u):
            u_map[u] = {
                'instance': inst,
                'template': dev_tmpl,
                'u_start': slot['u_pos'],
                'u_size': dev_u,
                'is_top': u == slot['u_pos'],
            }

    rows = []
    u = rack_u
    while u >= 1:
        if u in u_map and u_map[u]['is_top']:
            entry = u_map[u]
            rows.append({'u': u, 'type': 'device', **entry})
            u -= entry['u_size']
        elif u in u_map:
            u -= 1  # continuation row — skip (rendered as rowspan)
        else:
            rows.append({'u': u, 'type': 'empty'})
            u -= 1

    return {
        'rack_instance': rack_inst,
        'rack_template': rack_tmpl,
        'rack_u': rack_u,
        'rows': rows,
        'slots': slots,
        'total_power': total_power,
        'total_weight': total_weight,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Cable plant helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_cable(cid):
    """Retrieve cable instance by ID."""
    return redis_get(_cable_key(cid))


def save_cable(cable):
    """Save cable instance and update indices."""
    redis_save(_cable_key(cable['id']), cable)
    r.sadd(HW_CABLE_INDEX, cable['id'])
    r.sadd(f'project:{cable["project_id"]}:hw:cables', cable['id'])


def delete_cable(cid):
    """Delete cable and remove from indices."""
    cable = get_cable(cid)
    if not cable:
        return
    r.srem(HW_CABLE_INDEX, cid)
    r.srem(f'project:{cable["project_id"]}:hw:cables', cid)
    redis_delete(_cable_key(cid))


def project_cables(pid) -> list:
    """List all cables in a project, enriched with template and endpoint info."""
    key = f'project:{pid}:hw:cables'
    cables = []
    for cable in redis_all(key, get_cable):
        # Enrich with template + endpoint instance info
        cable['template'] = get_hw_template(cable.get('template_id'))
        cable['inst_a'] = get_hw_instance(cable['end_a'].get('instance_id', ''))
        cable['inst_b'] = get_hw_instance(cable['end_b'].get('instance_id', ''))
        cables.append(cable)
    return sorted(cables, key=lambda c: c.get('asset_tag', ''))


def _get_port(instance_id: str, port_id: str) -> dict | None:
    """Retrieve port definition from device's template."""
    inst = get_hw_instance(instance_id)
    if not inst:
        return None
    tmpl = get_hw_template(inst['template_id'])
    if not tmpl:
        return None
    return next((p for p in tmpl.get('ports', []) if p['id'] == port_id), None)


def trace_cable_path(cable_id: str) -> list:
    """
    Follow a cable and its endpoints to discover the logical connection path.
    Returns a list of elements:
    [{'type': 'device', 'id': iid, 'name': '...', 'port': '...'},
     {'type': 'cable', 'id': cid, 'name': '...'}, ...]
    """
    path = []
    seen_cables = {cable_id}

    initial_cable = get_cable(cable_id)
    if not initial_cable:
        return []

    # We'll trace in two directions from the cable: End A and End B
    # Direction A
    side_a = _trace_direction(cable_id, 'end_a', seen_cables)
    # Direction B
    side_b = _trace_direction(cable_id, 'end_b', seen_cables)

    # Path is side_a (reversed) + cable + side_b
    path = list(reversed(side_a))

    cable_tmpl = get_hw_template(initial_cable.get('template_id'))
    path.append({
        'type': 'cable',
        'id': cable_id,
        'asset_tag': initial_cable.get('asset_tag', 'UNTYPED'),
        'template_name': cable_tmpl['name'] if cable_tmpl else 'Generic Cable'
    })

    path.extend(side_b)
    return path


def _trace_direction(start_cable_id, start_end, _seen_cables) -> list:
    """Helper to follow connections from one end of a cable."""
    current_cable = get_cable(start_cable_id)
    segment = []

    while current_cable:
        end = current_cable.get(start_end, {})
        iid = end.get('instance_id')
        port_id = end.get('port_id')

        if not iid or not port_id:
            break

        inst = get_hw_instance(iid)
        port = _get_port(iid, port_id)
        if not inst or not port:
            break

        segment.append({
            'type': 'device',
            'id': iid,
            'asset_tag': inst.get('asset_tag', iid),
            'port_name': port.get('name', port_id),
            'port_id': port_id
        })

        # Look for another cable on this device but on a different port?
        # Actually, standard "tracing" in networking usually means following the SAME physical medium.
        # But if it's a patch panel, we might want to "jump" to the corresponding internal port.
        # For this IPAM, we'll implement "simple" tracing: only follow if the port itself
        # is connected to another cable (which shouldn't happen in a valid config,
        # as ports are 1:1 with cables).

        # However, some "devices" are passive (patch panels).
        # If the device is a 'patch-panel' category (we don't have this yet, but we have 'other'),
        # we might want to jump.

        # Let's check if this port is connected to ANY OTHER cable.
        # (This would be an error in validation, but let's see)
        # Standard tracing ends at the device port — no cross-connect implemented yet.
        break

    return segment


def used_ports(pid: str) -> dict:
    """Public alias for _used_ports — return (instance_id, port_id) → cable_id."""
    return _used_ports(pid)


def port_attached_subnets(hw_instance_id: str, port_id: str, pid: str) -> list:
    """
    Return subnets attached to (hw_instance_id, port_id).

    Direct:     NE iface bound to this port with an IP in port_overrides.
    Propagated: cable connects this port to a directly-bound port on another device.

    Returns list of dicts:
      {network_id, cidr, source ('direct'|'propagated'),
       ne_instance_id, iface_id, port_ip, cable_id}
    """
    import ipaddress as _ip
    from ipam import project_networks  # pylint: disable=import-outside-toplevel

    results = []
    nets = project_networks(pid)

    def _find_net(ip_str):
        try:
            addr = _ip.ip_address(ip_str)
        except ValueError:
            return None
        for n in nets:
            try:
                if addr in _ip.ip_network(n['cidr'], strict=False):
                    return n
            except ValueError:
                pass
        return None

    # ── Direct attachment via port_overrides ──────────────────────────────────
    inst = get_hw_instance(hw_instance_id)
    if inst:
        ip_str = inst.get('port_overrides', {}).get(port_id, {}).get('ip')
        if ip_str:
            net = _find_net(ip_str)
            if net:
                bound = get_port_bound(hw_instance_id, port_id)
                results.append({
                    'network_id':     net['id'],
                    'cidr':           net['cidr'],
                    'source':         'direct',
                    'ne_instance_id': (bound or {}).get('ne_instance_id'),
                    'iface_id':       (bound or {}).get('iface_id'),
                    'port_ip':        ip_str,
                    'cable_id':       None,
                })

    # ── Propagated via cable (one hop) ────────────────────────────────────────
    cable_map = _used_ports(pid)  # {(iid, pid): cable_id}
    cable_id = cable_map.get((hw_instance_id, port_id))
    if cable_id:
        cable = get_cable(cable_id)
        if cable:
            end_a = cable.get('end_a', {})
            end_b = cable.get('end_b', {})
            far_iid = far_pid_ = None
            if end_a.get('instance_id') == hw_instance_id and end_a.get('port_id') == port_id:
                far_iid  = end_b.get('instance_id')
                far_pid_ = end_b.get('port_id')
            elif end_b.get('instance_id') == hw_instance_id and end_b.get('port_id') == port_id:
                far_iid  = end_a.get('instance_id')
                far_pid_ = end_a.get('port_id')

            if far_iid and far_pid_:
                far_inst = get_hw_instance(far_iid)
                if far_inst:
                    far_ip = far_inst.get('port_overrides', {}).get(far_pid_, {}).get('ip')
                    if far_ip:
                        net = _find_net(far_ip)
                        if net:
                            # Skip if already emitted as direct (same subnet on same port)
                            already = any(r2['network_id'] == net['id'] and r2['source'] == 'direct'
                                          for r2 in results)
                            if not already:
                                results.append({
                                    'network_id':     net['id'],
                                    'cidr':           net['cidr'],
                                    'source':         'propagated',
                                    'ne_instance_id': None,
                                    'iface_id':       None,
                                    'port_ip':        None,
                                    'cable_id':       cable_id,
                                })
    return results


def _used_ports(pid: str) -> dict:
    """Return dict: (instance_id, port_id) -> cable_id for all cables in project."""
    used = {}
    for cid in r.smembers(f'project:{pid}:hw:cables'):
        cable = get_cable(cid)
        if not cable:
            continue
        key_a = (cable['end_a'].get('instance_id'), cable['end_a'].get('port_id'))
        key_b = (cable['end_b'].get('instance_id'), cable['end_b'].get('port_id'))
        if key_a[0]:
            used[key_a] = cid
        if key_b[0]:
            used[key_b] = cid
    return used


# ══════════════════════════════════════════════════════════════════════════════
# Cable pattern helpers
# ══════════════════════════════════════════════════════════════════════════════

_PATTERN_PLACEHOLDER = _re.compile(
    r'\{(a\.asset_tag|a\.port|a\.port_type|a\.connector'
    r'|b\.asset_tag|b\.port|b\.port_type|b\.connector'
    r'|cable_type|seq)\}'
)


def render_pattern(pattern: str, ctx: dict) -> str:
    """
    Render a cable pattern string using ctx.

    Unknown placeholders pass through literally so user notes like {tenant}
    survive.  Missing values render as empty strings.

    ctx shape:
        { 'a': {'asset_tag', 'port', 'port_type', 'connector'},
          'b': {'asset_tag', 'port', 'port_type', 'connector'},
          'cable_type': str,
          'seq':        str | None,   # already zero-padded; None → ''
        }
    """
    if not pattern:
        return ''

    def sub(m):
        key = m.group(1)
        if '.' in key:
            side, attr = key.split('.', 1)
            return str(ctx.get(side, {}).get(attr, '') or '')
        if key == 'seq':
            return ctx.get('seq') or ''
        return str(ctx.get(key, '') or '')

    return _PATTERN_PLACEHOLDER.sub(sub, pattern)


def cable_pattern_context(cable: dict, *, defer_seq: bool = False) -> dict:
    """Build a render-context dict from a cable's endpoints and template."""
    def side(end: dict) -> dict:
        iid  = end.get('instance_id') or ''
        pid_ = end.get('port_id') or ''
        inst = get_hw_instance(iid) if iid else None
        port = _get_port(iid, pid_) if iid and pid_ else None
        return {
            'asset_tag': inst.get('asset_tag', '') if inst else '',
            'port':      port.get('name', '')      if port else '',
            'port_type': port.get('port_type', '') if port else '',
            'connector': port.get('connector', '') if port else '',
        }

    tmpl = get_hw_template(cable.get('template_id')) if cable.get('template_id') else None
    return {
        'a':          side(cable.get('end_a', {})),
        'b':          side(cable.get('end_b', {})),
        'cable_type': tmpl.get('cable_type', '') if tmpl else '',
        'seq':        None if defer_seq else '',
    }


def next_cable_seq(pid: str, pattern: str) -> str:
    """Atomic per-(project, pattern) counter; returns 3-digit zero-padded string."""
    h = hashlib.md5(pattern.encode()).hexdigest()[:8]
    n = r.incr(f'project:{pid}:cable_seq:{h}')
    return f'{n:03d}'


# ══════════════════════════════════════════════════════════════════════════════
# Validation engine
# ══════════════════════════════════════════════════════════════════════════════

def validate_project(pid: str) -> list:
    """
    Run all hardware validation checks for a project.
    Returns list of {severity, code, message, context} dicts.
    """
    issues = []
    seed_connectors()

    cables = project_cables(pid)

    # ── Cable validation ──────────────────────────────────────────────────────
    seen_cable_ends = {}
    for cable in cables:
        cid = cable['id']
        tmpl = cable.get('template')
        end_a = cable['end_a']
        end_b = cable['end_b']

        # Both ends must be connected
        if not end_a.get('instance_id') or not end_a.get('port_id'):
            issues.append(_issue('warning', 'CABLE_UNCONNECTED_A',
                                 f'Cable {cable["asset_tag"]} end A is not connected',
                                 {'cable': cid}))
        if not end_b.get('instance_id') or not end_b.get('port_id'):
            issues.append(_issue('warning', 'CABLE_UNCONNECTED_B',
                                 f'Cable {cable["asset_tag"]} end B is not connected',
                                 {'cable': cid}))

        if not (end_a.get('instance_id') and end_b.get('instance_id')):
            continue

        port_a = _get_port(end_a['instance_id'], end_a['port_id'])
        port_b = _get_port(end_b['instance_id'], end_b['port_id'])

        if not port_a:
            issues.append(_issue('error', 'PORT_NOT_FOUND',
                                 f'Cable {cable["asset_tag"]} end A: port not found',
                                 {'cable': cid, 'instance': end_a['instance_id']}))
            continue
        if not port_b:
            issues.append(_issue('error', 'PORT_NOT_FOUND',
                                 f'Cable {cable["asset_tag"]} end B: port not found',
                                 {'cable': cid, 'instance': end_b['instance_id']}))
            continue

        conn_a = port_a.get('connector', '')
        conn_b = port_b.get('connector', '')

        # Connector compatibility check
        if conn_a and conn_b and not connectors_compatible(conn_a, conn_b):
            inst_a = get_hw_instance(end_a['instance_id'])
            inst_b = get_hw_instance(end_b['instance_id'])
            issues.append(_issue('error',
                                 'CONNECTOR_MISMATCH',
                                 f'Cable {cable["asset_tag"]}: '
                                 f'{conn_a} ({inst_a["asset_tag"] if inst_a else "?"}/{port_a["name"]}) '
                                 f'↔ {conn_b} ({inst_b["asset_tag"] if inst_b else "?"}/{port_b["name"]}) '
                                 f'— incompatible connectors',
                                 {'cable': cid}))

        # Port type mismatch — data cable on power port etc.
        if tmpl:
            cable_type = tmpl.get('cable_type', 'other')
            _check_cable_port_type(cable_type, port_a, port_b, cable, issues)

        # Speed mismatch warning (DAC/AOC require same speed)
        if tmpl and tmpl.get('cable_type') in ('DAC', 'AOC'):
            spd_a = port_a.get('speed_gbps')
            spd_b = port_b.get('speed_gbps')
            if spd_a and spd_b and spd_a != spd_b:
                issues.append(_issue('warning', 'SPEED_MISMATCH',
                                     f'Cable {cable["asset_tag"]}: DAC/AOC speed mismatch '
                                     f'({spd_a}G ↔ {spd_b}G)',
                                     {'cable': cid}))

        # Cable template connector and breakout constraints
        if tmpl:
            _check_cable_template_constraints(cable, tmpl, port_a, port_b, issues)

        # Port already used by another cable
        key_a = (end_a['instance_id'], end_a['port_id'])
        key_b = (end_b['instance_id'], end_b['port_id'])
        for key, end_name in ((key_a, 'A'), (key_b, 'B')):
            if key in seen_cable_ends:
                inst = get_hw_instance(key[0])
                issues.append(_issue('error', 'PORT_DOUBLE_CONNECTED',
                                     f'Cable {cable["asset_tag"]} end {end_name} port already used by cable '
                                     f'{seen_cable_ends[key]}',
                                     {'cable': cid,
                                      'device': inst['asset_tag'] if inst else key[0]}))
            else:
                seen_cable_ends[key] = cable['asset_tag']

    # ── Rack validation ───────────────────────────────────────────────────────
    rack_instances = project_instances(pid, category='rack')
    for rack in rack_instances:
        rack_tmpl = rack.get('template')
        if not rack_tmpl:
            continue
        rack_u = int(rack_tmpl.get('u_size', 42))
        max_power = float(rack_tmpl.get('max_power_w', 0) or 0)
        max_weight = float(rack_tmpl.get('max_weight_kg', 0) or 0)
        slots = get_rack_slots(rack['id'])

        # Build occupancy map and check overlaps / form factor / power / weight
        occupied = {}
        total_power = 0.0
        total_weight = 0.0
        for slot in slots:
            inst = get_hw_instance(slot['instance_id'])
            if not inst:
                continue
            dev_tmpl = get_hw_template(inst['template_id'])
            if not dev_tmpl:
                continue

            ff_issues = _check_form_factor(rack_tmpl, dev_tmpl, inst['id'])
            issues.extend(ff_issues)

            total_power += float(dev_tmpl.get('power_w', 0) or 0)
            total_weight += float(dev_tmpl.get('weight_kg', 0) or 0)

            dev_u = int(dev_tmpl.get('u_size', 1))
            for u in range(slot['u_pos'], slot['u_pos'] + dev_u):
                if u < 1 or u > rack_u:
                    issues.append(_issue('error', 'U_OVERFLOW',
                                         f'Device {inst["asset_tag"]} ({dev_u}U) at U{slot["u_pos"]} '
                                         f'extends outside rack {rack["asset_tag"]} ({rack_u}U)',
                                         {'rack': rack['id'], 'device': inst['id']}))
                    break
                if u in occupied:
                    issues.append(_issue('error', 'U_OVERLAP',
                                         f'U{u} in rack {rack["asset_tag"]} is occupied by both '
                                         f'{occupied[u]} and {inst["asset_tag"]}',
                                         {'rack': rack['id']}))
                else:
                    occupied[u] = inst['asset_tag']

        if 0 < max_power < total_power:
            issues.append(_issue('error', 'POWER_OVERFLOW',
                                 f'Rack {rack["asset_tag"]} power exceeded: {total_power}W > {max_power}W',
                                 {'rack': rack['id']}))
        if 0 < max_weight < total_weight:
            issues.append(_issue('error', 'WEIGHT_OVERFLOW',
                                 f'Rack {rack["asset_tag"]} weight exceeded: {total_weight}kg > {max_weight}kg',
                                 {'rack': rack['id']}))

    # ── NE binding validation ─────────────────────────────────────────────────
    _check_ne_bindings(pid, issues)

    # ── Checklist validation ──────────────────────────────────────────────────
    _check_checklists(pid, issues)

    # Cache results
    r.set(_validation_key(pid), json.dumps(issues))
    return issues


def _issue(severity, code, message, context=None):
    """Helper to create a standardized issue dictionary."""
    return {'severity': severity, 'code': code,
            'message': message, 'context': context or {}}


def _check_cable_port_type(cable_type, port_a, port_b, cable, issues):
    """Cross-check cable type vs port type and record issues."""
    pt_a = port_a.get('port_type', 'data')
    pt_b = port_b.get('port_type', 'data')
    if cable_type == 'power':
        if pt_a not in ('power',) or pt_b not in ('power',):
            issues.append(_issue('error',
                                 'CABLE_PORT_TYPE_MISMATCH',
                                 f'Cable {cable["asset_tag"]}: power cable connected to non-power port',
                                 {'cable': cable['id']}))
    elif cable_type in ('DAC', 'AOC', 'fiber-patch', 'copper-patch'):
        if pt_a == 'power' or pt_b == 'power':
            issues.append(_issue('error', 'CABLE_PORT_TYPE_MISMATCH',
                                 f'Cable {cable["asset_tag"]}: data cable connected to power port',
                                 {'cable': cable['id']}))


def _check_cable_template_constraints(cable, tmpl, port_a, port_b, issues):
    """Connector and breakout cross-checks against the cable template."""
    tag = cable.get('asset_tag', cable.get('id', '?'))
    expect_a = tmpl.get('connector_a', '')
    expect_b = tmpl.get('connector_b', '')
    actual_a = port_a.get('connector', '')
    actual_b = port_b.get('connector', '')

    if expect_a and actual_a and not connectors_compatible(expect_a, actual_a):
        issues.append(_issue('error', 'CABLE_CONNECTOR_MISMATCH',
            f'Cable {tag}: template expects {expect_a} at end A '
            f'but port connector is {actual_a}',
            {'cable': cable['id']}))
    if expect_b and actual_b and not connectors_compatible(expect_b, actual_b):
        issues.append(_issue('error', 'CABLE_CONNECTOR_MISMATCH',
            f'Cable {tag}: template expects {expect_b} at end B '
            f'but port connector is {actual_b}',
            {'cable': cable['id']}))

    t_brk = bool(tmpl.get('breakout'))
    c_brk = bool(cable.get('breakout'))
    if t_brk != c_brk:
        issues.append(_issue('warning', 'CABLE_BREAKOUT_MISMATCH',
            f'Cable {tag}: template breakout={t_brk} but cable breakout={c_brk}',
            {'cable': cable['id']}))
    elif t_brk:
        t_fan = int(tmpl.get('breakout_fan_out') or 1)
        c_fan = int(cable.get('breakout_fan_out') or 1)
        if t_fan != c_fan:
            issues.append(_issue('warning', 'CABLE_BREAKOUT_MISMATCH',
                f'Cable {tag}: template fan-out={t_fan} but cable fan-out={c_fan}',
                {'cable': cable['id']}))


def load_validation(pid: str) -> list:
    """Load cached hardware validation results from Redis."""
    raw = r.get(_validation_key(pid))
    return json.loads(raw) if raw else []


# ── NE binding validation helpers ─────────────────────────────────────────────

def _project_ne_instances_raw(pid: str) -> list:
    """Load NE instances for a project directly from Redis (no ne.py import)."""
    ids = r.smembers(f'project:{pid}:ne_instances')
    result = []
    for nid in ids:
        raw = r.get(f'ne_inst:{nid}')
        if raw:
            try:
                result.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return sorted(result, key=lambda x: x.get('name', ''))


def _ne_type_iface_map(ne_type_id: str) -> dict:
    """Return {iface_id: iface_dict} for an NE type, without importing ne.py."""
    if not ne_type_id:
        return {}
    raw = r.get(f'ne_type:{ne_type_id}')
    if not raw:
        return {}
    try:
        ne_type = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {i['id']: i for i in ne_type.get('interfaces', [])}


def _build_tmpl_port_map(iid: str, cache: dict) -> dict:
    """Return {port_id: port_dict} for a HW instance's template, with caching."""
    if iid not in cache:
        inst = get_hw_instance(iid)
        tmpl = get_hw_template(inst['template_id']) if inst and inst.get('template_id') else None
        cache[iid] = {p['id']: p for p in tmpl.get('ports', [])} if tmpl else {}
    return cache[iid]


def _check_ne_bindings(pid: str, issues: list) -> None:
    """
    Append NE-HW binding validation issues to *issues*.

    Checks emitted:
      error   — NE_PORT_DOUBLE_BOUND, NE_PORT_NOT_FOUND
      warning — NE_PORT_TYPE_MISMATCH, NE_LAG_SINGLE_PORT, NE_LAG_SPEED_MISMATCH,
                NE_RULE_NO_MATCH, NE_RULE_UNRACKED_PORT, NE_RULE_STALE
      info    — NE_LAG_MIXED_HOSTS, NE_RULE_EXPLICIT_OVERLAP
    """
    import rules as rules_mod  # local import — rules.py imports hw_logic, avoid circular

    ne_instances = _project_ne_instances_raw(pid)
    tmpl_port_cache: dict = {}

    # ── Pass 1: collect all explicitly bound (iid, port_id) across the project
    # Maps (iid, port_id) → (owner_nid, owner_ne_name, owner_iface_id)
    explicit_seen: dict[tuple, tuple] = {}

    for ne_inst in ne_instances:
        ne_name = ne_inst.get('name', ne_inst['id'])
        for iface_id, binding in ne_inst.get('iface_bindings', {}).items():
            if binding.get('bind_mode') not in ('single', 'lag', 'active-passive'):
                continue
            for p in binding.get('ports', []):
                key = (p['hw_instance_id'], p['port_id'])
                if key in explicit_seen:
                    _, prev_ne, prev_iface = explicit_seen[key]
                    hw_inst = get_hw_instance(p['hw_instance_id'])
                    hw_tag = hw_inst['asset_tag'] if hw_inst else p['hw_instance_id']
                    issues.append(_issue('error', 'NE_PORT_DOUBLE_BOUND',
                        f'Port {hw_tag}/{p["port_id"]} is bound by both '
                        f'{prev_ne}/{prev_iface} and {ne_name}/{iface_id}',
                        {'hw_instance': p['hw_instance_id'], 'port': p['port_id'],
                         'ne_instance': ne_inst['id']}))
                else:
                    explicit_seen[key] = (ne_inst['id'], ne_name, iface_id)

    # ── Pass 2: per-binding checks
    for ne_inst in ne_instances:
        ne_name = ne_inst.get('name', ne_inst['id'])
        iface_map = _ne_type_iface_map(ne_inst.get('ne_type_id', ''))

        # Explicit ports owned by OTHER NE instances (used for auto-rule exclusion)
        other_excl = {k for k, (owner_nid, _, _) in explicit_seen.items()
                      if owner_nid != ne_inst['id']}

        for iface_id, binding in ne_inst.get('iface_bindings', {}).items():
            iface = iface_map.get(iface_id, {})
            iface_name = iface.get('name', iface_id)
            iface_labels = set(iface.get('labels', []))
            bind_mode = binding.get('bind_mode', 'single')
            ports = binding.get('ports', [])
            ctx = {'ne_instance': ne_inst['id'], 'iface': iface_id}

            if bind_mode == 'auto-rule':
                rule = binding.get('rule') or {}

                # NE_RULE_NO_MATCH
                if not ports:
                    issues.append(_issue('warning', 'NE_RULE_NO_MATCH',
                        f'{ne_name} / {iface_name}: auto-rule binding matched zero ports',
                        ctx))

                # NE_RULE_UNRACKED_PORT — only relevant when rule groups by rack
                if 'rack' in rule.get('group_by', []):
                    for p in ports:
                        hw_inst = get_hw_instance(p['hw_instance_id'])
                        if hw_inst and not hw_inst.get('location', {}).get('rack_id'):
                            issues.append(_issue('warning', 'NE_RULE_UNRACKED_PORT',
                                f'{ne_name} / {iface_name}: auto-rule matched unracked device '
                                f'{hw_inst.get("asset_tag", p["hw_instance_id"])}',
                                {**ctx, 'hw_instance': p['hw_instance_id']}))

                # NE_RULE_STALE and NE_RULE_EXPLICIT_OVERLAP
                if rule and binding.get('rule_materialized_at'):
                    # What a re-materialize would produce today (respecting other explicit bindings)
                    fresh = rules_mod.materialize_binding(rule, pid, other_excl)
                    fresh_keys = {(p['hw_instance_id'], p['port_id']) for p in fresh}
                    stored_keys = {(p['hw_instance_id'], p['port_id']) for p in ports}

                    if fresh_keys != stored_keys:
                        issues.append(_issue('warning', 'NE_RULE_STALE',
                            f'{ne_name} / {iface_name}: auto-rule results are outdated '
                            f'— click ↺ Re-evaluate to refresh',
                            ctx))

                    # Ports the rule would match without any exclusions
                    full = rules_mod.materialize_binding(rule, pid, set())
                    full_keys = {(p['hw_instance_id'], p['port_id']) for p in full}
                    # Ports present in the full run but excluded by other explicit bindings
                    for key in sorted(full_keys - fresh_keys):
                        if key in explicit_seen:
                            iid, port_id = key
                            _, owner_ne, owner_iface = explicit_seen[key]
                            hw_inst = get_hw_instance(iid)
                            hw_tag = hw_inst['asset_tag'] if hw_inst else iid
                            issues.append(_issue('info', 'NE_RULE_EXPLICIT_OVERLAP',
                                f'{ne_name} / {iface_name}: auto-rule would match '
                                f'{hw_tag}/{port_id} but it is explicitly bound by '
                                f'{owner_ne}/{owner_iface}',
                                {**ctx, 'hw_instance': iid, 'port': port_id}))

            else:
                # ── Explicit-mode checks (single / lag / active-passive)

                # NE_PORT_NOT_FOUND
                for p in ports:
                    port_map = _build_tmpl_port_map(p['hw_instance_id'], tmpl_port_cache)
                    if port_map and p['port_id'] not in port_map:
                        hw_inst = get_hw_instance(p['hw_instance_id'])
                        hw_tag = hw_inst['asset_tag'] if hw_inst else p['hw_instance_id']
                        issues.append(_issue('error', 'NE_PORT_NOT_FOUND',
                            f'{ne_name} / {iface_name}: port {hw_tag}/{p["port_id"]} '
                            f'not found in HW template',
                            {**ctx, 'hw_instance': p['hw_instance_id'], 'port': p['port_id']}))

                # NE_PORT_TYPE_MISMATCH
                for p in ports:
                    port_map = _build_tmpl_port_map(p['hw_instance_id'], tmpl_port_cache)
                    port_def = port_map.get(p['port_id'])
                    if port_def:
                        port_type = port_def.get('port_type', '')
                        mismatch = (('mgmt' in iface_labels and port_type == 'data') or
                                    ('data' in iface_labels and port_type == 'mgmt'))
                        if mismatch:
                            hw_inst = get_hw_instance(p['hw_instance_id'])
                            hw_tag = hw_inst['asset_tag'] if hw_inst else p['hw_instance_id']
                            issues.append(_issue('warning', 'NE_PORT_TYPE_MISMATCH',
                                f'{ne_name} / {iface_name} (labels: {sorted(iface_labels)}): '
                                f'bound to {port_type} port {hw_tag}/{port_def["name"]}',
                                {**ctx, 'hw_instance': p['hw_instance_id'], 'port': p['port_id']}))

                # LAG-specific checks
                if bind_mode == 'lag':
                    if len(ports) == 1:
                        issues.append(_issue('warning', 'NE_LAG_SINGLE_PORT',
                            f'{ne_name} / {iface_name}: LAG binding has only one member port',
                            ctx))

                    speeds = []
                    for p in ports:
                        port_map = _build_tmpl_port_map(p['hw_instance_id'], tmpl_port_cache)
                        port_def = port_map.get(p['port_id'])
                        if port_def and port_def.get('speed_gbps'):
                            speeds.append(port_def['speed_gbps'])
                    if len(set(speeds)) > 1:
                        issues.append(_issue('warning', 'NE_LAG_SPEED_MISMATCH',
                            f'{ne_name} / {iface_name}: LAG members have mixed speeds '
                            f'({", ".join(str(s) + "G" for s in sorted(set(speeds)))})',
                            ctx))

                    if len({p['hw_instance_id'] for p in ports}) > 1:
                        issues.append(_issue('info', 'NE_LAG_MIXED_HOSTS',
                            f'{ne_name} / {iface_name}: LAG spans '
                            f'{len({p["hw_instance_id"] for p in ports})} HW instances (MC-LAG)',
                            ctx))

                # NE_PORT_NEEDS_IP_NO_SUBNET / NE_PORT_IP_OUTSIDE_SUBNET
                for p in ports:
                    port_map = _build_tmpl_port_map(p['hw_instance_id'], tmpl_port_cache)
                    port_def = port_map.get(p['port_id'], {})
                    if not port_def.get('requires_ip'):
                        continue
                    hw_inst = get_hw_instance(p['hw_instance_id'])
                    hw_tag  = hw_inst['asset_tag'] if hw_inst else p['hw_instance_id']
                    port_ip = (hw_inst or {}).get('port_overrides', {}).get(
                        p['port_id'], {}).get('ip')
                    if not port_ip:
                        issues.append(_issue('error', 'NE_PORT_NEEDS_IP_NO_SUBNET',
                            f'{ne_name} / {iface_name}: port {hw_tag}/{port_def.get("name", p["port_id"])} '
                            f'requires an IP but none has been allocated — use "Alloc IP"',
                            {**ctx, 'hw_instance': p['hw_instance_id'], 'port': p['port_id']}))
                    else:
                        # Check port IP is within one of the NE iface's subnets
                        subnets = port_attached_subnets(p['hw_instance_id'], p['port_id'], pid)
                        if subnets and not any(s['source'] == 'direct' for s in subnets):
                            issues.append(_issue('error', 'NE_PORT_IP_OUTSIDE_SUBNET',
                                f'{ne_name} / {iface_name}: port {hw_tag}/{port_def.get("name", p["port_id"])} '
                                f'has IP {port_ip} but it does not match any bound NE iface subnet',
                                {**ctx, 'hw_instance': p['hw_instance_id'], 'port': p['port_id'],
                                 'port_ip': port_ip}))

    # ── Pass 3: NE_SUBNET_CONFLICT_AT_CABLE_FAR_END ───────────────────────────
    cables = project_cables(pid)
    for cable in cables:
        end_a = cable.get('end_a', {})
        end_b = cable.get('end_b', {})
        iid_a, pid_a = end_a.get('instance_id'), end_a.get('port_id')
        iid_b, pid_b = end_b.get('instance_id'), end_b.get('port_id')
        if not (iid_a and pid_a and iid_b and pid_b):
            continue
        nets_a = {s['network_id'] for s in port_attached_subnets(iid_a, pid_a, pid)
                  if s['source'] == 'direct'}
        nets_b = {s['network_id'] for s in port_attached_subnets(iid_b, pid_b, pid)
                  if s['source'] == 'direct'}
        conflict = nets_a & nets_b  # same network on both ends is OK (loop)
        # Different networks on A-end vs B-end is a design error
        if nets_a and nets_b and (nets_a - nets_b or nets_b - nets_a):
            inst_a = get_hw_instance(iid_a)
            inst_b = get_hw_instance(iid_b)
            tag_a  = inst_a.get('asset_tag', iid_a) if inst_a else iid_a
            tag_b  = inst_b.get('asset_tag', iid_b) if inst_b else iid_b
            issues.append(_issue('error', 'NE_SUBNET_CONFLICT_AT_CABLE_FAR_END',
                f'Cable {cable.get("asset_tag", cable["id"])}: '
                f'{tag_a}/{pid_a} and {tag_b}/{pid_b} carry different subnets',
                {'cable_id': cable['id'],
                 'nets_a': sorted(nets_a), 'nets_b': sorted(nets_b)}))


# ══════════════════════════════════════════════════════════════════════════════
# Checklist validation (CHK_ codes)
# ══════════════════════════════════════════════════════════════════════════════

def _check_checklists(pid: str, issues: list) -> None:  # pylint: disable=too-many-branches
    """
    Emit CHK_ validation codes by scanning check templates and checklists
    directly from Redis.  Does NOT import checks_logic to avoid circular deps.
    """
    from jinja2.sandbox import SandboxedEnvironment
    _jinja = SandboxedEnvironment(autoescape=False)

    # ── Load all check templates ──────────────────────────────────────────────
    ct_ids = r.smembers('check_templates:index')
    templates = []
    for ctid in ct_ids:
        raw = r.get(f'check_template:{ctid}')
        if raw:
            try:
                templates.append(json.loads(raw))
            except json.JSONDecodeError:
                pass

    # CHK_TEMPLATE_BAD_JINJA — template body has a Jinja2 syntax error
    for ct in templates:
        for field in ('action_description', 'expected_result'):
            text = ct.get(field, '')
            if not text:
                continue
            try:
                _jinja.parse(text)
            except Exception as exc:  # pylint: disable=broad-except
                issues.append(_issue('error', 'CHK_TEMPLATE_BAD_JINJA',
                    f'Check template "{ct.get("name","?")}" — Jinja error in {field}: {exc}',
                    {'check_template_id': ct['id'], 'field': field}))

    # CHK_TEMPLATE_NO_SUBJECT — template would match zero subjects on this project
    ne_count    = r.scard(f'project:{pid}:ne_instances')
    cable_count = r.scard(f'project:{pid}:hw:cables')
    hw_count    = r.scard(f'project:{pid}:hw:instances')

    for ct in templates:
        attached_to = ct.get('attached_to', 'project')
        if attached_to == 'project':
            continue
        if attached_to in ('ne_type', 'ne_iface') and ne_count == 0:
            issues.append(_issue('warning', 'CHK_TEMPLATE_NO_SUBJECT',
                f'Check template "{ct.get("name","?")}" targets NE instances '
                f'but the project has none.',
                {'check_template_id': ct['id'], 'attached_to': attached_to}))
        elif attached_to == 'cable' and cable_count == 0:
            issues.append(_issue('warning', 'CHK_TEMPLATE_NO_SUBJECT',
                f'Check template "{ct.get("name","?")}" targets cables '
                f'but the project has none.',
                {'check_template_id': ct['id'], 'attached_to': attached_to}))
        elif attached_to == 'hw_template' and hw_count == 0:
            issues.append(_issue('warning', 'CHK_TEMPLATE_NO_SUBJECT',
                f'Check template "{ct.get("name","?")}" targets HW instances '
                f'but the project has none.',
                {'check_template_id': ct['id'], 'attached_to': attached_to}))

    # ── Load project checklists ───────────────────────────────────────────────
    cl_ids     = r.lrange(f'project:{pid}:checklists', 0, -1)
    checklists = []
    for cid in cl_ids:
        raw = r.get(f'checklist:{cid}')
        if raw:
            try:
                checklists.append(json.loads(raw))
            except json.JSONDecodeError:
                pass

    for cl in checklists:
        status = cl.get('status', '')
        checks = cl.get('checks', [])
        label  = cl.get('deployment_label', cl.get('id', '?'))

        # CHK_LIST_INCOMPLETE — in-progress checklist with pending items
        if status == 'in-progress':
            pending_count = sum(1 for c in checks if c.get('status') == 'pending')
            if pending_count:
                issues.append(_issue('info', 'CHK_LIST_INCOMPLETE',
                    f'Checklist "{label}" ({cl.get("phase","?")}) has '
                    f'{pending_count} pending check(s).',
                    {'checklist_id': cl['id'], 'pending': pending_count}))

        # CHK_LIST_FAILED_CRITICAL — signed-off checklist with failed critical checks
        if status == 'signed-off':
            crit_fails = [c for c in checks
                          if c.get('severity') == 'critical' and c.get('status') == 'fail']
            if crit_fails:
                issues.append(_issue('error', 'CHK_LIST_FAILED_CRITICAL',
                    f'Signed-off checklist "{label}" has '
                    f'{len(crit_fails)} failed critical check(s).',
                    {'checklist_id': cl['id'], 'failed_critical': len(crit_fails)}))

    # CHK_VENDOR_HINT_MISSING — HW vendor in project has no matching hint in template
    hint_templates = [ct for ct in templates if ct.get('vendor_hints')]
    if hint_templates:
        hw_vendors: set = set()
        for iid in r.smembers(f'project:{pid}:hw:instances'):
            inst_raw = r.get(f'hw:instance:{iid}')
            if not inst_raw:
                continue
            try:
                inst = json.loads(inst_raw)
            except json.JSONDecodeError:
                continue
            tid = inst.get('template_id', '')
            if not tid:
                continue
            tmpl_raw = r.get(f'hw:template:{tid}')
            if not tmpl_raw:
                continue
            try:
                tmpl   = json.loads(tmpl_raw)
                vendor = tmpl.get('vendor', '').lower().replace(' ', '_').strip()
                if vendor:
                    hw_vendors.add(vendor)
            except json.JSONDecodeError:
                continue

        for ct in hint_templates:
            hints        = ct.get('vendor_hints', {})
            hint_vendors = {v.lower().replace(' ', '_') for v in hints}
            for vendor in hw_vendors:
                if vendor not in hint_vendors:
                    issues.append(_issue('info', 'CHK_VENDOR_HINT_MISSING',
                        f'Check template "{ct.get("name","?")}" has no vendor hint '
                        f'for "{vendor}" — fallback used.',
                        {'check_template_id': ct['id'], 'vendor': vendor}))
