"""
IPAM blueprint — projects, subnets, IPs, labels, subnet templates, pool, search, overview.
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
import ipaddress, json, uuid
from db import r

ipam_bp = Blueprint('ipam', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Key helpers
# ══════════════════════════════════════════════════════════════════════════════

def project_key(pid):            return f'project:{pid}'
def project_nets_key(pid):       return f'project:{pid}:networks'
def project_labels_key(pid):     return f'project:{pid}:labels'
def project_templates_key(pid):  return f'project:{pid}:templates'
def net_key(nid):                return f'network:{nid}'
def net_ips_key(nid):            return f'network:{nid}:ips'
def net_labels_key(nid):         return f'network:{nid}:labels'
def label_nets_key(label):       return f'label:{label}:nets'
def template_key(tid):           return f'template:{tid}'
def ip_key(ip):                  return f'ip:{ip}'

PROJECTS_INDEX   = 'projects:index'
NETWORKS_INDEX   = 'networks:index'
GLOBAL_LABELS    = 'labels:global'
GLOBAL_TEMPLATES = 'templates:global'

def new_id() -> str:
    return str(uuid.uuid4())[:8]

# ══════════════════════════════════════════════════════════════════════════════
# Label helpers
# ══════════════════════════════════════════════════════════════════════════════

def parse_labels(form_value: str) -> list:
    if not form_value:
        return []
    seen, result = set(), []
    for l in form_value.split(','):
        l = l.strip()
        if l and l not in seen:
            seen.add(l); result.append(l)
    return result

def global_labels() -> list:
    return sorted(r.smembers(GLOBAL_LABELS))

def project_labels(pid: str) -> list:
    return sorted(r.smembers(project_labels_key(pid)))

def available_labels_for_project(pid: str) -> dict:
    return {'global': global_labels(), 'project': project_labels(pid)}

def add_global_label(label):      r.sadd(GLOBAL_LABELS, label)
def remove_global_label(label):   r.srem(GLOBAL_LABELS, label)
def add_project_label(pid, l):    r.sadd(project_labels_key(pid), l)
def remove_project_label(pid, l): r.srem(project_labels_key(pid), l)

def add_labels_to_network(net_id, labels):
    for label in labels:
        r.sadd(net_labels_key(net_id), label)
        r.sadd(label_nets_key(label), net_id)

def remove_labels_from_network(net_id, labels):
    for label in labels:
        r.srem(net_labels_key(net_id), label)
        r.srem(label_nets_key(label), net_id)

def get_network_labels(net_id: str) -> list:
    return sorted(r.smembers(net_labels_key(net_id)))

def label_scope(label: str, pid: str) -> str:
    if r.sismember(GLOBAL_LABELS, label):                      return 'global'
    if pid and r.sismember(project_labels_key(pid), label):    return 'project'
    return 'unknown'

# ══════════════════════════════════════════════════════════════════════════════
# Subnet template helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_template(tid):
    raw = r.get(template_key(tid))
    return json.loads(raw) if raw else None

def save_template(tmpl):
    r.set(template_key(tmpl['id']), json.dumps(tmpl))
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.sadd(project_templates_key(tmpl['project_id']), tmpl['id'])
    else:
        r.sadd(GLOBAL_TEMPLATES, tmpl['id'])

def delete_template(tid):
    tmpl = get_template(tid)
    if not tmpl: return
    if tmpl.get('scope') == 'project' and tmpl.get('project_id'):
        r.srem(project_templates_key(tmpl['project_id']), tid)
    else:
        r.srem(GLOBAL_TEMPLATES, tid)
    r.delete(template_key(tid))

def global_templates() -> list:
    return sorted(
        [t for t in (get_template(tid) for tid in r.smembers(GLOBAL_TEMPLATES)) if t],
        key=lambda t: t['name'])

def project_templates(pid: str) -> list:
    return sorted(
        [t for t in (get_template(tid) for tid in r.smembers(project_templates_key(pid))) if t],
        key=lambda t: t['name'])

def available_templates_for_project(pid: str) -> dict:
    return {'global': global_templates(),
            'project': project_templates(pid) if pid else []}

def template_scope(tid: str, pid: str) -> str:
    if r.sismember(GLOBAL_TEMPLATES, tid):                        return 'global'
    if pid and r.sismember(project_templates_key(pid), tid):      return 'project'
    return 'unknown'

# ══════════════════════════════════════════════════════════════════════════════
# Rule engine
# ══════════════════════════════════════════════════════════════════════════════

def resolve_template_rules(cidr: str, rules: list) -> list:
    network_obj = ipaddress.ip_network(cidr, strict=False)
    hosts       = list(network_obj.hosts())
    if not hosts:
        return []
    slot_map = {}
    for rule in rules:
        rtype  = rule.get('type')
        role   = rule.get('role', 'reserved')
        status = rule.get('status', 'reserved')
        if rtype == 'from_start':
            idx = int(rule.get('offset', 1)) - 1
            if 0 <= idx < len(hosts):
                slot_map[idx] = {'role': role, 'status': status}
        elif rtype == 'from_end':
            for i in range(int(rule.get('count', 1))):
                idx = len(hosts) - 1 - i
                if idx >= 0:
                    slot_map[idx] = {'role': role, 'status': status}
        elif rtype == 'range':
            frm = int(rule.get('from', 1)) - 1
            to  = int(rule.get('to',  1)) - 1
            for idx in range(frm, min(to + 1, len(hosts))):
                if idx >= 0:
                    slot_map[idx] = {'role': role, 'status': status}
    return [{'ip': str(hosts[idx]), 'role': info['role'], 'status': info['status']}
            for idx, info in sorted(slot_map.items())]


def _validate_rules(rules: list):
    valid_types    = {'from_start', 'from_end', 'range'}
    valid_statuses = {'reserved', 'allocated', 'dhcp'}
    for i, rule in enumerate(rules):
        rtype = rule.get('type')
        if rtype not in valid_types:
            raise ValueError(f'Rule {i}: unknown type "{rtype}"')
        if rtype == 'from_start':
            if not isinstance(rule.get('offset'), int) or rule['offset'] < 1:
                raise ValueError(f'Rule {i}: from_start needs integer offset >= 1')
        elif rtype == 'from_end':
            if not isinstance(rule.get('count'), int) or rule['count'] < 1:
                raise ValueError(f'Rule {i}: from_end needs integer count >= 1')
        elif rtype == 'range':
            frm, to = rule.get('from'), rule.get('to')
            if not isinstance(frm, int) or not isinstance(to, int) or frm < 1 or to < frm:
                raise ValueError(f'Rule {i}: range needs integer from >= 1, to >= from')
        if rule.get('status', 'reserved') not in valid_statuses:
            raise ValueError(f'Rule {i}: unknown status "{rule["status"]}"')

# ══════════════════════════════════════════════════════════════════════════════
# Pending slots
# ══════════════════════════════════════════════════════════════════════════════

def set_pending_slots(net_id: str, tid: str):
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
    net = get_network(net_id)
    if not net: return False
    pending = net.get('pending_slots', [])
    slot = next((s for s in pending if s['ip'] == ip_str), None)
    if not slot: return False
    save_ip({'ip': ip_str, 'hostname': '', 'description': slot['role'],
             'status': slot['status'], 'network_id': net_id,
             'from_template': net.get('template_id', '')})
    net['pending_slots'] = [s for s in pending if s['ip'] != ip_str]
    save_network(net)
    return True

def confirm_all_slots(net_id: str) -> dict:
    net = get_network(net_id)
    if not net: return {'created': 0, 'skipped': 0}
    pending = net.get('pending_slots', [])
    created = skipped = 0
    tid = net.get('template_id', '')
    for slot in pending:
        if get_ip(slot['ip']):
            skipped += 1
        else:
            save_ip({'ip': slot['ip'], 'hostname': '', 'description': slot['role'],
                     'status': slot['status'], 'network_id': net_id, 'from_template': tid})
            created += 1
    net['pending_slots'] = []
    save_network(net)
    return {'created': created, 'skipped': skipped}

def dismiss_slot(net_id: str, ip_str: str) -> bool:
    net = get_network(net_id)
    if not net: return False
    before = len(net.get('pending_slots', []))
    net['pending_slots'] = [s for s in net.get('pending_slots', []) if s['ip'] != ip_str]
    if len(net['pending_slots']) < before:
        save_network(net); return True
    return False

def dismiss_all_slots(net_id: str):
    net = get_network(net_id)
    if not net: return
    net['pending_slots'] = []
    save_network(net)

# ══════════════════════════════════════════════════════════════════════════════
# Core data helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_project(pid):
    raw = r.get(project_key(pid))
    return json.loads(raw) if raw else None

def save_project(proj):
    r.set(project_key(proj['id']), json.dumps(proj))
    r.sadd(PROJECTS_INDEX, proj['id'])

def all_projects():
    return [p for p in (get_project(pid) for pid in r.smembers(PROJECTS_INDEX)) if p]

def get_network(nid):
    raw = r.get(net_key(nid))
    return json.loads(raw) if raw else None

def save_network(net):
    r.set(net_key(net['id']), json.dumps(net))
    r.sadd(NETWORKS_INDEX, net['id'])

def get_ip(ip_str):
    raw = r.get(ip_key(ip_str))
    return json.loads(raw) if raw else None

def save_ip(addr):
    r.set(ip_key(addr['ip']), json.dumps(addr))
    r.sadd(net_ips_key(addr['network_id']), addr['ip'])

def all_networks():
    return [n for n in (get_network(nid) for nid in r.smembers(NETWORKS_INDEX)) if n]

def network_addresses(net_id):
    addrs = [get_ip(ip) for ip in r.smembers(net_ips_key(net_id))]
    return sorted([a for a in addrs if a], key=lambda a: ipaddress.ip_address(a['ip']))

def project_networks(pid):
    return [net_stats(n) for n in (get_network(nid) for nid in r.smembers(project_nets_key(pid))) if n]

def net_stats(net):
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
    result = []
    for nid in r.smembers(project_nets_key(pid)):
        net = get_network(nid)
        if net:
            result.append(ipaddress.ip_network(net['cidr'], strict=False))
    return result

def carve_next_subnet(parent_cidr: str, prefix_len: int, pid: str):
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
    proj = get_project(pid)
    if not proj: return {}
    nets    = project_networks(pid)
    parent  = ipaddress.ip_network(proj['supernet'], strict=False)
    total   = parent.num_addresses
    alloc   = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
    pending = sum(len(n.get('pending_slots', [])) for n in nets)
    return {
        'supernet': proj['supernet'], 'total_ips': total,
        'allocated_ips': alloc, 'free_ips': total - alloc,
        'pending': pending,
        'utilization': round((alloc / total) * 100, 1) if total else 0,
        'subnet_count': len(nets), 'pools': pool_by_label_set(nets),
    }

def global_pool_summary() -> dict:
    projects = all_projects()
    grand_total = grand_alloc = grand_pending = 0
    project_rows = []
    label_groups: dict = {}

    for proj in sorted(projects, key=lambda p: p['name']):
        nets   = project_networks(proj['id'])
        parent = ipaddress.ip_network(proj['supernet'], strict=False)
        proj_total   = parent.num_addresses
        proj_alloc   = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
        proj_pending = sum(len(n.get('pending_slots', [])) for n in nets)
        grand_total   += proj_total
        grand_alloc   += proj_alloc
        grand_pending += proj_pending
        project_rows.append({
            'id': proj['id'], 'name': proj['name'], 'supernet': proj['supernet'],
            'total_ips': proj_total, 'alloc_ips': proj_alloc,
            'free_ips': proj_total - proj_alloc, 'pending': proj_pending,
            'utilization': round((proj_alloc / proj_total) * 100, 1) if proj_total else 0,
            'subnet_count': len(nets),
        })
        for net in nets:
            labels  = frozenset(net.get('labels', []))
            key     = ' | '.join(sorted(labels))
            size    = ipaddress.ip_network(net['cidr']).num_addresses
            used    = net.get('used_count', 0)
            pend    = len(net.get('pending_slots', []))
            if key not in label_groups:
                label_groups[key] = {'label_set': sorted(labels), 'total_ips': 0,
                                     'alloc_ips': 0, 'pending': 0, 'subnet_count': 0}
            label_groups[key]['total_ips']    += size
            label_groups[key]['alloc_ips']    += used
            label_groups[key]['pending']      += pend
            label_groups[key]['subnet_count'] += 1

    for g in label_groups.values():
        g['free_ips']    = g['total_ips'] - g['alloc_ips']
        g['utilization'] = round((g['alloc_ips'] / g['total_ips']) * 100, 1) if g['total_ips'] else 0

    return {
        'total_ips': grand_total, 'alloc_ips': grand_alloc,
        'free_ips': grand_total - grand_alloc, 'pending': grand_pending,
        'utilization': round((grand_alloc / grand_total) * 100, 1) if grand_total else 0,
        'project_count': len(projects),
        'projects': project_rows,
        'label_pool': sorted(label_groups.values(), key=lambda g: g['total_ips'], reverse=True),
    }

def _delete_network_data(nid):
    for ip_str in r.smembers(net_ips_key(nid)):
        r.delete(ip_key(ip_str))
    for label in r.smembers(net_labels_key(nid)):
        r.srem(label_nets_key(label), nid)
    r.delete(net_ips_key(nid))
    r.delete(net_labels_key(nid))
    r.delete(net_key(nid))
    r.srem(NETWORKS_INDEX, nid)

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Dashboard & Projects
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/')
def index():
    projects = sorted(all_projects(), key=lambda p: p['name'])
    for p in projects:
        nets = project_networks(p['id'])
        p['subnet_count'] = len(nets)
        try:
            parent = ipaddress.ip_network(p['supernet'], strict=False)
            alloc  = sum(ipaddress.ip_network(n['cidr']).num_addresses for n in nets)
            p['utilization'] = round((alloc / parent.num_addresses) * 100, 1)
        except Exception:
            p['utilization'] = 0
    return render_template('index.html', projects=projects,
                           global_labels=global_labels(),
                           global_summary=global_pool_summary())


@ipam_bp.route('/overview')
def overview():
    return render_template('overview.html', summary=global_pool_summary())


@ipam_bp.route('/labels', methods=['GET', 'POST'])
def manage_global_labels():
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
def add_project():
    if request.method == 'POST':
        supernet = request.form['supernet'].strip()
        try:
            ipaddress.ip_network(supernet, strict=False)
        except ValueError:
            flash('Invalid supernet CIDR.', 'danger')
            return redirect(url_for('ipam.add_project'))
        proj = {'id': new_id(), 'name': request.form['name'].strip(),
                'supernet': supernet, 'description': request.form.get('description', '')}
        save_project(proj)
        flash(f'Project "{proj["name"]}" created.', 'success')
        return redirect(url_for('ipam.project_detail', pid=proj['id']))
    return render_template('project_form.html')


@ipam_bp.route('/projects/<pid>')
def project_detail(pid):
    proj = get_project(pid)
    if not proj: abort(404)
    nets      = sorted(project_networks(pid), key=lambda n: ipaddress.ip_network(n['cidr']))
    summary   = project_pool_summary(pid)
    labels    = available_labels_for_project(pid)
    templates = available_templates_for_project(pid)
    return render_template('project_detail.html', proj=proj, nets=nets,
                           summary=summary, labels=labels, templates=templates)


@ipam_bp.route('/projects/<pid>/delete', methods=['POST'])
def delete_project(pid):
    proj = get_project(pid)
    if not proj: abort(404)
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
def manage_project_labels(pid):
    proj = get_project(pid)
    if not proj: abort(404)
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
    return render_template('templates_list.html',
                           global_tmpl=global_templates(), project_tmpl=[])


@ipam_bp.route('/templates/add',                    methods=['GET', 'POST'])
@ipam_bp.route('/projects/<pid>/templates/add',     methods=['GET', 'POST'])
def add_template(pid=None):
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
def edit_template(tid):
    tmpl = get_template(tid)
    if not tmpl: abort(404)
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
def delete_template_route(tid):
    tmpl = get_template(tid)
    if not tmpl: abort(404)
    pid = tmpl.get('project_id') or None
    delete_template(tid)
    flash(f'Template "{tmpl["name"]}" deleted.', 'info')
    return redirect(url_for('ipam.manage_project_templates', pid=pid) if pid
                    else url_for('ipam.list_templates'))


@ipam_bp.route('/projects/<pid>/templates')
def manage_project_templates(pid):
    proj = get_project(pid)
    if not proj: abort(404)
    return render_template('project_templates.html', proj=proj,
                           proj_tmpl=project_templates(pid),
                           global_tmpl=global_templates())


@ipam_bp.route('/api/templates/<tid>/preview')
def preview_template(tid):
    cidr = request.args.get('cidr', '').strip()
    if not cidr:
        return jsonify({'error': 'Provide ?cidr=...'}), 400
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return jsonify({'error': 'Invalid CIDR'}), 400
    tmpl = get_template(tid)
    if not tmpl: abort(404)
    resolved = resolve_template_rules(cidr, tmpl['rules'])
    for slot in resolved:
        slot['already_allocated'] = bool(get_ip(slot['ip']))
    return jsonify({'cidr': cidr, 'resolved': resolved})


@ipam_bp.route('/api/templates/preview_inline', methods=['POST'])
def preview_template_inline():
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
def apply_template(net_id):
    net = get_network(net_id)
    if not net: abort(404)
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
def confirm_slot_route(net_id):
    ip_str = request.form.get('ip', '').strip()
    if ip_str:
        confirm_slot(net_id, ip_str)
        flash(f'{ip_str} confirmed and allocated.', 'success')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/confirm_all', methods=['POST'])
def confirm_all_slots_route(net_id):
    result = confirm_all_slots(net_id)
    flash(f'{result["created"]} slot(s) confirmed, {result["skipped"]} skipped.', 'success')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/dismiss',     methods=['POST'])
def dismiss_slot_route(net_id):
    ip_str = request.form.get('ip', '').strip()
    if ip_str:
        dismiss_slot(net_id, ip_str)
        flash(f'{ip_str} slot dismissed.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

@ipam_bp.route('/networks/<net_id>/slots/dismiss_all', methods=['POST'])
def dismiss_all_slots_route(net_id):
    dismiss_all_slots(net_id)
    flash('All pending slots dismissed.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Subnets
# ══════════════════════════════════════════════════════════════════════════════

@ipam_bp.route('/projects/<pid>/subnet/add', methods=['GET', 'POST'])
def add_subnet(pid):
    proj = get_project(pid)
    if not proj: abort(404)
    if request.method == 'POST':
        mode   = request.form.get('mode', 'manual')
        labels = parse_labels(request.form.get('labels', ''))
        name   = request.form.get('name', '').strip()
        tid    = request.form.get('template_id', '').strip() or None
        if mode == 'auto':
            prefix_len = request.form.get('prefix_len', '').strip()
            if not prefix_len or not prefix_len.isdigit():
                flash('Please enter a valid prefix length.', 'danger')
                return redirect(url_for('ipam.add_subnet', pid=pid))
            try:
                cidr = str(carve_next_subnet(proj['supernet'], int(prefix_len), pid))
            except ValueError as e:
                flash(str(e), 'danger')
                return redirect(url_for('ipam.add_subnet', pid=pid))
        else:
            cidr = request.form.get('cidr', '').strip()
            try:
                subnet_obj = ipaddress.ip_network(cidr, strict=False)
                if not subnet_obj.subnet_of(ipaddress.ip_network(proj['supernet'], strict=False)):
                    flash(f'{cidr} is not within {proj["supernet"]}.', 'danger')
                    return redirect(url_for('ipam.add_subnet', pid=pid))
            except (ValueError, TypeError) as e:
                flash(f'Invalid CIDR: {e}', 'danger')
                return redirect(url_for('ipam.add_subnet', pid=pid))
            for used in used_subnets_in_project(pid):
                if ipaddress.ip_network(cidr, strict=False).overlaps(used):
                    flash(f'{cidr} overlaps with {used}.', 'danger')
                    return redirect(url_for('ipam.add_subnet', pid=pid))
        net = {'id': new_id(), 'name': name or cidr, 'cidr': cidr,
               'description': request.form.get('description', ''),
               'vlan': request.form.get('vlan') or '', 'project_id': pid}
        save_network(net)
        r.sadd(project_nets_key(pid), net['id'])
        add_labels_to_network(net['id'], labels)
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
    return render_template('subnet_form.html', proj=proj,
                           labels=available_labels_for_project(pid),
                           templates=available_templates_for_project(pid))


@ipam_bp.route('/projects/<pid>/subnet/bulk', methods=['GET', 'POST'])
def bulk_add_subnets(pid):
    proj = get_project(pid)
    if not proj: abort(404)
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
                cidr = str(carve_next_subnet(proj['supernet'], int(req['prefix_len']), pid))
                net  = {'id': new_id(), 'name': req.get('name', '') or cidr, 'cidr': cidr,
                        'description': req.get('description', ''),
                        'vlan': req.get('vlan', ''), 'project_id': pid}
                save_network(net)
                r.sadd(project_nets_key(pid), net['id'])
                add_labels_to_network(net['id'], labels)
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
def edit_network(net_id):
    net = get_network(net_id)
    if not net: abort(404)
    pid = net.get('project_id')
    if request.method == 'POST':
        net['name']        = request.form.get('name', net['name']).strip()
        net['description'] = request.form.get('description', '')
        net['vlan']        = request.form.get('vlan') or ''
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
def delete_network(net_id):
    net = get_network(net_id)
    if not net: abort(404)
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
    net = get_network(net_id)
    if not net: abort(404)
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
def add_ip(net_id):
    net = get_network(net_id)
    if not net: abort(404)
    if request.method == 'POST':
        ip_str = request.form['ip'].strip()
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            flash('Invalid IP address.', 'danger')
            return redirect(url_for('ipam.add_ip', net_id=net_id))
        if ip_obj not in ipaddress.ip_network(net['cidr'], strict=False):
            flash(f'{ip_str} is not within {net["cidr"]}.', 'danger')
            return redirect(url_for('ipam.add_ip', net_id=net_id))
        if get_ip(ip_str):
            flash(f'{ip_str} is already allocated.', 'warning')
            return redirect(url_for('ipam.add_ip', net_id=net_id))
        net_rec    = get_network(net_id)
        is_pending = any(s['ip'] == ip_str for s in net_rec.get('pending_slots', []))
        save_ip({'ip': ip_str, 'hostname': request.form.get('hostname', ''),
                 'description': request.form.get('description', ''),
                 'status': request.form.get('status', 'allocated'), 'network_id': net_id})
        if is_pending:
            dismiss_slot(net_id, ip_str)
        flash(f'{ip_str} allocated.', 'success')
        return redirect(url_for('ipam.network_detail', net_id=net_id))
    return render_template('ip_form.html', net=net)


@ipam_bp.route('/ip/<path:ip_str>/edit', methods=['GET', 'POST'])
def edit_ip(ip_str):
    addr = get_ip(ip_str)
    if not addr: abort(404)
    net = get_network(addr['network_id'])
    if request.method == 'POST':
        addr['hostname']    = request.form.get('hostname', '')
        addr['description'] = request.form.get('description', '')
        addr['status']      = request.form.get('status', 'allocated')
        r.set(ip_key(ip_str), json.dumps(addr))
        flash(f'{ip_str} updated.', 'success')
        return redirect(url_for('ipam.network_detail', net_id=addr['network_id']))
    return render_template('ip_form.html', net=net, addr=addr)


@ipam_bp.route('/ip/<path:ip_str>/delete', methods=['POST'])
def delete_ip(ip_str):
    addr = get_ip(ip_str)
    if not addr: abort(404)
    net_id = addr['network_id']
    r.delete(ip_key(ip_str))
    r.srem(net_ips_key(net_id), ip_str)
    flash(f'{ip_str} released.', 'info')
    return redirect(url_for('ipam.network_detail', net_id=net_id))


@ipam_bp.route('/api/networks/<net_id>/next')
def next_available(net_id):
    net = get_network(net_id)
    if not net: abort(404)
    used    = r.smembers(net_ips_key(net_id))
    pending = {s['ip'] for s in net.get('pending_slots', [])}
    for host in ipaddress.ip_network(net['cidr'], strict=False).hosts():
        if str(host) not in used and str(host) not in pending:
            return jsonify({'next_available': str(host)})
    return jsonify({'error': 'No available addresses'}), 404

# ══════════════════════════════════════════════════════════════════════════════
# Routes — Pool query & Search
# ══════════════════════════════════════════════════════════════════════════════

def _pool_query(query_labels: list) -> dict:
    if not query_labels: return {}
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
    labels_param = request.args.get('labels', '')
    if not labels_param:
        return jsonify({'error': 'Provide ?labels=LabelA,LabelB'}), 400
    return jsonify(_pool_query([l.strip() for l in labels_param.split(',') if l.strip()]))


@ipam_bp.route('/pool')
def pool_ui():
    labels_param = request.args.get('labels', '')
    query_labels = [l.strip() for l in labels_param.split(',') if l.strip()]
    result = _pool_query(query_labels) if query_labels else None
    return render_template('pool.html', global_labels=global_labels(),
                           result=result, labels_param=labels_param)


@ipam_bp.route('/search')
def search():
    q = request.args.get('q', '').strip().lower()
    results = []
    if q:
        for key in r.scan_iter('ip:*'):
            raw = r.get(key)
            if not raw: continue
            addr = json.loads(raw)
            if (q in addr.get('ip', '').lower() or
                q in addr.get('hostname', '').lower() or
                q in addr.get('description', '').lower()):
                addr['network'] = get_network(addr['network_id'])
                results.append(addr)
        results.sort(key=lambda a: ipaddress.ip_address(a['ip']))
    return render_template('search.html', results=results, q=q)
