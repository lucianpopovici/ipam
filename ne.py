"""
Network Element (NE) blueprint
Covers: schemas, NE types, sites, PODs, and subnet requirement generation.
"""
import json
import re
import uuid
import pathlib
import yaml
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
from auth import editor_required
from db import r   # shared Redis connection
from core import relations
from core.forms import form_errors

# ── Load enums from config/app_config.yaml (fallback to hardcoded defaults) ──
_cfg_path = pathlib.Path(__file__).parent / 'config' / 'app_config.yaml'
try:
    with _cfg_path.open() as _f:
        _cfg = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _cfg = {}

ne_bp = Blueprint('ne', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Key helpers
# ══════════════════════════════════════════════════════════════════════════════

def _schema_key(entity, pid=None):
    """Return the Redis key for a schema."""
    return f'project:{pid}:schema:{entity}' if pid else f'schema:{entity}'

def _ne_type_key(tid):
    """Return the Redis key for an NE type."""
    return f'ne_type:{tid}'

def _site_key(sid):
    """Return the Redis key for a site."""
    return f'site:{sid}'

def _pod_key(pid_):
    """Return the Redis key for a POD."""
    return f'pod:{pid_}'

def _pod_slots_key(pid_):
    """Return the Redis key for POD slots."""
    return f'pod:{pid_}:slots'   # JSON list of ne-slots

def _proj_sites_key(pid):
    """Return the Redis key for project sites."""
    return f'project:{pid}:sites'

def _proj_pods_key(pid):
    """Return the Redis key for project PODs."""
    return f'project:{pid}:pods'

def _proj_netypes_key(pid):
    """Return the Redis key for project NE types."""
    return f'project:{pid}:ne_types'

NE_TYPES_INDEX  = 'ne_types:index'
NE_INSTS_INDEX  = 'ne:instances:index'
ENTITY_TYPES   = tuple(_cfg.get('entity_types',   ('site', 'pod', 'ne', 'interface')))
NE_KINDS       = tuple(_cfg.get('ne_kinds',        ('CNF', 'VNF', 'PNF', 'VM', 'Container')))
SHARING_LEVELS = tuple(_cfg.get('sharing_levels',  ('project', 'site', 'pod', 'ne', 'interface')))
FIELD_TYPES    = tuple(_cfg.get(
    'field_types', ('text', 'number', 'textarea', 'dropdown', 'multi-select', 'checkbox')))

# ══════════════════════════════════════════════════════════════════════════════
# Schema helpers
# A schema is a list of field definitions:
# [{ id, name, label, field_type, required, options[], default }, ...]
# ══════════════════════════════════════════════════════════════════════════════

def get_schema(entity: str, pid=None) -> list:
    """Return project schema if set, else global, else []."""
    if pid:
        raw = r.get(_schema_key(entity, pid))
        if raw:
            return json.loads(raw)
    raw = r.get(_schema_key(entity))
    return json.loads(raw) if raw else []

def save_schema(entity: str, fields: list, pid=None):
    """Save a schema to Redis."""
    r.set(_schema_key(entity, pid), json.dumps(fields))

def new_field(name, label, field_type='text', required=False, options=None, default=''):  # pylint: disable=too-many-positional-arguments
    """Create a new field definition."""
    return {
        'id':         str(uuid.uuid4())[:8],
        'name':       name,
        'label':      label,
        'field_type': field_type,
        'required':   required,
        'options':    options or [],
        'default':    default,
    }

def validate_params(values: dict, schema: list) -> list:
    """Return list of error strings for required fields that are missing."""
    errors = []
    for f in schema:
        if f.get('required') and not values.get(f['id']):
            errors.append(f'Field "{f["label"]}" is required.')
    return errors

# ══════════════════════════════════════════════════════════════════════════════
# NE Type helpers
# ══════════════════════════════════════════════════════════════════════════════
# NE Type record:
# {
#   id, name, kind (CNF|VNF|PNF|VM|Container),
#   description,
#   labels: [],
#   params: {field_id: value},
#   scope: 'global'|'project',  project_id: ''|'<pid>',
#   interfaces: [
#     { id, name, description, labels:[], params:{},
#       ipv4: {prefix_len:24}|null,
#       ipv6: {prefix_len:64}|null,
#       sharing: 'project'|'site'|'pod'|'ne'|'interface' }
#   ]
# }

def get_ne_type(tid):
    """Retrieve an NE type by ID."""
    raw = r.get(_ne_type_key(tid))
    return json.loads(raw) if raw else None

def save_ne_type(ne):
    """Save an NE type and update its index."""
    r.set(_ne_type_key(ne['id']), json.dumps(ne))
    if ne.get('scope') == 'project' and ne.get('project_id'):
        r.sadd(_proj_netypes_key(ne['project_id']), ne['id'])
    else:
        r.sadd(NE_TYPES_INDEX, ne['id'])

def delete_ne_type(tid):
    """Delete an NE type and remove from indices."""
    ne = get_ne_type(tid)
    if not ne:
        return
    if ne.get('scope') == 'project' and ne.get('project_id'):
        r.srem(_proj_netypes_key(ne['project_id']), tid)
    else:
        r.srem(NE_TYPES_INDEX, tid)
    r.delete(_ne_type_key(tid))

def global_ne_types() -> list:
    """Return all global NE types."""
    return sorted([t for t in (get_ne_type(tid) for tid in r.smembers(NE_TYPES_INDEX)) if t],
                  key=lambda t: t['name'])

def project_ne_types(pid) -> list:
    """Return all NE types for a specific project."""
    return sorted([t for t in (get_ne_type(tid) for tid in r.smembers(_proj_netypes_key(pid))) if t],
                  key=lambda t: t['name'])

def available_ne_types(pid) -> dict:
    """Return both global and project-specific NE types."""
    return {'global': global_ne_types(), 'project': project_ne_types(pid)}

# ══════════════════════════════════════════════════════════════════════════════
# Site helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_site(sid):
    """Retrieve a site by ID."""
    raw = r.get(_site_key(sid))
    return json.loads(raw) if raw else None

def save_site(site):
    """Save a site and update the project index."""
    r.set(_site_key(site['id']), json.dumps(site))
    r.sadd(_proj_sites_key(site['project_id']), site['id'])

def delete_site(sid):
    """Delete a site and its associations."""
    site = get_site(sid)
    if not site:
        return
    r.srem(_proj_sites_key(site['project_id']), sid)
    relations.clear_entity('pod_of_site', sid)
    r.delete(_site_key(sid))

def project_sites(pid) -> list:
    """Return all sites for a specific project."""
    return sorted([s for s in (get_site(sid) for sid in r.smembers(_proj_sites_key(pid))) if s],
                  key=lambda s: s['name'])

def site_pods(sid) -> list:
    """Return all PODs assigned to a specific site."""
    return [p for p in (get_pod(pid_) for pid_ in relations.related('pod_of_site', sid, 'rev')) if p]

# ══════════════════════════════════════════════════════════════════════════════
# POD helpers
# ══════════════════════════════════════════════════════════════════════════════
# POD record: { id, name, project_id, description, labels:[], params:{} }
# Slots stored separately as JSON list in pod:<id>:slots

def get_pod(pod_id):
    """Retrieve a POD by ID."""
    raw = r.get(_pod_key(pod_id))
    return json.loads(raw) if raw else None

def save_pod(pod):
    """Save a POD and update the project index."""
    r.set(_pod_key(pod['id']), json.dumps(pod))
    r.sadd(_proj_pods_key(pod['project_id']), pod['id'])

def delete_pod(pod_id):
    """Delete a POD and its associations."""
    pod = get_pod(pod_id)
    if not pod:
        return
    r.srem(_proj_pods_key(pod['project_id']), pod_id)
    relations.clear_entity('pod_of_site', pod_id)
    r.delete(_pod_slots_key(pod_id))
    r.delete(_pod_key(pod_id))

def project_pods(pid) -> list:
    """Return all PODs for a specific project."""
    return sorted([p for p in (get_pod(pod_id) for pod_id in r.smembers(_proj_pods_key(pid))) if p],
                  key=lambda p: p['name'])

def get_pod_slots(pod_id) -> list:
    """Retrieve NE slots for a specific POD."""
    raw = r.get(_pod_slots_key(pod_id))
    return json.loads(raw) if raw else []

def save_pod_slots(pod_id, slots: list):
    """Save NE slots for a specific POD."""
    r.set(_pod_slots_key(pod_id), json.dumps(slots))

def pod_sites(pod_id) -> list:
    """Return all sites where this POD is assigned."""
    return [s for s in (get_site(sid) for sid in relations.related('pod_of_site', pod_id, 'fwd')) if s]

def assign_pod_to_site(pod_id, sid):
    """Associate a POD with a site."""
    relations.relate('pod_of_site', pod_id, sid)

def unassign_pod_from_site(pod_id, sid):
    """Disassociate a POD from a site."""
    relations.unrelate('pod_of_site', pod_id, sid)

# ══════════════════════════════════════════════════════════════════════════════
# Bulk site creation from pattern
# Supports: prefix{0001..1200}suffix  e.g. ran{0001..1200}, site-{01..10}-prod
# ══════════════════════════════════════════════════════════════════════════════

def expand_site_pattern(pattern: str) -> list:
    """
    Expand a pattern like ran{0001..1200} into a list of name strings.
    Returns (names_list, error_string).  error_string is None on success.
    """
    m = re.search(r'\{(\d+)\.\.(\d+)\}', pattern)
    if not m:
        return None, 'Pattern must contain a range like {0001..1200}'
    start_str, end_str = m.group(1), m.group(2)
    start, end = int(start_str), int(end_str)
    if end < start:
        return None, 'Range end must be >= start'
    if end - start > 9999:
        return None, 'Range too large (max 10000 sites at once)'
    width   = len(start_str)   # preserve zero-padding from the pattern
    prefix  = pattern[:m.start()]
    suffix  = pattern[m.end():]
    names   = [f'{prefix}{str(i).zfill(width)}{suffix}' for i in range(start, end + 1)]
    return names, None

# ══════════════════════════════════════════════════════════════════════════════
# Subnet requirement engine
# ══════════════════════════════════════════════════════════════════════════════

def compute_requirements(pid: str) -> list:
    """
    Walk every site → pod → ne_slot → interface and emit subnet requirement dicts.
    Returns a flat list of requirement records, deduplicated by sharing level.
    Each record:
    {
      site_id, site_name,
      pod_id, pod_name,
      ne_type_id, ne_type_name, ne_kind,
      iface_id, iface_name,
      ip_version: 'ipv4'|'ipv6',
      prefix_len: int,
      sharing: str,
      labels: [sorted list],
      count: int,               # how many subnets this line represents
      key: str,                 # dedup key — same key = same subnet
    }
    """
    sites    = project_sites(pid)
    reqs     = []
    # keyed requirements for shared dedup
    shared_keys: dict = {}   # key → req record (shared ones appear once)

    for site in sites:  # pylint: disable=too-many-nested-blocks
        site_labels = set(site.get('labels', []))
        site_pods_list = site_pods(site['id'])

        for pod in site_pods_list:
            pod_labels = set(pod.get('labels', []))
            slots      = get_pod_slots(pod['id'])

            for slot in slots:
                ne_type = get_ne_type(slot['ne_type_id'])
                if not ne_type:
                    continue
                ne_labels    = set(ne_type.get('labels', []))
                ne_count     = int(slot.get('count', 1))
                slot_labels  = set(slot.get('label_override', []))

                for iface in ne_type.get('interfaces', []):
                    iface_labels = set(iface.get('labels', []))
                    all_labels   = sorted(site_labels | pod_labels | ne_labels |
                                          slot_labels | iface_labels)
                    sharing      = iface.get('sharing', 'interface')

                    for ip_ver in ('ipv4', 'ipv6'):
                        spec = iface.get(ip_ver)
                        if not spec:
                            continue
                        prefix_len = spec['prefix_len']

                        # Build dedup key based on sharing scope
                        if sharing == 'project':
                            key = f'proj:{pid}|iface:{iface["id"]}|v:{ip_ver}'
                        elif sharing == 'site':
                            key = f'site:{site["id"]}|iface:{iface["id"]}|v:{ip_ver}'
                        elif sharing == 'pod':
                            key = f'site:{site["id"]}|pod:{pod["id"]}|iface:{iface["id"]}|v:{ip_ver}'
                        elif sharing == 'ne':
                            key = (f'site:{site["id"]}|pod:{pod["id"]}'
                                   f'|slot:{slot["ne_type_id"]}|iface:{iface["id"]}|v:{ip_ver}')
                        else:  # interface — one per NE instance
                            key = None  # never deduped

                        if key and key in shared_keys:
                            continue   # already emitted

                        count = _sharing_count(sharing, ne_count)
                        rec = {
                            'site_id':       site['id'],
                            'site_name':     site['name'],
                            'pod_id':        pod['id'],
                            'pod_name':      pod['name'],
                            'ne_type_id':    ne_type['id'],
                            'ne_type_name':  ne_type['name'],
                            'ne_kind':       ne_type['kind'],
                            'iface_id':      iface['id'],
                            'iface_name':    iface['name'],
                            'ip_version':    ip_ver,
                            'prefix_len':    prefix_len,
                            'sharing':       sharing,
                            'labels':        all_labels,
                            'count':         count,
                            'key':           key or f'uniq:{uuid.uuid4()}',
                            'pushed':        False,
                        }
                        reqs.append(rec)
                        if key:
                            shared_keys[key] = rec

    return reqs

def _sharing_count(sharing: str, ne_count: int) -> int:
    """How many subnets does this requirement line represent."""
    # site/pod/project level = 1 per that scope (already deduped above)
    # ne = 1 per ne type slot (ne_count instances share one)
    # interface = one per NE instance
    if sharing in ('project', 'site', 'pod', 'ne'):
        return 1
    return ne_count   # interface-level: one per instance

def save_requirements(pid: str, reqs: list):
    """Save subnet requirements for a project."""
    r.set(f'project:{pid}:requirements', json.dumps(reqs))

def load_requirements(pid: str) -> list:
    """Load subnet requirements for a project."""
    raw = r.get(f'project:{pid}:requirements')
    return json.loads(raw) if raw else []

def mark_pushed(pid: str, req_key: str):
    """Mark a specific requirement as pushed to IPAM."""
    reqs = load_requirements(pid)
    for req in reqs:
        if req['key'] == req_key:
            req['pushed'] = True
    save_requirements(pid, reqs)

# ══════════════════════════════════════════════════════════════════════════════
# NE Instance model
# Each NE instance is a deployed instance of an NE type within a project.
# Redis key: ne:instance:{nid}  — JSON  {id, ne_type_id, project_id,
#   name, description, labels, params, iface_bindings}
# iface_bindings: {iface_id: {bind_mode, ports[], rule?, lag_id?, rule_materialized_at?}}
# ══════════════════════════════════════════════════════════════════════════════

def _ne_inst_key(nid):
    return f'ne:instance:{nid}'

def _proj_ne_insts_key(pid):
    return f'project:{pid}:ne_instances'


def get_ne_instance(nid):
    """Retrieve a NE instance by ID."""
    from db import redis_get
    return redis_get(_ne_inst_key(nid))


def save_ne_instance(inst: dict) -> dict:
    """Persist a NE instance and keep the port-bound index in sync."""
    import hw_logic
    from db import redis_save
    nid = inst['id']
    # Diff against the old bindings to update the port-bound index
    old = get_ne_instance(nid) or {}
    old_bindings = old.get('iface_bindings', {})
    new_bindings = inst.get('iface_bindings', {})

    # Collect old bound ports for this NE instance
    old_ports = {
        (p['hw_instance_id'], p['port_id'])
        for b in old_bindings.values()
        for p in b.get('ports', [])
    }
    # Collect new bound ports
    new_ports = {
        (p['hw_instance_id'], p['port_id'])
        for b in new_bindings.values()
        for p in b.get('ports', [])
    }
    # Release ports no longer bound
    for iid, port_id in old_ports - new_ports:
        hw_logic.clear_port_bound(iid, port_id)
    # Register newly bound ports
    for iface_id, binding in new_bindings.items():
        for p in binding.get('ports', []):
            if (p['hw_instance_id'], p['port_id']) not in old_ports:
                hw_logic.set_port_bound(
                    p['hw_instance_id'], p['port_id'],
                    nid, iface_id, binding.get('bind_mode', 'single'),
                )
    redis_save(_ne_inst_key(nid), inst)
    r.sadd(NE_INSTS_INDEX, nid)
    r.sadd(_proj_ne_insts_key(inst['project_id']), nid)
    return inst


def delete_ne_instance(nid: str):
    """Delete a NE instance and clean up the port-bound index."""
    import hw_logic
    from db import redis_delete
    inst = get_ne_instance(nid)
    if not inst:
        return
    for binding in inst.get('iface_bindings', {}).values():
        for p in binding.get('ports', []):
            hw_logic.clear_port_bound(p['hw_instance_id'], p['port_id'])
    r.srem(NE_INSTS_INDEX, nid)
    r.srem(_proj_ne_insts_key(inst['project_id']), nid)
    redis_delete(_ne_inst_key(nid))


def project_ne_instances(pid: str) -> list:
    """Return all NE instances in a project, sorted by name."""
    from db import redis_all
    return redis_all(_proj_ne_insts_key(pid), get_ne_instance,
                     sort_key=lambda x: x.get('name', ''))


def ne_instance_with_type(nid: str) -> dict | None:
    """Return a NE instance enriched with its NE type dict."""
    inst = get_ne_instance(nid)
    if not inst:
        return None
    ne_type = get_ne_type(inst.get('ne_type_id', ''))
    return {**inst, 'ne_type': ne_type}


def _collect_excluded_ports(project_id: str, skip_nid: str) -> set:
    """Return set of (iid, port_id) explicitly bound by OTHER NE instances."""
    excluded = set()
    for inst in project_ne_instances(project_id):
        if inst['id'] == skip_nid:
            continue
        for binding in inst.get('iface_bindings', {}).values():
            if binding.get('bind_mode') in ('single', 'lag', 'active-passive'):
                for p in binding.get('ports', []):
                    excluded.add((p['hw_instance_id'], p['port_id']))
    return excluded

# ══════════════════════════════════════════════════════════════════════════════
# Utility
# ══════════════════════════════════════════════════════════════════════════════

def new_id():
    """Generate a random 8-character ID."""
    return str(uuid.uuid4())[:8]

def parse_labels(form_value: str) -> list:
    """Parse comma-separated labels into a list of unique strings."""
    if not form_value:
        return []
    seen, result = set(), []
    for l in form_value.split(','):
        l = l.strip()
        if l and l not in seen:
            seen.add(l)
            result.append(l)
    return result

def collect_params(schema: list, form) -> dict:
    """Extract param values from a Flask form according to schema field IDs."""
    values = {}
    for f in schema:
        fid = f['id']
        if f['field_type'] == 'checkbox':
            values[fid] = fid in form
        elif f['field_type'] == 'multi-select':
            values[fid] = form.getlist(fid)
        else:
            values[fid] = form.get(fid, '').strip()
    return values

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Schema management
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/admin/schemas', methods=['GET', 'POST'])
@editor_required
def admin_schemas():
    """Manage global schemas for various entities."""
    if request.method == 'POST':
        entity     = request.form.get('entity')
        fields_raw = request.form.get('fields_json', '[]')
        if entity not in ENTITY_TYPES:
            flash('Invalid entity type.', 'danger')
        else:
            try:
                fields = json.loads(fields_raw)
                save_schema(entity, fields)
                flash(f'Global schema for {entity} saved.', 'success')
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON: {e}', 'danger')
        return redirect(url_for('ne.admin_schemas'))
    schemas = {e: get_schema(e) for e in ENTITY_TYPES}
    return render_template('ne/schemas.html', schemas=schemas,
                           entity_types=ENTITY_TYPES, field_types=FIELD_TYPES)


@ne_bp.route('/projects/<pid>/schemas', methods=['GET', 'POST'])
@editor_required
def project_schemas(pid):
    """Manage project-specific schemas."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        entity     = request.form.get('entity')
        fields_raw = request.form.get('fields_json', '[]')
        reset      = request.form.get('reset_to_global')
        if entity not in ENTITY_TYPES:
            flash('Invalid entity type.', 'danger')
        elif reset:
            r.delete(_schema_key(entity, pid))
            flash(f'Project schema for {entity} reset to global.', 'info')
        else:
            try:
                fields = json.loads(fields_raw)
                save_schema(entity, fields, pid)
                flash(f'Project schema for {entity} saved.', 'success')
            except json.JSONDecodeError as e:
                flash(f'Invalid JSON: {e}', 'danger')
        return redirect(url_for('ne.project_schemas', pid=pid))
    schemas        = {e: get_schema(e, pid) for e in ENTITY_TYPES}
    global_schemas = {e: get_schema(e)      for e in ENTITY_TYPES}
    return render_template('ne/project_schemas.html', proj=proj,
                           schemas=schemas, global_schemas=global_schemas,
                           entity_types=ENTITY_TYPES, field_types=FIELD_TYPES)


# ══════════════════════════════════════════════════════════════════════════════
# Routes — NE Types
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/ne-types')
def list_ne_types():
    """List all global NE types."""
    return render_template('ne/ne_types_list.html',
                           global_types=global_ne_types(), project_types=[])


@ne_bp.route('/ne-types/add',                       methods=['GET','POST'])
@ne_bp.route('/projects/<pid>/ne-types/add',        methods=['GET','POST'])
@editor_required
def add_ne_type(pid=None):
    """Add a new global or project-specific NE type."""
    from ipam import get_project
    proj         = get_project(pid) if pid else None
    ne_schema    = get_schema('ne', pid)
    iface_schema = get_schema('interface', pid)
    if request.method == 'POST':
        name       = request.form.get('name','').strip()
        ifaces_raw = request.form.get('interfaces_json','[]')
        try:
            interfaces = json.loads(ifaces_raw)
            iface_err  = None
        except json.JSONDecodeError as e:
            interfaces = []
            iface_err  = str(e)
        errors = form_errors(
            ('name',       bool(name),      'Name is required.'),
            ('interfaces', iface_err is None, f'Invalid interfaces JSON: {iface_err}'),
        )
        if errors:
            return render_template('ne/ne_type_form.html', ne=None, proj=proj,
                                   ne_schema=ne_schema, iface_schema=iface_schema,
                                   ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                                   field_types=FIELD_TYPES,
                                   errors=errors, form_values=request.form)
        ne = {
            'id':          new_id(),
            'name':        name,
            'kind':        request.form.get('kind', 'VNF'),
            'description': request.form.get('description',''),
            'labels':      parse_labels(request.form.get('labels','')),
            'params':      collect_params(ne_schema, request.form),
            'interfaces':  interfaces,
            'scope':       'project' if pid else 'global',
            'project_id':  pid or '',
        }
        save_ne_type(ne)
        flash(f'NE Type "{name}" saved.', 'success')
        return redirect(url_for('ne.list_project_ne_types', pid=pid) if pid
                        else url_for('ne.list_ne_types'))
    return render_template('ne/ne_type_form.html', ne=None, proj=proj,
                           ne_schema=ne_schema, iface_schema=iface_schema,
                           ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                           field_types=FIELD_TYPES, errors={}, form_values={})


@ne_bp.route('/ne-types/<tid>/edit', methods=['GET','POST'])
@editor_required
def edit_ne_type(tid):
    """Edit an existing NE type."""
    from ipam import get_project
    ne = get_ne_type(tid)
    if not ne:
        abort(404)
    pid          = ne.get('project_id') or None
    proj         = get_project(pid) if pid else None
    ne_schema    = get_schema('ne', pid)
    iface_schema = get_schema('interface', pid)
    if request.method == 'POST':
        ifaces_raw = request.form.get('interfaces_json','[]')
        try:
            interfaces = json.loads(ifaces_raw)
            iface_err  = None
        except json.JSONDecodeError as e:
            interfaces = ne.get('interfaces', [])
            iface_err  = str(e)
        name   = request.form.get('name', ne['name']).strip()
        errors = form_errors(
            ('name',       bool(name),      'Name is required.'),
            ('interfaces', iface_err is None, f'Invalid interfaces JSON: {iface_err}'),
        )
        if errors:
            return render_template('ne/ne_type_form.html', ne=ne, proj=proj,
                                   ne_schema=ne_schema, iface_schema=iface_schema,
                                   ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                                   field_types=FIELD_TYPES,
                                   errors=errors, form_values=request.form)
        ne['name']        = name
        ne['kind']        = request.form.get('kind', ne['kind'])
        ne['description'] = request.form.get('description','')
        ne['labels']      = parse_labels(request.form.get('labels',''))
        ne['params']      = collect_params(ne_schema, request.form)
        ne['interfaces']  = interfaces
        save_ne_type(ne)
        flash(f'NE Type "{ne["name"]}" updated.', 'success')
        return redirect(url_for('ne.list_project_ne_types', pid=pid) if pid
                        else url_for('ne.list_ne_types'))
    return render_template('ne/ne_type_form.html', ne=ne, proj=proj,
                           ne_schema=ne_schema, iface_schema=iface_schema,
                           ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                           field_types=FIELD_TYPES, errors={}, form_values={})


@ne_bp.route('/ne-types/<tid>/delete', methods=['POST'])
@editor_required
def delete_ne_type_route(tid):
    """Delete an NE type."""
    ne = get_ne_type(tid)
    if not ne:
        abort(404)
    pid = ne.get('project_id') or None
    delete_ne_type(tid)
    flash(f'NE Type "{ne["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_project_ne_types', pid=pid) if pid
                    else url_for('ne.list_ne_types'))


@ne_bp.route('/projects/<pid>/ne-types')
def list_project_ne_types(pid):
    """List NE types available to a project (global + local)."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    return render_template('ne/ne_types_list.html',
                           proj=proj,
                           global_types=global_ne_types(),
                           project_types=project_ne_types(pid))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Sites
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/sites')
def list_sites(pid):
    """List all sites in a project."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    sites  = project_sites(pid)
    schema = get_schema('site', pid)
    # Annotate each site with its assigned pods
    for site in sites:
        site['pods'] = site_pods(site['id'])
    return render_template('ne/sites_list.html', proj=proj, sites=sites, schema=schema)


@ne_bp.route('/projects/<pid>/sites/add', methods=['GET','POST'])
@editor_required
def add_site(pid):
    """Add a new site to a project."""
    from ipam import get_project
    proj   = get_project(pid)
    if not proj:
        abort(404)
    schema = get_schema('site', pid)
    if request.method == 'POST':
        name   = request.form.get('name','').strip()
        errors = form_errors(('name', bool(name), 'Site name is required.'))
        if errors:
            return render_template('ne/site_form.html', proj=proj, site=None,
                                   schema=schema, errors=errors, form_values=request.form)
        site = {
            'id':          new_id(),
            'name':        name,
            'project_id':  pid,
            'description': request.form.get('description',''),
            'labels':      parse_labels(request.form.get('labels','')),
            'params':      collect_params(schema, request.form),
        }
        save_site(site)
        flash(f'Site "{name}" created.', 'success')
        return redirect(url_for('ne.list_sites', pid=pid))
    return render_template('ne/site_form.html', proj=proj, site=None,
                           schema=schema, errors={}, form_values={})


@ne_bp.route('/projects/<pid>/sites/bulk', methods=['GET','POST'])
@editor_required
def bulk_add_sites(pid):
    """Bulk create sites from a numeric pattern (e.g. 'site[01-05]')."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    schema = get_schema('site', pid)
    if request.method == 'POST':
        pattern = request.form.get('pattern','').strip()
        names, err = expand_site_pattern(pattern)
        if err:
            flash(err, 'danger')
            return redirect(request.url)
        # Shared params for all sites in the batch
        shared_labels = parse_labels(request.form.get('labels',''))
        shared_params = collect_params(schema, request.form)
        created = 0
        for name in names:
            site = {
                'id':         new_id(),
                'name':       name,
                'project_id': pid,
                'description': request.form.get('description',''),
                'labels':     shared_labels,
                'params':     shared_params,
            }
            save_site(site)
            created += 1
        flash(f'{created} site(s) created from pattern "{pattern}".', 'success')
        return redirect(url_for('ne.list_sites', pid=pid))
    return render_template('ne/site_bulk_form.html', proj=proj, schema=schema)


