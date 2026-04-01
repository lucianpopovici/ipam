"""Network element (NE) domain helpers."""

from flask import Blueprint
from db import r
import json
import uuid

ne_bp = Blueprint('ne', __name__, url_prefix='')

# Redis index keys
SCHEMA_KEY = 'ne:schemas'
NE_TYPE_INDEX = 'ne_types:index'
SITE_INDEX = 'sites:index'
POD_INDEX = 'pods:index'


def _schema_key(entity, pid=None):
    scope = pid or 'global'
    return f'schema:{entity}:{scope}'


def _ne_type_key(tid):
    return f'ne_type:{tid}'


def _site_key(sid):
    return f'site:{sid}'


def _pod_key(pid):
    return f'pod:{pid}'


def _pod_slots_key(pid):
    return f'pod:{pid}:slots'


def _site_pods_key(sid):
    return f'site:{sid}:pods'


def _pod_sites_key(pid):
    return f'pod:{pid}:sites'


def new_id():
    return str(uuid.uuid4())[:8]


def expand_site_pattern(pattern):
    import re
    m = re.match(r'^(.*)\{(\d+)\.\.(\d+)\}(.*)$', pattern)
    if not m:
        return None, ValueError('invalid pattern')
    prefix, start, end, suffix = m.groups()
    start_n = int(start)
    end_n = int(end)
    if end_n < start_n:
        return None, ValueError('end < start')
    count = end_n - start_n + 1
    if count > 10000:
        return None, ValueError('range too large')
    w = max(len(start), len(end))
    names = [f"{prefix}{str(i).zfill(w)}{suffix}" for i in range(start_n, end_n + 1)]
    return names, None


# Schema

def save_schema(entity, fields, pid=None):
    r.set(_schema_key(entity, pid), json.dumps(fields))


def get_schema(entity, pid=None):
    if pid:
        raw = r.get(_schema_key(entity, pid))
        if raw:
            return json.loads(raw)
    raw = r.get(_schema_key(entity, None))
    return json.loads(raw) if raw else []


# NE Types

def save_ne_type(ne_type):
    tid = ne_type['id']
    r.set(_ne_type_key(tid), json.dumps(ne_type))
    r.sadd(NE_TYPE_INDEX, tid)
    return ne_type


def get_ne_type(tid):
    raw = r.get(_ne_type_key(tid))
    return json.loads(raw) if raw else None


def delete_ne_type(tid):
    r.delete(_ne_type_key(tid))
    r.srem(NE_TYPE_INDEX, tid)


def global_ne_types():
    types = []
    for tid in sorted(r.smembers(NE_TYPE_INDEX)):
        ne = get_ne_type(tid)
        if ne and ne.get('scope') == 'global':
            types.append(ne)
    return types


def project_ne_types(pid):
    types = []
    for tid in sorted(r.smembers(NE_TYPE_INDEX)):
        ne = get_ne_type(tid)
        if ne and ne.get('scope') == 'project' and ne.get('project_id') == pid:
            types.append(ne)
    return types


def available_ne_types(pid):
    return {'global': global_ne_types(), 'project': project_ne_types(pid)}


# Site

def save_site(site):
    sid = site['id']
    r.set(_site_key(sid), json.dumps(site))
    r.sadd(SITE_INDEX, sid)
    if site.get('project_id'):
        r.sadd(f'project:{site["project_id"]}:sites', sid)
    return site


def get_site(sid):
    raw = r.get(_site_key(sid))
    return json.loads(raw) if raw else None


def delete_site(sid):
    r.delete(_site_key(sid))
    r.srem(SITE_INDEX, sid)


def project_sites(pid):
    return [get_site(sid) for sid in sorted(r.smembers(f'project:{pid}:sites')) if get_site(sid)]


def site_pods(sid):
    return [get_pod(pid) for pid in sorted(r.smembers(_site_pods_key(sid))) if get_pod(pid)]


# Pod

def save_pod(pod):
    pid = pod['id']
    r.set(_pod_key(pid), json.dumps(pod))
    r.sadd(POD_INDEX, pid)
    if pod.get('project_id'):
        r.sadd(f'project:{pod["project_id"]}:pods', pid)
    return pod


def get_pod(pid):
    raw = r.get(_pod_key(pid))
    return json.loads(raw) if raw else None


def delete_pod(pid):
    r.delete(_pod_key(pid))
    r.srem(POD_INDEX, pid)


def project_pods(pid):
    return [get_pod(qid) for qid in sorted(r.smembers(f'project:{pid}:pods')) if get_pod(qid)]


def pod_sites(pid):
    return [get_site(sid) for sid in sorted(r.smembers(_pod_sites_key(pid))) if get_site(sid)]


def assign_pod_to_site(pod_id, site_id):
    r.sadd(_site_pods_key(site_id), pod_id)
    r.sadd(_pod_sites_key(pod_id), site_id)


def unassign_pod_from_site(pod_id, site_id):
    r.srem(_site_pods_key(site_id), pod_id)
    r.srem(_pod_sites_key(pod_id), site_id)


# Pod slots

def save_pod_slots(pod_id, slots):
    r.set(_pod_slots_key(pod_id), json.dumps(slots))


def get_pod_slots(pod_id):
    raw = r.get(_pod_slots_key(pod_id))
    return json.loads(raw) if raw else []


# Requirement engine

def _sharing_count(sharing, count):
    if sharing == 'interface':
        return count
    return 1


def compute_requirements(pid):
    sites = project_sites(pid)
    if not sites:
        return []
    req_by_key = {}

    for site in sites:
        for pod in site_pods(site['id']):
            for slot in get_pod_slots(pod['id']):
                ne = get_ne_type(slot['ne_type_id'])
                if not ne:
                    continue
                pool_labels = set(site.get('labels', []) + pod.get('labels', []) + ne.get('labels', []))
                for iface in ne.get('interfaces', []):
                    for ip_version in ('ipv4', 'ipv6'):
                        iface_cfg = iface.get(ip_version)
                        if not iface_cfg:
                            continue
                        count = _sharing_count(iface.get('sharing', ''), slot.get('count', 1))
                        key = (pid, iface['id'], ip_version, iface.get('sharing', ''), tuple(sorted(pool_labels)))
                        if key not in req_by_key:
                            req_by_key[key] = {
                                'project_id': pid,
                                'iface_name': iface.get('name', ''),
                                'ip_version': ip_version,
                                'sharing': iface.get('sharing', ''),
                                'count': count,
                                'labels': sorted(pool_labels),
                            }
                        else:
                            if iface.get('sharing', '') == 'interface':
                                req_by_key[key]['count'] += count

    return list(req_by_key.values())


def save_requirements(pid, requirements):
    r.set(f'project:{pid}:requirements', json.dumps(requirements))


def load_requirements(pid):
    raw = r.get(f'project:{pid}:requirements')
    return json.loads(raw) if raw else []


# Utility

def parse_labels(value):
    if value is None:
        return []
    return [p.strip() for p in str(value).split(',') if p.strip()]


def collect_params(schema, form):
    data = {}
    for field in schema:
        fid = field.get('id')
        ftype = field.get('field_type')
        if ftype == 'checkbox':
            data[fid] = bool(form.get(fid))
        elif ftype == 'multi-select':
            data[fid] = form.getlist(fid) if hasattr(form, 'getlist') else [form.get(fid)]
        else:
            data[fid] = form.get(fid, '')
    return data
