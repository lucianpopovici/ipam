"""
Generate-commit machinery — git-style versioning for the generate step.

Every call to generate_from_bom / generate_all_from_bom / NE instance
materialization produces a commit that freezes the current template+BoM
state, records what was created/updated/deleted, and gives every
generated element a traceable history entry.

Redis keys
----------
generate_commit:{cid}        JSON  — commit metadata
snapshot:{cid}               JSON  — frozen template + BoM state
project:{pid}:commits        List  — cid's, newest-first
project:{pid}:head_commit    String — current HEAD cid
project:{pid}:initial_commit String — first cid (set once)
"""

import datetime
import json
import uuid

import db

# Fields copied from the HW template into instance.template_snapshot.
# These are "tracked" fields — changes to them produce pending diffs.
HW_TRACKED_FIELDS = (
    'u_size', 'depth', 'weight_kg', 'power_w',
    'category', 'vendor', 'model', 'form_factor', 'cable_type',
)


# ── Key helpers ──────────────────────────────────────────────────────────────

def _commit_key(cid: str) -> str:
    return f'generate_commit:{cid}'

def _snapshot_key(cid: str) -> str:
    return f'snapshot:{cid}'

def _commits_list(pid: str) -> str:
    return f'project:{pid}:commits'

def _head_key(pid: str) -> str:
    return f'project:{pid}:head_commit'

def _initial_key(pid: str) -> str:
    return f'project:{pid}:initial_commit'


# ── Utilities ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _new_cid() -> str:
    return 'c-' + uuid.uuid4().hex[:8]


# ── Commit CRUD ──────────────────────────────────────────────────────────────

def get_generate_commit(cid: str) -> dict | None:
    raw = db.r.get(_commit_key(cid))
    return json.loads(raw) if raw else None

def save_generate_commit(commit: dict):
    db.r.set(_commit_key(commit['id']), json.dumps(commit))

def get_snapshot(cid: str) -> dict | None:
    raw = db.r.get(_snapshot_key(cid))
    return json.loads(raw) if raw else None

def save_snapshot(cid: str, snap: dict):
    db.r.set(_snapshot_key(cid), json.dumps(snap))

def get_project_head(pid: str) -> str | None:
    return db.r.get(_head_key(pid))

def project_commits(pid: str, limit: int = 50) -> list:
    cids = db.r.lrange(_commits_list(pid), 0, limit - 1)
    result = []
    for cid in cids:
        c = get_generate_commit(cid)
        if c:
            result.append(c)
    return result


# ── Snapshot building ────────────────────────────────────────────────────────

def build_snapshot(pid: str) -> dict:
    """Freeze the current template + BoM state for project *pid*."""
    from hw_logic import get_hw_template, get_bom

    tmpl_ids = (
        set(db.r.smembers(f'project:{pid}:hw:templates'))
        | set(db.r.smembers('hw:templates:index'))
    )
    hw_templates = {}
    for tid in tmpl_ids:
        t = get_hw_template(tid)
        if t:
            hw_templates[tid] = t

    bom = get_bom(pid)

    return {
        'commit_id':    None,   # filled in by callers after allocation
        'project_id':  pid,
        'hw_templates': hw_templates,
        'bom':          bom,
    }


def derive_template_snapshot(inst: dict, hw_templates: dict) -> dict:
    """Extract tracked template fields from an instance."""
    tmpl = hw_templates.get(inst.get('template_id', ''), {})
    snap = {f: tmpl.get(f) for f in HW_TRACKED_FIELDS if f in tmpl}
    snap['ports'] = [
        {'id': p.get('id', ''), 'name': p.get('name', ''), 'type': p.get('type', '')}
        for p in tmpl.get('ports', [])
    ]
    return snap


# ── Diff ─────────────────────────────────────────────────────────────────────

