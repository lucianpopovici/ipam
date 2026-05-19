"""
Services blueprint — customer-scoped service definitions.

Routes:
  /customers/<cid>/services                          List services
  /customers/<cid>/services/add                      Create service (GET/POST)
  /customers/<cid>/services/<sid>/edit               Edit service (GET/POST)
  /customers/<cid>/services/<sid>/delete             Delete service (POST)
  /customers/<cid>/services/<sid>/impact             Impact preview (GET)
  /ne-types/<tid>/ifaces/<ifid>/services             Replace linked services (POST)
  /api/customers/<cid>/services                      JSON list (GET)
  /api/projects/<pid>/requirements/service/<key>/preview  Dynamic preview (GET)
  /projects/<pid>/requirements/service/<key>         Save mode+payload (POST)
"""
import json
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, abort, jsonify
)
from auth import editor_required
from customer import get_customer
import services_logic as svc_logic

services_bp = Blueprint('services', __name__, url_prefix='')

# ── helpers ───────────────────────────────────────────────────────────────────

FIELD_TYPES    = ('text', 'number', 'textarea', 'dropdown', 'multi-select', 'checkbox')
SCOPE_LEVELS   = ('project', 'site', 'pod', 'ne', 'interface')
EMPTY_CUSTOM   = {
    'custom_suffix': '',
    'custom_prefix': '',
    'custom_env': '',
    'custom_extra': '',
}


def _parse_schema_from_form(form) -> list:
    """Reconstruct service schema from submitted JSON hidden field."""
    raw = form.get('fields_json', '[]')
    try:
        fields = json.loads(raw)
    except (ValueError, TypeError):
        fields = []
    # Ensure each field has a scope_override key (None means inherit iface)
    for f in fields:
        if 'scope_override' not in f:
            f['scope_override'] = None
        if not f.get('id'):
            f['id'] = str(uuid.uuid4())[:8]
    return fields

# ── Service CRUD routes ───────────────────────────────────────────────────────

@services_bp.route('/customers/<cid>/services')
def services_list(cid):
    customer = get_customer(cid)
    if not customer:
        abort(404)
    services = svc_logic.customer_services(cid)
    # Enrich each service with linked-iface count
    for svc in services:
        svc['_iface_count'] = len(svc_logic.ifaces_for_service(svc['id']))
    return render_template('services/services_list.html',
                           customer=customer, services=services)


@services_bp.route('/customers/<cid>/services/add', methods=['GET', 'POST'])
@editor_required
def add_service(cid):
    customer = get_customer(cid)
    if not customer:
        abort(404)

    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        desc   = request.form.get('description', '').strip()
        schema = _parse_schema_from_form(request.form)
        if not name:
            flash('Name is required.', 'warning')
            return render_template('services/service_form.html',
                                   customer=customer, service=None,
                                   form_values=request.form, schema=schema,
                                   field_types=FIELD_TYPES, scope_levels=SCOPE_LEVELS)
        sid = f'svc-{str(uuid.uuid4())[:8]}'
        svc_logic.save_service({
            'id':          sid,
            'name':        name,
            'description': desc,
            'customer_id': cid,
            'schema':      schema,
        })
        flash(f'Service {name!r} created.', 'success')
        return redirect(url_for('services.services_list', cid=cid))

    return render_template('services/service_form.html',
                           customer=customer, service=None,
                           form_values={}, schema=[],
                           field_types=FIELD_TYPES, scope_levels=SCOPE_LEVELS)