@ne_bp.route('/projects/<pid>/sites/<sid>/edit', methods=['GET','POST'])
@editor_required
def edit_site(pid, sid):
    """Edit site metadata."""
    from ipam import get_project
    proj = get_project(pid)
    site = get_site(sid)
    if not proj or not site:
        abort(404)
    schema = get_schema('site', pid)
    if request.method == 'POST':
        site['name']        = request.form.get('name', site['name']).strip()
        site['description'] = request.form.get('description','')
        site['labels']      = parse_labels(request.form.get('labels',''))
        site['params']      = collect_params(schema, request.form)
        save_site(site)
        flash('Site updated.', 'success')
        return redirect(url_for('ne.list_sites', pid=pid))
    return render_template('ne/site_form.html', proj=proj, site=site, schema=schema)


@ne_bp.route('/projects/<pid>/sites/<sid>/delete', methods=['POST'])
@editor_required
def delete_site_route(pid, sid):
    """Delete a site."""
    site = get_site(sid)
    if not site:
        abort(404)
    delete_site(sid)
    flash(f'Site "{site["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_sites', pid=pid))


@ne_bp.route('/projects/<pid>/sites/<sid>/assign-pod', methods=['POST'])
@editor_required
def assign_pod_to_site_route(pid, sid):
    """Assign a POD to a site."""
    pod_id = request.form.get('pod_id','').strip()
    if pod_id:
        assign_pod_to_site(pod_id, sid)
        flash('POD assigned to site.', 'success')
    return redirect(url_for('ne.site_detail', pid=pid, sid=sid))


