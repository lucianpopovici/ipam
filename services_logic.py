"""
Business logic for customer-scoped service definitions.

A service is a named bundle of required fields (e.g. "oob" with fields
"hostname" and "domain") linked to NE-type interfaces. Values are stored
per-binding and resolved on the requirements page.
"""
import json
import uuid

import db
from core import relations

# ── Redis key helpers ─────────────────────────────────────────────────────────

def _service_key(sid: str) -> str:
    return f'service:{sid}'

def _customer_services_key(cid: str) -> str:
    return f'customer:{cid}:services'

# ── Composite iface key (ne_type_id:iface_id) ─────────────────────────────────

def iface_key(ne_type_id: str, iface_id: str) -> str:
    return f'{ne_type_id}:{iface_id}'

def split_iface_key(ik: str):
    """Return (ne_type_id, iface_id) or (ik, '') on bad format."""
    parts = ik.split(':', 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (ik, '')

# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_service(sid: str) -> dict | None:
    raw = db.r.get(_service_key(sid))
    return json.loads(raw) if raw else None


def save_service(svc: dict) -> dict:
    sid = svc['id']
    db.r.set(_service_key(sid), json.dumps(svc))
    db.r.sadd(_customer_services_key(svc['customer_id']), sid)
    return svc


def delete_service(sid: str):
    svc = get_service(sid)
    if not svc:
        return
    for ik in list(relations.related('iface_of_service', sid, 'rev')):
        relations.unrelate('iface_of_service', ik, sid)
    db.r.srem(_customer_services_key(svc['customer_id']), sid)
    db.r.delete(_service_key(sid))


def customer_services(cid: str) -> list:
    sids = db.r.smembers(_customer_services_key(cid))
    svcs = [s for sid in sids if (s := get_service(sid))]
    return sorted(svcs, key=lambda s: s.get('name', ''))


def new_service_field(name: str, label: str, field_type: str = 'text',
                      required: bool = False, options=None,
                      default: str = '', scope_override=None) -> dict:
    return {
        'id':             str(uuid.uuid4())[:8],
        'name':           name,
        'label':          label,
        'field_type':     field_type,
        'required':       required,
        'options':        options or [],
        'default':        default,
        'scope_override': scope_override,
    }

# ── iface↔service relations ───────────────────────────────────────────────────

def link_service_to_iface(service_id: str, ne_type_id: str, iface_id: str):
    relations.relate('iface_of_service', iface_key(ne_type_id, iface_id), service_id)


def unlink_service_from_iface(service_id: str, ne_type_id: str, iface_id: str):
    relations.unrelate('iface_of_service', iface_key(ne_type_id, iface_id), service_id)


def services_for_iface(ne_type_id: str, iface_id: str) -> list:
    ik = iface_key(ne_type_id, iface_id)
    sids = relations.related('iface_of_service', ik, 'fwd')
    svcs = [s for sid in sids if (s := get_service(sid))]
    return sorted(svcs, key=lambda s: s.get('name', ''))


def ifaces_for_service(service_id: str) -> list:
    """Return list of (ne_type_id, iface_id) tuples linked to this service."""
    iks = relations.related('iface_of_service', service_id, 'rev')
    return [split_iface_key(ik) for ik in iks]


def clear_iface_service_links(ne_type_id: str, iface_id: str):
    """Remove all service links for a given iface (called on iface deletion)."""
    ik = iface_key(ne_type_id, iface_id)
    for sid in list(relations.related('iface_of_service', ik, 'fwd')):
        relations.unrelate('iface_of_service', ik, sid)

# ── Scope helpers ─────────────────────────────────────────────────────────────

SCOPE_LEVELS = ('project', 'site', 'pod', 'ne', 'interface')


def effective_scope(iface: dict, field: dict) -> str:
    return field.get('scope_override') or iface.get('sharing', 'interface')

# ── Per-binding service value helpers ─────────────────────────────────────────

def read_service_values(binding: dict, service_id: str, field_id: str) -> dict | None:
    """Return the stored mode+payload dict for (service, field) in a binding, or None."""
    return binding.get('service_values', {}).get(service_id, {}).get(field_id)


def write_service_values(binding: dict, service_id: str, field_id: str, payload: dict):
    """Write mode+payload into a binding dict in-place."""
    sv = binding.setdefault('service_values', {})
    sv.setdefault(service_id, {})[field_id] = payload


def _resolved_count(sv: dict | None, expected: int) -> int:
    """Compute how many of the expected targets are resolved given a stored payload."""
    if not sv or not sv.get('mode'):
        return 0
    mode = sv['mode']
    if mode == 'text':
        return expected if sv.get('value') else 0
    if mode == 'dynamic':
        return expected if sv.get('template') else 0
    if mode == 'list':
        return sum(1 for v in sv.get('values', {}).values() if v)
    return 0

# ── Dynamic template evaluation ───────────────────────────────────────────────

def evaluate_dynamic(template_str: str, custom: dict, ctx_vars: dict) -> str:
    """Render a dynamic Jinja2 template string in the sandboxed environment."""
    from core.derivations import _JINJA  # pylint: disable=import-outside-toplevel,protected-access
    full_ctx = {**ctx_vars, **{k: v for k, v in custom.items()}}
    try:
        return _JINJA.from_string(template_str).render(**full_ctx)
    except Exception:  # pylint: disable=broad-except
        return ''


def evaluate_dynamic_for_preview(sv: dict, ne_type: dict, iface: dict,
                                  site: dict, pod: dict,
                                  ne_instances: list, limit: int = 3) -> list:
    """Return up to *limit* evaluated strings for a dynamic-mode payload."""
    template_str = sv.get('template', '')
    custom = sv.get('custom', {})
    results = []
    for i, inst in enumerate(ne_instances[:limit], start=1):
        ctx = {
            'site': site or {},
            'pod': pod or {},
            'ne_type': ne_type or {},
            'ne_instance': inst,
            'iface': iface or {},
            'port': {},
            'hw_instance': {},
            'index': i,
        }
        results.append(evaluate_dynamic(template_str, custom, ctx))
    return results

# ── Requirements computation helpers ─────────────────────────────────────────

def compute_service_rows(pid: str, site: dict, pod: dict,
                         ne_type: dict, iface: dict,
                         ne_count: int, cust_id: str,
                         project_ne_instances_of_type: list,
                         shared_keys: dict) -> list:
    """
    Emit service requirement rows for one (site, pod, ne_type, iface) tuple.

    Returns a list of new rows (may be empty if all deduped).
    Updates shared_keys in-place.
    """
    rows = []
    for svc in services_for_iface(ne_type['id'], iface['id']):
        if cust_id and svc.get('customer_id') != cust_id:
            continue
        for field in svc.get('schema', []):
            eff_scope = effective_scope(iface, field)
            sid = svc['id']
            fid = field['id']

            # Build stable dedup key
            if eff_scope == 'project':
                svc_key = f'svc:{sid}:{fid}:proj'
            elif eff_scope == 'site':
                svc_key = f'svc:{sid}:{fid}:site:{site["id"]}'
            elif eff_scope == 'pod':
                svc_key = f'svc:{sid}:{fid}:pod:{pod["id"]}'
            elif eff_scope == 'ne':
                svc_key = f'svc:{sid}:{fid}:ne:{pod["id"]}:{ne_type["id"]}:{iface["id"]}'
            else:  # interface
                svc_key = (f'svc:{sid}:{fid}:iface:'
                           f'{pod["id"]}:{ne_type["id"]}:{iface["id"]}')

            if svc_key in shared_keys:
                continue

            # Expected count
            if eff_scope in ('project', 'site', 'pod'):
                expected = 1
            else:
                expected = ne_count  # ne/interface: one per instance (slot count)

            # Resolved count: inspect actual NE instances in project
            insts = project_ne_instances_of_type
            input_mode = None
            mode_payload = None
            if eff_scope in ('project', 'site', 'pod'):
                resolved = 0
                for inst in insts:
                    binding = inst.get('iface_bindings', {}).get(iface['id'], {})
                    sv = read_service_values(binding, sid, fid)
                    if sv and sv.get('mode'):
                        input_mode = sv.get('mode')
                        mode_payload = sv
                        resolved = 1
                        break
            else:
                resolved = 0
                for inst in insts:
                    binding = inst.get('iface_bindings', {}).get(iface['id'], {})
                    sv = read_service_values(binding, sid, fid)
                    if sv and sv.get('mode'):
                        if not input_mode:
                            input_mode = sv.get('mode')
                            mode_payload = sv
                        resolved += _resolved_count(sv, 1)

            rec = {
                'kind':            'service',
                'key':             svc_key,
                'service_id':      sid,
                'service_name':    svc['name'],
                'field_id':        fid,
                'field_name':      field['name'],
                'field_label':     field.get('label', field['name']),
                'field_type':      field.get('field_type', 'text'),
                'required':        field.get('required', False),
                'ne_type_id':      ne_type['id'],
                'ne_type_name':    ne_type['name'],
                'iface_id':        iface['id'],
                'iface_name':      iface['name'],
                'effective_scope': eff_scope,
                'site_id':         site['id'],
                'site_name':       site['name'],
                'pod_id':          pod['id'],
                'pod_name':        pod['name'],
                'expected_count':  expected,
                'resolved_count':  min(resolved, expected),
                'missing':         max(0, expected - min(resolved, expected)),
                'input_mode':      input_mode,
                'mode_payload':    mode_payload,
                'pushed':          False,
            }
            rows.append(rec)
            shared_keys[svc_key] = rec

    return rows


def save_service_row(pid: str, row_key: str, mode: str, payload: dict):
    """
    Persist a service-row mode+value to all relevant NE instance bindings.

    Parses the row_key to find (service_id, field_id, scope, scope_target).
    Writes the payload into every binding in scope.
    Returns the updated row dict or None if the row was not found.
    """
    from ne import load_requirements, save_requirements  # pylint: disable=import-outside-toplevel
    from ne import project_ne_instances, get_ne_instance, save_ne_instance  # pylint: disable=import-outside-toplevel

    reqs = load_requirements(pid)
    target_row = next((r for r in reqs if r.get('key') == row_key), None)
    if not target_row or target_row.get('kind') != 'service':
        return None

    sid = target_row['service_id']
    fid = target_row['field_id']
    iface_id = target_row['iface_id']
    ne_type_id = target_row['ne_type_id']
    eff_scope = target_row['effective_scope']

    all_insts = project_ne_instances(pid)
    relevant = [i for i in all_insts if i.get('ne_type_id') == ne_type_id]

    for inst in relevant:
        bindings = inst.get('iface_bindings', {})
        if iface_id not in bindings:
            continue
        write_service_values(bindings[iface_id], sid, fid, payload)
        save_ne_instance(inst)

    # Recompute the row's resolved/missing from fresh instances
    fresh_insts = [get_ne_instance(i['id']) for i in relevant]
    fresh_insts = [i for i in fresh_insts if i]
    expected = target_row['expected_count']
    if eff_scope in ('project', 'site', 'pod'):
        resolved = 0
        for inst in fresh_insts:
            binding = inst.get('iface_bindings', {}).get(iface_id, {})
            sv = read_service_values(binding, sid, fid)
            if sv and sv.get('mode'):
                resolved = 1
                break
    else:
        resolved = sum(
            _resolved_count(
                read_service_values(
                    inst.get('iface_bindings', {}).get(iface_id, {}), sid, fid
                ),
                1
            )
            for inst in fresh_insts
        )

    target_row['input_mode'] = mode
    target_row['mode_payload'] = payload
    target_row['resolved_count'] = min(resolved, expected)
    target_row['missing'] = max(0, expected - min(resolved, expected))
    save_requirements(pid, reqs)
    return target_row


# ── Health Engine gap emission ─────────────────────────────────────────────────

def service_field_gaps(pid: str) -> list:
    """Return list of service_field_missing gap dicts for a project."""
    from ne import load_requirements  # pylint: disable=import-outside-toplevel
    gaps = []
    reqs = load_requirements(pid)
    for row in reqs:
        if row.get('kind') != 'service':
            continue
        if not row.get('required'):
            continue
        if row.get('missing', 0) > 0:
            gaps.append({
                'kind':       'service_field_missing',
                'severity':   'error',
                'service':    row['service_name'],
                'field':      row['field_name'],
                'iface':      row['iface_name'],
                'ne_type':    row['ne_type_name'],
                'missing':    row['missing'],
                'project_id': pid,
                'row_key':    row['key'],
            })
    return gaps
