"""
Documents blueprint — preview, artifact generation, and artifact management.

Routes:
  GET  /projects/<pid>/preview/design
  GET  /projects/<pid>/preview/configs/<ne_id>
  POST /projects/<pid>/generate
  GET  /projects/<pid>/artifacts
  GET  /artifacts/<aid>
  GET  /artifacts/<aid>/download/<filename>
  POST /artifacts/<aid>/submit-for-review
  POST /artifacts/<aid>/approve
  POST /artifacts/<aid>/reject
  POST /artifacts/<aid>/regenerate
"""
import os

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, abort, Response, send_file, g
)
from flask_login import current_user
from auth import editor_required
import db
from document_generation.context   import build_context
from document_generation.resolver  import (
    get_template_search_paths, check_schema_compatibility
)
from document_generation.renderer  import render_template_to_string
from document_generation.storage   import (
    save_artifact, get_artifact, list_project_artifacts,
    update_artifact_status, add_artifact_approval,
    load_context_snapshot, stream_artifact_file,
)

documents_bp = Blueprint('documents', __name__, url_prefix='')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_user_info():
    uid   = getattr(current_user, 'id', 'system')
    name  = uid
    email = ''
    return uid, name, email


def _get_project_or_404(pid):
    from ipam import get_project
    proj = get_project(pid)
    if not proj:
        abort(404)
    return proj


# ── Preview routes ────────────────────────────────────────────────────────────

@documents_bp.route('/projects/<pid>/preview/design')
def preview_design(pid):
    """Render design.html in-browser (no PDF step) for fast iteration."""
    _get_project_or_404(pid)
    uid, name, email = _current_user_info()

    try:
        context = build_context(pid, uid, name, email)
    except ValueError as exc:
        abort(404, str(exc))

    compat_err = check_schema_compatibility(context)
    if compat_err:
        flash(compat_err, 'danger')

    search_paths = get_template_search_paths(context)
    error = None
    html  = ''
    missing = []

    try:
        html, missing = render_template_to_string('design.html', context, search_paths)
    except FileNotFoundError as exc:
        error = str(exc)
    except SyntaxError as exc:
        error = str(exc)
    except RuntimeError as exc:
        error = str(exc)

    if error:
        flash(error, 'danger')

    return render_template('documents/preview.html',
                           project=context['project'],
                           preview_html=html,
                           missing=missing,
                           error=error,
                           preview_type='design')


@documents_bp.route('/projects/<pid>/preview/configs/<ne_id>')
def preview_config(pid, ne_id):
    """Render a single NE config template as plain text."""
    _get_project_or_404(pid)
    uid, name, email = _current_user_info()

    try:
        context = build_context(pid, uid, name, email)
    except ValueError as exc:
        abort(404, str(exc))

    ne_inst = next((n for n in context.get('ne_instances', [])
                    if n.get('id') == ne_id), None)
    if not ne_inst:
        abort(404, f'NE instance {ne_id!r} not found')

    ne_types_by_id = {nt['id']: nt for nt in context.get('ne_types', [])}
    ne_type = ne_types_by_id.get(ne_inst.get('ne_type_id', ''), {})
    tmpl_name = ne_type.get('config_template')

    if not tmpl_name:
        return render_template('documents/preview.html',
                               project=context['project'],
                               preview_html='<pre>No config_template set for this NE type.</pre>',
                               missing=[], error=None,
                               preview_type='config')

    search_paths = get_template_search_paths(context)
    config_ctx = {'ne': ne_inst, 'ne_type': ne_type, **context}

    try:
        text, missing = render_template_to_string(
            f'configs/{tmpl_name}', config_ctx, search_paths
        )
        html = f'<pre style="white-space:pre-wrap">{text}</pre>'
    except (FileNotFoundError, SyntaxError, RuntimeError) as exc:
        html  = ''
        flash(str(exc), 'danger')
        missing = []

    return render_template('documents/preview.html',
                           project=context['project'],
                           preview_html=html,
                           missing=missing,
                           error=None,
                           preview_type='config')


# ── Generate artifact ─────────────────────────────────────────────────────────