@ne_bp.route('/projects/<pid>/sites/<sid>/unassign-pod', methods=['POST'])
@editor_required
def unassign_pod_from_site_route(pid, sid):
    """Remove a POD assignment from a site."""
    pod_id = request.form.get('pod_id','').strip()
    if pod_id:
        unassign_pod_from_site(pod_id, sid)
        flash('POD unassigned from site.', 'info')
    return redirect(url_for('ne.site_detail', pid=pid, sid=sid))


@ne_bp.route('/projects/<pid>/sites/<sid>')
def site_detail(pid, sid):
    """View site details and pod assignments."""
    from ipam import get_project
    proj = get_project(pid)
    site = get_site(sid)
    if not proj or not site:
        abort(404)
    schema       = get_schema('site', pid)
    assigned     = site_pods(sid)
    assigned_ids = {p['id'] for p in assigned}
    all_pods     = [p for p in project_pods(pid) if p['id'] not in assigned_ids]
    return render_template('ne/site_detail.html', proj=proj, site=site,
                           schema=schema, assigned_pods=assigned,
                           available_pods=all_pods)


# ══════════════════════════════════════════════════════════════════════════════
# Routes — PODs
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/pods')
def list_pods(pid):
    """List all PODs in a project."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    pods   = project_pods(pid)
    schema = get_schema('pod', pid)
    for pod in pods:
        pod['sites']      = pod_sites(pod['id'])
        pod['slots']      = get_pod_slots(pod['id'])
        pod['slot_count'] = len(pod['slots'])
    return render_template('ne/pods_list.html', proj=proj, pods=pods, schema=schema)


@ne_bp.route('/projects/<pid>/pods/add', methods=['GET','POST'])
@editor_required
def add_pod(pid):
    """Create a new POD in a project."""
    from ipam import get_project
    proj   = get_project(pid)
    if not proj:
        abort(404)
    schema = get_schema('pod', pid)
    if request.method == 'POST':
        name   = request.form.get('name','').strip()
        errors = form_errors(('name', bool(name), 'POD name is required.'))
        if errors:
            return render_template('ne/pod_form.html', proj=proj, pod=None,
                                   schema=schema, errors=errors, form_values=request.form)
        pod = {
            'id':          new_id(),
            'name':        name,
            'project_id':  pid,
            'description': request.form.get('description',''),
            'labels':      parse_labels(request.form.get('labels','')),
            'params':      collect_params(schema, request.form),
        }
        save_pod(pod)
        flash(f'POD "{name}" created.', 'success')
        return redirect(url_for('ne.list_pods', pid=pid))
    return render_template('ne/pod_form.html', proj=proj, pod=None,
                           schema=schema, errors={}, form_values={})


@ne_bp.route('/projects/<pid>/pods/<pod_id>/edit', methods=['GET','POST'])
@editor_required
def edit_pod(pid, pod_id):
    """Edit POD metadata."""
    from ipam import get_project
    proj = get_project(pid)
    pod  = get_pod(pod_id)
    if not proj or not pod:
        abort(404)
    schema = get_schema('pod', pid)
    if request.method == 'POST':
        pod['name']        = request.form.get('name', pod['name']).strip()
        pod['description'] = request.form.get('description','')
        pod['labels']      = parse_labels(request.form.get('labels',''))
        pod['params']      = collect_params(schema, request.form)
        save_pod(pod)
        flash('POD updated.', 'success')
        return redirect(url_for('ne.pod_detail', pid=pid, pod_id=pod_id))
    return render_template('ne/pod_form.html', proj=proj, pod=pod, schema=schema)


@ne_bp.route('/projects/<pid>/pods/<pod_id>/delete', methods=['POST'])
@editor_required
def delete_pod_route(pid, pod_id):
    """Delete a POD."""
    pod = get_pod(pod_id)
    if not pod:
        abort(404)
    delete_pod(pod_id)
    flash(f'POD "{pod["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_pods', pid=pid))


@ne_bp.route('/projects/<pid>/pods/<pod_id>')
def pod_detail(pid, pod_id):
    """View POD details, site assignments, and NE slots."""
    from ipam import get_project
    proj = get_project(pid)
    pod  = get_pod(pod_id)
    if not proj or not pod:
        abort(404)
    schema       = get_schema('pod', pid)
    slots        = get_pod_slots(pod_id)
    # Enrich slots with NE type info
    enriched = []
    for slot in slots:
        ne = get_ne_type(slot['ne_type_id'])
        enriched.append({**slot, 'ne_type': ne})
    assigned_sites   = pod_sites(pod_id)
    assigned_site_ids = {s['id'] for s in assigned_sites}
    available_sites  = [s for s in project_sites(pid) if s['id'] not in assigned_site_ids]
    available_types  = available_ne_types(pid)
    return render_template('ne/pod_detail.html', proj=proj, pod=pod,
                           schema=schema, slots=enriched,
                           assigned_sites=assigned_sites,
                           available_sites=available_sites,
                           available_types=available_types)


@ne_bp.route('/projects/<pid>/pods/<pod_id>/slots', methods=['POST'])
@editor_required
def update_pod_slots(pid, pod_id):  # pylint: disable=unused-argument
    """Replace the NE slot list for a POD (posted as JSON)."""
    pod = get_pod(pod_id)
    if not pod:
        abort(404)
    try:
        slots = request.get_json(force=True)
        if not isinstance(slots, list):
            raise ValueError('Expected JSON array')
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    save_pod_slots(pod_id, slots)
    return jsonify({'ok': True, 'count': len(slots), 'saved': len(slots)})


@ne_bp.route('/projects/<pid>/pods/<pod_id>/assign-site', methods=['POST'])
@editor_required
def assign_site_to_pod_route(pid, pod_id):
    """Assign a site to a POD."""
    sid = request.form.get('site_id','').strip()
    if sid:
        assign_pod_to_site(pod_id, sid)
        flash('Site assigned to POD.', 'success')
    return redirect(url_for('ne.pod_detail', pid=pid, pod_id=pod_id))


@ne_bp.route('/projects/<pid>/pods/<pod_id>/unassign-site', methods=['POST'])
@editor_required
def unassign_site_from_pod_route(pid, pod_id):
    """Remove a site assignment from a POD."""
    sid = request.form.get('site_id','').strip()
    if sid:
        unassign_pod_from_site(pod_id, sid)
        flash('Site unassigned.', 'info')
    return redirect(url_for('ne.pod_detail', pid=pid, pod_id=pod_id))


@ne_bp.route('/projects/<pid>/topology')
def topology_page(pid):
    """Render the topology visualization page."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    return render_template('topology.html', proj=proj)


