"""
Hardware Management logic and data access helpers.
"""

import json
from db import r, new_id, redis_get, redis_save, redis_delete, redis_all

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

CATEGORIES = ('server', 'switch', 'router', 'pdu', 'rack', 'cable', 'other')
FORM_FACTORS = ('19"', '21"', 'OCP', 'desktop', 'tower', '0U', 'N/A')
PORT_TYPES = ('data', 'mgmt', 'power', 'console', 'usb')
CABLE_TYPES = ('DAC', 'AOC', 'fiber-patch', 'copper-patch', 'power', 'console', 'other')
SEVERITIES = ('error', 'warning', 'info')

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
    """Save hardware template and update global or project index."""
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
    redis_delete(_inst_key(iid))


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
    created = []
    for i in range(qty):
        tag = f'{prefix}-{str(start + i).zfill(pad)}'
        inst = {
            'id': new_id(),
            'template_id': item['template_id'],
            'project_id': pid,
            'asset_tag': tag,
            'serial': '',
            'status': 'in-stock',
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
        save_hw_instance(inst)

    return issues


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
    for slot in slots:
        inst = get_hw_instance(slot['instance_id'])
        dev_tmpl = get_hw_template(inst['template_id']) if inst else None
        dev_u = int(dev_tmpl.get('u_size', 1)) if dev_tmpl else 1
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
        slots = get_rack_slots(rack['id'])

        # Build occupancy map and check overlaps / form factor
        occupied = {}
        for slot in slots:
            inst = get_hw_instance(slot['instance_id'])
            if not inst:
                continue
            dev_tmpl = get_hw_template(inst['template_id'])
            if not dev_tmpl:
                continue

            ff_issues = _check_form_factor(rack_tmpl, dev_tmpl, inst['id'])
            issues.extend(ff_issues)

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


def load_validation(pid: str) -> list:
    """Load cached hardware validation results from Redis."""
    raw = r.get(_validation_key(pid))
    return json.loads(raw) if raw else []
