"""
Checks & Checklists blueprint.

Pre- and post-deployment structured verification checklists.
Routes under /admin/checks/... (global templates) and
/projects/<pid>/checklists + /checklists/<cid> (project instances).
"""
import json
import datetime
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort, jsonify)
from flask_login import current_user
from auth import editor_required
from core.forms import form_errors
from db import r, new_id
from checks_logic import (
    get_check_template, save_check_template, delete_check_template,
    all_check_templates, get_checklist, save_checklist, project_checklists,
    materialize_checklist, transition_checklist, auto_complete_if_ready,
    preview_materialization, can_transition,
    ATTACHED_TO_VALUES, PHASE_VALUES, SEVERITY_VALUES, SCOPE_VALUES,
    CHECK_STATUS_VALUES,
)

checks_bp = Blueprint('checks', __name__, url_prefix='')


# ── Small helpers ──────────────────────────────────────────────────────────────

def _get_project(pid):
    raw = r.get(f'project:{pid}')
    return json.loads(raw) if raw else None


def _all_projects():
    ids = r.smembers('projects:index')
    projects = []
    for pid in ids:
        raw = r.get(f'project:{pid}')
        if raw:
            try: projects.append(json.loads(raw))
            except json.JSONDecodeError: pass
    return sorted(projects, key=lambda p: p.get('name', ''))


def _actor_id():
    if current_user and current_user.is_authenticated:
        return current_user.id
    return ''


# ── Admin: check template library ─────────────────────────────────────────────

@checks_bp.route('/admin/checks/templates')
@editor_required
def list_check_templates():
    templates = all_check_templates()
    phase_f    = request.args.get('phase', '')
    attached_f = request.args.get('attached_to', '')
    if phase_f:
        templates = [t for t in templates if t.get('phase') == phase_f]
    if attached_f:
        templates = [t for t in templates if t.get('attached_to') == attached_f]
    return render_template('checks/templates_list.html',
                           templates=templates,
                           phase_filter=phase_f,
                           attached_filter=attached_f,
                           phase_values=PHASE_VALUES,
                           attached_to_values=ATTACHED_TO_VALUES,
                           all_projects=_all_projects())


@checks_bp.route('/admin/checks/templates/add', methods=['GET', 'POST'])
@editor_required
def add_check_template():
    errors, form_values = {}, {}
    if request.method == 'POST':
        form_values = request.form
        name        = request.form.get('name', '').strip()
        phase       = request.form.get('phase', '')
        attached_to = request.form.get('attached_to', '')
        severity    = request.form.get('severity', 'standard')
        scope       = request.form.get('scope', 'global')

        try:
            vendor_hints = json.loads(request.form.get('vendor_hints_json', '') or '{}')
        except (json.JSONDecodeError, ValueError):
            vendor_hints = {}
        try:
            attachment_filter = json.loads(
                request.form.get('attachment_filter_json', '') or '{}')
        except (json.JSONDecodeError, ValueError):
            attachment_filter = {}

        errors = form_errors(
            ('name',        bool(name),                       'Name is required.'),
            ('phase',       phase in PHASE_VALUES,            'Invalid phase.'),
            ('attached_to', attached_to in ATTACHED_TO_VALUES,'Invalid attachment point.'),
            ('severity',    severity in SEVERITY_VALUES,      'Invalid severity.'),
        )
        if not errors:
            tmpl = {
                'id':                 new_id(),
                'name':               name,
                'description':        request.form.get('description', '').strip(),
                'phase':              phase,
                'attached_to':        attached_to,
                'attachment_filter':  attachment_filter,
                'action_description': request.form.get('action_description', '').strip(),
                'expected_result':    request.form.get('expected_result', '').strip(),
                'vendor_hints':       vendor_hints,
                'severity':           severity,
                'tags':               [t.strip()
                                       for t in request.form.get('tags', '').split(',')
                                       if t.strip()],
                'scope':              scope,
                'project_id':         request.form.get('project_id', ''),
            }
            save_check_template(tmpl)
            flash(f'Check template "{name}" created.', 'success')
            return redirect(url_for('checks.list_check_templates'))

    return render_template('checks/template_form.html',
                           tmpl=None, errors=errors, form_values=form_values,
                           phase_values=PHASE_VALUES,
                           attached_to_values=ATTACHED_TO_VALUES,
                           severity_values=SEVERITY_VALUES,
                           scope_values=SCOPE_VALUES,
                           all_projects=_all_projects())