@ne_bp.route('/api/projects/<pid>/topology')
def topology_data(pid):
    """Return topology nodes and edges as JSON for Cytoscape.js."""
    from ipam import get_project
    from hw_logic import project_instances, project_cables
    proj = get_project(pid)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404

    nodes = []
    edges = []

    # 1. Logical: Sites -> PODs
    sites = project_sites(pid)
    for s in sites:
        nodes.append({
            'data': {'id': f"site_{s['id']}", 'label': s['name'], 'type': 'site'}
        })
        pods = site_pods(s['id'])
        for p in pods:
            # Edges Site -> POD
            edges.append({
                'data': {'id': f"e_s_p_{s['id']}_{p['id']}", 'source': f"site_{s['id']}", 'target': f"pod_{p['id']}"}
            })

    all_pods = project_pods(pid)
    for p in all_pods:
        nodes.append({
            'data': {'id': f"pod_{p['id']}", 'label': p['name'], 'type': 'pod'}
        })
        # NE Slots in POD
        slots = get_pod_slots(p['id'])
        for idx, slot in enumerate(slots):
            tid = slot.get('ne_type_id')
            netype = get_ne_type(tid)
            if netype:
                nodes.append({
                    'data': {
                        'id': f"pod_{p['id']}_slot_{idx}",
                        'label': f"{netype['name']} Slot",
                        'type': 'ne_slot',
                        'parent': f"pod_{p['id']}"
                    }
                })

    # 2. Physical: Racks -> Instances
    instances = project_instances(pid)
    for inst in instances:
        tmpl = inst.get('template', {})
        cat = tmpl.get('category')
        nodes.append({
            'data': {
                'id': f"inst_{inst['id']}",
                'label': inst.get('asset_tag', inst['id']),
                'type': 'device',
                'category': cat
            }
        })

        # If it's in a rack, make the rack a parent or just link them
        loc = inst.get('location', {})
        if loc.get('rack_id'):
            edges.append({
                'data': {
                    'id': f"e_r_i_{loc['rack_id']}_{inst['id']}",
                    'source': f"inst_{loc['rack_id']}",
                    'target': f"inst_{inst['id']}",
                    'type': 'rack_containment'
                }
            })

    # 3. Connectivity: Cables
    cables = project_cables(pid)
    for c in cables:
        end_a = c.get('end_a', {})
        end_b = c.get('end_b', {})
        if end_a.get('instance_id') and end_b.get('instance_id'):
            edges.append({
                'data': {
                    'id': f"cable_{c['id']}",
                    'source': f"inst_{end_a['instance_id']}",
                    'target': f"inst_{end_b['instance_id']}",
                    'label': c.get('asset_tag', ''),
                    'type': 'cable'
                }
            })

    return jsonify({'elements': nodes + edges})


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Subnet Requirements
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/requirements')
def requirements(pid):
    """Calculate and display subnet requirements for all sites/pods in the project."""
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    reqs = compute_requirements(pid)
    save_requirements(pid, reqs)
    # Summary stats
    total   = sum(r_['count'] for r_ in reqs)
    pushed  = sum(r_['count'] for r_ in reqs if r_.get('pushed'))
    return render_template('ne/requirements.html', proj=proj, reqs=reqs,
                           total=total, pushed=pushed,
                           sharing_levels=SHARING_LEVELS)


