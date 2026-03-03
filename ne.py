"""
Network Element (NE) blueprint
Covers: schemas, NE types, sites, PODs, and subnet requirement generation.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
import ipaddress, json, re, uuid, os
from db import r   # shared Redis connection

ne_bp = Blueprint('ne', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Key helpers
# ══════════════════════════════════════════════════════════════════════════════

def _schema_key(entity, pid=None):
    return f'project:{pid}:schema:{entity}' if pid else f'schema:{entity}'

def _ne_type_key(tid):    return f'ne_type:{tid}'
def _site_key(sid):       return f'site:{sid}'
def _site_pods_key(sid):  return f'site:{sid}:pods'
def _pod_key(pid_):       return f'pod:{pid_}'
def _pod_sites_key(pid_): return f'pod:{pid_}:sites'
def _pod_slots_key(pid_): return f'pod:{pid_}:slots'   # JSON list of ne-slots
def _proj_sites_key(pid): return f'project:{pid}:sites'
def _proj_pods_key(pid):  return f'project:{pid}:pods'
def _proj_netypes_key(pid): return f'project:{pid}:ne_types'

NE_TYPES_INDEX = 'ne_types:index'
ENTITY_TYPES   = ('site', 'pod', 'ne', 'interface')
NE_KINDS       = ('CNF', 'VNF', 'PNF', 'VM', 'Container')
SHARING_LEVELS = ('project', 'site', 'pod', 'ne', 'interface')
FIELD_TYPES    = ('text', 'number', 'textarea', 'dropdown', 'multi-select', 'checkbox')

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
    r.set(_schema_key(entity, pid), json.dumps(fields))

def new_field(name, label, field_type='text', required=False, options=None, default=''):
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
    raw = r.get(_ne_type_key(tid))
    return json.loads(raw) if raw else None

def save_ne_type(ne):
    r.set(_ne_type_key(ne['id']), json.dumps(ne))
    if ne.get('scope') == 'project' and ne.get('project_id'):
        r.sadd(_proj_netypes_key(ne['project_id']), ne['id'])
    else:
        r.sadd(NE_TYPES_INDEX, ne['id'])

def delete_ne_type(tid):
    ne = get_ne_type(tid)
    if not ne: return
    if ne.get('scope') == 'project' and ne.get('project_id'):
        r.srem(_proj_netypes_key(ne['project_id']), tid)
    else:
        r.srem(NE_TYPES_INDEX, tid)
    r.delete(_ne_type_key(tid))

def global_ne_types() -> list:
    return sorted([t for t in (get_ne_type(tid) for tid in r.smembers(NE_TYPES_INDEX)) if t],
                  key=lambda t: t['name'])

def project_ne_types(pid) -> list:
    return sorted([t for t in (get_ne_type(tid) for tid in r.smembers(_proj_netypes_key(pid))) if t],
                  key=lambda t: t['name'])

def available_ne_types(pid) -> dict:
    return {'global': global_ne_types(), 'project': project_ne_types(pid)}

# ══════════════════════════════════════════════════════════════════════════════
# Site helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_site(sid):
    raw = r.get(_site_key(sid))
    return json.loads(raw) if raw else None

def save_site(site):
    r.set(_site_key(site['id']), json.dumps(site))
    r.sadd(_proj_sites_key(site['project_id']), site['id'])

def delete_site(sid):
    site = get_site(sid)
    if not site: return
    r.srem(_proj_sites_key(site['project_id']), sid)
    for pod_id in r.smembers(_site_pods_key(sid)):
        r.srem(_pod_sites_key(pod_id), sid)
    r.delete(_site_pods_key(sid))
    r.delete(_site_key(sid))

def project_sites(pid) -> list:
    return sorted([s for s in (get_site(sid) for sid in r.smembers(_proj_sites_key(pid))) if s],
                  key=lambda s: s['name'])

def site_pods(sid) -> list:
    from ne import get_pod
    return [p for p in (get_pod(pid_) for pid_ in r.smembers(_site_pods_key(sid))) if p]

# ══════════════════════════════════════════════════════════════════════════════
# POD helpers
# ══════════════════════════════════════════════════════════════════════════════
# POD record: { id, name, project_id, description, labels:[], params:{} }
# Slots stored separately as JSON list in pod:<id>:slots

def get_pod(pod_id):
    raw = r.get(_pod_key(pod_id))
    return json.loads(raw) if raw else None

def save_pod(pod):
    r.set(_pod_key(pod['id']), json.dumps(pod))
    r.sadd(_proj_pods_key(pod['project_id']), pod['id'])

def delete_pod(pod_id):
    pod = get_pod(pod_id)
    if not pod: return
    r.srem(_proj_pods_key(pod['project_id']), pod_id)
    for sid in r.smembers(_pod_sites_key(pod_id)):
        r.srem(_site_pods_key(sid), pod_id)
    r.delete(_pod_sites_key(pod_id))
    r.delete(_pod_slots_key(pod_id))
    r.delete(_pod_key(pod_id))

def project_pods(pid) -> list:
    return sorted([p for p in (get_pod(pod_id) for pod_id in r.smembers(_proj_pods_key(pid))) if p],
                  key=lambda p: p['name'])

def get_pod_slots(pod_id) -> list:
    raw = r.get(_pod_slots_key(pod_id))
    return json.loads(raw) if raw else []

def save_pod_slots(pod_id, slots: list):
    r.set(_pod_slots_key(pod_id), json.dumps(slots))

def pod_sites(pod_id) -> list:
    return [s for s in (get_site(sid) for sid in r.smembers(_pod_sites_key(pod_id))) if s]

def assign_pod_to_site(pod_id, sid):
    r.sadd(_site_pods_key(sid), pod_id)
    r.sadd(_pod_sites_key(pod_id), sid)

def unassign_pod_from_site(pod_id, sid):
    r.srem(_site_pods_key(sid), pod_id)
    r.srem(_pod_sites_key(pod_id), sid)

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

    for site in sites:
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
                            key = f'site:{site["id"]}|pod:{pod["id"]}|slot:{slot["ne_type_id"]}|iface:{iface["id"]}|v:{ip_ver}'
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
    r.set(f'project:{pid}:requirements', json.dumps(reqs))

def load_requirements(pid: str) -> list:
    raw = r.get(f'project:{pid}:requirements')
    return json.loads(raw) if raw else []

def mark_pushed(pid: str, req_key: str):
    reqs = load_requirements(pid)
    for req in reqs:
        if req['key'] == req_key:
            req['pushed'] = True
    save_requirements(pid, reqs)

# ══════════════════════════════════════════════════════════════════════════════
# Utility
# ══════════════════════════════════════════════════════════════════════════════

def new_id():
    return str(uuid.uuid4())[:8]

def parse_labels(form_value: str) -> list:
    if not form_value:
        return []
    seen, result = set(), []
    for l in form_value.split(','):
        l = l.strip()
        if l and l not in seen:
            seen.add(l); result.append(l)
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
def admin_schemas():
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
def project_schemas(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
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
    return render_template('ne/ne_types_list.html',
                           global_types=global_ne_types(), project_types=[])


@ne_bp.route('/ne-types/add',                       methods=['GET','POST'])
@ne_bp.route('/projects/<pid>/ne-types/add',        methods=['GET','POST'])
def add_ne_type(pid=None):
    from ipam import get_project
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        if not name:
            flash('Name is required.', 'danger')
            return redirect(request.url)
        ifaces_raw = request.form.get('interfaces_json','[]')
        try:
            interfaces = json.loads(ifaces_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid interfaces JSON: {e}', 'danger')
            return redirect(request.url)
        ne_schema = get_schema('ne', pid)
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
    ne_schema    = get_schema('ne', pid)
    iface_schema = get_schema('interface', pid)
    return render_template('ne/ne_type_form.html', ne=None, proj=proj,
                           ne_schema=ne_schema, iface_schema=iface_schema,
                           ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                           field_types=FIELD_TYPES)


@ne_bp.route('/ne-types/<tid>/edit', methods=['GET','POST'])
def edit_ne_type(tid):
    from ipam import get_project
    ne = get_ne_type(tid)
    if not ne: abort(404)
    pid  = ne.get('project_id') or None
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        ifaces_raw = request.form.get('interfaces_json','[]')
        try:
            interfaces = json.loads(ifaces_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid interfaces JSON: {e}', 'danger')
            return redirect(request.url)
        ne_schema = get_schema('ne', pid)
        ne['name']        = request.form.get('name', ne['name']).strip()
        ne['kind']        = request.form.get('kind', ne['kind'])
        ne['description'] = request.form.get('description','')
        ne['labels']      = parse_labels(request.form.get('labels',''))
        ne['params']      = collect_params(ne_schema, request.form)
        ne['interfaces']  = interfaces
        save_ne_type(ne)
        flash(f'NE Type "{ne["name"]}" updated.', 'success')
        return redirect(url_for('ne.list_project_ne_types', pid=pid) if pid
                        else url_for('ne.list_ne_types'))
    ne_schema    = get_schema('ne', pid)
    iface_schema = get_schema('interface', pid)
    return render_template('ne/ne_type_form.html', ne=ne, proj=proj,
                           ne_schema=ne_schema, iface_schema=iface_schema,
                           ne_kinds=NE_KINDS, sharing_levels=SHARING_LEVELS,
                           field_types=FIELD_TYPES)


@ne_bp.route('/ne-types/<tid>/delete', methods=['POST'])
def delete_ne_type_route(tid):
    ne = get_ne_type(tid)
    if not ne: abort(404)
    pid = ne.get('project_id') or None
    delete_ne_type(tid)
    flash(f'NE Type "{ne["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_project_ne_types', pid=pid) if pid
                    else url_for('ne.list_ne_types'))


@ne_bp.route('/projects/<pid>/ne-types')
def list_project_ne_types(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    return render_template('ne/ne_types_list.html',
                           proj=proj,
                           global_types=global_ne_types(),
                           project_types=project_ne_types(pid))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Sites
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/sites')
def list_sites(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    sites  = project_sites(pid)
    schema = get_schema('site', pid)
    # Annotate each site with its assigned pods
    for site in sites:
        site['pods'] = site_pods(site['id'])
    return render_template('ne/sites_list.html', proj=proj, sites=sites, schema=schema)


@ne_bp.route('/projects/<pid>/sites/add', methods=['GET','POST'])
def add_site(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    schema = get_schema('site', pid)
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        if not name:
            flash('Site name is required.', 'danger')
            return redirect(request.url)
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
    return render_template('ne/site_form.html', proj=proj, site=None, schema=schema)


@ne_bp.route('/projects/<pid>/sites/bulk', methods=['GET','POST'])
def bulk_add_sites(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
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
def edit_site(pid, sid):
    from ipam import get_project
    proj = get_project(pid)
    site = get_site(sid)
    if not proj or not site: abort(404)
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
def delete_site_route(pid, sid):
    site = get_site(sid)
    if not site: abort(404)
    delete_site(sid)
    flash(f'Site "{site["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_sites', pid=pid))


@ne_bp.route('/projects/<pid>/sites/<sid>/assign-pod', methods=['POST'])
def assign_pod_to_site_route(pid, sid):
    pod_id = request.form.get('pod_id','').strip()
    if pod_id:
        assign_pod_to_site(pod_id, sid)
        flash('POD assigned to site.', 'success')
    return redirect(url_for('ne.site_detail', pid=pid, sid=sid))


@ne_bp.route('/projects/<pid>/sites/<sid>/unassign-pod', methods=['POST'])
def unassign_pod_from_site_route(pid, sid):
    pod_id = request.form.get('pod_id','').strip()
    if pod_id:
        unassign_pod_from_site(pod_id, sid)
        flash('POD unassigned from site.', 'info')
    return redirect(url_for('ne.site_detail', pid=pid, sid=sid))


@ne_bp.route('/projects/<pid>/sites/<sid>')
def site_detail(pid, sid):
    from ipam import get_project
    proj = get_project(pid)
    site = get_site(sid)
    if not proj or not site: abort(404)
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
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    pods   = project_pods(pid)
    schema = get_schema('pod', pid)
    for pod in pods:
        pod['sites']      = pod_sites(pod['id'])
        pod['slots']      = get_pod_slots(pod['id'])
        pod['slot_count'] = len(pod['slots'])
    return render_template('ne/pods_list.html', proj=proj, pods=pods, schema=schema)


@ne_bp.route('/projects/<pid>/pods/add', methods=['GET','POST'])
def add_pod(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    schema = get_schema('pod', pid)
    if request.method == 'POST':
        name = request.form.get('name','').strip()
        if not name:
            flash('POD name is required.', 'danger')
            return redirect(request.url)
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
    return render_template('ne/pod_form.html', proj=proj, pod=None, schema=schema)


@ne_bp.route('/projects/<pid>/pods/<pod_id>/edit', methods=['GET','POST'])
def edit_pod(pid, pod_id):
    from ipam import get_project
    proj = get_project(pid)
    pod  = get_pod(pod_id)
    if not proj or not pod: abort(404)
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
def delete_pod_route(pid, pod_id):
    pod = get_pod(pod_id)
    if not pod: abort(404)
    delete_pod(pod_id)
    flash(f'POD "{pod["name"]}" deleted.', 'info')
    return redirect(url_for('ne.list_pods', pid=pid))


@ne_bp.route('/projects/<pid>/pods/<pod_id>')
def pod_detail(pid, pod_id):
    from ipam import get_project
    proj = get_project(pid)
    pod  = get_pod(pod_id)
    if not proj or not pod: abort(404)
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
def update_pod_slots(pid, pod_id):
    """Replace the NE slot list for a POD (posted as JSON)."""
    pod = get_pod(pod_id)
    if not pod: abort(404)
    try:
        slots = request.get_json(force=True)
        if not isinstance(slots, list):
            raise ValueError('Expected JSON array')
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    save_pod_slots(pod_id, slots)
    return jsonify({'ok': True, 'count': len(slots)})


@ne_bp.route('/projects/<pid>/pods/<pod_id>/assign-site', methods=['POST'])
def assign_site_to_pod_route(pid, pod_id):
    sid = request.form.get('site_id','').strip()
    if sid:
        assign_pod_to_site(pod_id, sid)
        flash('Site assigned to POD.', 'success')
    return redirect(url_for('ne.pod_detail', pid=pid, pod_id=pod_id))


@ne_bp.route('/projects/<pid>/pods/<pod_id>/unassign-site', methods=['POST'])
def unassign_site_from_pod_route(pid, pod_id):
    sid = request.form.get('site_id','').strip()
    if sid:
        unassign_pod_from_site(pod_id, sid)
        flash('Site unassigned.', 'info')
    return redirect(url_for('ne.pod_detail', pid=pid, pod_id=pod_id))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Subnet Requirements
# ══════════════════════════════════════════════════════════════════════════════

@ne_bp.route('/projects/<pid>/requirements')
def requirements(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj: abort(404)
    reqs = compute_requirements(pid)
    save_requirements(pid, reqs)
    # Summary stats
    total   = sum(r_['count'] for r_ in reqs)
    pushed  = sum(r_['count'] for r_ in reqs if r_.get('pushed'))
    return render_template('ne/requirements.html', proj=proj, reqs=reqs,
                           total=total, pushed=pushed,
                           sharing_levels=SHARING_LEVELS)


@ne_bp.route('/projects/<pid>/requirements/push', methods=['POST'])
def push_requirements(pid):
    """
    Push selected (or all) requirements to IPAM as subnets.
    Expects JSON: { "keys": ["key1","key2",...] }  or  { "all": true }
    """
    from ipam import get_project, get_network, save_network as _save_net, \
                    carve_next_subnet, add_labels_to_network, project_nets_key, new_id as _new_id
    proj = get_project(pid)
    if not proj: abort(404)

    data     = request.get_json(force=True) or {}
    reqs     = load_requirements(pid)
    push_all = data.get('all', False)
    keys     = set(data.get('keys', []))

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
                from ipam import save_network as _sn
                from db import r as _r
                _sn(net)
                _r.sadd(project_nets_key(pid), net['id'])
                add_labels_to_network(net['id'], req['labels'])
                results.append({'cidr': cidr, 'labels': req['labels']})
            except ValueError as e:
                errors.append({'req': req['key'], 'error': str(e)})
        if not errors:
            mark_pushed(pid, req['key'])

    return jsonify({'pushed': results, 'errors': errors})
