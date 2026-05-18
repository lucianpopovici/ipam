"""
Business logic for the Checks & Checklists feature.

Entities
────────
  check_template:{ctid}        JSON — reusable, phase-scoped check definition
  checklist:{cid}              JSON — per-deployment materialized instance

Indices
───────
  check_templates:index              Set  — all ctid's
  checklist_template:scope:{scope}   Set  — ctid's filtered by scope
  project:{pid}:checklists           List — cid's, newest-first
"""
import json
import datetime
from jinja2.sandbox import SandboxedEnvironment
from db import r, new_id, redis_get, redis_save, redis_delete

# ── Constants ──────────────────────────────────────────────────────────────────

ATTACHED_TO_VALUES = ('project', 'ne_type', 'ne_iface', 'cable', 'hw_template')
PHASE_VALUES       = ('pre', 'post')
SEVERITY_VALUES    = ('critical', 'standard', 'advisory')
SCOPE_VALUES       = ('global', 'project')
STATUS_VALUES      = ('draft', 'in-progress', 'completed', 'signed-off', 'archived')
CHECK_STATUS_VALUES = ('pending', 'pass', 'fail', 'skip', 'n/a')

# Valid forward/backward transitions
TRANSITIONS = {
    'draft':       {'in-progress'},
    'in-progress': {'completed', 'draft'},
    'completed':   {'signed-off', 'in-progress'},
    'signed-off':  {'archived'},
    'archived':    set(),
}

# Transitions that require admin role
ADMIN_ONLY_TRANSITIONS = {
    ('in-progress', 'draft'),
    ('completed',   'in-progress'),
}

CT_INDEX = 'check_templates:index'

_JINJA = SandboxedEnvironment(autoescape=False)


# ── Redis key helpers ──────────────────────────────────────────────────────────

def _ct_key(ctid):
    return f'check_template:{ctid}'

def _cl_key(cid):
    return f'checklist:{cid}'

def _proj_cl_key(pid):
    return f'project:{pid}:checklists'


# ── CRUD — check_template ──────────────────────────────────────────────────────

def get_check_template(ctid):
    return redis_get(_ct_key(ctid))


def save_check_template(tmpl):
    redis_save(_ct_key(tmpl['id']), tmpl)
    r.sadd(CT_INDEX, tmpl['id'])
    scope = tmpl.get('scope', 'global')
    r.sadd(f'checklist_template:scope:{scope}', tmpl['id'])
    return tmpl


def delete_check_template(ctid):
    tmpl = get_check_template(ctid)
    if not tmpl:
        return
    scope = tmpl.get('scope', 'global')
    r.srem(f'checklist_template:scope:{scope}', ctid)
    r.srem(CT_INDEX, ctid)
    redis_delete(_ct_key(ctid))


def all_check_templates() -> list:
    result = []
    for ctid in r.smembers(CT_INDEX):
        t = get_check_template(ctid)
        if t:
            result.append(t)
    return sorted(result, key=lambda t: (t.get('phase', ''), t.get('name', '')))


# ── CRUD — checklist ───────────────────────────────────────────────────────────

def get_checklist(cid):
    return redis_get(_cl_key(cid))


def save_checklist(cl):
    redis_save(_cl_key(cl['id']), cl)
    return cl


def project_checklists(pid) -> list:
    """Return checklists for a project newest-first."""
    result = []
    for cid in r.lrange(_proj_cl_key(pid), 0, -1):
        cl = get_checklist(cid)
        if cl:
            result.append(cl)
    return result


# ── Context snapshot ───────────────────────────────────────────────────────────

def build_context_snapshot(pid: str) -> dict:
    """
    Collect all project data needed by materialization into one dict.
    Local imports to avoid circular dependencies with ne/hw modules.
    """
    from hw_logic import project_instances as hw_project_instances, project_cables

    proj_raw = r.get(f'project:{pid}')
    project  = json.loads(proj_raw) if proj_raw else {}

    ne_instances = []
    for nid in r.smembers(f'project:{pid}:ne_instances'):
        raw = r.get(f'ne_inst:{nid}')
        if not raw:
            continue
        try:
            inst = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ne_type_raw = r.get(f'ne_type:{inst.get("ne_type_id", "")}')
        ne_type = json.loads(ne_type_raw) if ne_type_raw else {}
        ne_instances.append({**inst, 'ne_type': ne_type})

    sites, pods = [], []
    for sid in r.smembers(f'project:{pid}:sites'):
        raw = r.get(f'site:{sid}')
        if raw:
            try:
                sites.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    for pod_id in r.smembers(f'project:{pid}:pods'):
        raw = r.get(f'pod:{pod_id}')
        if raw:
            try:
                pods.append(json.loads(raw))
            except json.JSONDecodeError:
                pass

    return {
        'project':      project,
        'ne_instances': ne_instances,
        'sites':        sites,
        'pods':         pods,
        'hw_instances': hw_project_instances(pid),
        'cables':       project_cables(pid),
    }