@ne_bp.route('/projects/<pid>/requirements/push', methods=['POST'])
@ne_bp.route('/projects/<pid>/requirements/push-all', methods=['POST'])
@editor_required
def push_requirements(pid):
    """
    Push selected (or all) requirements to IPAM as subnets.
    Expects JSON: { "keys": ["key1","key2",...] }  or  { "all": true }
    """
    from ipam import (get_project, save_network as _save_net,
                      carve_next_subnet, add_labels_to_network, project_nets_key, new_id as _new_id)
    proj = get_project(pid)
    if not proj:
        abort(404)

    if request.is_json:
        data = request.get_json()
    else:
        data = request.form

    reqs     = load_requirements(pid)
    push_all = data.get('all') in (True, 'true', 'on')
    keys     = set(data.getlist('keys') if hasattr(data, 'getlist') else data.get('keys', []))

    results, errors = [], []
    for req in reqs:
        if req.get('pushed'):
            continue
        if not push_all and req['key'] not in keys:
            continue
        # Repeat `count` times for interface-level sharing
        for _ in range(req['count']):
            try:
                cidr = str(carve_next_subnet(proj['supernet'],
                                             req['prefix_len'], pid))
                net = {
                    'id':          _new_id(),
                    'name':        f"{req['ne_type_name']}/{req['iface_name']}",
                    'cidr':        cidr,
                    'description': (f"{req['ne_kind']} {req['ne_type_name']} "
                                    f"iface {req['iface_name']} "
                                    f"[{req['sharing']}]"),
                    'vlan':        '',
                    'project_id':  pid,
                }
                _save_net(net)
                r.sadd(project_nets_key(pid), net['id'])
                add_labels_to_network(net['id'], req['labels'])
                results.append({'cidr': cidr, 'labels': req['labels']})
            except ValueError as e:
                errors.append({'req': req['key'], 'error': str(e)})
        if not errors:
            mark_pushed(pid, req['key'])

    return jsonify({'pushed': results, 'errors': errors})


