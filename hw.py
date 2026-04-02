"""
Hardware Management blueprint.
Covers: connector types, compatibility matrix, hardware templates,
        project BoM, physical instances, rack layout, cable plant,
        and validation engine.
"""

import json

from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, abort)

from db import new_id
from ipam import get_project
from hw_logic import (
    CATEGORIES, FORM_FACTORS, PORT_TYPES, CABLE_TYPES,
    seed_connectors, all_connectors, add_connector, remove_connector,
    full_compat_matrix, set_compat,
    get_hw_template, save_hw_template, delete_hw_template,
    global_hw_templates, project_hw_templates,
    all_hw_templates_for_project,
    get_bom, save_bom, bom_with_templates,
    get_hw_instance, save_hw_instance, delete_hw_instance,
    project_instances, generate_instances_from_bom_line,
    get_rack_slots, place_in_rack, _remove_from_rack, rack_layout_view,
    get_cable, save_cable, delete_cable, project_cables, _used_ports,
    validate_project, load_validation
)

hw_bp = Blueprint('hw', __name__, url_prefix='')


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Global config (connectors + compat matrix)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/admin/hw/connectors', methods=['GET', 'POST'])
def hw_connectors():
    """Admin route to manage physical connector types and their compatibility."""
    seed_connectors()
    if request.method == 'POST':
        action = request.form.get('action')
        name = request.form.get('name', '').strip()
        if action == 'add' and name:
            add_connector(name)
            flash(f'Connector "{name}" added.', 'success')
        elif action == 'delete' and name:
            remove_connector(name)
            flash(f'Connector "{name}" removed.', 'info')
        elif action == 'compat':
            conn_a = request.form.get('conn_a', '')
            conn_b = request.form.get('conn_b', '')
            val = request.form.get('compatible') == '1'
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
    """List all global hardware templates."""
    return render_template('hw/templates_list.html',
                           global_tmpls=global_hw_templates(),
                           categories=CATEGORIES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/add', methods=['GET', 'POST'])
@hw_bp.route('/projects/<pid>/hw/templates/add', methods=['GET', 'POST'])
def add_hw_template(pid=None):
    """Add a new hardware template (either global or project-specific)."""
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
            'id': new_id(),
            'name': name,
            'vendor': request.form.get('vendor', '').strip(),
            'model': request.form.get('model', '').strip(),
            'category': request.form.get('category', 'server'),
            'form_factor': request.form.get('form_factor', '19"'),
            'u_size': int(request.form.get('u_size', 1) or 1),
            'cable_type': request.form.get('cable_type', ''),
            'description': request.form.get('description', ''),
            'ports': ports,
            'scope': 'project' if pid else 'global',
            'project_id': pid or '',
        }
        save_hw_template(tmpl)
        flash(f'Hardware template "{name}" saved.', 'success')
        return redirect(url_for('hw.project_hw_templates_route', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=None, proj=proj,
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/edit', methods=['GET', 'POST'])
def edit_hw_template(tid):
    """Edit an existing hardware template."""
    tmpl = get_hw_template(tid)
    if not tmpl:
        abort(404)
    pid = tmpl.get('project_id') or None
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        ports_raw = request.form.get('ports_json', '[]')
        try:
            ports = json.loads(ports_raw)
        except json.JSONDecodeError as e:
            flash(f'Invalid ports JSON: {e}', 'danger')
            return redirect(request.url)
        tmpl['name'] = request.form.get('name', tmpl['name']).strip()
        tmpl['vendor'] = request.form.get('vendor', '').strip()
        tmpl['model'] = request.form.get('model', '').strip()
        tmpl['category'] = request.form.get('category', tmpl['category'])
        tmpl['form_factor'] = request.form.get('form_factor', tmpl['form_factor'])
        tmpl['u_size'] = int(request.form.get('u_size', 1) or 1)
        tmpl['cable_type'] = request.form.get('cable_type', '')
        tmpl['description'] = request.form.get('description', '')
        tmpl['ports'] = ports
        save_hw_template(tmpl)
        flash(f'Template "{tmpl["name"]}" updated.', 'success')
        return redirect(url_for('hw.project_hw_templates_route', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=tmpl, proj=proj,
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/delete', methods=['POST'])
def delete_hw_template_route(tid):
    """Delete a hardware template."""
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
    """List hardware templates available to a specific project."""
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
    """Manage the Bill of Materials (BoM) for a project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
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
                item['id'] = new_id()
        save_bom(pid, bom)
        flash('Bill of Materials saved.', 'success')
        return redirect(url_for('hw.project_bom', pid=pid))
    bom = bom_with_templates(pid)
    templates = all_hw_templates_for_project(pid)
    return render_template('hw/bom.html', proj=proj, bom=bom,
                           templates=templates, categories=CATEGORIES)


@hw_bp.route('/projects/<pid>/bom/generate', methods=['POST'])
def generate_from_bom(pid):
    """Generate hardware instances from a single BoM line."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    item_id = request.form.get('item_id', '').strip()
    bom = get_bom(pid)
    item = next((i for i in bom if i['id'] == item_id), None)
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
    proj = get_project(pid)
    if not proj:
        abort(404)
    bom = get_bom(pid)
    total = 0
    for item in bom:
        try:
            created = generate_instances_from_bom_line(pid, item)
            total += len(created)
        except ValueError:
            pass
    flash(f'{total} instance(s) generated from full BoM.', 'success')
    return redirect(url_for('hw.project_inventory', pid=pid))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Inventory (instances)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/inventory')
def project_inventory(pid):
    """List physical hardware instances in project inventory."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    cat = request.args.get('category', '')
    instances = project_instances(pid, category=cat or None)
    racks = project_instances(pid, category='rack')
    return render_template('hw/inventory.html', proj=proj,
                           instances=instances, racks=racks,
                           categories=CATEGORIES, selected_cat=cat)


@hw_bp.route('/projects/<pid>/hw/instances/add', methods=['GET', 'POST'])
def add_hw_instance(pid):
    """Add a new physical hardware instance manually."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        tid = request.form.get('template_id', '').strip()
        if not tid:
            flash('Select a template.', 'danger')
            return redirect(request.url)
        inst = {
            'id': new_id(),
            'template_id': tid,
            'project_id': pid,
            'asset_tag': request.form.get('asset_tag', '').strip(),
            'serial': request.form.get('serial', '').strip(),
            'status': request.form.get('status', 'in-stock'),
            'location': {},
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
    """Edit an existing hardware instance."""
    proj = get_project(pid)
    inst = get_hw_instance(iid)
    if not proj or not inst:
        abort(404)
    if request.method == 'POST':
        inst['asset_tag'] = request.form.get('asset_tag', '').strip()
        inst['serial'] = request.form.get('serial', '').strip()
        inst['status'] = request.form.get('status', 'in-stock')
        save_hw_instance(inst)
        flash('Instance updated.', 'success')
        return redirect(url_for('hw.project_inventory', pid=pid))
    templates = all_hw_templates_for_project(pid)
    return render_template('hw/instance_form.html', proj=proj,
                           inst=inst, templates=templates)


@hw_bp.route('/projects/<pid>/hw/instances/<iid>/delete', methods=['POST'])
def delete_hw_instance_route(pid, iid):
    """Delete a hardware instance."""
    delete_hw_instance(iid)
    flash('Instance deleted.', 'info')
    return redirect(url_for('hw.project_inventory', pid=pid))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Rack layout
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/racks')
def rack_list(pid):
    """List all racks in a project with utilization stats."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    racks = project_instances(pid, category='rack')
    for rack in racks:
        slots = get_rack_slots(rack['id'])
        rack_u = int(rack['template']['u_size']) if rack.get('template') else 42
        used_u = 0
        for slot in slots:
            inst = get_hw_instance(slot['instance_id'])
            if inst:
                t = get_hw_template(inst['template_id'])
                if t:
                    used_u += int(t.get('u_size', 1))
        rack['slot_count'] = len(slots)
        rack['used_u'] = used_u
        rack['free_u'] = rack_u - used_u
        rack['utilization'] = round((used_u / rack_u) * 100) if rack_u else 0
    return render_template('hw/rack_list.html', proj=proj, racks=racks)


@hw_bp.route('/projects/<pid>/hw/racks/<rack_iid>')
def rack_detail(pid, rack_iid):
    """Visual representation of a specific rack and its contents."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    view = rack_layout_view(rack_iid)
    if not view:
        abort(404)
    unplaced = [i for i in project_instances(pid)
                if i['id'] != rack_iid
                and not i.get('location', {}).get('rack_id')
                and i.get('template', {}).get('category') not in ('rack', 'cable')]
    issues = load_validation(pid)
    rack_issues = [i for i in issues
                   if i.get('context', {}).get('rack') == rack_iid]
    return render_template('hw/rack_detail.html', proj=proj,
                           view=view, unplaced=unplaced,
                           issues=rack_issues)


@hw_bp.route('/projects/<pid>/hw/racks/<rack_iid>/place', methods=['POST'])
def place_device(pid, rack_iid):
    """Place a device in a rack at a specific U position."""
    iid = request.form.get('instance_id', '').strip()
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
    """Remove a device from its rack position."""
    iid = request.form.get('instance_id', '').strip()
    inst = get_hw_instance(iid)
    if inst:
        _remove_from_rack(rack_iid, iid)
        inst['location'] = {}
        save_hw_instance(inst)
        flash('Device removed from rack.', 'info')
    return redirect(url_for('hw.rack_detail', pid=pid, rack_iid=rack_iid))


@hw_bp.route('/api/projects/<pid>/hw/racks/<rack_iid>/place', methods=['POST'])
def api_place_device(_pid, rack_iid):
    """JSON API for drag-and-drop placement."""
    data = request.get_json(force=True) or {}
    iid = data.get('instance_id', '')
    u_pos = int(data.get('u_pos', 1))
    issues = place_in_rack(rack_iid, iid, u_pos)
    return jsonify({'issues': issues,
                    'ok': not any(i['severity'] == 'error' for i in issues)})


@hw_bp.route('/projects/<pid>/hw/rack-table', methods=['GET', 'POST'])
def rack_table(pid):
    """Table-based bulk placement — useful for 60+ rack deployments."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    racks = project_instances(pid, category='rack')
    if request.method == 'POST':
        placements = request.get_json(force=True) or []
        results = []
        for p in placements:
            issues = place_in_rack(p['rack_id'], p['instance_id'], int(p['u_pos']))
            results.append({'rack': p['rack_id'], 'device': p['instance_id'],
                            'issues': issues,
                            'ok': not any(i['severity'] == 'error' for i in issues)})
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
    """List all cables in a project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    cables = project_cables(pid)
    issues = load_validation(pid)
    cable_issue_ids = {i.get('context', {}).get('cable') for i in issues}
    return render_template('hw/cable_list.html', proj=proj,
                           cables=cables, cable_issue_ids=cable_issue_ids)


@hw_bp.route('/projects/<pid>/hw/cables/add', methods=['GET', 'POST'])
def add_cable(pid):
    """Add a new physical cable between two ports."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    if request.method == 'POST':
        tid = request.form.get('template_id', '').strip() or None
        end_a = {
            'instance_id': request.form.get('end_a_instance', '').strip(),
            'port_id': request.form.get('end_a_port', '').strip(),
        }
        end_b = {
            'instance_id': request.form.get('end_b_instance', '').strip(),
            'port_id': request.form.get('end_b_port', '').strip(),
        }
        cable = {
            'id': new_id(),
            'template_id': tid,
            'project_id': pid,
            'asset_tag': request.form.get('asset_tag', '').strip(),
            'label': request.form.get('label', '').strip(),
            'length_m': request.form.get('length_m', ''),
            'end_a': end_a,
            'end_b': end_b,
            'breakout': request.form.get('breakout') == '1',
            'breakout_fan_out': int(request.form.get('breakout_fan_out', 1) or 1),
        }
        save_cable(cable)
        flash(f'Cable {cable["asset_tag"] or cable["id"]} added.', 'success')
        return redirect(url_for('hw.cable_list', pid=pid))
    cable_tmpls = all_hw_templates_for_project(pid, category='cable')
    instances = [i for i in project_instances(pid)
                 if i.get('template', {}).get('category') != 'cable']
    return render_template('hw/cable_form.html', proj=proj,
                           cable=None, cable_tmpls=cable_tmpls,
                           instances=instances)


@hw_bp.route('/projects/<pid>/hw/cables/<cid>/edit', methods=['GET', 'POST'])
def edit_cable(pid, cid):
    """Edit an existing cable or its connectivity."""
    proj = get_project(pid)
    cable = get_cable(cid)
    if not proj or not cable:
        abort(404)
    if request.method == 'POST':
        cable['asset_tag'] = request.form.get('asset_tag', '').strip()
        cable['label'] = request.form.get('label', '').strip()
        cable['length_m'] = request.form.get('length_m', '')
        cable['template_id'] = request.form.get('template_id', '') or None
        cable['end_a'] = {
            'instance_id': request.form.get('end_a_instance', '').strip(),
            'port_id': request.form.get('end_a_port', '').strip(),
        }
        cable['end_b'] = {
            'instance_id': request.form.get('end_b_instance', '').strip(),
            'port_id': request.form.get('end_b_port', '').strip(),
        }
        cable['breakout'] = request.form.get('breakout') == '1'
        cable['breakout_fan_out'] = int(request.form.get('breakout_fan_out', 1) or 1)
        save_cable(cable)
        flash('Cable updated.', 'success')
        return redirect(url_for('hw.cable_list', pid=pid))
    cable_tmpls = all_hw_templates_for_project(pid, category='cable')
    instances = [i for i in project_instances(pid)
                 if i.get('template', {}).get('category') != 'cable']
    return render_template('hw/cable_form.html', proj=proj,
                           cable=cable, cable_tmpls=cable_tmpls,
                           instances=instances)


@hw_bp.route('/projects/<pid>/hw/cables/<cid>/delete', methods=['POST'])
def delete_cable_route(pid, cid):
    """Delete a cable."""
    delete_cable(cid)
    flash('Cable deleted.', 'info')
    return redirect(url_for('hw.cable_list', pid=pid))


@hw_bp.route('/api/projects/<pid>/hw/instance-ports/<iid>')
def api_instance_ports(pid, iid):
    """Return ports for a given instance — used by cable form JS."""
    inst = get_hw_instance(iid)
    if not inst:
        return jsonify([])
    tmpl = get_hw_template(inst['template_id'])
    if not tmpl:
        return jsonify([])
    used = _used_ports(pid)
    ports = []
    for p in tmpl.get('ports', []):
        key = (iid, p['id'])
        port = dict(p)
        port['in_use'] = key in used
        port['cable_tag'] = get_cable(used[key])['asset_tag'] if key in used else None
        ports.append(port)
    return jsonify(ports)


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Validation
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/validate')
def hw_validate(pid):
    """Perform and display validation checks for project hardware."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    issues = validate_project(pid)
    errors = [i for i in issues if i['severity'] == 'error']
    warnings = [i for i in issues if i['severity'] == 'warning']
    return render_template('hw/validation.html', proj=proj,
                           issues=issues, errors=errors, warnings=warnings)


@hw_bp.route('/api/projects/<pid>/hw/validate')
def api_hw_validate(pid):
    """JSON API for hardware validation status."""
    issues = validate_project(pid)
    return jsonify({
        'total': len(issues),
        'errors': sum(1 for i in issues if i['severity'] == 'error'),
        'warnings': sum(1 for i in issues if i['severity'] == 'warning'),
        'issues': issues,
    })