@documents_bp.route('/projects/<pid>/generate', methods=['POST'])
@editor_required
def generate_artifact(pid):
    """Generate a PDF, config bundle, combined, or checklist-combined artifact."""
    proj = _get_project_or_404(pid)
    artifact_type = request.form.get('type', 'pdf')  # pdf | bundle | combined | checklist-combined
    label         = request.form.get('label', '')
    uid, name, email = _current_user_info()

    # checklist-combined: design PDF + pre + post checklists in one document
    if artifact_type == 'checklist-combined':
        return _generate_checklist_combined(pid, proj, label, uid, name, email)

    try:
        context = build_context(pid, uid, name, email)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))

    compat_err = check_schema_compatibility(context)
    if compat_err:
        flash(compat_err, 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))

    search_paths = get_template_search_paths(context)
    customer = context.get('customer', {})
    ts_id    = customer.get('template_set_id', '')
    proj_name = context['project'].get('name', pid)
    cust_slug  = customer.get('slug', 'design')

    artifacts_created = []

    try:
        if artifact_type in ('pdf', 'combined'):
            html, missing = render_template_to_string('design.html', context, search_paths)
            context['missing']['fields'].extend(missing)

            # Try WeasyPrint; fall back to HTML file if not installed
            try:
                from document_generation.pdf import html_to_pdf
                pdf_bytes = html_to_pdf(html)
                filename  = f'{cust_slug}-{proj_name}-design.pdf'
                art = save_artifact(pid, 'pdf', filename, pdf_bytes, context,
                                    ts_id, label=label, generated_by=uid,
                                    approvals=context.get('approvals', []))
                artifacts_created.append(art)
            except ImportError as ie:
                flash(f'WeasyPrint not available — saving HTML instead. ({ie})', 'warning')
                html_bytes = html.encode('utf-8')
                filename   = f'{cust_slug}-{proj_name}-design.html'
                art = save_artifact(pid, 'pdf', filename, html_bytes, context,
                                    ts_id, label=label, generated_by=uid)
                artifacts_created.append(art)

        if artifact_type in ('bundle', 'combined'):
            from document_generation.bundle import render_config_bundle
            zip_bytes, missing = render_config_bundle(context, search_paths)
            context['missing']['fields'].extend(missing)
            filename = f'{cust_slug}-{proj_name}-configs.zip'
            art = save_artifact(pid, 'config-bundle', filename, zip_bytes, context,
                                ts_id, label=label, generated_by=uid)
            artifacts_created.append(art)

    except FileNotFoundError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))
    except SyntaxError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))
    except RuntimeError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))

    if artifacts_created:
        flash(f'Generated {len(artifacts_created)} artifact(s) successfully.', 'success')
        if len(artifacts_created) == 1:
            return redirect(url_for('documents.artifact_detail', aid=artifacts_created[0]['id']))

    return redirect(url_for('documents.project_artifacts', pid=pid))


def _generate_checklist_combined(pid, proj, label, uid, name, email):
    """
    Build a combined PDF: network design body + pre/post checklists for `label`.
    Falls back to HTML if WeasyPrint is absent.
    """
    import re as _re
    import datetime

    from checks_logic import project_checklists

    # Look up matching checklists for the deployment label
    all_cls    = project_checklists(pid)
    pre_cl     = next((c for c in all_cls
                       if c.get('deployment_label') == label and c.get('phase') == 'pre'), None)
    post_cl    = next((c for c in all_cls
                       if c.get('deployment_label') == label and c.get('phase') == 'post'), None)

    if not pre_cl and not post_cl:
        flash(f'No checklists found for deployment label "{label}".', 'warning')
        return redirect(url_for('checks.project_checklists_list', pid=pid))

    # Build design context and search paths
    try:
        context = build_context(pid, uid, name, email)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.project_artifacts', pid=pid))

    search_paths = get_template_search_paths(context)

    def _body(html: str) -> str:
        m = _re.search(r'<body[^>]*>(.*?)</body>', html, _re.DOTALL | _re.IGNORECASE)
        return m.group(1).strip() if m else ''

    # Render design section (optional — gracefully absent if no design.html exists)
    design_body = ''
    try:
        design_html, _ = render_template_to_string('design.html', context, search_paths)
        design_body = _body(design_html)
    except (FileNotFoundError, KeyError):
        pass  # no design template; combined still includes checklists

    now_iso   = datetime.datetime.now(datetime.timezone.utc).isoformat()
    proj_name = proj.get('name', pid)
    tmpl_ctx  = {
        'project':         proj,
        'deployment_label': label,
        'generated_at':    now_iso,
        'design_body':     design_body,
        'pre_checklist':   pre_cl,
        'post_checklist':  post_cl,
    }

    default_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), 'var', 'ipam', 'template-sets', 'default')
    )
    try:
        html, _ = render_template_to_string('combined.html', tmpl_ctx, [default_dir])
    except (FileNotFoundError, SyntaxError, RuntimeError) as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('checks.project_checklists_list', pid=pid))

    label_slug   = label.lower().replace(' ', '-')
    proj_slug    = proj_name.lower().replace(' ', '-')
    base_name    = f'{proj_slug}-{label_slug}-combined'
    context_snap = {'project': proj, 'pre_checklist': pre_cl, 'post_checklist': post_cl}

    try:
        from document_generation.pdf import html_to_pdf
        file_bytes = html_to_pdf(html)
        filename   = f'{base_name}.pdf'
    except ImportError:
        flash('WeasyPrint not available — saving HTML artifact instead.', 'warning')
        file_bytes = html.encode('utf-8')
        filename   = f'{base_name}.html'

    art = save_artifact(pid, 'combined', filename, file_bytes, context_snap,
                        label=label, generated_by=uid)
    flash('Combined deployment package generated.', 'success')
    return redirect(url_for('documents.artifact_detail', aid=art['id']))


# ── Artifact list ─────────────────────────────────────────────────────────────

@documents_bp.route('/projects/<pid>/artifacts')
def project_artifacts(pid):
    """List artifacts generated for a project."""
    proj = _get_project_or_404(pid)
    artifacts = list_project_artifacts(pid)
    return render_template('documents/artifact_list.html',
                           project=proj, artifacts=artifacts)


