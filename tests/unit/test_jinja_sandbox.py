"""Unit tests verifying Jinja2 sandbox prevents malicious template execution."""
import pytest
import sys
import os
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _render(template_source, ctx=None):
    """Helper: write template to temp dir and render it."""
    from document_generation.renderer import render_template_to_string
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpl_path = os.path.join(tmpdir, 'test.html')
        with open(tmpl_path, 'w') as f:
            f.write(template_source)
        result, missing = render_template_to_string('test.html', ctx or {}, [tmpdir])
        return result, missing


@pytest.mark.unit
def test_basic_render():
    """Normal rendering works."""
    result, _ = _render('hello {{ name }}', {'name': 'world'})
    assert 'world' in result


@pytest.mark.unit
def test_sandbox_blocks_class_mro():
    """Accessing __class__.__mro__ is blocked by sandbox."""
    from jinja2.exceptions import SecurityError
    with pytest.raises((SecurityError, Exception)):
        _render("{{ ''.__class__.__mro__ }}")


@pytest.mark.unit
def test_sandbox_blocks_builtins_access():
    """Accessing __builtins__ is blocked."""
    from jinja2.exceptions import SecurityError
    with pytest.raises((SecurityError, Exception)):
        _render("{{ ().__class__.__bases__[0].__subclasses__() }}")


@pytest.mark.unit
def test_required_filter_marks_missing():
    """required filter returns MISSING marker for empty values."""
    result, missing = _render("{{ val | required('myfield') }}", {'val': ''})
    assert 'MISSING' in result
    assert 'myfield' in result
    assert 'myfield' in missing


@pytest.mark.unit
def test_required_filter_passes_through_value():
    """required filter returns the value unchanged when present."""
    result, missing = _render("{{ val | required('myfield') }}", {'val': '10.0.0.1'})
    assert '10.0.0.1' in result
    assert not missing


@pytest.mark.unit
def test_ipnet_filter():
    """ipnet filter formats CIDR correctly."""
    result, _ = _render("{{ cidr | ipnet }}", {'cidr': '10.0.1.0/24'})
    assert '10.0.1.0/24' in result


@pytest.mark.unit
def test_syntax_error_raises_descriptive():
    """Template with Jinja syntax error raises SyntaxError with GEN_TEMPLATE_SYNTAX prefix."""
    with pytest.raises(SyntaxError, match='GEN_TEMPLATE_SYNTAX'):
        _render("{% for x in %}")


@pytest.mark.unit
def test_missing_template_raises():
    """Rendering a non-existent template raises FileNotFoundError."""
    from document_generation.renderer import render_template_to_string
    with pytest.raises(FileNotFoundError, match='GEN_TEMPLATE_NOT_FOUND'):
        render_template_to_string('nonexistent.html', {}, ['/tmp'])
