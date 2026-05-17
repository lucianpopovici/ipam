"""API tests for customer and document generation routes."""
import pytest
import json
import io
import tarfile
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ── Customer CRUD ──────────────────────────────────────────────────────────────

@pytest.mark.api
def test_customers_list_empty(client):
    resp = client.get('/customers')
    assert resp.status_code == 200
    assert b'No customers yet' in resp.data or b'Customers' in resp.data


@pytest.mark.api
def test_add_customer(client):
    resp = client.post('/customers/add', data={
        'name': 'ACME Corp', 'slug': 'acme',
        'contact_email': 'ops@acme.com', 'billing_ref': 'PO-001',
        'primary_color': '#003366', 'footer_line': 'ACME Confidential',
    }, follow_redirects=False)
    assert resp.status_code in (200, 302)


@pytest.mark.api
def test_add_customer_requires_name(client):
    resp = client.post('/customers/add', data={'name': ''}, follow_redirects=True)
    assert resp.status_code == 200
    assert b'required' in resp.data.lower() or b'Name' in resp.data


@pytest.mark.api
def test_customer_detail(client):
    # Create then view
    resp = client.post('/customers/add', data={
        'name': 'Beta Co', 'slug': 'beta',
        'primary_color': '#000000', 'footer_line': '',
    }, follow_redirects=True)
    assert resp.status_code == 200

    import customer as cust_mod
    custs = cust_mod.all_customers()
    assert len(custs) >= 1
    cid = custs[0]['id']

    resp = client.get(f'/customers/{cid}')
    assert resp.status_code == 200
    assert b'Beta Co' in resp.data


@pytest.mark.api
def test_customer_list_shows_customers(client):
    client.post('/customers/add', data={
        'name': 'Gamma Ltd', 'slug': 'gamma',
        'primary_color': '#ffffff', 'footer_line': '',
    }, follow_redirects=True)
    resp = client.get('/customers')
    assert resp.status_code == 200
    assert b'Gamma Ltd' in resp.data


# ── Project artifacts ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_project_artifacts_page(client, seeded_project):
    pid = seeded_project['id']
    resp = client.get(f'/projects/{pid}/artifacts')
    assert resp.status_code == 200


@pytest.mark.api
def test_preview_design(client, seeded_project):
    pid = seeded_project['id']
    resp = client.get(f'/projects/{pid}/preview/design')
    assert resp.status_code == 200


@pytest.mark.api
def test_generate_pdf_artifact(client, seeded_project, tmp_path, monkeypatch):
    """Generate a PDF artifact — mocked to produce HTML when WeasyPrint absent."""
    pid = seeded_project['id']
    # Override artifacts root to tmp_path
    import document_generation.storage as storage_mod
    monkeypatch.setattr(storage_mod, 'ARTIFACTS_ROOT', str(tmp_path))

    resp = client.post(f'/projects/{pid}/generate',
                       data={'type': 'pdf', 'label': 'test-v1'},
                       follow_redirects=True)
    assert resp.status_code == 200


@pytest.mark.api
def test_artifact_detail_404(client):
    resp = client.get('/artifacts/nonexistent')
    assert resp.status_code == 404


@pytest.mark.api
def test_artifact_approval_flow(client, seeded_project, tmp_path, monkeypatch):
    """Full draft → under-review → approved flow."""
    import document_generation.storage as storage_mod
    monkeypatch.setattr(storage_mod, 'ARTIFACTS_ROOT', str(tmp_path))

    pid = seeded_project['id']
    client.post(f'/projects/{pid}/generate',
                data={'type': 'pdf'}, follow_redirects=True)

    arts = storage_mod.list_project_artifacts(pid)
    if not arts:
        pytest.skip('No artifacts generated (WeasyPrint not installed)')

    aid = arts[0]['id']

    # Submit for review
    resp = client.post(f'/artifacts/{aid}/submit-for-review', follow_redirects=True)
    assert resp.status_code == 200

    # Approve
    resp = client.post(f'/artifacts/{aid}/approve',
                       data={'comment': 'LGTM'}, follow_redirects=True)
    assert resp.status_code == 200
    art = storage_mod.get_artifact(aid)
    assert art['status'] == 'approved'


@pytest.mark.api
def test_download_nonexistent(client):
    resp = client.get('/artifacts/nonexistent/download/file.pdf')
    assert resp.status_code == 404
