"""
Stage 3b: Render per-device config files and zip them into a bundle.
"""
import io
import zipfile
from .renderer import render_template_to_string


def render_config_bundle(context, search_paths):
    """
    For each NE instance with render_config=True (default), render its config
    template and collect into a ZIP.

    Returns (zip_bytes, missing_fields_list).
    """
    ne_types_by_id = {nt['id']: nt for nt in context.get('ne_types', [])}
    all_missing = []

    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for ne_inst in context.get('ne_instances', []):
            if not ne_inst.get('render_config', True):
                continue

            ne_type = ne_types_by_id.get(ne_inst.get('ne_type_id', ''), {})
            tmpl_name = ne_type.get('config_template')
            if not tmpl_name:
                continue

            config_tmpl = f'configs/{tmpl_name}'
            try:
                text, missing = render_template_to_string(
                    config_tmpl,
                    {'ne': ne_inst, 'ne_type': ne_type, **context},
                    search_paths,
                )
            except FileNotFoundError:
                text = f'# Config template {config_tmpl!r} not found\n'
                missing = []

            all_missing.extend(missing)
            filename = f'{ne_inst.get("name", ne_inst.get("id", "unknown"))}.cfg'
            zf.writestr(filename, text)

    return out.getvalue(), all_missing
