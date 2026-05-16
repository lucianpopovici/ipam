"""
Stage 5-6: Package artifacts and persist them to filesystem + Redis metadata.

Files live at /var/ipam/artifacts/{aid}/ (or IPAM_ARTIFACTS_ROOT override).
Redis holds only metadata; binary files are never stored in Redis.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import db

ARTIFACTS_ROOT = os.environ.get(
    'IPAM_ARTIFACTS_ROOT',
    os.path.join(os.path.dirname(__file__), '..', 'var', 'ipam', 'artifacts'),
)


# ── Redis key helpers ─────────────────────────────────────────────────────────

def _artifact_key(aid):
    return f'artifact:{aid}'

def _project_artifacts_key(pid):
    return f'project:{pid}:artifacts'


# ── Persist ───────────────────────────────────────────────────────────────────

def save_artifact(pid, artifact_type, filename, file_bytes,
                  context_snapshot, template_set_id=None,
                  template_set_version=None, label='',
                  supersedes_aid=None, generated_by='system',
                  approvals=None):
    """
    Write artifact file + context snapshot to disk, store metadata in Redis.

    Returns the artifact dict.
    """
    aid = db.new_id()
    art_dir = os.path.join(ARTIFACTS_ROOT, aid)
    os.makedirs(art_dir, exist_ok=True)

    # Write context snapshot
    ctx_path = os.path.join(art_dir, 'context.json')
    with open(ctx_path, 'w') as f:
        json.dump(context_snapshot, f, indent=2)
    os.chmod(ctx_path, 0o444)

    # Write artifact file
    art_path = os.path.join(art_dir, filename)
    with open(art_path, 'wb') as f:
        f.write(file_bytes)
    os.chmod(art_path, 0o444)

    sha256 = hashlib.sha256(file_bytes).hexdigest()

    customer_id = context_snapshot.get('project', {}).get('customer_id', '')

    artifact = {
        'id':                    aid,
        'project_id':            pid,
        'customer_id':           customer_id,
        'type':                  artifact_type,
        'filename':              filename,
        'size_bytes':            len(file_bytes),
        'sha256':                sha256,
        'generated_at':          datetime.now(timezone.utc).isoformat(),
        'generated_by':          generated_by,
        'context_snapshot_path': ctx_path,
        'artifact_path':         art_path,
        'template_set_id':       template_set_id or '',
        'template_set_version':  template_set_version or 0,
        'approvals':             approvals or [],
        'label':                 label,
        'supersedes_artifact_id': supersedes_aid or '',
        'status':                'draft',
    }

    # Write metadata JSON for offline reconstruction
    meta_path = os.path.join(art_dir, 'metadata.json')
    with open(meta_path, 'w') as f:
        json.dump(artifact, f, indent=2)

    # Store in Redis
    db.r.set(_artifact_key(aid), json.dumps(artifact))
    # Prepend to project artifact list (newest first)
    db.r.lpush(_project_artifacts_key(pid), aid)

    return artifact


def get_artifact(aid):
    """Load artifact metadata from Redis."""
    raw = db.r.get(_artifact_key(aid))
    return json.loads(raw) if raw else None


def list_project_artifacts(pid):
    """Return list of artifact dicts for a project, newest first."""
    aids = db.r.lrange(_project_artifacts_key(pid), 0, -1)
    artifacts = []
    for aid in aids:
        art = get_artifact(aid)
        if art:
            artifacts.append(art)
    return artifacts


def update_artifact_status(aid, status):
    """Update artifact status field (draft / under-review / approved / rejected)."""
    art = get_artifact(aid)
    if not art:
        return None
    art['status'] = status
    db.r.set(_artifact_key(aid), json.dumps(art))
    return art


def add_artifact_approval(aid, user_id, user_name, decision, comment=''):
    """Append an approval record to the artifact."""
    art = get_artifact(aid)
    if not art:
        return None
    from datetime import datetime, timezone
    art.setdefault('approvals', []).append({
        'user_id':   user_id,
        'name':      user_name,
        'decision':  decision,
        'comment':   comment,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    })
    if decision == 'approved':
        art['status'] = 'approved'
    elif decision == 'rejected':
        art['status'] = 'draft'
    db.r.set(_artifact_key(aid), json.dumps(art))
    return art


def load_context_snapshot(aid):
    """Load the frozen context JSON for an artifact."""
    art = get_artifact(aid)
    if not art:
        return None
    ctx_path = art.get('context_snapshot_path', '')
    if not os.path.isfile(ctx_path):
        return None
    with open(ctx_path) as f:
        return json.load(f)


def stream_artifact_file(aid, filename):
    """
    Return the file bytes for an artifact's named file.
    filename must be one of: the artifact filename, 'context.json', 'configs.zip'.
    """
    art = get_artifact(aid)
    if not art:
        return None
    art_dir = os.path.join(ARTIFACTS_ROOT, aid)
    path = os.path.join(art_dir, os.path.basename(filename))
    if not os.path.isfile(path):
        return None
    with open(path, 'rb') as f:
        return f.read()