# ══════════════════════════════════════════════════════════════════════════════
# Impact API — used by the confirm_delete modal
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/api/sites/<sid>/impact')
def site_impact(sid):
    """Return cascade effects of deleting a site."""
    site = get_site(sid)
    if not site:
        abort(404)
    pods = site_pods(sid)
    cascades = []
    if pods:
        n = len(pods)
        cascades.append({'kind': 'detach', 'count': n,
                         'what': f'{n} POD{"s" if n != 1 else ""} will be detached'})
    return jsonify({'label': site['name'], 'cascades': cascades})


@ne_bp.route('/api/pods/<pod_id>/impact')
def pod_impact(pod_id):
    """Return cascade effects of deleting a POD."""
    pod = get_pod(pod_id)
    if not pod:
        abort(404)
    sites_ = pod_sites(pod_id)
    slots  = get_pod_slots(pod_id)
    cascades = []
    if sites_:
        n = len(sites_)
        cascades.append({'kind': 'detach', 'count': n,
                         'what': f'{n} site{"s" if n != 1 else ""} will lose this POD'})
    if slots:
        n = len(slots)
        cascades.append({'kind': 'delete', 'count': n,
                         'what': f'{n} NE slot{"s" if n != 1 else ""} will be deleted'})
    return jsonify({'label': pod['name'], 'cascades': cascades})


@ne_bp.route('/api/ne-types/<tid>/impact')
def ne_type_impact(tid):
    """Return cascade effects of deleting an NE type."""
    ne = get_ne_type(tid)
    if not ne:
        abort(404)
    pid = ne.get('project_id') or None
    slot_count = 0
    if pid:
        for pod_id in r.smembers(_proj_pods_key(pid)):
            slot_count += sum(1 for s in get_pod_slots(pod_id) if s.get('ne_type_id') == tid)
    cascades = []
    if slot_count:
        cascades.append({'kind': 'orphan', 'count': slot_count,
                         'what': f'{slot_count} POD slot{"s" if slot_count != 1 else ""} will be orphaned'})
    return jsonify({'label': ne['name'], 'cascades': cascades})


# ══════════════════════════════════════════════════════════════════════════════
# Bulk delete endpoints
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/sites/bulk-delete', methods=['POST'])
@editor_required
def bulk_delete_sites(pid):
    """Delete multiple sites by ID."""
    ids = request.get_json(silent=True, force=True) or {}
    ids = ids.get('ids', [])
    for sid in ids:
        site = get_site(sid)
        if site and site.get('project_id') == pid:
            delete_site(sid)
    return jsonify({'deleted': len(ids)})


@ne_bp.route('/projects/<pid>/pods/bulk-delete', methods=['POST'])
@editor_required
def bulk_delete_pods(pid):
    """Delete multiple PODs by ID."""
    ids = request.get_json(silent=True, force=True) or {}
    ids = ids.get('ids', [])
    for pod_id in ids:
        pod = get_pod(pod_id)
        if pod and pod.get('project_id') == pid:
            delete_pod(pod_id)
    return jsonify({'deleted': len(ids)})


# ══════════════════════════════════════════════════════════════════════════════
# NE Instance routes
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/ne-instances')
def list_ne_instances(pid):
    from ipam import get_project
    proj = get_project(pid) or abort(404)
    instances = project_ne_instances(pid)
    # Enrich each with its NE type
    enriched = []
    for inst in instances:
        ne_type = get_ne_type(inst.get('ne_type_id', ''))
        enriched.append({**inst, 'ne_type': ne_type})
    return render_template('ne/ne_instances_list.html', proj=proj,
                           instances=enriched)


@ne_bp.route('/projects/<pid>/ne-instances/add', methods=['GET', 'POST'])
@editor_required
def add_ne_instance(pid):
    from ipam import get_project
    proj = get_project(pid) or abort(404)
    ne_types = available_ne_types(pid)
    all_types = ne_types['global'] + ne_types['project']
    errors, form_values = {}, {}

    if request.method == 'POST':
        name      = request.form.get('name', '').strip()
        ne_type_id = request.form.get('ne_type_id', '').strip()
        description = request.form.get('description', '').strip()
        labels    = parse_labels(request.form.get('labels', ''))
        form_values = request.form

        errors = form_errors(
            ('name',       bool(name),       'Name is required.'),
            ('ne_type_id', bool(ne_type_id), 'NE type is required.'),
        )
        if not errors:
            inst = {
                'id':           new_id(),
                'ne_type_id':   ne_type_id,
                'project_id':   pid,
                'name':         name,
                'description':  description,
                'labels':       labels,
                'params':       {},
                'iface_bindings': {},
            }
            save_ne_instance(inst)
            flash(f'NE instance "{name}" created.', 'success')
            return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=inst['id']))

    return render_template('ne/ne_instance_form.html', proj=proj,
                           ne_types=all_types, errors=errors,
                           form_values=form_values)


