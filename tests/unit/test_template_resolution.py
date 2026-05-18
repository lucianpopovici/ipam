"""Unit tests for document_generation.resolver — template path resolution."""
import os
import sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _make_context(ts_id=None, project_overrides=None):
    return {
        'context_schema_version': 1,
        'project': {'id': 'pid', 'name': 'P', 'template_overrides': project_overrides or {}},
        'customer': {'id': 'cid', 'template_set_id': ts_id},
    }


@pytest.mark.unit
def test_default_template_found(tmp_path):
    """Falls back to default set when no customer template set is configured."""
    from document_generation import resolver
    # Override paths to tmp_path
    default_dir = tmp_path / 'default'
    default_dir.mkdir()
    (default_dir / 'design.html').write_text('<html>default</html>')

    original = resolver.DEFAULT_SET_PATH
    resolver.DEFAULT_SET_PATH = str(default_dir)
    try:
        ctx = _make_context()
        path = resolver.resolve_template_path('design.html', ctx)
        assert path.endswith('design.html')
    finally:
        resolver.DEFAULT_SET_PATH = original


@pytest.mark.unit
def test_customer_template_overrides_default(tmp_path):
    """Customer template set takes priority over default."""
    from document_generation import resolver

    ts_root = tmp_path / 'sets'
    ts_root.mkdir()
    customer_dir = ts_root / 'ts-abc'
    customer_dir.mkdir()
    (customer_dir / 'design.html').write_text('<html>customer</html>')
    default_dir = ts_root / 'default'
    default_dir.mkdir()
    (default_dir / 'design.html').write_text('<html>default</html>')

    original_root = resolver.TEMPLATE_SETS_ROOT
    original_default = resolver.DEFAULT_SET_PATH
    resolver.TEMPLATE_SETS_ROOT = str(ts_root)
    resolver.DEFAULT_SET_PATH   = str(default_dir)
    try:
        ctx = _make_context(ts_id='ts-abc')
        path = resolver.resolve_template_path('design.html', ctx)
        assert 'ts-abc' in path
    finally:
        resolver.TEMPLATE_SETS_ROOT = original_root
        resolver.DEFAULT_SET_PATH   = original_default


@pytest.mark.unit
def test_project_override_beats_customer_template(tmp_path):
    """Project-level override wins over customer template set."""
    from document_generation import resolver

    override_file = tmp_path / 'override-design.html'
    override_file.write_text('<html>override</html>')

    ctx = _make_context(ts_id='ts-abc',
                        project_overrides={'design.html': str(override_file)})
    path = resolver.resolve_template_path('design.html', ctx)
    assert str(override_file) == path


@pytest.mark.unit
def test_missing_template_raises(tmp_path):
    """FileNotFoundError raised when template not found anywhere."""
    from document_generation import resolver

    original_root = resolver.TEMPLATE_SETS_ROOT
    original_default = resolver.DEFAULT_SET_PATH
    resolver.TEMPLATE_SETS_ROOT = str(tmp_path / 'empty-sets')
    resolver.DEFAULT_SET_PATH   = str(tmp_path / 'empty-default')
    try:
        ctx = _make_context()
        with pytest.raises(FileNotFoundError, match='GEN_TEMPLATE_NOT_FOUND'):
            resolver.resolve_template_path('design.html', ctx)
    finally:
        resolver.TEMPLATE_SETS_ROOT = original_root
        resolver.DEFAULT_SET_PATH   = original_default


@pytest.mark.unit
def test_schema_compatibility_ok():
    """Returns None when template set schema range covers context version."""
    from document_generation import resolver
    ctx = {'context_schema_version': 1, 'customer': {'template_set_id': None}}
    assert resolver.check_schema_compatibility(ctx) is None
