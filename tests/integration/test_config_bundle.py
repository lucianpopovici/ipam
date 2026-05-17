"""Integration tests for the config bundle pipeline."""
import pytest
import os
import json
import zipfile
import io
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


@pytest.mark.api
def test_config_bundle_empty_project(client, seeded_project, tmp_path, monkeypatch):
    """Empty project produces an empty zip (no NE instances)."""
    import document_generation.storage as storage_mod
    monkeypatch.setattr(storage_mod, 'ARTIFACTS_ROOT', str(tmp_path))

    from document_generation.context import build_context
    from document_generation.bundle import render_config_bundle
    from document_generation.resolver import get_template_search_paths

    pid = seeded_project['id']
    ctx = build_context(pid)
    search_paths = get_template_search_paths(ctx)
    zip_bytes, missing = render_config_bundle(ctx, search_paths)

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert len(zf.namelist()) == 0  # no NE instances → empty bundle


@pytest.mark.api
def test_config_bundle_excludes_render_config_false(client, seeded_project, tmp_path, monkeypatch):
    """NE instance with render_config=False is excluded from the bundle."""
    import db
    from document_generation.context import build_context
    from document_generation.bundle import render_config_bundle
    from document_generation.resolver import get_template_search_paths
    import document_generation.storage as storage_mod
    monkeypatch.setattr(storage_mod, 'ARTIFACTS_ROOT', str(tmp_path))

    pid = seeded_project['id']

    # Insert an NE instance with render_config=False
    ne_inst = {
        'id': 'ne-test-1', 'name': 'router-1',
        'ne_type_id': 'nt-1', 'render_config': False,
        'iface_bindings': [],
    }
    db.r.set('ne:instance:ne-test-1', json.dumps(ne_inst))
    db.r.sadd(f'ne:instances:project:{pid}', 'ne-test-1')

    ctx = build_context(pid)
    search_paths = get_template_search_paths(ctx)
    zip_bytes, _ = render_config_bundle(ctx, search_paths)

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert 'router-1.cfg' not in zf.namelist()


@pytest.mark.api
def test_artifact_persistence(client, seeded_project, tmp_path, monkeypatch):
    """Saving an artifact writes files to disk and records metadata in Redis."""
    import document_generation.storage as storage_mod
    monkeypatch.setattr(storage_mod, 'ARTIFACTS_ROOT', str(tmp_path))

    from document_generation.context import build_context
    from document_generation.storage import save_artifact, get_artifact, list_project_artifacts

    pid = seeded_project['id']
    ctx = build_context(pid)
    file_bytes = b'hello pdf world'
    art = save_artifact(pid, 'pdf', 'test.pdf', file_bytes, ctx,
                        label='test', generated_by='pytest')

    assert art['id']
    assert art['project_id'] == pid
    assert art['size_bytes'] == len(file_bytes)
    assert art['status'] == 'draft'

    # File exists on disk
    art_path = os.path.join(tmp_path, art['id'], 'test.pdf')
    assert os.path.isfile(art_path)

    # Context snapshot exists
    ctx_path = os.path.join(tmp_path, art['id'], 'context.json')
    assert os.path.isfile(ctx_path)

    # Loadable from Redis
    loaded = get_artifact(art['id'])
    assert loaded['id'] == art['id']

    # Listed for project
    arts = list_project_artifacts(pid)
    assert any(a['id'] == art['id'] for a in arts)