@ne_bp.route('/projects/<pid>/ne-instances/<nid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_ne_instance(pid, nid):
    from ipam import get_project
    proj = get_project(pid) or abort(404)
    inst = get_ne_instance(nid) or abort(404)
    errors, form_values = {}, {}

    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        labels      = parse_labels(request.form.get('labels', ''))
        form_values = request.form

        errors = form_errors(
            ('name', bool(name), 'Name is required.'),
        )
        if not errors:
            inst = {**inst, 'name': name, 'description': description,
                    'labels': labels}
            save_ne_instance(inst)
            flash(f'NE instance "{name}" updated.', 'success')
            return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    return render_template('ne/ne_instance_form.html', proj=proj, inst=inst,
                           ne_types=[], errors=errors, form_values=form_values)


@ne_bp.route('/projects/<pid>/ne-instances/<nid>/delete', methods=['POST'])
@editor_required
def delete_ne_instance_route(pid, nid):
    inst = get_ne_instance(nid) or abort(404)
    delete_ne_instance(nid)
    flash(f'NE instance "{inst["name"]}" deleted.', 'success')
    return redirect(url_for('ne.list_ne_instances', pid=pid))


@ne_bp.route('/projects/<pid>/ne-instances/<nid>')
def ne_instance_detail(pid, nid):
    from ipam import get_project
    import hw_logic
    proj = get_project(pid) or abort(404)
    inst = get_ne_instance(nid) or abort(404)
    ne_type = get_ne_type(inst.get('ne_type_id', '')) or {}
    ifaces = ne_type.get('interfaces', [])

    # Build iface → binding map with enrichment
    bindings = {}
    for iface in ifaces:
        iid = iface['id']
        binding = inst.get('iface_bindings', {}).get(iid, {})
        # Enrich ports with asset tags
        enriched_ports = []
        for p in binding.get('ports', []):
            hw_inst = hw_logic.get_hw_instance(p['hw_instance_id'])
            enriched_ports.append({
                **p,
                'asset_tag': hw_inst.get('asset_tag', p['hw_instance_id']) if hw_inst else '?',
            })
        bindings[iid] = {**binding, 'enriched_ports': enriched_ports}

    # HW instances in project for the bind modal
    hw_instances = hw_logic.project_instances(pid)

    from core.derivations import evaluate_all
    derived = evaluate_all('ne_instance', inst)

    return render_template('ne/ne_instance_detail.html', proj=proj, inst=inst,
                           ne_type=ne_type, ifaces=ifaces, bindings=bindings,
                           hw_instances=hw_instances, derived=derived)


# ── Binding routes ─────────────────────────────────────────────────────────────

@ne_bp.route('/ne-instances/<nid>/bindings/<iface_id>', methods=['POST'])
@editor_required
def bind_iface(nid, iface_id):
    """Create or replace a binding on one NE iface (Workflows A and D)."""
    import datetime
    import rules as rules_mod

    inst = get_ne_instance(nid) or abort(404)
    pid  = inst['project_id']
    ne_type = get_ne_type(inst.get('ne_type_id', ''))
    if not ne_type:
        abort(404)

    iface_map = {i['id']: i for i in ne_type.get('interfaces', [])}
    if iface_id not in iface_map:
        abort(404)

    bind_mode = request.form.get('bind_mode', 'single')

    if bind_mode == 'auto-rule':
        # Workflow D — parse rule, materialize
        try:
            rule = json.loads(request.form.get('rule_json', '{}'))
        except (json.JSONDecodeError, ValueError):
            flash('Invalid rule JSON.', 'danger')
            return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

        excluded = _collect_excluded_ports(pid, nid)
        ports = rules_mod.materialize_binding(rule, pid, excluded)
        binding = {
            'bind_mode':             'auto-rule',
            'rule':                  rule,
            'rule_materialized_at':  datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'ports':                 ports,
        }
    else:
        # Workflow A — explicit port selection
        try:
            ports_raw = json.loads(request.form.get('ports_json', '[]'))
        except (json.JSONDecodeError, ValueError):
            ports_raw = []

        lag_id = request.form.get('lag_id', '')

        # Check for conflicts with other NE instances
        excluded = _collect_excluded_ports(pid, nid)
        conflicts = []
        for p in ports_raw:
            key = (p.get('hw_instance_id', ''), p.get('port_id', ''))
            if key in excluded:
                conflicts.append(f"{key[0]}/{key[1]}")

        if conflicts:
            flash(f'Port(s) already bound: {", ".join(conflicts[:3])}', 'danger')
            return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

        ports = [{'hw_instance_id': p['hw_instance_id'],
                  'port_id':        p['port_id'],
                  'role':           p.get('role', 'primary'),
                  'bucket':         []}
                 for p in ports_raw]

        binding = {'bind_mode': bind_mode, 'ports': ports}
        if lag_id:
            binding['lag_id'] = lag_id

    # Replace the iface binding and save
    iface_bindings = dict(inst.get('iface_bindings', {}))
    iface_bindings[iface_id] = binding
    inst = {**inst, 'iface_bindings': iface_bindings}
    save_ne_instance(inst)

    iface_name = iface_map.get(iface_id, {}).get('name', iface_id)
    flash(f'Binding for iface "{iface_name}" saved ({bind_mode}).', 'success')
    return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))


@ne_bp.route('/ne-instances/<nid>/bindings/<iface_id>/delete', methods=['POST'])
@editor_required
def unbind_iface(nid, iface_id):
    """Remove a binding from one NE iface."""
    inst = get_ne_instance(nid) or abort(404)
    pid  = inst['project_id']
    iface_bindings = dict(inst.get('iface_bindings', {}))
    iface_bindings.pop(iface_id, None)
    inst = {**inst, 'iface_bindings': iface_bindings}
    save_ne_instance(inst)
    flash('Binding removed.', 'success')
    return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))


@ne_bp.route('/ne-instances/<nid>/bindings/<iface_id>/rematerialize', methods=['POST'])
@editor_required
def rematerialize_iface(nid, iface_id):
    """Refresh the ports[] of an auto-rule binding."""
    import datetime
    import rules as rules_mod

    inst = get_ne_instance(nid) or abort(404)
    pid  = inst['project_id']
    binding = inst.get('iface_bindings', {}).get(iface_id)
    if not binding or binding.get('bind_mode') != 'auto-rule':
        flash('Not an auto-rule binding.', 'warning')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    excluded = _collect_excluded_ports(pid, nid)
    ports = rules_mod.materialize_binding(binding['rule'], pid, excluded)
    binding = {
        **binding,
        'ports':                ports,
        'rule_materialized_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    iface_bindings = {**inst.get('iface_bindings', {}), iface_id: binding}
    save_ne_instance({**inst, 'iface_bindings': iface_bindings})
    flash(f'Auto-rule rematerialized: {len(ports)} port(s) matched.', 'success')
    return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))


# ── API endpoints ─────────────────────────────────────────────────────────────

@ne_bp.route('/api/projects/<pid>/hw/<hwid>/free-ports')
def api_hw_free_ports(pid, hwid):
    """
    List all ports on a HW instance with their binding/cable status.
    Used by the bind modal picker (Workflow A).
    Query param: ?type=<port_type> to filter.
    """
    import hw_logic
    inst = hw_logic.get_hw_instance(hwid)
    if not inst or inst.get('project_id') != pid:
        return jsonify({'error': 'not found'}), 404

    tmpl = hw_logic.get_hw_template(inst['template_id']) if inst.get('template_id') else None
    if not tmpl:
        return jsonify({'ports': []})

    type_filter = request.args.get('type', '')
    used_cables = hw_logic.used_ports(pid)

    ports = []
    for port in tmpl.get('ports', []):
        if type_filter and port.get('port_type') != type_filter:
            continue
        count = int(port.get('count', 1))
        for n in range(count):
            pid_ = port['id']
            pname = port['name'] if count == 1 else f"{port['name']}-{n}"
            bound = hw_logic.get_port_bound(hwid, pid_)
            cabled = (hwid, pid_) in used_cables
            ports.append({
                'port_id':    pid_,
                'name':       pname,
                'port_type':  port.get('port_type', ''),
                'connector':  port.get('connector', ''),
                'is_bound':   bool(bound),
                'bound_to':   bound,
                'is_cabled':  cabled,
            })

    return jsonify({'hw_instance_id': hwid, 'asset_tag': inst.get('asset_tag', ''),
                    'ports': ports})


@ne_bp.route('/api/projects/<pid>/hw/<hwid>/bindings')
def api_hw_bindings(pid, hwid):
    """Return NE bindings for a HW instance (read-only HW-side view)."""
    import hw_logic
    inst = hw_logic.get_hw_instance(hwid)
    if not inst or inst.get('project_id') != pid:
        return jsonify({'error': 'not found'}), 404
    bindings = hw_logic.hw_instance_bindings(hwid)
    # Enrich with NE instance names
    enriched = []
    for b in bindings:
        ne_inst = get_ne_instance(b['ne_instance_id'])
        enriched.append({
            **b,
            'ne_name': ne_inst.get('name', b['ne_instance_id']) if ne_inst else '?',
        })
    return jsonify({'bindings': enriched})


@ne_bp.route('/api/projects/<pid>/rules/preview', methods=['POST'])
def api_rules_preview(pid):
    """
    Preview Workflow D rule without committing.
    Body: {rule: {...}, exclude_nid: '...' (optional)}
    """
    import rules as rules_mod
    body = request.get_json(silent=True, force=True) or {}
    rule = body.get('rule', {})
    exclude_nid = body.get('exclude_nid', '')

    if not isinstance(rule, dict):
        return jsonify({'error': 'rule must be a JSON object'}), 400

    excluded = _collect_excluded_ports(pid, exclude_nid) if exclude_nid else set()
    summary = rules_mod.preview_binding(rule, pid, excluded)
    return jsonify(summary)


