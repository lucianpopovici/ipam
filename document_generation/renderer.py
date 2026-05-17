"""
Stage 3: Render Jinja2 templates in a sandboxed environment.

Sandboxing is mandatory — customer templates are attacker-equivalent code.
"""
import ipaddress
import threading
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import FileSystemLoader, TemplateNotFound, TemplateSyntaxError

# Thread-local accumulator for `required` filter calls
_missing = threading.local()


def _get_missing_list():
    if not hasattr(_missing, 'fields'):
        _missing.fields = []
    return _missing.fields


def _reset_missing():
    _missing.fields = []


def _required_filter(value, field_name=''):
    """Mark a missing value with a visible MISSING marker."""
    if value is None or value == '' or value == [] or value == {}:
        marker = field_name or 'value'
        _get_missing_list().append(marker)
        return f'<span class="missing">MISSING: {marker}</span>'
    return value


def _ipnet_filter(value):
    """Format a CIDR string as a network object summary."""
    try:
        net = ipaddress.ip_network(value, strict=False)
        return str(net)
    except (ValueError, TypeError):
        return value or ''


def _cidr_range_filter(value):
    """Return first–last host range string for a CIDR."""
    try:
        net = ipaddress.ip_network(value, strict=False)
        hosts = list(net.hosts())
        if hosts:
            return f'{hosts[0]}–{hosts[-1]}'
        return str(net.network_address)
    except (ValueError, TypeError):
        return value or ''


def build_jinja_env(search_paths):
    """
    Build a sandboxed Jinja2 environment with custom filters.
    search_paths is a list of filesystem directories, tried in order.
    """
    env = SandboxedEnvironment(
        loader=FileSystemLoader(search_paths),
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters['required']    = _required_filter
    env.filters['ipnet']       = _ipnet_filter
    env.filters['cidr_range']  = _cidr_range_filter
    return env


def render_template_to_string(template_name, context, search_paths):
    """
    Render a Jinja2 template to a string inside a sandboxed environment.

    Returns (html_string, missing_fields_list).
    Raises TemplateSyntaxError or TemplateNotFound on template errors.
    """
    _reset_missing()
    env = build_jinja_env(search_paths)
    try:
        tmpl = env.get_template(template_name)
    except TemplateNotFound:
        raise FileNotFoundError(
            f"GEN_TEMPLATE_NOT_FOUND: {template_name!r} not found in {search_paths}"
        )
    except TemplateSyntaxError as exc:
        raise SyntaxError(
            f"GEN_TEMPLATE_SYNTAX: {template_name}:{exc.lineno}: {exc.message}"
        ) from exc

    try:
        result = tmpl.render(**context)
    except Exception as exc:
        raise RuntimeError(
            f"GEN_TEMPLATE_RUNTIME: {type(exc).__name__}: {exc}"
        ) from exc

    missing = list(_get_missing_list())
    _reset_missing()
    return result, missing