def _bom_diff(old_bom: list, new_bom: list) -> dict:
    old = {item['id']: item for item in old_bom}
    new = {item['id']: item for item in new_bom}
    added   = [new[k] for k in new if k not in old]
    deleted = [old[k]['id'] for k in old if k not in new]
    edited  = []
    for k in old:
        if k in new:
            changed = [f for f in ('qty', 'template_id', 'tag_prefix', 'tag_start', 'tag_pad')
                       if old[k].get(f) != new[k].get(f)]
            if changed:
                edited.append({'id': k, 'fields': changed})
    return {'added': added, 'edited': edited, 'deleted': deleted}


def _template_diff(old_tmpls: dict, new_tmpls: dict) -> dict:
    added, edited, deleted = [], [], []
    for tid, tmpl in new_tmpls.items():
        if tid not in old_tmpls:
            added.append(tid)
        else:
            changed = [f for f in HW_TRACKED_FIELDS + ('ports',)
                       if old_tmpls[tid].get(f) != tmpl.get(f)]
            if changed:
                edited.append({'id': tid, 'fields': changed})
    deleted = [tid for tid in old_tmpls if tid not in new_tmpls]
    return {'added': added, 'edited': edited, 'deleted': deleted}


def compute_pending(pid: str) -> dict:
    """
    Compute pending changes: diff current state vs HEAD snapshot.

    Returns a summary dict with:
      - diff_summary.templates_{added,edited,deleted}
      - diff_summary.bom_{added,edited,deleted}
      - impact_preview: list of human-readable change descriptions
      - has_changes: bool
    """
    head = get_project_head(pid)
    now_snap = build_snapshot(pid)

    if not head:
        return {
            'has_changes': bool(now_snap['bom']),
            'diff_summary': {'templates_added': [], 'templates_edited': [], 'templates_deleted': [],
                             'bom_added': now_snap['bom'], 'bom_edited': [], 'bom_deleted': []},
            'impact_preview': [f"New BoM line: {item.get('tag_prefix','?')} × {item.get('qty',1)}"
                               for item in now_snap['bom']],
        }

    head_snap = get_snapshot(head)
    if not head_snap:
        return {'has_changes': False, 'diff_summary': {}, 'impact_preview': []}

    tmpl_diff = _template_diff(head_snap.get('hw_templates', {}), now_snap['hw_templates'])
    bom_d     = _bom_diff(head_snap.get('bom', []), now_snap['bom'])

    has_changes = any([
        tmpl_diff['added'], tmpl_diff['edited'], tmpl_diff['deleted'],
        bom_d['added'], bom_d['edited'], bom_d['deleted'],
    ])

    # Build human-readable preview lines
    preview = []
    for item in bom_d['added']:
        preview.append(f"New BoM line: {item.get('tag_prefix','?')} × {item.get('qty',1)}")
    for item in bom_d['edited']:
        preview.append(f"Changed BoM line {item['id']}: {', '.join(item['fields'])}")
    for iid in bom_d['deleted']:
        preview.append(f"Removed BoM line {iid}")
    for tid in tmpl_diff['added']:
        preview.append(f"New template: {tid}")
    for item in tmpl_diff['edited']:
        preview.append(f"Template {item['id']} changed: {', '.join(item['fields'])}")
    for tid in tmpl_diff['deleted']:
        preview.append(f"Removed template: {tid}")

    return {
        'has_changes': has_changes,
        'diff_summary': {
            'templates_added':   tmpl_diff['added'],
            'templates_edited':  tmpl_diff['edited'],
            'templates_deleted': tmpl_diff['deleted'],
            'bom_added':   bom_d['added'],
            'bom_edited':  bom_d['edited'],
            'bom_deleted': bom_d['deleted'],
        },
        'impact_preview': preview,
    }


# ── 3-way merge ──────────────────────────────────────────────────────────────