@ne_bp.route('/ne-instances/<nid>/bindings/bulk', methods=['POST'])
@editor_required
def bulk_bind_ifaces(nid):
    """Workflow B: bind multiple NE ifaces to HW ports in a single form submit."""
    inst = get_ne_instance(nid) or abort(404)
    pid  = inst['project_id']

    try:
        pairs = json.loads(request.form.get('pairs_json', '[]'))
    except (json.JSONDecodeError, ValueError):
        flash('Invalid binding data.', 'danger')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    if not pairs:
        flash('No bindings specified.', 'warning')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    ne_type = get_ne_type(inst.get('ne_type_id', '')) or abort(404)
    iface_map = {i['id']: i for i in ne_type.get('interfaces', [])}
    bind_mode = request.form.get('bind_mode', 'single')

    excluded = _collect_excluded_ports(pid, nid)
    conflicts = [
        f"{p.get('hw_instance_id','')}/{p.get('port_id','')}"
        for p in pairs
        if (p.get('hw_instance_id', ''), p.get('port_id', '')) in excluded
    ]
    if conflicts:
        flash(f'Port(s) already bound by another NE: {", ".join(conflicts[:3])}', 'danger')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    iface_bindings = dict(inst.get('iface_bindings', {}))
    saved = 0
    for pair in pairs:
        iface_id = pair.get('iface_id', '')
        hw_iid   = pair.get('hw_instance_id', '')
        port_id  = pair.get('port_id', '')
        if iface_id not in iface_map or not hw_iid or not port_id:
            continue
        iface_bindings[iface_id] = {
            'bind_mode': bind_mode,
            'ports': [{'hw_instance_id': hw_iid, 'port_id': port_id,
                       'role': 'primary', 'bucket': []}],
        }
        saved += 1

    save_ne_instance({**inst, 'iface_bindings': iface_bindings})
    flash(f'Bulk bound {saved} interface(s).', 'success')
    return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))


@ne_bp.route('/ne-instances/<nid>/bindings/autoresolve', methods=['POST'])
@editor_required
def autoresolve_bindings(nid):
    """Workflow C: apply the auto-resolved iface-to-port pairs chosen by the user."""
    inst = get_ne_instance(nid) or abort(404)
    pid  = inst['project_id']

    try:
        pairs = json.loads(request.form.get('pairs_json', '[]'))
    except (json.JSONDecodeError, ValueError):
        flash('Invalid binding data.', 'danger')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    if not pairs:
        flash('No iface-to-port matches to apply.', 'warning')
        return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))

    ne_type = get_ne_type(inst.get('ne_type_id', '')) or abort(404)
    iface_map = {i['id']: i for i in ne_type.get('interfaces', [])}

    iface_bindings = dict(inst.get('iface_bindings', {}))
    saved = 0
    for pair in pairs:
        iface_id = pair.get('iface_id', '')
        hw_iid   = pair.get('hw_instance_id', '')
        port_id  = pair.get('port_id', '')
        if iface_id not in iface_map or not hw_iid or not port_id:
            continue
        iface_bindings[iface_id] = {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': hw_iid, 'port_id': port_id,
                       'role': 'primary', 'bucket': []}],
        }
        saved += 1

    save_ne_instance({**inst, 'iface_bindings': iface_bindings})
    flash(f'Auto-resolved {saved} interface binding(s).', 'success')
    return redirect(url_for('ne.ne_instance_detail', pid=pid, nid=nid))


@ne_bp.route('/projects/<pid>/rematerialize-rules', methods=['POST'])
@editor_required
def rematerialize_all_rules(pid):
    """
    Refresh every auto-rule binding across all NE instances in the project.
    Useful after bulk HW import or template port changes.
    """
    import datetime
    import rules as rules_mod
    from ipam import get_project

    proj = get_project(pid) or abort(404)
    instances = project_ne_instances(pid)

    updated_ne = 0
    updated_bindings = 0

    for inst in instances:
        bindings = inst.get('iface_bindings', {})
        changed  = False
        new_bindings = dict(bindings)

        for iface_id, binding in bindings.items():
            if binding.get('bind_mode') != 'auto-rule':
                continue
            rule = binding.get('rule') or {}
            excluded = _collect_excluded_ports(pid, inst['id'])
            fresh_ports = rules_mod.materialize_binding(rule, pid, excluded)
            new_bindings[iface_id] = {
                **binding,
                'ports':                fresh_ports,
                'rule_materialized_at': datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
            }
            updated_bindings += 1
            changed = True

        if changed:
            save_ne_instance({**inst, 'iface_bindings': new_bindings})
            updated_ne += 1

    flash(
        f'Re-evaluated auto-rule bindings across {updated_ne} NE instance(s) '
        f'({updated_bindings} binding(s) refreshed).',
        'success',
    )
    return redirect(url_for('ne.list_ne_instances', pid=proj['id']))


@ne_bp.route('/api/ne-instances/<nid>/autoresolve-preview')
def api_autoresolve_preview(nid):
    """
    Workflow C preview: compute name-based iface→port matches without saving.
    Query param: hw_instance_id=<hwid>
    Returns: {matches: [{iface_id, iface_name, hw_instance_id, port_id, port_name, match_type}],
              unmatched: [{iface_id, iface_name}]}
    """
    import hw_logic as hw_logic_mod
    inst = get_ne_instance(nid)
    if not inst:
        abort(404)
    hwid = request.args.get('hw_instance_id', '')
    if not hwid:
        return jsonify({'error': 'hw_instance_id required'}), 400

    ne_type = get_ne_type(inst.get('ne_type_id', ''))
    if not ne_type:
        return jsonify({'matches': [], 'unmatched': []})

    hw_inst = hw_logic_mod.get_hw_instance(hwid)
    if not hw_inst:
        return jsonify({'error': 'HW instance not found'}), 404

    tmpl = hw_logic_mod.get_hw_template(hw_inst.get('template_id', ''))
    ports = []
    if tmpl:
        for port in tmpl.get('ports', []):
            count = int(port.get('count', 1))
            for n in range(count):
                pname = port['name'] if count == 1 else f"{port['name']}-{n}"
                ports.append({
                    'port_id':   port['id'],
                    'name':      pname,
                    'port_type': port.get('port_type', ''),
                    'notes':     port.get('notes', ''),
                    'is_bound':  bool(hw_logic_mod.get_port_bound(hwid, port['id'])),
                })

    already_bound = set(inst.get('iface_bindings', {}).keys())
    ifaces = [i for i in ne_type.get('interfaces', []) if i['id'] not in already_bound]

    used_port_ids: set = set()
    matches   = []
    unmatched = []

    for iface in ifaces:
        iname = iface['name'].lower()
        ilabels = set(iface.get('labels', []))
        best = None
        best_score = 0

        for port in ports:
            if port['is_bound'] or port['port_id'] in used_port_ids:
                continue
            pname_lower = port['name'].lower()
            notes_lower = port['notes'].lower()
            score = 0
            if iname == pname_lower:
                score = 3
            elif iname in pname_lower or iname in notes_lower:
                score = 2
            if score > 0 and port['port_type'] and port['port_type'] in ilabels:
                score += 1
            if score > best_score:
                best_score = score
                best = port

        if best:
            used_port_ids.add(best['port_id'])
            matches.append({
                'iface_id':       iface['id'],
                'iface_name':     iface['name'],
                'hw_instance_id': hwid,
                'port_id':        best['port_id'],
                'port_name':      best['name'],
                'match_type':     'exact' if best_score >= 3 else 'substring',
            })
        else:
            unmatched.append({'iface_id': iface['id'], 'iface_name': iface['name']})

    return jsonify({'matches': matches, 'unmatched': unmatched})


@ne_bp.route('/api/ne-instances/<nid>/impact')
def ne_instance_impact(nid):
    """Return cascade info for delete-confirm modal."""
    inst = get_ne_instance(nid)
    if not inst:
        abort(404)
    n_bindings = sum(
        1 for b in inst.get('iface_bindings', {}).values()
        if b.get('ports')
    )
    cascades = []
    if n_bindings:
        cascades.append({'kind': 'detach', 'count': n_bindings,
                         'what': f'{n_bindings} iface binding(s) will be released'})
    return jsonify({'label': inst['name'], 'cascades': cascades})
