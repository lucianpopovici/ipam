"""
Hardware Management blueprint.
Covers: connector types, compatibility matrix, hardware templates,
        project BoM, physical instances, rack layout, cable plant,
        and validation engine.
"""
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, abort)
import json, uuid
from db import r

hw_bp = Blueprint('hw', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

CATEGORIES   = ('server', 'switch', 'router', 'pdu', 'rack', 'cable', 'other')
FORM_FACTORS = ('19"', '21"', 'OCP', 'desktop', 'tower', '0U', 'N/A')
PORT_TYPES   = ('data', 'mgmt', 'power', 'console', 'usb')
CABLE_TYPES  = ('DAC', 'AOC', 'fiber-patch', 'copper-patch', 'power', 'console', 'other')
SEVERITIES   = ('error', 'warning', 'info')

# Default connectors seeded on first run
DEFAULT_CONNECTORS = [
    'RJ45', 'SFP', 'SFP+', 'SFP28', 'QSFP28', 'QSFP-DD',
    'IEC-C13', 'IEC-C14', 'IEC-C19', 'IEC-C20',
    'NEMA-515', 'USB-A', 'USB-C', 'RJ11', 'DB9',
]

# Default compatibility matrix  {connector → set of compatible connectors}
# Symmetric: if A compat B then B compat A (enforced in get_compat)
DEFAULT_COMPAT = {
    'RJ45':    {'RJ45'},
    'SFP':     {'SFP', 'SFP+', 'SFP28'},
    'SFP+':    {'SFP+', 'SFP', 'SFP28'},
    'SFP28':   {'SFP28', 'SFP+', 'SFP'},
    'QSFP28':  {'QSFP28', 'QSFP-DD'},
    'QSFP-DD': {'QSFP-DD', 'QSFP28'},
    'IEC-C13': {'IEC-C13', 'IEC-C14', 'IEC-C19', 'IEC-C20'},
    'IEC-C14': {'IEC-C14', 'IEC-C13'},
    'IEC-C19': {'IEC-C19', 'IEC-C20', 'IEC-C13'},
    'IEC-C20': {'IEC-C20', 'IEC-C19'},
    'NEMA-515':{'NEMA-515'},
    'USB-A':   {'USB-A', 'USB-C'},
    'USB-C':   {'USB-C', 'USB-A'},
    'RJ11':    {'RJ11'},
    'DB9':     {'DB9'},
}

# ══════════════════════════════════════════════════════════════════════════════
# Redis key helpers
# ══════════════════════════════════════════════════════════════════════════════

HW_CONNECTORS   = 'hw:connectors'
HW_TMPL_INDEX   = 'hw:templates:index'
HW_INST_INDEX   = 'hw:instances:index'
HW_CABLE_INDEX  = 'hw:cables:index'

def _compat_key(conn):        return f'hw:compat:{conn}'
def _tmpl_key(tid):           return f'hw:template:{tid}'
def _inst_key(iid):           return f'hw:instance:{iid}'
def _rack_slots_key(iid):     return f'hw:rack:{iid}:slots'
def _cable_key(cid):          return f'hw:cable:{cid}'
def _bom_key(pid):            return f'project:{pid}:bom'
def _validation_key(pid):     return f'project:{pid}:hw:validation'

def _new_id():
    return str(uuid.uuid4())[:8]

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
    seed_connectors()
    return sorted(r.smembers(HW_CONNECTORS))

def add_connector(name: str):
    r.sadd(HW_CONNECTORS, name)
    # Self-compatible by default
    r.sadd(_compat_key(name), name)

def remove_connector(name: str):
    r.srem(HW_CONNECTORS, name)
    r.delete(_compat_key(name))
    # Remove from all other compat sets
    for conn in r.smembers(HW_CONNECTORS):
        r.srem(_compat_key(conn), name)

def get_compat(conn: str) -> set:
    return r.smembers(_compat_key(conn))

def set_compat(conn_a: str, conn_b: str, compatible: bool):
    if compatible:
        r.sadd(_compat_key(conn_a), conn_b)
        r.sadd(_compat_key(conn_b), conn_a)
    else:
        r.srem(_compat_key(conn_a), conn_b)
        r.srem(_compat_key(conn_b), conn_a)

def connectors_compatible(a: str, b: str) -> bool:
    return bool(r.sismember(_compat_key(a), b))

def full_compat_matrix() -> dict:
    """Return {conn: [list of compatible connectors]} for all connectors."""
    conns = all_connectors()
    return {c: sorted(get_compat(c)) for c in conns}

# ══════════════════════════════════════════════════════════════════════════════
# Hardware template helpers
# ══════════════════════════════════════════════════════════════════════════════
#
# Template record:
# {
#   id, name, vendor, model, category,
#   form_factor,          # '19"' | '21"' | 'OCP' | ...
#   u_size,               # rack units consumed (0 for 0U / cables)
#   description,
#   scope: 'global'|'project',  project_id,
#   ports: [
#     { id, name, port_type, connector, speed_gbps, count,
#       breakout_fan_out }   # >1 means this port supports breakout
#   ],
#   params: {},           # free-form extra fields
# }

def get_hw_template(tid):
    raw = r.get(_tmpl_key(tid))
    return json.loads(raw) if raw else None

def save_hw_template(tmpl):
    r.set(_tmpl_key(tmpl['id']), json.dumps(tmpl))
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.sadd(f'project:{tmpl["project_id"]}:hw:templates', tmpl['id'])
    else:
        r.sadd(HW_TMPL_INDEX, tmpl['id'])

def delete_hw_template(tid):
    tmpl = get_hw_template(tid)
    if not tmpl: return
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.srem(f'project:{tmpl["project_id"]}:hw:templates', tid)
    else:
        r.srem(HW_TMPL_INDEX, tid)
    r.delete(_tmpl_key(tid))

def global_hw_templates(category=None) -> list:
    tmpls = [t for t in (get_hw_template(tid) for tid in r.smembers(HW_TMPL_INDEX)) if t]
    if category:
        tmpls = [t for t in tmpls if t['category'] == category]
    return sorted(tmpls, key=lambda t: (t['category'], t['name']))

def project_hw_templates(pid, category=None) -> list:
    key   = f'project:{pid}:hw:templates'
    tmpls = [t for t in (get_hw_template(tid) for tid in r.smembers(key)) if t]
    if category:
        tmpls = [t for t in tmpls if t['category'] == category]
    return sorted(tmpls, key=lambda t: (t['category'], t['name']))

def available_hw_templates(pid, category=None) -> dict:
    return {
        'global':  global_hw_templates(category),
        'project': project_hw_templates(pid, category),
    }

def all_hw_templates_for_project(pid, category=None) -> list:
    """Flat list: global + project."""
    av = available_hw_templates(pid, category)
    return av['global'] + av['project']

# ══════════════════════════════════════════════════════════════════════════════
# Bill of Materials helpers
# ══════════════════════════════════════════════════════════════════════════════
#
# BoM = list of line items:
# { id, template_id, qty, tag_prefix, tag_start, tag_pad, description }

def get_bom(pid) -> list:
    raw = r.get(_bom_key(pid))
    return json.loads(raw) if raw else []

def save_bom(pid, bom: list):
    r.set(_bom_key(pid), json.dumps(bom))

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
#
# Instance record:
# { id, template_id, project_id, asset_tag, serial, status,
#   location: { rack_id, u_pos } | {}  }

def get_hw_instance(iid):
    raw = r.get(_inst_key(iid))
    return json.loads(raw) if raw else None

def save_hw_instance(inst):
    r.set(_inst_key(inst['id']), json.dumps(inst))
    r.sadd(HW_INST_INDEX, inst['id'])
    r.sadd(f'project:{inst["project_id"]}:hw:instances', inst['id'])

def delete_hw_instance(iid):
    inst = get_hw_instance(iid)
    if not inst: return
    r.srem(HW_INST_INDEX, iid)
    r.srem(f'project:{inst["project_id"]}:hw:instances', iid)
    # Remove from rack if placed
    if inst.get('location', {}).get('rack_id'):
        _remove_from_rack(inst['location']['rack_id'], iid)
    r.delete(_inst_key(iid))

def project_instances(pid, category=None) -> list:
    key   = f'project:{pid}:hw:instances'
    insts = []
    for iid in r.smembers(key):
        inst = get_hw_instance(iid)
        if not inst: continue
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
    tmpl   = get_hw_template(item['template_id'])
    if not tmpl: raise ValueError(f'Template {item["template_id"]} not found')
    prefix = item.get('tag_prefix', tmpl['name'][:8].replace(' ', '-'))
    start  = int(item.get('tag_start', 1))
    pad    = int(item.get('tag_pad', 3))
    qty    = int(item.get('qty', 1))
    created = []
    for i in range(qty):
        tag  = f'{prefix}-{str(start + i).zfill(pad)}'
        inst = {
            'id':         _new_id(),
            'template_id': item['template_id'],
            'project_id':  pid,
            'asset_tag':   tag,
            'serial':      '',
            'status':      'in-stock',
            'location':    {},
            'port_overrides': {},  # port_id → {notes, mac, ip}
        }
        save_hw_instance(inst)
        created.append(inst)
    return created

# ══════════════════════════════════════════════════════════════════════════════
# Rack layout helpers
# ══════════════════════════════════════════════════════════════════════════════
#
# Slots list: [{ u_pos (1-based, bottom=1), instance_id }]

def get_rack_slots(rack_iid) -> list:
    raw = r.get(_rack_slots_key(rack_iid))
    return json.loads(raw) if raw else []

def save_rack_slots(rack_iid, slots: list):
    r.set(_rack_slots_key(rack_iid), json.dumps(slots))

def _remove_from_rack(rack_iid, instance_iid):
    slots = [s for s in get_rack_slots(rack_iid) if s['instance_id'] != instance_iid]
    save_rack_slots(rack_iid, slots)

def place_in_rack(rack_iid: str, instance_iid: str, u_pos: int) -> list:
    """
    Place an instance in a rack at u_pos.
    Returns list of validation issues (may be empty).
    """
    issues = []
    rack_inst = get_hw_instance(rack_iid)
    inst      = get_hw_instance(instance_iid)
    if not rack_inst or not inst:
        return [{'severity': 'error', 'code': 'NOT_FOUND',
                 'message': 'Rack or instance not found'}]

    rack_tmpl = get_hw_template(rack_inst['template_id'])
    dev_tmpl  = get_hw_template(inst['template_id'])
    if not rack_tmpl or not dev_tmpl:
        return [{'severity': 'error', 'code': 'TEMPLATE_NOT_FOUND',
                 'message': 'Template missing'}]

    # Form factor check
    ff_issues = _check_form_factor(rack_tmpl, dev_tmpl, instance_iid)
    issues.extend(ff_issues)

    # U space check
    rack_u  = int(rack_tmpl.get('u_size', 42))
    dev_u   = int(dev_tmpl.get('u_size', 1))
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
        other_u    = int(other_tmpl.get('u_size', 1)) if other_tmpl else 1
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
    rack_ff = rack_tmpl.get('form_factor', '19"')
    dev_ff  = dev_tmpl.get('form_factor', '19"')
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
    if not rack_inst: return {}
    rack_tmpl = get_hw_template(rack_inst['template_id'])
    rack_u    = int(rack_tmpl.get('u_size', 42)) if rack_tmpl else 42

    slots     = get_rack_slots(rack_iid)
    # Build u → slot map
    u_map = {}
    for slot in slots:
        inst      = get_hw_instance(slot['instance_id'])
        dev_tmpl  = get_hw_template(inst['template_id']) if inst else None
        dev_u     = int(dev_tmpl.get('u_size', 1)) if dev_tmpl else 1
        for u in range(slot['u_pos'], slot['u_pos'] + dev_u):
            u_map[u] = {
                'instance': inst,
                'template': dev_tmpl,
                'u_start':  slot['u_pos'],
                'u_size':   dev_u,
                'is_top':   u == slot['u_pos'],
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
        'rack_u':        rack_u,
        'rows':          rows,
        'slots':         slots,
    }

# ══════════════════════════════════════════════════════════════════════════════
# Cable plant helpers
# ══════════════════════════════════════════════════════════════════════════════
#
# Cable record:
# { id, template_id, project_id, asset_tag, label, length_m,
#   end_a: { instance_id, port_id, port_name },
#   end_b: { instance_id, port_id, port_name },
#   breakout: false,         # true for fan-out cables
#   breakout_fan_out: 1,     # e.g. 4 for QSFP28→4×SFP28
# }

def get_cable(cid):
    raw = r.get(_cable_key(cid))
    return json.loads(raw) if raw else None

def save_cable(cable):
    r.set(_cable_key(cable['id']), json.dumps(cable))
    r.sadd(HW_CABLE_INDEX, cable['id'])
    r.sadd(f'project:{cable["project_id"]}:hw:cables', cable['id'])

def delete_cable(cid):
    cable = get_cable(cid)
    if not cable: return
    r.srem(HW_CABLE_INDEX, cid)
    r.srem(f'project:{cable["project_id"]}:hw:cables', cid)
    r.delete(_cable_key(cid))

def project_cables(pid) -> list:
    key    = f'project:{pid}:hw:cables'
    cables = []
    for cid in r.smembers(key):
        cable = get_cable(cid)
        if not cable: continue
        # Enrich with template + endpoint instance info
        cable['template']   = get_hw_template(cable.get('template_id'))
        cable['inst_a']     = get_hw_instance(cable['end_a'].get('instance_id',''))
        cable['inst_b']     = get_hw_instance(cable['end_b'].get('instance_id',''))
        cables.append(cable)
    return sorted(cables, key=lambda c: c.get('asset_tag',''))

def _get_port(instance_id: str, port_id: str) -> dict | None:
    inst = get_hw_instance(instance_id)
    if not inst: return None
    tmpl = get_hw_template(inst['template_id'])
    if not tmpl: return None
    return next((p for p in tmpl.get('ports', []) if p['id'] == port_id), None)

def _used_ports(pid: str) -> dict:
    """
    Return dict: (instance_id, port_id) → cable_id
    for all cables in project.
    """
    used = {}
    for cid in r.smembers(f'project:{pid}:hw:cables'):
        cable = get_cable(cid)
        if not cable: continue
        key_a = (cable['end_a'].get('instance_id'), cable['end_a'].get('port_id'))
        key_b = (cable['end_b'].get('instance_id'), cable['end_b'].get('port_id'))
        if key_a[0]: used[key_a] = cid
        if key_b[0]: used[key_b] = cid
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
    used_ports = _used_ports(pid)
    if used_ports:
        pass

    # ── Cable validation ──────────────────────────────────────────────────────
    seen_cable_ends = {}
    for cable in cables:
        cid    = cable['id']
        tmpl   = cable.get('template')
        end_a  = cable['end_a']
        end_b  = cable['end_b']

        # Both ends must be connected
        if not end_a.get('instance_id') or not end_a.get('port_id'):
            issues.append(_issue('warning', 'CABLE_UNCONNECTED_A',
                f'Cable {cable["asset_tag"]} end A is not connected', {'cable': cid}))
        if not end_b.get('instance_id') or not end_b.get('port_id'):
            issues.append(_issue('warning', 'CABLE_UNCONNECTED_B',
                f'Cable {cable["asset_tag"]} end B is not connected', {'cable': cid}))

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
            issues.append(_issue('error', 'CONNECTOR_MISMATCH',
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
        if not rack_tmpl: continue
        rack_u    = int(rack_tmpl.get('u_size', 42))
        slots     = get_rack_slots(rack['id'])

        # Build occupancy map and check overlaps / form factor
        occupied = {}
        for slot in slots:
            inst     = get_hw_instance(slot['instance_id'])
            if not inst: continue
            dev_tmpl = get_hw_template(inst['template_id'])
            if not dev_tmpl: continue

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
    return {'severity': severity, 'code': code,
            'message': message, 'context': context or {}}


def _check_cable_port_type(cable_type, port_a, port_b, cable, issues):
    """Cross-check cable type vs port type."""
    pt_a = port_a.get('port_type', 'data')
    pt_b = port_b.get('port_type', 'data')
    if cable_type == 'power':
        if pt_a not in ('power',) or pt_b not in ('power',):
            issues.append(_issue('error', 'CABLE_PORT_TYPE_MISMATCH',
                f'Cable {cable["asset_tag"]}: power cable connected to non-power port',
                {'cable': cable['id']}))
    elif cable_type in ('DAC', 'AOC', 'fiber-patch', 'copper-patch'):
        if pt_a == 'power' or pt_b == 'power':
            issues.append(_issue('error', 'CABLE_PORT_TYPE_MISMATCH',
                f'Cable {cable["asset_tag"]}: data cable connected to power port',
                {'cable': cable['id']}))


def load_validation(pid: str) -> list:
    raw = r.get(_validation_key(pid))
    return json.loads(raw) if raw else []

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Global config (connectors + compat matrix)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/admin/hw/connectors', methods=['GET', 'POST'])
def hw_connectors():
    seed_connectors()
    if request.method == 'POST':
        action = request.form.get('action')
        name   = request.form.get('name', '').strip()
        if action == 'add' and name:
            add_connector(name)
            flash(f'Connector "{name}" added.', 'success')
        elif action == 'delete' and name:
            remove_connector(name)
            flash(f'Connector "{name}" removed.', 'info')
        elif action == 'compat':
            conn_a = request.form.get('conn_a', '')
            conn_b = request.form.get('conn_b', '')
            val    = request.form.get('compatible') == '1'
            if conn_a and conn_b:
                set_compat(conn_a, conn_b, val)
                flash(f'Compatibility {conn_a} ↔ {conn_b} updated.', 'success')
        return redirect(url_for('hw.hw_connectors'))
    return render_template('hw/connectors.html',
                           connectors=all_connectors(),
                           matrix=full_compat_matrix())

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Hardware templates
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/hw/templates')
def hw_templates_list():
    return render_template('hw/templates_list.html',
                           global_tmpls=global_hw_templates(),
                           categories=CATEGORIES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/add',              methods=['GET', 'POST'])
@hw_bp.route('/projects/<pid>/hw/templates/add', methods=['GET', 'POST'])
def add_hw_template(pid=None):
    from ipam import get_project
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Name is required.', 'danger')
            return redirect(request.url)
        ports_raw = request.form.get('ports_json', '[]')
        try:
            ports = json.loads(ports_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid ports JSON: {e}', 'danger')
            return redirect(request.url)
        tmpl = {
            'id':           _new_id(),
            'name':         name,
            'vendor':       request.form.get('vendor', '').strip(),
            'model':        request.form.get('model', '').strip(),
            'category':     request.form.get('category', 'server'),
            'form_factor':  request.form.get('form_factor', '19"'),
            'u_size':       int(request.form.get('u_size', 1) or 1),
            'cable_type':   request.form.get('cable_type', ''),
            'description':  request.form.get('description', ''),
            'ports':        ports,
            'scope':        'project' if pid else 'global',
            'project_id':   pid or '',
        }
        save_hw_template(tmpl)
        flash(f'Hardware template "{name}" saved.', 'success')
        return redirect(url_for('hw.project_hw_templates', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=None, proj=proj,
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/edit', methods=['GET', 'POST'])
def edit_hw_template(tid):
    from ipam import get_project
    tmpl = get_hw_template(tid)
    if not tmpl: abort(404)
    pid  = tmpl.get('project_id') or None
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        ports_raw = request.form.get('ports_json', '[]')
        try:
            ports = json.loads(ports_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid ports JSON: {e}', 'danger')
            return redirect(request.url)
        tmpl['name']        = request.form.get('name', tmpl['name']).strip()
        tmpl['vendor']      = request.form.get('vendor', '').strip()
        tmpl['model']       = request.form.get('model', '').strip()
        tmpl['category']    = request.form.get('category', tmpl['category'])
        tmpl['form_factor'] = request.form.get('form_factor', tmpl['form_factor'])
        tmpl['u_size']      = int(request.form.get('u_size', 1) or 1)
        tmpl['cable_type']  = request.form.get('cable_type', '')
        tmpl['description'] = request.form.get('description', '')
        tmpl['ports']       = ports
        save_hw_template(tmpl)
        flash(f'Template "{tmpl["name"]}" updated.', 'success')
        return redirect(url_for('hw.project_hw_templates', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=tmpl, proj=proj,
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/delete', methods=['POST'])
def delete_hw_template_route(tid):
    tmpl = get_hw_template(tid)
    if not tmpl:
        abort(404)
    pid = tmpl.get('project_id') or None
    delete_hw_template(tid)
    flash(f'Template "{tmpl["name"]}" deleted.', 'info')
    return redirect(url_for('hw.project_hw_templates_route', pid=pid) if pid
                    else url_for('hw.hw_templates_list'))


@hw_bp.route('/projects/<pid>/hw/templates')
def project_hw_templates_route(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    return render_template('hw/project_templates.html',
                           proj=proj,
                           global_tmpls=global_hw_templates(),
                           project_tmpls=project_hw_templates(pid),
                           categories=CATEGORIES)

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Bill of Materials
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/bom', methods=['GET', 'POST'])
def project_bom(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    if request.method == 'POST':
        bom_raw = request.form.get('bom_json', '[]')
        try:
            bom = json.loads(bom_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid BoM JSON: {e}', 'danger')
            return redirect(request.url)
        # Ensure all lines have an id
        for item in bom:
            if not item.get('id'):
                item['id'] = _new_id()
        save_bom(pid, bom)
        flash('Bill of Materials saved.', 'success')
        return redirect(url_for('hw.project_bom', pid=pid))
    bom       = bom_with_templates(pid)
    templates = all_hw_templates_for_project(pid)
    return render_template('hw/bom.html', proj=proj, bom=bom,
                           templates=templates, categories=CATEGORIES)


@hw_bp.route('/projects/<pid>/bom/generate', methods=['POST'])
def generate_from_bom(pid):
    """Generate hardware instances from a single BoM line."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    item_id = request.form.get('item_id', '').strip()
    bom     = get_bom(pid)
    item    = next((i for i in bom if i['id'] == item_id), None)
    if not item:
        flash('BoM line not found.', 'danger')
        return redirect(url_for('hw.project_bom', pid=pid))
    try:
        created = generate_instances_from_bom_line(pid, item)
        flash(f'{len(created)} instance(s) created from BoM line.', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('hw.project_inventory', pid=pid))


@hw_bp.route('/projects/<pid>/bom/generate-all', methods=['POST'])
def generate_all_from_bom(pid):
    """Generate instances for all BoM lines."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    bom   = get_bom(pid)
    total = 0
    for item in bom:
        try:
            created = generate_instances_from_bom_line(pid, item)
            total  += len(created)
        except ValueError:
            pass
    flash(f'{total} instance(s) generated from full BoM.', 'success')
    return redirect(url_for('hw.project_inventory', pid=pid))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Inventory (instances)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/inventory')
def project_inventory(pid):
    from ipam import get_project
    proj      = get_project(pid)
    if not proj: abort(404)
    cat       = request.args.get('category', '')
    instances = project_instances(pid, category=cat or None)
    racks     = project_instances(pid, category='rack')
    return render_template('hw/inventory.html', proj=proj,
                           instances=instances, racks=racks,
                           categories=CATEGORIES, selected_cat=cat)


@hw_bp.route('/projects/<pid>/hw/instances/add', methods=['GET', 'POST'])
def add_hw_instance(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    if request.method == 'POST':
        tid = request.form.get('template_id', '').strip()
        if not tid:
            flash('Select a template.', 'danger')
            return redirect(request.url)
        inst = {
            'id':          _new_id(),
            'template_id': tid,
            'project_id':  pid,
            'asset_tag':   request.form.get('asset_tag', '').strip(),
            'serial':      request.form.get('serial', '').strip(),
            'status':      request.form.get('status', 'in-stock'),
            'location':    {},
            'port_overrides': {},
        }
        save_hw_instance(inst)
        flash(f'Instance {inst["asset_tag"] or inst["id"]} added.', 'success')
        return redirect(url_for('hw.project_inventory', pid=pid))
    templates = all_hw_templates_for_project(pid)
    return render_template('hw/instance_form.html', proj=proj,
                           inst=None, templates=templates)


@hw_bp.route('/projects/<pid>/hw/instances/<iid>/edit', methods=['GET', 'POST'])
def edit_hw_instance(pid, iid):
    from ipam import get_project
    proj = get_project(pid)
    inst = get_hw_instance(iid)
    if not proj or not inst: abort(404)
    if request.method == 'POST':
        inst['asset_tag'] = request.form.get('asset_tag', '').strip()
        inst['serial']    = request.form.get('serial', '').strip()
        inst['status']    = request.form.get('status', 'in-stock')
        save_hw_instance(inst)
        flash('Instance updated.', 'success')
        return redirect(url_for('hw.project_inventory', pid=pid))
    templates = all_hw_templates_for_project(pid)
    return render_template('hw/instance_form.html', proj=proj,
                           inst=inst, templates=templates)


@hw_bp.route('/projects/<pid>/hw/instances/<iid>/delete', methods=['POST'])
def delete_hw_instance_route(pid, iid):
    delete_hw_instance(iid)
    flash('Instance deleted.', 'info')
    return redirect(url_for('hw.project_inventory', pid=pid))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Rack layout
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/racks')
def rack_list(pid):
    from ipam import get_project
    proj  = get_project(pid)
    if not proj: abort(404)
    racks = project_instances(pid, category='rack')
    for rack in racks:
        slots    = get_rack_slots(rack['id'])
        rack_u   = int(rack['template']['u_size']) if rack.get('template') else 42
        used_u   = 0
        for slot in slots:
            inst = get_hw_instance(slot['instance_id'])
            if inst:
                t = get_hw_template(inst['template_id'])
                if t: used_u += int(t.get('u_size', 1))
        rack['slot_count'] = len(slots)
        rack['used_u']     = used_u
        rack['free_u']     = rack_u - used_u
        rack['utilization']= round((used_u / rack_u) * 100) if rack_u else 0
    return render_template('hw/rack_list.html', proj=proj, racks=racks)


@hw_bp.route('/projects/<pid>/hw/racks/<rack_iid>')
def rack_detail(pid, rack_iid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    view      = rack_layout_view(rack_iid)
    if not view: abort(404)
    unplaced  = [i for i in project_instances(pid)
                 if i['id'] != rack_iid
                 and not i.get('location', {}).get('rack_id')
                 and i.get('template', {}).get('category') not in ('rack', 'cable')]
    issues    = load_validation(pid)
    rack_issues = [i for i in issues
                   if i.get('context', {}).get('rack') == rack_iid]
    return render_template('hw/rack_detail.html', proj=proj,
                           view=view, unplaced=unplaced,
                           issues=rack_issues)


@hw_bp.route('/projects/<pid>/hw/racks/<rack_iid>/place', methods=['POST'])
def place_device(pid, rack_iid):
    iid   = request.form.get('instance_id', '').strip()
    u_pos = int(request.form.get('u_pos', 1) or 1)
    issues = place_in_rack(rack_iid, iid, u_pos)
    errors = [i for i in issues if i['severity'] == 'error']
    if errors:
        for e in errors:
            flash(e['message'], 'danger')
    else:
        warnings = [i for i in issues if i['severity'] == 'warning']
        for w in warnings:
            flash(w['message'], 'warning')
        flash('Device placed successfully.', 'success')
    return redirect(url_for('hw.rack_detail', pid=pid, rack_iid=rack_iid))


@hw_bp.route('/projects/<pid>/hw/racks/<rack_iid>/remove', methods=['POST'])
def remove_from_rack_route(pid, rack_iid):
    iid  = request.form.get('instance_id', '').strip()
    inst = get_hw_instance(iid)
    if inst:
        _remove_from_rack(rack_iid, iid)
        inst['location'] = {}
        save_hw_instance(inst)
        flash('Device removed from rack.', 'info')
    return redirect(url_for('hw.rack_detail', pid=pid, rack_iid=rack_iid))


@hw_bp.route('/api/projects/<pid>/hw/racks/<rack_iid>/place', methods=['POST'])
def api_place_device(pid, rack_iid):
    """JSON API for drag-and-drop placement."""
    data   = request.get_json(force=True) or {}
    iid    = data.get('instance_id', '')
    u_pos  = int(data.get('u_pos', 1))
    issues = place_in_rack(rack_iid, iid, u_pos)
    return jsonify({'issues': issues,
                    'ok': not any(i['severity']=='error' for i in issues)})


@hw_bp.route('/projects/<pid>/hw/rack-table', methods=['GET', 'POST'])
def rack_table(pid):
    """Table-based bulk placement — useful for 60+ rack deployments."""
    from ipam import get_project
    proj  = get_project(pid)
    if not proj: abort(404)
    racks = project_instances(pid, category='rack')
    if request.method == 'POST':
        placements = request.get_json(force=True) or []
        results    = []
        for p in placements:
            issues = place_in_rack(p['rack_id'], p['instance_id'], int(p['u_pos']))
            results.append({'rack': p['rack_id'], 'device': p['instance_id'],
                            'issues': issues,
                            'ok': not any(i['severity']=='error' for i in issues)})
        return jsonify(results)
    devices = [i for i in project_instances(pid)
               if i.get('template', {}).get('category') not in ('rack', 'cable')]
    return render_template('hw/rack_table.html', proj=proj,
                           racks=racks, devices=devices)

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Cable plant
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/cables')
def cable_list(pid):
    from ipam import get_project
    proj   = get_project(pid)
    if not proj: abort(404)
    cables = project_cables(pid)
    issues = load_validation(pid)
    cable_issue_ids = {i.get('context', {}).get('cable') for i in issues}
    return render_template('hw/cable_list.html', proj=proj,
                           cables=cables, cable_issue_ids=cable_issue_ids)


@hw_bp.route('/projects/<pid>/hw/cables/add', methods=['GET', 'POST'])
def add_cable(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    if request.method == 'POST':
        tid  = request.form.get('template_id', '').strip() or None
        end_a = {
            'instance_id': request.form.get('end_a_instance', '').strip(),
            'port_id':     request.form.get('end_a_port', '').strip(),
        }
        end_b = {
            'instance_id': request.form.get('end_b_instance', '').strip(),
            'port_id':     request.form.get('end_b_port', '').strip(),
        }
        cable = {
            'id':          _new_id(),
            'template_id': tid,
            'project_id':  pid,
            'asset_tag':   request.form.get('asset_tag', '').strip(),
            'label':       request.form.get('label', '').strip(),
            'length_m':    request.form.get('length_m', ''),
            'end_a':       end_a,
            'end_b':       end_b,
            'breakout':    request.form.get('breakout') == '1',
            'breakout_fan_out': int(request.form.get('breakout_fan_out', 1) or 1),
        }
        save_cable(cable)
        flash(f'Cable {cable["asset_tag"] or cable["id"]} added.', 'success')
        return redirect(url_for('hw.cable_list', pid=pid))
    cable_tmpls = all_hw_templates_for_project(pid, category='cable')
    instances   = [i for i in project_instances(pid)
                   if i.get('template', {}).get('category') != 'cable']
    return render_template('hw/cable_form.html', proj=proj,
                           cable=None, cable_tmpls=cable_tmpls,
                           instances=instances)


@hw_bp.route('/projects/<pid>/hw/cables/<cid>/edit', methods=['GET', 'POST'])
def edit_cable(pid, cid):
    from ipam import get_project
    proj  = get_project(pid)
    cable = get_cable(cid)
    if not proj or not cable: abort(404)
    if request.method == 'POST':
        cable['asset_tag']        = request.form.get('asset_tag', '').strip()
        cable['label']            = request.form.get('label', '').strip()
        cable['length_m']         = request.form.get('length_m', '')
        cable['template_id']      = request.form.get('template_id', '') or None
        cable['end_a']            = {
            'instance_id': request.form.get('end_a_instance','').strip(),
            'port_id':     request.form.get('end_a_port','').strip(),
        }
        cable['end_b']            = {
            'instance_id': request.form.get('end_b_instance','').strip(),
            'port_id':     request.form.get('end_b_port','').strip(),
        }
        cable['breakout']         = request.form.get('breakout') == '1'
        cable['breakout_fan_out'] = int(request.form.get('breakout_fan_out',1) or 1)
        save_cable(cable)
        flash('Cable updated.', 'success')
        return redirect(url_for('hw.cable_list', pid=pid))
    cable_tmpls = all_hw_templates_for_project(pid, category='cable')
    instances   = [i for i in project_instances(pid)
                   if i.get('template', {}).get('category') != 'cable']
    return render_template('hw/cable_form.html', proj=proj,
                           cable=cable, cable_tmpls=cable_tmpls,
                           instances=instances)


@hw_bp.route('/projects/<pid>/hw/cables/<cid>/delete', methods=['POST'])
def delete_cable_route(pid, cid):
    delete_cable(cid)
    flash('Cable deleted.', 'info')
    return redirect(url_for('hw.cable_list', pid=pid))


@hw_bp.route('/api/projects/<pid>/hw/instance-ports/<iid>')
def api_instance_ports(pid, iid):
    """Return ports for a given instance — used by cable form JS."""
    inst = get_hw_instance(iid)
    if not inst: return jsonify([])
    tmpl = get_hw_template(inst['template_id'])
    if not tmpl: return jsonify([])
    used = _used_ports(pid)
    ports = []
    for p in tmpl.get('ports', []):
        key  = (iid, p['id'])
        port = dict(p)
        port['in_use']    = key in used
        port['cable_tag'] = get_cable(used[key])['asset_tag'] if key in used else None
        ports.append(port)
    return jsonify(ports)

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Validation
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/validate')
def hw_validate(pid):
    from ipam import get_project
    proj   = get_project(pid)
    if not proj: abort(404)
    issues = validate_project(pid)
    errors   = [i for i in issues if i['severity'] == 'error']
    warnings = [i for i in issues if i['severity'] == 'warning']
    return render_template('hw/validation.html', proj=proj,
                           issues=issues, errors=errors, warnings=warnings)


@hw_bp.route('/api/projects/<pid>/hw/validate')
def api_hw_validate(pid):
    issues = validate_project(pid)
    return jsonify({
        'total':    len(issues),
        'errors':   sum(1 for i in issues if i['severity']=='error'),
        'warnings': sum(1 for i in issues if i['severity']=='warning'),
        'issues':   issues,
    })
