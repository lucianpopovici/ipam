"""
API tests for the checks blueprint.
Uses Flask test client + fakeredis.
"""
import json
import pytest
import fakeredis


@pytest.fixture()
def client(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    import db, checks_logic, checks, hw_logic, ipam, ne, vmware, auth, customer
    for mod in (db, checks_logic, checks, hw_logic, ipam, ne, vmware, auth, customer):
        monkeypatch.setattr(mod, 'r', fr)

    from app import app
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['_user_id'] = 'admin'
        yield c, fr


@pytest.fixture()
def project(client):
    c, fr = client
    c.post('/projects/add', data={'name': 'TestProj', 'supernet': '10.0.0.0/8'},
           follow_redirects=True)
    pid = fr.smembers('projects:index').pop()
    return pid


@pytest.fixture()
def check_template(client, project):
    c, fr = client
    from db import new_id
    from checks_logic import save_check_template
    tmpl = {
        'id': new_id(), 'name': 'Iface up', 'description': '',
        'phase': 'post', 'attached_to': 'project', 'attachment_filter': {},
        'action_description': 'Verify the project is operational.',
        'expected_result': 'All systems go.',
        'vendor_hints': {}, 'severity': 'standard', 'tags': [],
        'scope': 'global', 'project_id': '',
    }
    save_check_template(tmpl)
    return tmpl


# ── Check template admin ───────────────────────────────────────────────────────

@pytest.mark.api
def test_list_check_templates_empty(client):
    c, _ = client
    r = c.get('/admin/checks/templates')
    assert r.status_code == 200
    assert b'No check templates yet' in r.data


@pytest.mark.api
def test_add_check_template(client):
    c, fr = client
    r = c.post('/admin/checks/templates/add', data={
        'name': 'My Check', 'phase': 'post', 'attached_to': 'project',
        'severity': 'standard', 'scope': 'global',
        'action_description': 'Do the thing.',
        'expected_result': 'It is done.',
        'vendor_hints_json': '{}', 'attachment_filter_json': '{}',
        'description': '', 'tags': '',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b'My Check' in r.data


@pytest.mark.api
def test_add_check_template_missing_name(client):
    c, _ = client
    r = c.post('/admin/checks/templates/add', data={
        'name': '', 'phase': 'post', 'attached_to': 'project',
        'severity': 'standard', 'scope': 'global',
        'vendor_hints_json': '{}', 'attachment_filter_json': '{}',
    })
    assert r.status_code == 200
    assert b'is-invalid' in r.data


@pytest.mark.api
def test_edit_check_template(client, check_template):
    c, _ = client
    ctid = check_template['id']
    r = c.post(f'/admin/checks/templates/{ctid}/edit', data={
        'name': 'Updated Name', 'phase': 'pre', 'attached_to': 'ne_type',
        'severity': 'critical', 'scope': 'global',
        'action_description': 'Updated action.',
        'expected_result': 'Updated expected.',
        'vendor_hints_json': '{}', 'attachment_filter_json': '{}',
        'description': '', 'tags': 'updated',
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b'Updated Name' in r.data


@pytest.mark.api
def test_delete_check_template(client, check_template):
    c, fr = client
    ctid = check_template['id']
    r = c.post(f'/admin/checks/templates/{ctid}/delete', follow_redirects=True)
    assert r.status_code == 200
    assert fr.scard('check_templates:index') == 0


@pytest.mark.api
def test_preview_check_template(client, check_template, project):
    c, _ = client
    ctid = check_template['id']
    r = c.post(f'/admin/checks/templates/{ctid}/preview',
               data=json.dumps({'project_id': project}),
               content_type='application/json')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert 'subjects' in data


# ── Checklist list ─────────────────────────────────────────────────────────────

@pytest.mark.api
def test_list_checklists_empty(client, project):
    c, _ = client
    r = c.get(f'/projects/{project}/checklists')
    assert r.status_code == 200
    assert b'No checklists yet' in r.data


@pytest.mark.api
def test_create_checklist(client, project, check_template):
    c, _ = client
    r = c.post(f'/projects/{project}/checklists/create',
               data={'phase': 'post', 'deployment_label': 'Q3 wave 1'},
               follow_redirects=True)
    assert r.status_code == 200
    # Redirected to checklist detail
    assert b'Q3 wave 1' in r.data
    assert b'draft' in r.data


@pytest.mark.api
def test_create_checklist_missing_label(client, project):
    c, _ = client
    r = c.post(f'/projects/{project}/checklists/create',
               data={'phase': 'post', 'deployment_label': ''},
               follow_redirects=True)
    assert r.status_code == 200
    assert b'Deployment label is required' in r.data


# ── Checklist detail and updates ──────────────────────────────────────────────

def _make_checklist(fr, project):
    from db import new_id
    from checks_logic import save_checklist
    cid = new_id()
    cl = {
        'id': cid, 'project_id': project, 'phase': 'post',
        'deployment_label': 'Test wave', 'status': 'in-progress',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'generated_by': 'admin',
        'checks': [{
            'id': 'chk-1', 'check_template_id': 'ct', 'subject': {},
            'action_text': 'Verify it', 'expected_text': 'OK',
            'vendor_hint_text': '', 'severity': 'standard',
            'status': 'pending', 'notes': '',
            'reported_by_external': '', 'recorded_by': '', 'recorded_at': None,
        }],
        'signed_off_at': None, 'signed_off_by': None,
        'artifact_id': None, 'supersedes_checklist_id': None,
    }
    save_checklist(cl)
    fr.lpush(f'project:{project}:checklists', cid)
    return cl


@pytest.mark.api
def test_checklist_detail(client, project):
    c, fr = client
    cl = _make_checklist(fr, project)
    r = c.get(f'/checklists/{cl["id"]}')
    assert r.status_code == 200
    assert b'Test wave' in r.data
    assert b'Verify it' in r.data


@pytest.mark.api
def test_update_checklist_records_status(client, project):
    c, fr = client
    cl = _make_checklist(fr, project)
    r = c.post(f'/checklists/{cl["id"]}/update',
               data={'status_chk-1': 'pass', 'notes_chk-1': 'Looks good',
                     'reported_by_chk-1': ''},
               follow_redirects=True)
    assert r.status_code == 200
    from checks_logic import get_checklist
    updated = get_checklist(cl['id'])
    assert updated['checks'][0]['status'] == 'pass'
    assert updated['checks'][0]['notes'] == 'Looks good'


@pytest.mark.api
def test_update_promotes_to_completed_when_all_done(client, project):
    c, fr = client
    cl = _make_checklist(fr, project)
    c.post(f'/checklists/{cl["id"]}/update',
           data={'status_chk-1': 'pass', 'notes_chk-1': ''},
           follow_redirects=True)
    from checks_logic import get_checklist
    updated = get_checklist(cl['id'])
    assert updated['status'] == 'completed'


@pytest.mark.api
def test_transition_draft_to_inprogress(client, project):
    c, fr = client
    from checks_logic import save_checklist, get_checklist
    from db import new_id
    cid = new_id()
    cl = {'id': cid, 'project_id': project, 'phase': 'post',
          'deployment_label': 'w', 'status': 'draft', 'generated_at': '',
          'generated_by': '', 'checks': [], 'signed_off_at': None,
          'signed_off_by': None, 'artifact_id': None,
          'supersedes_checklist_id': None}
    save_checklist(cl)
    fr.lpush(f'project:{project}:checklists', cid)

    r = c.post(f'/checklists/{cid}/transition',
               data={'to_status': 'in-progress'}, follow_redirects=True)
    assert r.status_code == 200
    assert get_checklist(cid)['status'] == 'in-progress'


@pytest.mark.api
def test_signoff_requires_completed_status(client, project):
    c, fr = client
    cl = _make_checklist(fr, project)   # status = in-progress
    r = c.post(f'/checklists/{cl["id"]}/signoff', follow_redirects=True)
    assert r.status_code == 200
    from checks_logic import get_checklist
    # Should have flashed an error; status unchanged
    assert get_checklist(cl['id'])['status'] == 'in-progress'


@pytest.mark.api
def test_supersede_creates_new_checklist(client, project, check_template):
    c, fr = client
    # Create an original checklist
    r = c.post(f'/projects/{project}/checklists/create',
               data={'phase': 'post', 'deployment_label': 'original'},
               follow_redirects=True)
    assert r.status_code == 200
    from checks_logic import project_checklists
    orig_cl = project_checklists(project)[0]

    r = c.post(f'/checklists/{orig_cl["id"]}/supersede', follow_redirects=True)
    assert r.status_code == 200
    cls = project_checklists(project)
    assert len(cls) == 2
    new_cl = cls[0]   # newest first
    assert new_cl['supersedes_checklist_id'] == orig_cl['id']


@pytest.mark.api
def test_locked_checklist_rejects_update(client, project):
    c, fr = client
    from checks_logic import save_checklist
    from db import new_id
    cid = new_id()
    cl = {'id': cid, 'project_id': project, 'phase': 'post',
          'deployment_label': 'w', 'status': 'signed-off',
          'generated_at': '', 'generated_by': '', 'checks': [],
          'signed_off_at': '2026-01-01', 'signed_off_by': 'alice',
          'artifact_id': None, 'supersedes_checklist_id': None}
    save_checklist(cl)
    fr.lpush(f'project:{project}:checklists', cid)

    r = c.post(f'/checklists/{cid}/update', data={}, follow_redirects=True)
    assert r.status_code == 200
    assert b'locked' in r.data


@pytest.mark.api
def test_pending_count_api(client, project):
    c, fr = client
    cl = _make_checklist(fr, project)
    r = c.get(f'/api/projects/{project}/checklists/pending-count')
    assert r.status_code == 200
    data = json.loads(r.data)
    assert data['pending'] == 1
    assert data['checklists'] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Generate-PDF route
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.api
def test_generate_pdf_creates_artifact_and_links_checklist(client, project, monkeypatch):
    """
    The generate-pdf route renders the checklist HTML template, saves an
    artifact via save_artifact, and sets checklist.artifact_id.
    WeasyPrint is not required — the route falls back to HTML when it is absent.
    """
    c, fr = client
    cl = _make_checklist(fr, project)

    # Patch html_to_pdf so the test doesn't need a real WeasyPrint installation
    import document_generation.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, 'html_to_pdf',
                        lambda html, **kw: html.encode('utf-8'))

    r = c.post(f'/checklists/{cl["id"]}/generate-pdf', follow_redirects=True)
    assert r.status_code == 200

    # Checklist should now have an artifact_id set
    from checks_logic import get_checklist
    updated = get_checklist(cl['id'])
    assert updated['artifact_id']

    # The artifact should be findable in Redis
    from document_generation.storage import get_artifact
    art = get_artifact(updated['artifact_id'])
    assert art is not None
    assert art['type'] == 'checklist'
    assert art['project_id'] == project


@pytest.mark.api
def test_generate_pdf_falls_back_to_html_when_weasyprint_missing(client, project, monkeypatch):
    """When WeasyPrint raises ImportError the route saves an HTML artifact."""
    c, fr = client
    cl = _make_checklist(fr, project)

    def _raise_import(html, **kw):
        raise ImportError('weasyprint not installed')

    import document_generation.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, 'html_to_pdf', _raise_import)

    r = c.post(f'/checklists/{cl["id"]}/generate-pdf', follow_redirects=True)
    assert r.status_code == 200

    from checks_logic import get_checklist
    updated = get_checklist(cl['id'])
    assert updated['artifact_id']

    from document_generation.storage import get_artifact
    art = get_artifact(updated['artifact_id'])
    assert art['filename'].endswith('.html')


@pytest.mark.api
def test_generate_pdf_produces_different_artifact_per_generation(client, project, monkeypatch):
    """Each call to generate-pdf creates a new artifact; old ones are retained."""
    c, fr = client
    cl = _make_checklist(fr, project)

    import document_generation.pdf as pdf_mod
    monkeypatch.setattr(pdf_mod, 'html_to_pdf',
                        lambda html, **kw: html.encode('utf-8'))

    c.post(f'/checklists/{cl["id"]}/generate-pdf', follow_redirects=True)
    from checks_logic import get_checklist
    aid1 = get_checklist(cl['id'])['artifact_id']

    c.post(f'/checklists/{cl["id"]}/generate-pdf', follow_redirects=True)
    aid2 = get_checklist(cl['id'])['artifact_id']

    assert aid1 != aid2     # new artifact per generation

    # Both artifacts should still exist in Redis
    from document_generation.storage import get_artifact
    assert get_artifact(aid1) is not None
    assert get_artifact(aid2) is not None