# ── Materialization ────────────────────────────────────────────────────────────

def applicable_check_templates(pid: str, phase: str) -> list:
    """Templates that apply to this project/phase (global + project-scoped)."""
    return [
        t for t in all_check_templates()
        if t.get('phase') == phase
        and (t.get('scope') == 'global'
             or (t.get('scope') == 'project' and t.get('project_id') == pid))
    ]


def resolve_subjects(tmpl: dict, ctx: dict) -> list:
    """
    Return a list of subject dicts for one check template.
    Each subject carries enough data to build the per-item Jinja2 context.
    """
    attached_to = tmpl.get('attached_to', 'project')
    filt = tmpl.get('attachment_filter') or {}

    if attached_to == 'project':
        return [{'_type': 'project'}]

    if attached_to == 'ne_type':
        wanted = filt.get('ne_type_id')
        return [
            {'_type': 'ne_type', 'ne_instance': inst, 'ne_type': inst.get('ne_type', {})}
            for inst in ctx['ne_instances']
            if not wanted or inst.get('ne_type', {}).get('id') == wanted
        ]

    if attached_to == 'ne_iface':
        iface_labels_any  = set(filt.get('iface_labels_any')  or [])
        binding_modes_any = set(filt.get('binding_modes_any') or [])
        wanted_ne_type    = filt.get('ne_type_id')
        results = []
        for inst in ctx['ne_instances']:
            ne_type = inst.get('ne_type', {})
            if wanted_ne_type and ne_type.get('id') != wanted_ne_type:
                continue
            iface_map = {i['id']: i for i in ne_type.get('interfaces', [])}
            for iface_id, binding in inst.get('iface_bindings', {}).items():
                iface  = iface_map.get(iface_id, {})
                labels = set(iface.get('labels', []))
                mode   = binding.get('bind_mode', 'single')
                if iface_labels_any and not labels & iface_labels_any:
                    continue
                if binding_modes_any and mode not in binding_modes_any:
                    continue
                results.append({'_type': 'ne_iface', 'ne_instance': inst,
                                'ne_type': ne_type, 'iface': iface, 'binding': binding})
        return results

    if attached_to == 'cable':
        return [{'_type': 'cable', 'cable': c} for c in ctx['cables']]

    if attached_to == 'hw_template':
        wanted_tmpl    = filt.get('hw_template_id')
        categories_any = set(filt.get('hw_categories_any') or [])
        results = []
        for hw in ctx['hw_instances']:
            hw_tmpl = hw.get('template') or {}
            if wanted_tmpl and hw_tmpl.get('id') != wanted_tmpl:
                continue
            if categories_any and hw_tmpl.get('category') not in categories_any:
                continue
            results.append({'_type': 'hw_template', 'hw_instance': hw, 'hw_template': hw_tmpl})
        return results

    return []


def _build_item_ctx(subject: dict) -> dict:
    """Build the Jinja2 variable dict for a single check item."""
    ctx  = {}
    kind = subject.get('_type', '')
    if kind in ('ne_type', 'ne_iface'):
        inst = subject.get('ne_instance', {})
        ctx['ne'] = {'id': inst.get('id'), 'name': inst.get('name', ''),
                     'kind': inst.get('ne_type', {}).get('kind', '')}
    if kind == 'ne_iface':
        iface = subject.get('iface', {})
        ctx['iface']   = {'id': iface.get('id'), 'name': iface.get('name', ''),
                          'labels': iface.get('labels', [])}
        binding = subject.get('binding', {})
        ctx['binding'] = {'mode': binding.get('bind_mode', ''),
                          'ports': binding.get('ports', [])}
    if kind == 'cable':
        c = subject.get('cable', {})
        ctx['cable'] = {'id': c.get('id'), 'label': c.get('asset_tag', ''),
                        'end_a': c.get('end_a', {}), 'end_b': c.get('end_b', {})}
    if kind == 'hw_template':
        hw   = subject.get('hw_instance', {})
        tmpl = subject.get('hw_template', {})
        ctx['hw'] = {'id': hw.get('id'), 'asset_tag': hw.get('asset_tag', ''),
                     'expected_software': hw.get('expected_software', ''),
                     'template_name': tmpl.get('name', ''),
                     'category': tmpl.get('category', ''),
                     'vendor': tmpl.get('vendor', '')}
    return ctx


def _render_safe(template_str: str, ctx: dict) -> tuple:
    """Render a Jinja2 string safely. Returns (rendered_str, error_str|None)."""
    try:
        return _JINJA.from_string(template_str or '').render(**ctx), None
    except Exception as exc:  # pylint: disable=broad-except
        return template_str or '', str(exc)


