"""
Lightweight async job tracking — Phase 4 of UX improvements.

Jobs are stored in Redis with a 1-hour TTL:
  job:{id} → JSON {status, progress, total, message, result}

Pattern:
  1.  POST endpoint calls create_job(), starts a thread via run_job(), returns {job_id}.
  2.  Worker calls update_job() to report progress.
  3.  Client polls GET /api/jobs/<id> every 500 ms until status != 'running'.

For form-submit callers (e.g. tests), endpoints check request.is_json and
fall through to the existing synchronous code path.
"""
import json
import threading
import db

_JOB_TTL = 3600  # 1 hour


def _key(job_id: str) -> str:
    return f'job:{job_id}'


def create_job() -> str:
    """Create a new job record in Redis and return its ID."""
    from db import new_id
    job_id = new_id()
    db.r.setex(_key(job_id), _JOB_TTL, json.dumps({
        'status': 'running', 'progress': 0, 'total': 0,
        'message': 'Starting…', 'result': None,
    }))
    return job_id


def update_job(job_id: str, done: int, total: int, *,
               message: str = '', status: str = 'running', result=None):
    """Update job progress and status in Redis."""
    db.r.setex(_key(job_id), _JOB_TTL, json.dumps({
        'status': status, 'progress': done, 'total': total,
        'message': message, 'result': result,
    }))


def get_job(job_id: str) -> dict | None:
    """Retrieve job data from Redis."""
    raw = db.r.get(_key(job_id))
    return json.loads(raw) if raw else None


def run_job(app, job_id: str, fn, *args, **kwargs):
    """
    Run fn(job_id, *args, **kwargs) in a daemon thread with the Flask app context.
    Catches exceptions and marks the job as errored.
    """
    def _wrapper():
        with app.app_context():
            try:
                fn(job_id, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                update_job(job_id, 0, 0, message=str(exc), status='error')

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