# ── Artifact detail ───────────────────────────────────────────────────────────

@documents_bp.route('/artifacts/<aid>')
def artifact_detail(aid):
    """Show artifact metadata, download links, and approval controls."""
    art = get_artifact(aid)
    if not art:
        abort(404)
    from ipam import get_project
    proj = get_project(art.get('project_id', ''))
    return render_template('documents/artifact_detail.html',
                           artifact=art, project=proj)


# ── Download ──────────────────────────────────────────────────────────────────

@documents_bp.route('/artifacts/<aid>/download/<path:filename>')
def download_artifact(aid, filename):
    """Stream an artifact file to the browser."""
    art = get_artifact(aid)
    if not art:
        abort(404)

    data = stream_artifact_file(aid, filename)
    if data is None:
        abort(404)

    # Determine MIME type
    fn_lower = filename.lower()
    if fn_lower.endswith('.pdf'):
        mimetype = 'application/pdf'
    elif fn_lower.endswith('.zip'):
        mimetype = 'application/zip'
    elif fn_lower.endswith('.json'):
        mimetype = 'application/json'
    elif fn_lower.endswith('.html'):
        mimetype = 'text/html'
    else:
        mimetype = 'application/octet-stream'

    return Response(
        data,
        mimetype=mimetype,
        headers={'Content-Disposition': f'attachment; filename="{os.path.basename(filename)}"'},
    )


# ── Approval workflow ─────────────────────────────────────────────────────────

@documents_bp.route('/artifacts/<aid>/submit-for-review', methods=['POST'])
@editor_required
def submit_for_review(aid):
    """Move artifact to under-review state."""
    art = update_artifact_status(aid, 'under-review')
    if not art:
        abort(404)
    flash('Artifact submitted for review.', 'info')
    return redirect(url_for('documents.artifact_detail', aid=aid))


@documents_bp.route('/artifacts/<aid>/approve', methods=['POST'])
@editor_required
def approve_artifact(aid):
    """Approve an artifact."""
    uid, name, _ = _current_user_info()
    comment = request.form.get('comment', '')
    art = add_artifact_approval(aid, uid, name, 'approved', comment)
    if not art:
        abort(404)
    flash('Artifact approved.', 'success')
    return redirect(url_for('documents.artifact_detail', aid=aid))


@documents_bp.route('/artifacts/<aid>/reject', methods=['POST'])
@editor_required
def reject_artifact(aid):
    """Reject an artifact, returning it to draft."""
    uid, name, _ = _current_user_info()
    comment = request.form.get('comment', '')
    art = add_artifact_approval(aid, uid, name, 'rejected', comment)
    if not art:
        abort(404)
    flash('Artifact rejected — returned to draft.', 'warning')
    return redirect(url_for('documents.artifact_detail', aid=aid))


# ── Regenerate ────────────────────────────────────────────────────────────────

@documents_bp.route('/artifacts/<aid>/regenerate', methods=['POST'])
@editor_required
def regenerate_artifact(aid):
    """
    Re-run stages 2-6 on the existing frozen context snapshot.
    Produces a new artifact linked back via supersedes_artifact_id.
    """
    old_art = get_artifact(aid)
    if not old_art:
        abort(404)

    context = load_context_snapshot(aid)
    if not context:
        flash('Cannot regenerate: context snapshot is missing.', 'danger')
        return redirect(url_for('documents.artifact_detail', aid=aid))

    pid          = old_art['project_id']
    ts_id        = old_art.get('template_set_id', '')
    uid, name, _ = _current_user_info()
    customer     = context.get('customer', {})
    proj_name    = context['project'].get('name', pid)
    cust_slug    = customer.get('slug', 'design')
    artifact_type = old_art.get('type', 'pdf')

    search_paths = get_template_search_paths(context)

    try:
        if artifact_type in ('pdf',):
            html, _ = render_template_to_string('design.html', context, search_paths)
            try:
                from document_generation.pdf import html_to_pdf
                file_bytes = html_to_pdf(html)
                filename   = f'{cust_slug}-{proj_name}-design.pdf'
            except ImportError:
                file_bytes = html.encode('utf-8')
                filename   = f'{cust_slug}-{proj_name}-design.html'
        elif artifact_type == 'config-bundle':
            from document_generation.bundle import render_config_bundle
            file_bytes, _ = render_config_bundle(context, search_paths)
            filename = f'{cust_slug}-{proj_name}-configs.zip'
        else:
            flash(f'Regeneration not supported for type {artifact_type!r}.', 'warning')
            return redirect(url_for('documents.artifact_detail', aid=aid))

    except (FileNotFoundError, SyntaxError, RuntimeError) as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('documents.artifact_detail', aid=aid))

    new_art = save_artifact(
        pid, artifact_type, filename, file_bytes, context,
        ts_id, label=f'Regenerated from {aid}',
        supersedes_aid=aid, generated_by=uid,
    )
    flash('Artifact regenerated successfully.', 'success')
    return redirect(url_for('documents.artifact_detail', aid=new_art['id']))