def _merge_instance_fields(inst: dict, new_tmpl: dict, merge_mode: str) -> tuple[dict, list]:
    """
    Apply 3-way merge for tracked template fields on one HW instance.

    Returns (updated_inst, conflicts) where conflicts is a list of dicts.
    """
    conflicts = []
    base_snap = inst.get('template_snapshot', {})
    updated   = dict(inst)
    ts_update = dict(base_snap)
    history   = list(inst.get('history', []))
    ts        = _now()

    for field in HW_TRACKED_FIELDS:
        if field not in new_tmpl:
            continue
        base   = base_snap.get(field)
        local  = inst.get(field)
        remote = new_tmpl[field]
        if local == remote or base == remote:
            # converged or no change from template
            ts_update[field] = remote
            continue
        if local == base:
            # clean: apply template change
            updated[field] = remote
            ts_update[field] = remote
            history.append({'commit_id': None, 'ts': ts, 'field': field,
                            'old': base, 'new': remote, 'reason': 'template-edit-applied'})
        else:
            # conflict: local edit diverges from incoming template change
            if merge_mode == 'overwrite':
                updated[field] = remote
                ts_update[field] = remote
                history.append({'commit_id': None, 'ts': ts, 'field': field,
                                'old': local, 'new': remote, 'reason': 'overwrite'})
            else:  # 3way (default): keep local, record conflict
                ts_update[field] = remote  # advance base
                conflicts.append({'instance_id': inst['id'], 'field': field,
                                  'base': base, 'local': local, 'remote': remote,
                                  'merge_mode': merge_mode, 'resolution': 'kept-local'})
                history.append({'commit_id': None, 'ts': ts, 'field': field,
                                'old': local, 'new': local, 'reason': 'conflict-kept-local'})

    updated['template_snapshot'] = ts_update
    updated['history'] = history
    return updated, conflicts


# ── Commit execution ─────────────────────────────────────────────────────────

def record_commit(pid: str, user: str, kind: str, trigger: dict,
                  hw_created: list, hw_updated: list, hw_deleted: list,
                  ne_created: list = None, ne_rematerialized: list = None,
                  conflicts: list = None, notes: str = '') -> str:
    """
    Finalize and save a commit after a generate run.

    hw_created / hw_updated / hw_deleted are lists of instance IDs.
    Returns the new commit ID.
    """
    from hw_logic import get_hw_template, save_hw_instance, get_hw_instance

    head  = get_project_head(pid)
    cid   = _new_cid()
    ts    = _now()

    snap = build_snapshot(pid)
    snap['commit_id'] = cid

    hw_templates = snap['hw_templates']

    # Stamp created instances with commit fields
    for iid in hw_created:
        inst = get_hw_instance(iid)
        if inst and 'created_in_commit' not in inst:
            ts_snap = derive_template_snapshot(inst, hw_templates)
            history = list(inst.get('history', []))
            history.append({'commit_id': cid, 'ts': ts, 'field': None,
                            'old': None, 'new': None, 'reason': 'created'})
            save_hw_instance({
                **inst,
                'created_in_commit':      cid,
                'last_touched_in_commit': cid,
                'template_snapshot':      ts_snap,
                'merge_mode':             None,
                'history':                history,
            })

    # Stamp updated instances
    for iid in hw_updated:
        inst = get_hw_instance(iid)
        if inst:
            history = list(inst.get('history', []))
            # back-fill commit_id in any history entries that are None (from merge)
            for h in history:
                if h.get('commit_id') is None:
                    h['commit_id'] = cid
            save_hw_instance({**inst, 'last_touched_in_commit': cid, 'history': history})

    commit = {
        'id':              cid,
        'project_id':      pid,
        'created_at':      ts,
        'user':            user,
        'kind':            kind,
        'trigger':         trigger,
        'parent_commit':   head,
        'snapshot_key':    _snapshot_key(cid),
        'diff_summary':    {},
        'impact': {
            'hw_created':        hw_created,
            'hw_updated':        hw_updated,
            'hw_deleted':        hw_deleted,
            'ne_created':        ne_created or [],
            'ne_updated':        [],
            'ne_deleted':        [],
            'ne_rematerialized': ne_rematerialized or [],
            'conflicts':         conflicts or [],
        },
        'merge_mode_default': '3way',
        'notes':           notes,
    }

    save_generate_commit(commit)
    save_snapshot(cid, snap)
    db.r.lpush(_commits_list(pid), cid)
    db.r.set(_head_key(pid), cid)
    if not db.r.get(_initial_key(pid)):
        db.r.set(_initial_key(pid), cid)

    return cid


