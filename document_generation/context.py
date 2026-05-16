"""
Stage 1: Build a frozen context snapshot from live project data.

This is the contract that customer Jinja2 templates depend on.
context_schema_version must be bumped on any breaking change.
"""
import json
from datetime import datetime, timezone

CONTEXT_SCHEMA_VERSION = 1


def build_context(pid, user_id=None, user_name=None, user_email=None):
    """
    Freeze the current state of project `pid` into a context dict.

    All subsequent pipeline stages are deterministic from this dict.
    """
    import db
    from ipam import get_project, project_networks, get_network
    from ne import (
        get_site, get_pod,
        _proj_sites_key, _proj_pods_key, _proj_netypes_key,
        get_ne_type, NE_INSTS_INDEX,
    )
    from hw_logic import (
        get_hw_template, get_hw_instance,
        project_instances, HW_INST_INDEX,
    )
    from customer import get_customer

    r = db.r

    # ── Project ──────────────────────────────────────────────────────────────
    project = get_project(pid)
    if not project:
        raise ValueError(f"Project {pid!r} not found")

    customer_id = project.get('customer_id', '')
    customer = get_customer(customer_id) if customer_id else {}

    # ── Sites ─────────────────────────────────────────────────────────────────
    site_ids = list(r.smembers(f'project:{pid}:sites'))
    sites = [s for sid in site_ids if (s := get_site(sid))]

    # ── PODs ─────────────────────────────────────────────────────────────────
    pod_ids = list(r.smembers(f'project:{pid}:pods'))
    pods = []
    for pod_id in pod_ids:
        pod = get_pod(pod_id)
        if pod:
            # attach site references via relations
            from core.relations import related
            site_fwd = related('pod_of_site', pod_id, direction='fwd')
            pod['site_ids'] = list(site_fwd)
            pods.append(pod)

    # ── NE types ─────────────────────────────────────────────────────────────
    ne_type_ids = list(r.smembers(f'project:{pid}:ne_types')) + \
                  list(r.smembers('ne_types:index'))
    ne_types = []
    seen_ne_type_ids = set()
    for tid in ne_type_ids:
        if tid not in seen_ne_type_ids:
            nt = get_ne_type(tid)
            if nt:
                ne_types.append(nt)
                seen_ne_type_ids.add(tid)

    # ── NE instances ─────────────────────────────────────────────────────────
    ne_inst_ids = list(r.smembers(f'ne:instances:project:{pid}'))
    ne_instances = []
    for iid in ne_inst_ids:
        raw = r.get(f'ne:instance:{iid}')
        if raw:
            ne_instances.append(json.loads(raw))

    # ── HW templates ─────────────────────────────────────────────────────────
    hw_tmpl_ids = list(r.smembers(f'project:{pid}:hw:templates')) + \
                  list(r.smembers('hw:templates:index'))
    hw_templates = []
    seen_hw_tmpl_ids = set()
    for tid in hw_tmpl_ids:
        if tid not in seen_hw_tmpl_ids:
            tmpl = get_hw_template(tid)
            if tmpl:
                hw_templates.append(tmpl)
                seen_hw_tmpl_ids.add(tid)

    # ── HW instances ─────────────────────────────────────────────────────────
    hw_instance_ids = list(r.smembers(f'project:{pid}:hw:instances'))
    hw_instances = []
    racks = []
    for iid in hw_instance_ids:
        inst = get_hw_instance(iid)
        if inst:
            hw_instances.append(inst)
            if inst.get('category') == 'rack':
                # attach rack slots
                slots_raw = r.get(f'hw:rack:{iid}:slots')
                inst['rack_slots'] = json.loads(slots_raw) if slots_raw else []
                racks.append(inst)

    # ── Cables ───────────────────────────────────────────────────────────────
    cable_ids = list(r.smembers(f'project:{pid}:hw:cables'))
    cables = []
    for cid in cable_ids:
        raw = r.get(f'hw:cable:{cid}')
        if raw:
            cables.append(json.loads(raw))

    # ── Networks / subnets ───────────────────────────────────────────────────
    net_ids = list(r.smembers(f'project:{pid}:networks'))
    networks = []
    ip_allocations = []
    for nid in net_ids:
        net = get_network(nid)
        if not net:
            continue
        networks.append(net)
        # Expand IPs
        for ip_str in r.smembers(f'network:{nid}:ips'):
            ip_raw = r.get(f'ip:{ip_str}')
            if ip_raw:
                ip_rec = json.loads(ip_raw)
                ip_rec['network_id'] = nid
                ip_allocations.append(ip_rec)

    # ── VRFs ─────────────────────────────────────────────────────────────────
    vrf_ids = list(r.smembers(f'project:{pid}:vrfs'))
    vrfs = []
    for vid in vrf_ids:
        raw = r.get(f'vrf:{vid}')
        if raw:
            vrfs.append(json.loads(raw))

    # ── bindings_flat (denormalized NE-iface ↔ HW-port rows) ─────────────────
    bindings_flat = _build_bindings_flat(ne_instances, hw_instances, ne_types)

    # ── Missing summary ───────────────────────────────────────────────────────
    missing_fields = []
    # Simple heuristic: flag NE instances without mgmt IP
    for ne_inst in ne_instances:
        if not ne_inst.get('mgmt_ip') and not ne_inst.get('params', {}).get('mgmt_ip'):
            missing_fields.append(f"ne_instances[{ne_inst.get('id','?')}].mgmt_ip")

    return {
        'context_schema_version': CONTEXT_SCHEMA_VERSION,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'generated_by': {
            'user_id':  user_id  or 'system',
            'name':     user_name  or 'system',
            'email':    user_email or '',
        },
        'approvals': [],

        'project':  project,
        'customer': customer,

        'sites': sites,
        'pods':  pods,
        'racks': racks,

        'vrfs':           vrfs,
        'networks':       networks,
        'ip_allocations': ip_allocations,

        'ne_types':     ne_types,
        'ne_instances': ne_instances,

        'hw_templates': hw_templates,
        'hw_instances': hw_instances,
        'cables':       cables,

        'bindings_flat': bindings_flat,

        'validation': {},

        'missing': {
            'fields':  missing_fields,
            'summary': f'{len(missing_fields)} issues require attention before release'
                       if missing_fields else 'No issues detected',
        },
    }


def _build_bindings_flat(ne_instances, hw_instances, ne_types):
    """Produce one row per NE-iface ↔ HW-port binding, denormalized."""
    hw_by_id = {h['id']: h for h in hw_instances}
    rows = []
    for ne_inst in ne_instances:
        for iface in ne_inst.get('iface_bindings', []):
            hw_iid  = iface.get('hw_instance_id')
            hw_inst = hw_by_id.get(hw_iid, {})
            rows.append({
                'ne_instance_id':   ne_inst.get('id'),
                'ne_instance_name': ne_inst.get('name'),
                'iface_name':       iface.get('iface_name') or iface.get('name'),
                'hw_instance_id':   hw_iid,
                'hw_instance_name': hw_inst.get('name', ''),
                'port_name':        iface.get('port_name', ''),
                'connector':        iface.get('connector', ''),
            })
    return rows