@services_bp.route('/customers/<cid>/services/<sid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_service(cid, sid):
    customer = get_customer(cid)
    service  = svc_logic.get_service(sid)
    if not customer or not service or service.get('customer_id') != cid:
        abort(404)

    if request.method == 'POST':
        name   = request.form.get('name', '').strip()
        desc   = request.form.get('description', '').strip()
        schema = _parse_schema_from_form(request.form)
        if not name:
            flash('Name is required.', 'warning')
            return render_template('services/service_form.html',
                                   customer=customer, service=service,
                                   form_values=request.form, schema=schema,
                                   field_types=FIELD_TYPES, scope_levels=SCOPE_LEVELS)
        service['name']        = name
        service['description'] = desc
        service['schema']      = schema
        svc_logic.save_service(service)
        flash('Service updated.', 'success')
        return redirect(url_for('services.services_list', cid=cid))

    return render_template('services/service_form.html',
                           customer=customer, service=service,
                           form_values=service, schema=service.get('schema', []),
                           field_types=FIELD_TYPES, scope_levels=SCOPE_LEVELS)


@services_bp.route('/customers/<cid>/services/<sid>/delete', methods=['POST'])
@editor_required
def delete_service(cid, sid):
    service = svc_logic.get_service(sid)
    if not service or service.get('customer_id') != cid:
        abort(404)
    linked = svc_logic.ifaces_for_service(sid)
    if linked:
        flash(
            f'Cannot delete: service is linked to {len(linked)} iface(s). '
            'Unlink first or use the impact page for force-delete.',
            'danger',
        )
        return redirect(url_for('services.services_list', cid=cid))
    svc_logic.delete_service(sid)
    flash(f'Service {service["name"]!r} deleted.', 'success')
    return redirect(url_for('services.services_list', cid=cid))


@services_bp.route('/customers/<cid>/services/<sid>/delete-force', methods=['POST'])
@editor_required
def force_delete_service(cid, sid):
    """Force-delete a service even if ifaces are linked."""
    service = svc_logic.get_service(sid)
    if not service or service.get('customer_id') != cid:
        abort(404)
    svc_logic.delete_service(sid)
    flash(f'Service {service["name"]!r} deleted (force).', 'success')
    return redirect(url_for('services.services_list', cid=cid))


@services_bp.route('/customers/<cid>/services/<sid>/impact')
def service_impact(cid, sid):
    service = svc_logic.get_service(sid)
    if not service or service.get('customer_id') != cid:
        abort(404)
    customer = get_customer(cid)
    linked_ifaces = svc_logic.ifaces_for_service(sid)
    from ne import get_ne_type  # pylint: disable=import-outside-toplevel
    iface_details = []
    for ne_type_id, iface_id in linked_ifaces:
        ne_type = get_ne_type(ne_type_id) or {}
        iface   = next((i for i in ne_type.get('interfaces', [])
                        if i['id'] == iface_id), {})
        iface_details.append({
            'ne_type': ne_type,
            'iface':   iface,
        })
    if request.headers.get('Accept', '').startswith('application/json'):
        return jsonify({
            'label':    service['name'],
            'cascades': [
                f"{d['ne_type'].get('name', '?')} / {d['iface'].get('name', '?')}"
                for d in iface_details
            ],
        })
    return render_template('services/service_impact.html',
                           customer=customer, service=service,
                           iface_details=iface_details)

# ── NE type iface ↔ service linking ──────────────────────────────────────────

@services_bp.route('/ne-types/<tid>/ifaces/<ifid>/services', methods=['POST'])
@editor_required
def update_iface_services(tid, ifid):
    """Replace the set of services linked to one NE-type iface."""
    from ne import get_ne_type  # pylint: disable=import-outside-toplevel
    ne_type = get_ne_type(tid)
    if not ne_type:
        abort(404)
    iface = next((i for i in ne_type.get('interfaces', []) if i['id'] == ifid), None)
    if not iface:
        abort(404)

    wanted_ids = set(request.form.getlist('service_ids'))
    current_ids = {s['id'] for s in svc_logic.services_for_iface(tid, ifid)}

    for sid in current_ids - wanted_ids:
        svc_logic.unlink_service_from_iface(sid, tid, ifid)
    for sid in wanted_ids - current_ids:
        if svc_logic.get_service(sid):
            svc_logic.link_service_to_iface(sid, tid, ifid)

    flash('Service links updated.', 'success')
    pid = request.args.get('pid') or request.form.get('pid')
    if pid:
        return redirect(url_for('ne.edit_ne_type', tid=tid, pid=pid))
    return redirect(url_for('ne.edit_ne_type', tid=tid))

# ── API: JSON service catalogue ───────────────────────────────────────────────

@services_bp.route('/api/customers/<cid>/services')
def api_customer_services(cid):
    if not get_customer(cid):
        abort(404)
    services = svc_logic.customer_services(cid)
    return jsonify(services)

# ── API: dynamic template preview ─────────────────────────────────────────────

@services_bp.route('/api/projects/<pid>/requirements/service/<path:row_key>/preview')
def service_preview(pid, row_key):
    from ne import load_requirements, project_ne_instances, get_ne_type  # pylint: disable=import-outside-toplevel
    from ipam import get_project  # pylint: disable=import-outside-toplevel
    proj = get_project(pid)
    if not proj:
        abort(404)
    reqs = load_requirements(pid)
    row  = next((r for r in reqs if r.get('key') == row_key), None)
    if not row or row.get('kind') != 'service':
        abort(404)

    template_str = request.args.get('template', '')
    custom = {
        'custom_suffix': request.args.get('custom_suffix', ''),
        'custom_prefix': request.args.get('custom_prefix', ''),
        'custom_env':    request.args.get('custom_env', ''),
        'custom_extra':  request.args.get('custom_extra', ''),
    }

    ne_type_id = row['ne_type_id']
    iface_id   = row['iface_id']
    ne_type    = get_ne_type(ne_type_id) or {}
    iface      = next((i for i in ne_type.get('interfaces', [])
                       if i['id'] == iface_id), {})

    all_insts = project_ne_instances(pid)
    insts = [i for i in all_insts if i.get('ne_type_id') == ne_type_id][:3]

    sv = {'mode': 'dynamic', 'template': template_str, 'custom': custom}
    results = svc_logic.evaluate_dynamic_for_preview(
        sv, ne_type, iface, {}, {}, insts, limit=3
    )
    return jsonify({'previews': results})

# ── Save service row mode+payload ─────────────────────────────────────────────

@services_bp.route('/projects/<pid>/requirements/service/<path:row_key>',
                   methods=['POST'])
@editor_required
def save_service_row(pid, row_key):
    from ipam import get_project  # pylint: disable=import-outside-toplevel
    if not get_project(pid):
        abort(404)

    mode = request.form.get('mode', '').strip()
    if mode not in ('text', 'list', 'dynamic'):
        return jsonify({'error': 'Invalid mode.'}), 400

    if mode == 'text':
        payload = {'mode': 'text', 'value': request.form.get('value', '')}
    elif mode == 'list':
        raw = request.form.get('values_json', '{}')
        try:
            values = json.loads(raw)
        except (ValueError, TypeError):
            values = {}
        payload = {'mode': 'list', 'values': values}
    else:  # dynamic
        custom = {
            'custom_suffix': request.form.get('custom_suffix', ''),
            'custom_prefix': request.form.get('custom_prefix', ''),
            'custom_env':    request.form.get('custom_env', ''),
            'custom_extra':  request.form.get('custom_extra', ''),
        }
        payload = {
            'mode':     'dynamic',
            'template': request.form.get('template', ''),
            'custom':   custom,
        }

    row = svc_logic.save_service_row(pid, row_key, mode, payload)
    if not row:
        abort(404)

    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'resolved_count': row['resolved_count'],
            'missing':        row['missing'],
            'input_mode':     row['input_mode'],
        })
    flash('Service values saved.', 'success')
    return redirect(url_for('ne.requirements', pid=pid))
