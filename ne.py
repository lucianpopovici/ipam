"""Network element (NE) domain helpers."""

import json
from flask import Blueprint
from db import r, redis_get, redis_save, redis_delete, redis_all

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


def project_sites_key(pid):
    return f'project:{pid}:sites'


def project_pods_key(pid):
    return f'project:{pid}:pods'


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
    redis_save(_schema_key(entity, pid), fields)


def get_schema(entity, pid=None):
    if pid:
        raw = redis_get(_schema_key(entity, pid))
        if raw:
            return raw
    raw = redis_get(_schema_key(entity, None))
    return raw if raw else []


# NE Types

def save_ne_type(ne_type):
    return redis_save(_ne_type_key(ne_type['id']), ne_type, NE_TYPE_INDEX)


def get_ne_type(tid):
    return redis_get(_ne_type_key(tid))


def delete_ne_type(tid):
    redis_delete(_ne_type_key(tid), NE_TYPE_INDEX, tid)


def all_ne_types():
    return redis_all(NE_TYPE_INDEX, get_ne_type, sort_key=lambda t: t.get('name', ''))


def global_ne_types():
    return [t for t in all_ne_types() if t.get('scope') == 'global']


def project_ne_types(pid):
    return [t for t in all_ne_types() if t.get('scope') == 'project' and t.get('project_id') == pid]


def available_ne_types(pid):
    return {'global': global_ne_types(), 'project': project_ne_types(pid)}


# Site

def save_site(site):
    sid = site['id']
    redis_save(_site_key(sid), site, SITE_INDEX)
    if site.get('project_id'):
        r.sadd(project_sites_key(site['project_id']), sid)
    return site


def get_site(sid):
    return redis_get(_site_key(sid))


def delete_site(sid):
    site = get_site(sid)
    if not site:
        return
    if site.get('project_id'):
        r.srem(project_sites_key(site['project_id']), sid)
    redis_delete(_site_key(sid), SITE_INDEX, sid)


def project_sites(pid):
    return redis_all(project_sites_key(pid), get_site, sort_key=lambda s: s.get('name', ''))


def site_pods(sid):
    return redis_all(_site_pods_key(sid), get_pod)


# Pod

def save_pod(pod):
    pid = pod['id']
    redis_save(_pod_key(pid), pod, POD_INDEX)
    if pod.get('project_id'):
        r.sadd(project_pods_key(pod['project_id']), pid)
    return pod


def get_pod(pid):
    return redis_get(_pod_key(pid))


def delete_pod(pid):
    pod = get_pod(pid)
    if not pod:
        return
    if pod.get('project_id'):
        r.srem(project_pods_key(pod['project_id']), pid)
    redis_delete(_pod_key(pid), POD_INDEX, pid)


def project_pods(pid):
    return redis_all(project_pods_key(pid), get_pod)


def pod_sites(pid):
    return redis_all(_pod_sites_key(pid), get_site)


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
    """
    Compute aggregate resource requirements for all PODs and NEs in a project.
    Groups identical requirements by (interface, version, labels).
    """
    sites = project_sites(pid)
    if not sites:
        return []

    # Flatten to pods to reduce nesting
    pods_with_site = []
    for site in sites:
        for pod in site_pods(site['id']):
            pods_with_site.append((site, pod))

    req_by_key = {}
    for site, pod in pods_with_site:
        for slot in get_pod_slots(pod['id']):
            ne_type = get_ne_type(slot['ne_type_id'])
            if not ne_type:
                continue

            for iface in ne_type.get('interfaces', []):
                pool_labels = set(site.get('labels', []) + pod.get('labels', []) +
                                  ne_type.get('labels', []) + iface.get('labels', []))
                for ip_version in ('ipv4', 'ipv6'):
                    iface_cfg = iface.get(ip_version)
                    if not iface_cfg:
                        continue

                    count = _sharing_count(iface.get('sharing', ''), slot.get('count', 1))
                    sharing = iface.get('sharing', '')
                    labels_tuple = tuple(sorted(pool_labels))
                    key = (pid, iface['id'], ip_version, sharing, labels_tuple)

                    if key not in req_by_key:
                        req_by_key[key] = {
                            'project_id': pid,
                            'iface_name': iface.get('name', ''),
                            'ip_version': ip_version,
                            'sharing': sharing,
                            'count': count,
                            'labels': sorted(pool_labels),
                        }
                    elif sharing == 'interface':
                        req_by_key[key]['count'] += count

    return list(req_by_key.values())


def save_requirements(pid, requirements):
    """Persist computed requirements to Redis."""
    r.set(f'project:{pid}:requirements', json.dumps(requirements))


def load_requirements(pid):
    """Load previously saved requirements from Redis."""
    raw = r.get(f'project:{pid}:requirements')
    return json.loads(raw) if raw else []


# Utility

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