def _pick_vendor_hint(check_tmpl: dict, item_ctx: dict) -> str:
    hints = check_tmpl.get('vendor_hints') or {}
    if not hints:
        return ''
    vendor = (item_ctx.get('hw') or {}).get('vendor', '').lower().replace(' ', '_')
    raw = hints.get(vendor) or next(iter(hints.values()))
    rendered, _ = _render_safe(raw, item_ctx)
    return rendered


def _subject_ref(subject: dict) -> dict:
    """Compact serialisable subject reference (no large nested objects)."""
    kind = subject.get('_type', '')
    out  = {'type': kind}
    if kind in ('ne_type', 'ne_iface'):
        out['ne_instance_id'] = subject.get('ne_instance', {}).get('id', '')
    if kind == 'ne_iface':
        out['iface_id'] = subject.get('iface', {}).get('id', '')
    if kind == 'cable':
        out['cable_id'] = subject.get('cable', {}).get('id', '')
    if kind == 'hw_template':
        out['hw_instance_id'] = subject.get('hw_instance', {}).get('id', '')
    return out


def materialize_checklist(pid: str, phase: str, deployment_label: str,
                           generated_by: str = '') -> dict:
    """
    Build and persist a new checklist from all applicable check templates.
    Renders Jinja2 eagerly — the text is frozen at creation time.
    """
    ctx       = build_context_snapshot(pid)
    templates = applicable_check_templates(pid, phase)

    checks = []
    for ct in templates:
        for subject in resolve_subjects(ct, ctx):
            item_ctx = _build_item_ctx(subject)
            action,   _ = _render_safe(ct.get('action_description', ''), item_ctx)
            expected, _ = _render_safe(ct.get('expected_result',    ''), item_ctx)
            checks.append({
                'id':                   new_id(),
                'check_template_id':    ct['id'],
                'subject':              _subject_ref(subject),
                'action_text':          action,
                'expected_text':        expected,
                'vendor_hint_text':     _pick_vendor_hint(ct, item_ctx),
                'severity':             ct.get('severity', 'standard'),
                'status':               'pending',
                'notes':                '',
                'reported_by_external': '',
                'recorded_by':          '',
                'recorded_at':          None,
            })

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cid = new_id()
    cl  = {
        'id':                      cid,
        'project_id':              pid,
        'phase':                   phase,
        'deployment_label':        deployment_label,
        'status':                  'draft',
        'generated_at':            now,
        'generated_by':            generated_by,
        'checks':                  checks,
        'signed_off_at':           None,
        'signed_off_by':           None,
        'artifact_id':             None,
        'supersedes_checklist_id': None,
    }
    save_checklist(cl)
    r.lpush(_proj_cl_key(pid), cid)   # newest-first
    return cl


def preview_materialization(pid: str, ctid: str) -> dict:
    """Return subject count without persisting anything."""
    ct = get_check_template(ctid)
    if not ct:
        return {'subjects': 0}
    ctx      = build_context_snapshot(pid)
    subjects = resolve_subjects(ct, ctx)
    return {'subjects': len(subjects)}


# ── State machine ──────────────────────────────────────────────────────────────

def can_transition(cl: dict, to_status: str,
                   is_admin: bool = False) -> tuple:
    """Return (ok: bool, reason: str). reason is '' when ok."""
    from_status = cl.get('status', '')
    allowed     = TRANSITIONS.get(from_status, set())

    if to_status not in allowed:
        return False, f'Cannot move from "{from_status}" to "{to_status}".'
    if (from_status, to_status) in ADMIN_ONLY_TRANSITIONS and not is_admin:
        return False, f'Moving "{from_status}" → "{to_status}" requires admin.'
    if to_status == 'completed':
        pending = sum(1 for c in cl.get('checks', []) if c['status'] == 'pending')
        if pending:
            return False, f'{pending} check(s) still pending.'
    return True, ''


def transition_checklist(cl: dict, to_status: str, actor: str,
                          is_admin: bool = False) -> tuple:
    """Apply state transition. Returns (updated_cl, error_str)."""
    ok, reason = can_transition(cl, to_status, is_admin)
    if not ok:
        return cl, reason
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cl  = {**cl, 'status': to_status}
    if to_status == 'signed-off':
        cl['signed_off_at'] = now
        cl['signed_off_by'] = actor
    save_checklist(cl)
    return cl, ''


def auto_complete_if_ready(cl: dict) -> dict:
    """Promote in-progress → completed when all checks are non-pending."""
    if cl.get('status') != 'in-progress':
        return cl
    if any(c['status'] == 'pending' for c in cl.get('checks', [])):
        return cl
    cl = {**cl, 'status': 'completed'}
    save_checklist(cl)
    return cl
