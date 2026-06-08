"""
Rule materializer for NE-HW binding.

The auto-rule bind mode stores a rule predicate on an iface binding.
This module resolves the rule against current project inventory and
returns a flat ports[] list for iface_bindings[iface_id]['ports'].
"""
import re as _re


# All valid values — kept here so callers can import without hw_logic
_ALL_PORT_TYPES = ('data', 'mgmt', 'power', 'console', 'usb')
_ALL_CATEGORIES = ('server', 'switch', 'router', 'pdu', 'rack', 'cable', 'other')


def materialize_binding(rule: dict, pid: str,
                        excluded_ports: set | None = None) -> list:
    """
    Evaluate *rule* against all HW instances in project *pid*.

    Returns ports[] list:
        [{'hw_instance_id': ..., 'port_id': ..., 'role': 'primary',
          'bucket': ['rack:R-01', ...]}, ...]

    *excluded_ports* — set of (hw_instance_id, port_id) already explicitly
    bound to another iface; those ports are skipped silently.
    """
    from hw_logic import project_instances, get_hw_instance  # avoid circular

    excluded  = excluded_ports or set()
    port_types = set(rule.get('port_types') or _ALL_PORT_TYPES)
    categories = set(rule.get('categories') or _ALL_CATEGORIES)
    name_re    = _re.compile(rule.get('name_regex') or '.*', _re.IGNORECASE)
    group_by   = rule.get('group_by') or []
    port_labels = set(rule.get('port_labels') or [])   # match ports carrying any

    buckets: dict = {}
    for inst in project_instances(pid):
        tmpl = inst.get('template')
        if not tmpl or tmpl.get('category') not in categories:
            continue
        for port in tmpl.get('ports', []):
            if port.get('port_type') not in port_types:
                continue
            if port_labels and not (set(port.get('labels') or []) & port_labels):
                continue
            count = int(port.get('count', 1))
            for n in range(count):
                pname = port['name'] if count == 1 else f"{port['name']}-{n}"
                if not name_re.match(pname):
                    continue
                if (inst['id'], port['id']) in excluded:
                    continue
                key = _bucket_key(inst, tmpl, group_by, get_hw_instance)
                buckets.setdefault(key, []).append(
                    (inst['id'], port['id'], pname)
                )

    ports = []
    for bucket_key, matches in buckets.items():
        labels = bucket_synthetic_labels(bucket_key)
        for iid, port_id, _pname in matches:
            ports.append({
                'hw_instance_id': iid,
                'port_id':        port_id,
                'role':           'primary',
                'bucket':         labels,
            })
    return ports


def preview_binding(rule: dict, pid: str,
                    excluded_ports: set | None = None) -> dict:
    """
    Return summary for the Workflow D preview pane without committing.

    Returns::
        {
          'total_ports': int,
          'buckets': [{'label': 'rack:R-01', 'count': 12, 'warn': False}, ...]
        }
    The 'warn' flag is True when the bucket includes unracked ports.
    """
    from hw_logic import project_instances, get_hw_instance

    excluded   = excluded_ports or set()
    port_types = set(rule.get('port_types') or _ALL_PORT_TYPES)
    categories = set(rule.get('categories') or _ALL_CATEGORIES)
    name_re    = _re.compile(rule.get('name_regex') or '.*', _re.IGNORECASE)
    group_by   = rule.get('group_by') or []
    port_labels = set(rule.get('port_labels') or [])   # match ports carrying any

    bucket_counts: dict[str, int]  = {}
    bucket_unracked: dict[str, bool] = {}

    for inst in project_instances(pid):
        tmpl = inst.get('template')
        if not tmpl or tmpl.get('category') not in categories:
            continue
        for port in tmpl.get('ports', []):
            if port.get('port_type') not in port_types:
                continue
            if port_labels and not (set(port.get('labels') or []) & port_labels):
                continue
            count = int(port.get('count', 1))
            for n in range(count):
                pname = port['name'] if count == 1 else f"{port['name']}-{n}"
                if not name_re.match(pname):
                    continue
                if (inst['id'], port['id']) in excluded:
                    continue
                bkey = _bucket_key(inst, tmpl, group_by, get_hw_instance)
                labels = bucket_synthetic_labels(bkey)
                label_str = ', '.join(labels) if labels else 'all'
                bucket_counts[label_str] = bucket_counts.get(label_str, 0) + 1
                if any('unracked' in lbl for lbl in labels):
                    bucket_unracked[label_str] = True

    buckets = [
        {'label': lbl, 'count': cnt, 'warn': bucket_unracked.get(lbl, False)}
        for lbl, cnt in sorted(bucket_counts.items())
    ]
    return {'total_ports': sum(b['count'] for b in buckets), 'buckets': buckets}


def bucket_synthetic_labels(bucket_key: tuple) -> list:
    """Return sorted 'kind:value' strings for a bucket key tuple."""
    return sorted(f'{k}:{v}' for k, v in bucket_key)


# ── Private ───────────────────────────────────────────────────────────────────

def _bucket_key(inst: dict, tmpl: dict, group_by: list,
                get_hw_instance_fn) -> tuple:
    parts = []
    loc   = inst.get('location', {})
    for g in group_by:
        if g == 'rack':
            rack_id = loc.get('rack_id')
            if rack_id:
                rack_inst = get_hw_instance_fn(rack_id)
                if rack_inst and rack_inst.get('labels'):
                    val = rack_inst['labels'][0]
                elif rack_inst:
                    val = rack_inst.get('asset_tag', rack_id)
                else:
                    val = rack_id
            else:
                val = 'unracked'
            parts.append(('rack', val))
        elif g == 'hw_template':
            parts.append(('hw-tmpl', tmpl.get('name', 'unknown')))
        elif g == 'category':
            parts.append(('category', tmpl.get('category', 'other')))
    return tuple(parts) if parts else (('all', 'all'),)
