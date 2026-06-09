# pylint: disable=duplicate-code
"""
Hardware Management blueprint.
Covers: connector types, compatibility matrix, hardware templates,
        project BoM, physical instances, rack layout, cable plant,
        and validation engine.
"""

import json

from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, abort, current_app)
from db import new_id
from ipam import get_project
from ne import project_sites, get_site
from auth import editor_required
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
    validate_project, load_validation, trace_cable_path,
    hw_instance_bindings, port_attached_subnets,
    render_pattern, cable_pattern_context, next_cable_seq,
)

hw_bp = Blueprint('hw', __name__, url_prefix='')


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Global config (connectors + compat matrix)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/admin/hw/connectors', methods=['GET', 'POST'])
@editor_required
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
                if conn_a == conn_b and not val:
                    flash(f'Cannot disable self-compatibility ({conn_a} ↔ {conn_a}).',
                          'warning')
                else:
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
@editor_required
def add_hw_template(pid=None):
    """Add a new hardware template (either global or project-specific)."""
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        ports_raw = request.form.get('ports_json', '[]')
        errors = {}
        ports = []
        if not name:
            errors['name'] = 'Name is required.'
        try:
            ports = json.loads(ports_raw)
        except json.JSONDecodeError as e:
            errors['ports_json'] = f'Invalid ports JSON: {e}'
        if errors:
            return render_template('hw/template_form.html',
                                   tmpl=None, proj=proj, errors=errors,
                                   form_values=request.form,
                                   categories=CATEGORIES, form_factors=FORM_FACTORS,
                                   port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                                   connectors=all_connectors())
        tmpl = {
            'id': new_id(),
            'name': name,
            'vendor': request.form.get('vendor', '').strip(),
            'model': request.form.get('model', '').strip(),
            'category': request.form.get('category', 'server'),
            'form_factor': request.form.get('form_factor', '19"'),
            'u_size': int(request.form.get('u_size', 1) or 1),
            'power_w': float(request.form.get('power_w', 0) or 0),
            'weight_kg': float(request.form.get('weight_kg', 0) or 0),
            'max_power_w': float(request.form.get('max_power_w', 0) or 0),
            'max_weight_kg': float(request.form.get('max_weight_kg', 0) or 0),
            'cable_type': request.form.get('cable_type', ''),
            'description': request.form.get('description', ''),
            'ports': ports,
            'scope': 'project' if pid else 'global',
            'project_id': pid or '',
            'connector_a': request.form.get('connector_a', '').strip(),
            'connector_b': request.form.get('connector_b', '').strip(),
            'breakout': request.form.get('breakout') == '1',
            'breakout_fan_out': int(request.form.get('breakout_fan_out', 1) or 1),
            'asset_tag_pattern': request.form.get('asset_tag_pattern', '').strip(),
            'label_pattern': request.form.get('label_pattern', '').strip(),
        }
        try:
            save_hw_template(tmpl)
        except ValueError as e:
            return render_template('hw/template_form.html',
                                   tmpl=tmpl, proj=proj, errors={'__all__': str(e)},
                                   form_values=request.form,
                                   categories=CATEGORIES, form_factors=FORM_FACTORS,
                                   port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                                   connectors=all_connectors())
        flash(f'Hardware template "{name}" saved.', 'success')
        return redirect(url_for('hw.project_hw_templates_route', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=None, proj=proj, errors={}, form_values={},
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_hw_template(tid):
    """Edit an existing hardware template."""
    tmpl = get_hw_template(tid)
    if not tmpl:
        abort(404)
    pid = tmpl.get('project_id') or None
    proj = get_project(pid) if pid else None
    if request.method == 'POST':
        ports_raw = request.form.get('ports_json', '[]')
        errors = {}
        ports = []
        try:
            ports = json.loads(ports_raw)
        except json.JSONDecodeError as e:
            errors['ports_json'] = f'Invalid ports JSON: {e}'
        # Mutate tmpl with form values so re-render shows submitted data
        tmpl['name'] = request.form.get('name', tmpl['name']).strip()
        tmpl['vendor'] = request.form.get('vendor', '').strip()
        tmpl['model'] = request.form.get('model', '').strip()
        tmpl['category'] = request.form.get('category', tmpl['category'])
        tmpl['form_factor'] = request.form.get('form_factor', tmpl['form_factor'])
        tmpl['u_size'] = int(request.form.get('u_size', 1) or 1)
        tmpl['power_w'] = float(request.form.get('power_w', 0) or 0)
        tmpl['weight_kg'] = float(request.form.get('weight_kg', 0) or 0)
        tmpl['max_power_w'] = float(request.form.get('max_power_w', 0) or 0)
        tmpl['max_weight_kg'] = float(request.form.get('max_weight_kg', 0) or 0)
        tmpl['cable_type'] = request.form.get('cable_type', '')
        tmpl['description'] = request.form.get('description', '')
        tmpl['ports'] = ports
        tmpl['connector_a'] = request.form.get('connector_a', '').strip()
        tmpl['connector_b'] = request.form.get('connector_b', '').strip()
        tmpl['breakout'] = request.form.get('breakout') == '1'
        tmpl['breakout_fan_out'] = int(request.form.get('breakout_fan_out', 1) or 1)
        tmpl['asset_tag_pattern'] = request.form.get('asset_tag_pattern', '').strip()
        tmpl['label_pattern'] = request.form.get('label_pattern', '').strip()
        if errors:
            return render_template('hw/template_form.html',
                                   tmpl=tmpl, proj=proj, errors=errors,
                                   form_values=request.form,
                                   categories=CATEGORIES, form_factors=FORM_FACTORS,
                                   port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                                   connectors=all_connectors())
        try:
            save_hw_template(tmpl)
        except ValueError as e:
            return render_template('hw/template_form.html',
                                   tmpl=tmpl, proj=proj, errors={'__all__': str(e)},
                                   form_values=request.form,
                                   categories=CATEGORIES, form_factors=FORM_FACTORS,
                                   port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                                   connectors=all_connectors())
        flash(f'Template "{tmpl["name"]}" updated.', 'success')
        return redirect(url_for('hw.project_hw_templates_route', pid=pid) if pid
                        else url_for('hw.hw_templates_list'))
    return render_template('hw/template_form.html',
                           tmpl=tmpl, proj=proj, errors={}, form_values={},
                           categories=CATEGORIES, form_factors=FORM_FACTORS,
                           port_types=PORT_TYPES, cable_types=CABLE_TYPES,
                           connectors=all_connectors())


@hw_bp.route('/hw/templates/<tid>/delete', methods=['POST'])
@editor_required
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
@editor_required
def project_bom(pid):
    """Manage the Bill of Materials (BoM) for a project."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    bom = bom_with_templates(pid)
    templates = all_hw_templates_for_project(pid)
    sites = project_sites(pid)
    if request.method == 'POST':
        bom_raw = request.form.get('bom_json', '[]')
        try:
            new_bom = json.loads(bom_raw)
        except json.JSONDecodeError as e:
            return render_template('hw/bom.html', proj=proj, bom=bom,
                                   templates=templates, sites=sites,
                                   categories=CATEGORIES,
                                   errors={'bom_json': f'Invalid BoM JSON: {e}'},
                                   form_values=request.form)
        for item in new_bom:
            if not item.get('id'):
                item['id'] = new_id()
        save_bom(pid, new_bom)
        flash('Bill of Materials saved.', 'success')
        return redirect(url_for('hw.project_bom', pid=pid))
    return render_template('hw/bom.html', proj=proj, bom=bom,
                           templates=templates, sites=sites,
                           categories=CATEGORIES,
                           errors={}, form_values={})


@hw_bp.route('/projects/<pid>/bom/generate', methods=['POST'])
@editor_required
def generate_from_bom(pid):
    """Generate hardware instances from a single BoM line."""
    from flask_login import current_user
    import generate_commits as gc

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
        user = getattr(current_user, 'id', 'system')
        cid, created = gc.generate_with_commit(
            pid=pid, user=user, kind='bom-line',
            trigger={'bom_line_id': item_id},
            work_fn=lambda: generate_instances_from_bom_line(pid, item),
        )
        flash(f'{len(created)} instance(s) created from BoM line (commit {cid}).', 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('hw.project_inventory', pid=pid))


@hw_bp.route('/projects/<pid>/bom/generate-all', methods=['POST'])
@editor_required
def generate_all_from_bom(pid):
    """Generate instances for all BoM lines. JSON POST triggers async job."""
    from flask_login import current_user
    import generate_commits as gc

    proj = get_project(pid)
    if not proj:
        abort(404)

    user = getattr(current_user, 'id', 'system')

    if request.is_json:
        from core.jobs import create_job, run_job, update_job
        bom_snap = get_bom(pid)
        job_id   = create_job()
        app      = current_app._get_current_object()  # pylint: disable=protected-access

        def _work(job_id, pid, bom_snap, user):
            total_items = len(bom_snap)
            all_created = []
            for i, item in enumerate(bom_snap):
                update_job(job_id, i, total_items, message=f'Processing item {i + 1}/{total_items}…')
                try:
                    all_created.extend(generate_instances_from_bom_line(pid, item))
                except ValueError:
                    pass
            # One commit for all lines
            try:
                gc.generate_with_commit(
                    pid=pid, user=user, kind='bom-all', trigger={},
                    work_fn=lambda: all_created,
                )
            except Exception:
                pass
            update_job(job_id, total_items, total_items,
                       message=f'{len(all_created)} instance(s) generated', status='done',
                       result={'created': len(all_created)})

        run_job(app, job_id, _work, pid, bom_snap, user)
        return jsonify({'job_id': job_id})

    # Sync path (form submit / tests)
    bom = get_bom(pid)
    all_created = []
    for item in bom:
        try:
            all_created.extend(generate_instances_from_bom_line(pid, item))
        except ValueError:
            pass
    if all_created:
        try:
            cid, _ = gc.generate_with_commit(
                pid=pid, user=user, kind='bom-all', trigger={},
                work_fn=lambda: all_created,
            )
            flash(f'{len(all_created)} instance(s) generated from full BoM (commit {cid}).', 'success')
        except Exception:
            flash(f'{len(all_created)} instance(s) generated from full BoM.', 'success')
    else:
        flash('No instances generated (BoM may be empty or all slots filled).', 'info')
    return redirect(url_for('hw.project_inventory', pid=pid))


# ── Commit log routes ──────────────────────────────────────────────────────────

@hw_bp.route('/projects/<pid>/commits')
def project_commit_log(pid):
    """List all generate commits for a project."""
    import generate_commits as gc
    proj = get_project(pid)
    if not proj:
        abort(404)
    commits = gc.project_commits(pid, limit=50)
    return render_template('hw/commit_log.html', proj=proj, commits=commits)


@hw_bp.route('/projects/<pid>/commits/preview')
def project_commits_preview(pid):
    """Show pending changes (diff current state vs HEAD) before committing."""
    import generate_commits as gc
    proj = get_project(pid)
    if not proj:
        abort(404)
    pending = gc.compute_pending(pid)
    head    = gc.get_project_head(pid)
    head_commit = gc.get_generate_commit(head) if head else None
    return render_template('hw/commits_preview.html', proj=proj,
                           pending=pending, head_commit=head_commit)


@hw_bp.route('/projects/<pid>/commits/<cid>')
def commit_detail(pid, cid):
    """Show the detail of one generate commit."""
    import generate_commits as gc
    proj = get_project(pid)
    if not proj:
        abort(404)
    commit = gc.get_generate_commit(cid)
    if not commit or commit.get('project_id') != pid:
        abort(404)
    return render_template('hw/commit_detail.html', proj=proj, commit=commit)


@hw_bp.route('/projects/<pid>/commits/init', methods=['POST'])
@editor_required
def init_project_commit(pid):
    """Create the initial commit for a project (migration / first-time setup)."""
    import generate_commits as gc
    proj = get_project(pid)
    if not proj:
        abort(404)
    cid = gc.create_initial_commit(pid)
    flash(f'Initial commit created: {cid}', 'success')
    return redirect(url_for('hw.project_commit_log', pid=pid))


# ══════════════════════════════════════════════════════════════════════════════
# Routes — Inventory (instances)
# ══════════════════════════════════════════════════════════════════════════════

@hw_bp.route('/projects/<pid>/hw/inventory/export')
def export_hw_inventory(pid):
    """Export project hardware inventory to a CSV file."""
    import csv
    import io
    from flask import make_response

    proj = get_project(pid)
    if not proj:
        abort(404)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Asset Tag', 'Serial', 'Template Name', 'Category', 'Status', 'Site', 'Rack/Location'])

    instances = project_instances(pid)
    all_racks = project_instances(pid, category='rack')
    rack_map = {r['id']: r['asset_tag'] for r in all_racks}
    site_map = {s['id']: s['name'] for s in project_sites(pid)}

    for inst in instances:
        tmpl = inst.get('template')
        location = ''
        if inst.get('location', {}).get('rack_id'):
            rack_tag = rack_map.get(inst['location']['rack_id'], '?')
            location = f"Rack {rack_tag} U{inst['location']['u_pos']}"

        writer.writerow([
            inst.get('asset_tag') or inst['id'],
            inst.get('serial', ''),
            tmpl['name'] if tmpl else '?',
            tmpl['category'] if tmpl else '?',
            inst.get('status', ''),
            site_map.get(inst.get('site_id'), ''),
            location
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename=hw_inventory_{pid}.csv'
    response.headers['Content-type'] = 'text/csv'
    return response


@hw_bp.route('/projects/<pid>/hw/inventory')
def project_inventory(pid):
    """List physical hardware instances in project inventory."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    cat = request.args.get('category', '')
    instances = project_instances(pid, category=cat or None)
    racks = project_instances(pid, category='rack')
    sites = project_sites(pid)
    site_map = {s['id']: s['name'] for s in sites}

    # Totals by hardware type (template), reflecting the active category filter.
    type_totals = {}
    for inst in instances:
        tmpl = inst.get('template')
        name = tmpl['name'] if tmpl else 'Missing template'
        type_totals[name] = type_totals.get(name, 0) + 1
    type_totals = sorted(type_totals.items())

    return render_template('hw/inventory.html', proj=proj,
                           instances=instances, racks=racks,
                           categories=CATEGORIES, selected_cat=cat,
                           type_totals=type_totals, total_count=len(instances),
                           site_map=site_map)


@hw_bp.route('/projects/<pid>/hw/instances/<iid>')
def hw_instance_detail(pid, iid):
    """Hardware instance detail: port table with bound/cabled/free states."""
    from ne import get_ne_instance
    proj = get_project(pid)
    if not proj:
        abort(404)
    inst = get_hw_instance(iid)
    if not inst or inst.get('project_id') != pid:
        abort(404)
    tmpl = get_hw_template(inst.get('template_id', ''))
    used_cables = _used_ports(pid)
    bound_info  = hw_instance_bindings(iid)
    bound_map   = {b['port_id']: b for b in bound_info}

    # Build per-port rows with binding + cable + subnet info
    port_rows = []
    for port in (tmpl.get('ports', []) if tmpl else []):
        count = int(port.get('count', 1))
        for n in range(count):
            pid_ = port['id']
            pname = port['name'] if count == 1 else f"{port['name']}-{n}"
            bound = bound_map.get(pid_)
            cabled = (iid, pid_) in used_cables
            ne_inst = get_ne_instance(bound['ne_instance_id']) if bound else None
            # Cable far-end info
            cable_far = None
            if cabled:
                cable_id = used_cables.get((iid, pid_))
                cable = get_cable(cable_id) if cable_id else None
                if cable:
                    end_a, end_b = cable.get('end_a', {}), cable.get('end_b', {})
                    if end_a.get('instance_id') == iid and end_a.get('port_id') == pid_:
                        far_iid, far_pid = end_b.get('instance_id'), end_b.get('port_id')
                    else:
                        far_iid, far_pid = end_a.get('instance_id'), end_a.get('port_id')
                    if far_iid:
                        far_hw = get_hw_instance(far_iid)
                        cable_far = {
                            'asset_tag': far_hw.get('asset_tag', far_iid) if far_hw else far_iid,
                            'port_id':   far_pid or '',
                            'cable_id':  cable_id,
                        }
            # Subnet attachments
            subnets = port_attached_subnets(iid, pid_, proj['id'])
            port_ip = inst.get('port_overrides', {}).get(pid_, {}).get('ip')
            port_rows.append({
                'port_id':      pid_,
                'name':         pname,
                'port_type':    port.get('port_type', ''),
                'connector':    port.get('connector', ''),
                'speed_gbps':   port.get('speed_gbps', 0),
                'labels':       port.get('labels', []),
                'requires_ip':  port.get('requires_ip', False),
                'is_bound':     bool(bound),
                'ne_name':      ne_inst.get('name', '') if ne_inst else '',
                'ne_id':        bound.get('ne_instance_id', '') if bound else '',
                'iface_id':     bound.get('iface_id', '') if bound else '',
                'bind_mode':    bound.get('bind_mode', '') if bound else '',
                'is_cabled':    cabled,
                'cable_far':    cable_far,
                'subnets':      subnets,
                'port_ip':      port_ip,
            })

    return render_template('hw/instance_detail.html', proj=proj, inst=inst,
                           tmpl=tmpl, port_rows=port_rows)


@hw_bp.route('/projects/<pid>/hw/instances/add', methods=['GET', 'POST'])
@editor_required
def add_hw_instance(pid):
    """Add a new physical hardware instance manually."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    templates = all_hw_templates_for_project(pid)
    sites = project_sites(pid)
    site_ids = {s['id'] for s in sites}
    if request.method == 'POST':
        tid = request.form.get('template_id', '').strip()
        site_id = request.form.get('site_id', '').strip()
        errors = {}
        if not tid:
            errors['template_id'] = 'Select a template.'
        if not site_id:
            errors['site_id'] = 'Select a site.'
        elif site_id not in site_ids:
            errors['site_id'] = 'Unknown site for this project.'
        if errors:
            return render_template('hw/instance_form.html', proj=proj,
                                   inst=None, templates=templates, sites=sites,
                                   errors=errors, form_values=request.form)
        inst = {
            'id': new_id(),
            'template_id': tid,
            'project_id': pid,
            'asset_tag': request.form.get('asset_tag', '').strip(),
            'serial': request.form.get('serial', '').strip(),
            'status': request.form.get('status', 'in-stock'),
            'site_id': site_id,
            'location': {},
            'port_overrides': {},
        }
        save_hw_instance(inst)
        flash(f'Instance {inst["asset_tag"] or inst["id"]} added.', 'success')
        return redirect(url_for('hw.project_inventory', pid=pid))
    return render_template('hw/instance_form.html', proj=proj,
                           inst=None, templates=templates, sites=sites,
                           errors={}, form_values={})


@hw_bp.route('/projects/<pid>/hw/instances/<iid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_hw_instance(pid, iid):
    """Edit an existing hardware instance."""
    proj = get_project(pid)
    inst = get_hw_instance(iid)
    if not proj or not inst:
        abort(404)
    templates = all_hw_templates_for_project(pid)
    sites = project_sites(pid)
    site_ids = {s['id'] for s in sites}
    if request.method == 'POST':
        site_id = request.form.get('site_id', '').strip()
        errors = {}
        if not site_id:
            errors['site_id'] = 'Select a site.'
        elif site_id not in site_ids:
            errors['site_id'] = 'Unknown site for this project.'
        if errors:
            return render_template('hw/instance_form.html', proj=proj,
                                   inst=inst, templates=templates, sites=sites,
                                   errors=errors, form_values=request.form)
        inst['asset_tag'] = request.form.get('asset_tag', '').strip()
        inst['serial'] = request.form.get('serial', '').strip()
        inst['status'] = request.form.get('status', 'in-stock')
        inst['site_id'] = site_id
        merge_mode = request.form.get('merge_mode', '').strip()
        if merge_mode in ('3way', 'frozen', 'overwrite'):
            inst['merge_mode'] = merge_mode
        elif merge_mode == '':
            inst['merge_mode'] = None
        save_hw_instance(inst)
        flash('Instance updated.', 'success')
        return redirect(url_for('hw.project_inventory', pid=pid))
    return render_template('hw/instance_form.html', proj=proj,
                           inst=inst, templates=templates, sites=sites,
                           errors={}, form_values={})


@hw_bp.route('/projects/<pid>/hw/instances/<iid>/delete', methods=['POST'])
@editor_required
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
@editor_required
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
@editor_required
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
@editor_required
def api_place_device(pid, rack_iid):  # pylint: disable=unused-argument
    """JSON API for drag-and-drop placement."""
    data = request.get_json(force=True) or {}
    iid = data.get('instance_id', '')
    u_pos = int(data.get('u_pos', 1))
    issues = place_in_rack(rack_iid, iid, u_pos)
    return jsonify({'issues': issues,
                    'ok': not any(i['severity'] == 'error' for i in issues)})


@hw_bp.route('/projects/<pid>/hw/rack-table', methods=['GET', 'POST'])
@editor_required
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

def _apply_cable_patterns(cable: dict, pid: str) -> None:
    """Server-side fallback: fill blank asset_tag / label from template patterns."""
    tmpl = get_hw_template(cable.get('template_id')) if cable.get('template_id') else None
    if not tmpl:
        return
    if not cable.get('asset_tag') and tmpl.get('asset_tag_pattern'):
        ctx = cable_pattern_context(cable, defer_seq=True)
        if '{seq}' in tmpl['asset_tag_pattern']:
            ctx['seq'] = next_cable_seq(pid, tmpl['asset_tag_pattern'])
        cable['asset_tag'] = render_pattern(tmpl['asset_tag_pattern'], ctx)
    if not cable.get('label') and tmpl.get('label_pattern'):
        ctx = cable_pattern_context(cable, defer_seq=True)
        if '{seq}' in tmpl['label_pattern']:
            ctx['seq'] = next_cable_seq(pid, tmpl['label_pattern'])
        cable['label'] = render_pattern(tmpl['label_pattern'], ctx)


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


@hw_bp.route('/projects/<pid>/hw/cables/<cid>/trace')
def cable_trace(pid, cid):
    """Trace the end-to-end path of a cable."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    path = trace_cable_path(cid)
    if not path:
        abort(404)
    return render_template('hw/trace.html', proj=proj, path=path, cid=cid)


@hw_bp.route('/projects/<pid>/hw/cables/add', methods=['GET', 'POST'])
@editor_required
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
        _apply_cable_patterns(cable, pid)
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
@editor_required
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
        _apply_cable_patterns(cable, pid)
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
@editor_required
def delete_cable_route(pid, cid):
    """Delete a cable."""
    delete_cable(cid)
    flash('Cable deleted.', 'info')
    return redirect(url_for('hw.cable_list', pid=pid))


@hw_bp.route('/api/hw/templates/<tid>')
def api_hw_template(tid):
    """Return a single HW template as JSON (used by the cable form JS)."""
    tmpl = get_hw_template(tid)
    if not tmpl:
        return jsonify({'error': 'not found'}), 404
    return jsonify(tmpl)


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