# ── Initial commit migration ─────────────────────────────────────────────────

def create_initial_commit(pid: str) -> str:
    """
    Create a synthetic initial commit for an existing project.
    Idempotent — returns existing cid if already done.
    """
    existing = db.r.get(_initial_key(pid))
    if existing:
        return existing

    from hw_logic import project_instances, save_hw_instance

    cid = _new_cid()
    ts  = _now()

    snap = build_snapshot(pid)
    snap['commit_id'] = cid
    hw_templates = snap['hw_templates']

    for inst in project_instances(pid):
        ts_snap = derive_template_snapshot(inst, hw_templates)
        history = list(inst.get('history', []))
        if not history:
            history.append({'commit_id': cid, 'ts': ts, 'field': None,
                            'old': None, 'new': None, 'reason': 'initial-import'})
        save_hw_instance({
            **inst,
            'created_in_commit':      inst.get('created_in_commit') or cid,
            'last_touched_in_commit': inst.get('last_touched_in_commit') or cid,
            'template_snapshot':      inst.get('template_snapshot') or ts_snap,
            'merge_mode':             inst.get('merge_mode'),
            'history':                history,
        })

    commit = {
        'id':            cid,
        'project_id':    pid,
        'created_at':    ts,
        'user':          'system',
        'kind':          'initial',
        'trigger':       {},
        'parent_commit': None,
        'snapshot_key':  _snapshot_key(cid),
        'diff_summary':  {},
        'impact': {
            'hw_created': [], 'hw_updated': [], 'hw_deleted': [],
            'ne_created': [], 'ne_updated': [], 'ne_deleted': [],
            'ne_rematerialized': [], 'conflicts': [],
        },
        'merge_mode_default': '3way',
        'notes': 'Synthetic initial commit — pre-existing state.',
    }

    save_generate_commit(commit)
    save_snapshot(cid, snap)
    db.r.lpush(_commits_list(pid), cid)
    db.r.set(_head_key(pid), cid)
    db.r.set(_initial_key(pid), cid)

    return cid


# ── Wrapped generate with commit ─────────────────────────────────────────────

def generate_with_commit(pid: str, user: str, kind: str, trigger: dict,
                         work_fn, notes: str = ''):
    """
    Run *work_fn()* and then record a commit.

    work_fn must return a list of newly created HW instance dicts.
    Returns (commit_id, created_instances).
    """
    # Ensure initial commit exists before first real commit
    if not db.r.get(_initial_key(pid)):
        create_initial_commit(pid)

    created = work_fn()
    created_ids = [inst['id'] for inst in created] if created else []

    cid = record_commit(
        pid=pid, user=user, kind=kind, trigger=trigger,
        hw_created=created_ids, hw_updated=[], hw_deleted=[],
        notes=notes,
    )
    return cid, created


# ── Template-change propagation (3-way merge over existing instances) ─────────

def apply_template_changes(pid: str, changed_template_ids: list,
                            merge_mode_default: str = '3way') -> dict:
    """
    For instances whose template is in *changed_template_ids*, run 3-way merge.

    Returns dict with 'updated', 'skipped_frozen', 'conflicts'.
    Called from the commit flow when a template edit is detected in the diff.
    """
    from hw_logic import project_instances, get_hw_template, save_hw_instance

    tmpl_cache = {tid: get_hw_template(tid) for tid in changed_template_ids}
    updated, skipped, conflicts = [], [], []

    for inst in project_instances(pid):
        tid = inst.get('template_id', '')
        if tid not in changed_template_ids:
            continue
        new_tmpl = tmpl_cache.get(tid)
        if not new_tmpl:
            continue

        eff_mode = inst.get('merge_mode') or merge_mode_default
        if eff_mode == 'frozen':
            skipped.append(inst['id'])
            continue

        merged, inst_conflicts = _merge_instance_fields(inst, new_tmpl, eff_mode)
        save_hw_instance(merged)
        updated.append(inst['id'])
        conflicts.extend(inst_conflicts)

    return {'updated': updated, 'skipped_frozen': skipped, 'conflicts': conflicts}