@checks_bp.route('/admin/checks/templates/<ctid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_check_template(ctid):
    tmpl = get_check_template(ctid) or abort(404)
    errors, form_values = {}, {}
    if request.method == 'POST':
        form_values = request.form
        name        = request.form.get('name', '').strip()
        phase       = request.form.get('phase', '')
        attached_to = request.form.get('attached_to', '')
        severity    = request.form.get('severity', 'standard')
        scope       = request.form.get('scope', 'global')

        try:
            vendor_hints = json.loads(request.form.get('vendor_hints_json', '') or '{}')
        except (json.JSONDecodeError, ValueError):
            vendor_hints = {}
        try:
            attachment_filter = json.loads(
                request.form.get('attachment_filter_json', '') or '{}')
        except (json.JSONDecodeError, ValueError):
            attachment_filter = {}

        errors = form_errors(
            ('name',        bool(name),                       'Name is required.'),
            ('phase',       phase in PHASE_VALUES,            'Invalid phase.'),
            ('attached_to', attached_to in ATTACHED_TO_VALUES,'Invalid attachment point.'),
            ('severity',    severity in SEVERITY_VALUES,      'Invalid severity.'),
        )
        if not errors:
            tmpl = {
                **tmpl,
                'name':               name,
                'description':        request.form.get('description', '').strip(),
                'phase':              phase,
                'attached_to':        attached_to,
                'attachment_filter':  attachment_filter,
                'action_description': request.form.get('action_description', '').strip(),
                'expected_result':    request.form.get('expected_result', '').strip(),
                'vendor_hints':       vendor_hints,
                'severity':           severity,
                'tags':               [t.strip()
                                       for t in request.form.get('tags', '').split(',')
                                       if t.strip()],
                'scope':              scope,
                'project_id':         request.form.get('project_id', ''),
            }
            save_check_template(tmpl)
            flash(f'Check template "{name}" updated.', 'success')
            return redirect(url_for('checks.list_check_templates'))

    return render_template('checks/template_form.html',
                           tmpl=tmpl, errors=errors, form_values=form_values,
                           phase_values=PHASE_VALUES,
                           attached_to_values=ATTACHED_TO_VALUES,
                           severity_values=SEVERITY_VALUES,
                           scope_values=SCOPE_VALUES,
                           all_projects=_all_projects())


@checks_bp.route('/admin/checks/templates/<ctid>/delete', methods=['POST'])
@editor_required
def delete_check_template_route(ctid):
    tmpl = get_check_template(ctid) or abort(404)
    delete_check_template(ctid)
    flash(f'Check template "{tmpl["name"]}" deleted.', 'success')
    return redirect(url_for('checks.list_check_templates'))


@checks_bp.route('/admin/checks/templates/<ctid>/preview', methods=['POST'])
@editor_required
def preview_check_template(ctid):
    """Return subject count for a given project (JSON — used by the live preview pane)."""
    get_check_template(ctid) or abort(404)
    body = request.get_json(silent=True, force=True) or {}
    pid  = body.get('project_id', '')
    if not pid:
        return jsonify({'error': 'project_id required'}), 400
    result = preview_materialization(pid, ctid)
    return jsonify(result)


# ── Project: checklist list ────────────────────────────────────────────────────

@checks_bp.route('/projects/<pid>/checklists')
@editor_required
def project_checklists_list(pid):
    proj = _get_project(pid) or abort(404)
    cls  = project_checklists(pid)
    return render_template('checks/checklist_list.html',
                           proj=proj, checklists=cls,
                           phase_values=PHASE_VALUES)


@checks_bp.route('/projects/<pid>/checklists/create', methods=['POST'])
@editor_required
def create_checklist(pid):
    _get_project(pid) or abort(404)
    phase            = request.form.get('phase', '')
    deployment_label = request.form.get('deployment_label', '').strip()

    if phase not in PHASE_VALUES:
        flash('Invalid phase.', 'danger')
        return redirect(url_for('checks.project_checklists_list', pid=pid))
    if not deployment_label:
        flash('Deployment label is required.', 'danger')
        return redirect(url_for('checks.project_checklists_list', pid=pid))

    cl = materialize_checklist(pid, phase, deployment_label, generated_by=_actor_id())
    flash(f'Checklist "{deployment_label}" created with {len(cl["checks"])} check(s).', 'success')
    return redirect(url_for('checks.checklist_detail', cid=cl['id']))


# ── Single checklist ───────────────────────────────────────────────────────────

@checks_bp.route('/checklists/<cid>')
@editor_required
def checklist_detail(cid):
    cl   = get_checklist(cid) or abort(404)
    proj = _get_project(cl['project_id']) or abort(404)

    status_filter   = request.args.get('status', '')
    severity_filter = request.args.get('severity', '')
    checks = cl.get('checks', [])
    if status_filter:
        checks = [c for c in checks if c['status'] == status_filter]
    if severity_filter:
        checks = [c for c in checks if c.get('severity') == severity_filter]

    all_checks = cl.get('checks', [])
    counts = {s: sum(1 for c in all_checks if c['status'] == s) for s in CHECK_STATUS_VALUES}
    counts['total'] = len(all_checks)

    can_signoff = cl['status'] == 'completed'
    allowed_transitions = [
        t for t in ('in-progress', 'completed', 'signed-off', 'archived', 'draft')
        if can_transition(cl, t)[0]
    ]

    return render_template('checks/checklist_detail.html',
                           proj=proj, cl=cl, checks=checks, counts=counts,
                           status_filter=status_filter,
                           severity_filter=severity_filter,
                           check_status_values=CHECK_STATUS_VALUES,
                           severity_values=SEVERITY_VALUES,
                           can_signoff=can_signoff,
                           allowed_transitions=allowed_transitions)


@checks_bp.route('/checklists/<cid>/update', methods=['POST'])
@editor_required
def update_checklist(cid):
    """Batch-update check statuses and notes (one form submit from the detail page)."""
    cl = get_checklist(cid) or abort(404)
    if cl['status'] in ('signed-off', 'archived'):
        flash('Checklist is locked — cannot record results.', 'danger')
        return redirect(url_for('checks.checklist_detail', cid=cid))

    actor = _actor_id()
    now   = datetime.datetime.now(datetime.timezone.utc).isoformat()
    checks = list(cl.get('checks', []))

    for check in checks:
        chk_id     = check['id']
        new_status = request.form.get(f'status_{chk_id}', '')
        new_notes  = request.form.get(f'notes_{chk_id}', '')
        reported   = request.form.get(f'reported_by_{chk_id}', '')

        if new_status and new_status in CHECK_STATUS_VALUES and new_status != check['status']:
            check['status']      = new_status
            check['recorded_by'] = actor
            check['recorded_at'] = now
        if new_notes != check.get('notes', ''):
            check['notes'] = new_notes
        if reported:
            check['reported_by_external'] = reported

    cl = {**cl, 'checks': checks}
    if cl['status'] == 'draft':
        cl['status'] = 'in-progress'
    save_checklist(cl)
    cl = auto_complete_if_ready(cl)
    flash('Results saved.', 'success')
    return redirect(url_for('checks.checklist_detail', cid=cid))


@checks_bp.route('/checklists/<cid>/transition', methods=['POST'])
@editor_required
def transition_cl(cid):
    cl        = get_checklist(cid) or abort(404)
    to_status = request.form.get('to_status', '')
    # is_admin: wired to real role when auth expands; editors can do all transitions for now
    cl, err = transition_checklist(cl, to_status, _actor_id(), is_admin=True)
    if err:
        flash(err, 'danger')
    else:
        flash(f'Checklist moved to "{to_status}".', 'success')
    return redirect(url_for('checks.checklist_detail', cid=cid))


@checks_bp.route('/checklists/<cid>/signoff', methods=['POST'])
@editor_required
def signoff_checklist(cid):
    cl      = get_checklist(cid) or abort(404)
    cl, err = transition_checklist(cl, 'signed-off', _actor_id(), is_admin=True)
    if err:
        flash(err, 'danger')
    else:
        flash('Checklist signed off.', 'success')
    return redirect(url_for('checks.checklist_detail', cid=cid))


@checks_bp.route('/checklists/<cid>/supersede', methods=['POST'])
@editor_required
def supersede_checklist(cid):
    old_cl = get_checklist(cid) or abort(404)
    pid    = old_cl['project_id']
    new_cl = materialize_checklist(pid, old_cl['phase'],
                                   old_cl['deployment_label'] + ' (v2)',
                                   generated_by=_actor_id())
    new_cl = {**new_cl, 'supersedes_checklist_id': cid}
    save_checklist(new_cl)
    flash(f'New checklist created ({len(new_cl["checks"])} checks).', 'success')
    return redirect(url_for('checks.checklist_detail', cid=new_cl['id']))


@checks_bp.route('/checklists/<cid>/generate-pdf', methods=['POST'])
@editor_required
def generate_checklist_pdf(cid):
    """
    Render a checklist to PDF (or HTML fallback) and store it as an artifact.
    Uses checklist_pre.html / checklist_post.html from the default template set.
    Links the new artifact_id back onto the checklist record.
    Each generation produces a new artifact; previous ones are retained.
    """
    import os
    cl   = get_checklist(cid) or abort(404)
    proj = _get_project(cl['project_id']) or abort(404)
    pid  = cl['project_id']
    uid  = _actor_id()

    template_name = f'checklist_{cl["phase"]}.html'

    # Build the template rendering context
    now_iso   = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tmpl_ctx  = {
        'checklist':    cl,
        'project':      proj,
        'checks':       cl.get('checks', []),
        'generated_at': now_iso,
    }

    # Search path: default template set (same directory as design.html)
    default_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__),
                     'var', 'ipam', 'template-sets', 'default')
    )

    try:
        from document_generation.renderer import render_template_to_string
        html, _missing = render_template_to_string(template_name, tmpl_ctx, [default_dir])
    except (FileNotFoundError, SyntaxError, RuntimeError) as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('checks.checklist_detail', cid=cid))

    # Try WeasyPrint; fall back to HTML file if not installed
    proj_slug = proj['name'].lower().replace(' ', '-')
    label_slug = cl['deployment_label'].lower().replace(' ', '-')
    base_name  = f'{proj_slug}-{label_slug}-{cl["phase"]}'
    try:
        from document_generation.pdf import html_to_pdf
        file_bytes = html_to_pdf(html)
        filename   = f'{base_name}.pdf'
        art_type   = 'checklist'
    except ImportError:
        flash('WeasyPrint not available — saving HTML artifact instead.', 'warning')
        file_bytes = html.encode('utf-8')
        filename   = f'{base_name}.html'
        art_type   = 'checklist'

    # Persist via the existing artifact pipeline
    from document_generation.storage import save_artifact
    context_snapshot = {'checklist': cl, 'project': proj}
    art = save_artifact(
        pid, art_type, filename, file_bytes,
        context_snapshot,
        label=f'{cl["deployment_label"]} ({cl["phase"]})',
        generated_by=uid,
    )

    # Link the new artifact onto the checklist (multiple generations are retained in the
    # artifact store; artifact_id always points to the most recent one)
    save_checklist({**cl, 'artifact_id': art['id']})

    flash('Checklist PDF generated successfully.', 'success')
    return redirect(url_for('documents.artifact_detail', aid=art['id']))


# ── Dashboard API ──────────────────────────────────────────────────────────────

@checks_bp.route('/api/projects/<pid>/checklists/pending-count')
def api_pending_count(pid):
    cls = project_checklists(pid)
    pending = sum(
        sum(1 for c in cl.get('checks', []) if c['status'] == 'pending')
        for cl in cls if cl.get('status') in ('draft', 'in-progress')
    )
    return jsonify({'pending': pending, 'checklists': len(cls)})
