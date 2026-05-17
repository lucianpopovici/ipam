"""
Stage 2: Resolve which template file to use for a given render pass.

Three-layer lookup: project override → customer template set → default.
First found wins.
"""
import os
import json

TEMPLATE_SETS_ROOT = os.environ.get('IPAM_TEMPLATE_SETS_ROOT',
                                    os.path.join(os.path.dirname(__file__), '..', 'var', 'ipam', 'template-sets'))
DEFAULT_SET_PATH = os.path.join(TEMPLATE_SETS_ROOT, 'default')


def resolve_template_path(template_name, context):
    """
    Return the filesystem path to the Jinja2 template file.

    Search order:
      1. Project-level override (project.template_overrides[template_name])
      2. Customer template set (template_set_id from customer record)
      3. Default template set

    Raises FileNotFoundError (GEN_TEMPLATE_NOT_FOUND) if nothing found.
    """
    project = context.get('project', {})
    customer = context.get('customer', {})

    # 1. Project override
    overrides = project.get('template_overrides', {})
    if template_name in overrides:
        candidate = overrides[template_name]
        if os.path.isfile(candidate):
            return candidate

    # 2. Customer template set
    ts_id = customer.get('template_set_id')
    if ts_id:
        customer_path = os.path.join(TEMPLATE_SETS_ROOT, ts_id, template_name)
        if os.path.isfile(customer_path):
            return customer_path

    # 3. Default
    default_path = os.path.join(DEFAULT_SET_PATH, template_name)
    if os.path.isfile(default_path):
        return default_path

    raise FileNotFoundError(
        f"GEN_TEMPLATE_NOT_FOUND: No template found for {template_name!r}. "
        f"Checked customer set {ts_id!r} and default."
    )


def get_template_search_paths(context):
    """Return Jinja2 loader search paths in priority order."""
    paths = []
    customer = context.get('customer', {})
    ts_id = customer.get('template_set_id')
    if ts_id:
        paths.append(os.path.join(TEMPLATE_SETS_ROOT, ts_id))
    paths.append(DEFAULT_SET_PATH)
    return paths


def get_template_set_metadata(ts_id):
    """Load template set metadata JSON, or return empty dict."""
    meta_path = os.path.join(TEMPLATE_SETS_ROOT, ts_id, 'metadata.json')
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def check_schema_compatibility(context):
    """
    Verify that the template set supports context_schema_version.
    Returns None if OK, or an error string.
    """
    customer = context.get('customer', {})
    ts_id = customer.get('template_set_id')
    if not ts_id:
        return None  # using defaults — always compatible

    meta = get_template_set_metadata(ts_id)
    schema_min = meta.get('context_schema_min', 1)
    schema_max = meta.get('context_schema_max', 9999)
    schema_ver = context.get('context_schema_version', 1)

    if not (schema_min <= schema_ver <= schema_max):
        return (
            f"GEN_SCHEMA_INCOMPATIBLE: template set {ts_id!r} supports "
            f"schema versions {schema_min}–{schema_max}, "
            f"but context is version {schema_ver}."
        )
    return None
