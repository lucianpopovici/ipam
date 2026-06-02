"""DNS zone export blueprint.

Manages forward and reverse DNS zones derived from IPAM IP records,
and exports them as RFC 1035 BIND-format zone files.
"""
import io
import json
import ipaddress
import tarfile
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response,
)

import db
from auth import editor_required
from core.forms import form_errors
from ipam import (
    new_id, all_networks, get_network_labels, net_ips_key, get_ip,
)

dns_bp = Blueprint('dns', __name__, url_prefix='')
r = db.r

# ── Redis keys ──────────────────────────────────────────────────────────────────

ZONE_INDEX = 'dns:zones:index'


def _zone_key(zid: str) -> str:
    return f'dns:zone:{zid}'


# ── Data access ─────────────────────────────────────────────────────────────────

def get_zone(zid: str) -> dict | None:
    raw = r.get(_zone_key(zid))
    return json.loads(raw) if raw else None


def save_zone(zone: dict) -> None:
    r.set(_zone_key(zone['id']), json.dumps(zone))
    r.sadd(ZONE_INDEX, zone['id'])


def delete_zone_record(zid: str) -> None:
    r.delete(_zone_key(zid))
    r.delete(f'{_zone_key(zid)}:serial_date')
    r.srem(ZONE_INDEX, zid)


def all_zones() -> list:
    return sorted(
        [z for z in (get_zone(zid) for zid in r.smembers(ZONE_INDEX)) if z],
        key=lambda z: z['name'],
    )


# ── Scope matching ──────────────────────────────────────────────────────────────

def _label_sets_match(net_labels: list, include_sets: list, exclude_sets: list) -> bool:
    """A network matches if it passes both filters.

    include_sets = [[l1, l2], [l3]] → must contain all of {l1,l2} OR all of {l3}.
    Empty include_sets means "accept everything."
    exclude_sets: if any set fully matches, exclude the network.
    """
    ns = set(net_labels)
    if include_sets:
        non_empty = [ls for ls in include_sets if ls]
        if non_empty and not any(all(l in ns for l in ls) for ls in non_empty):
            return False
    if exclude_sets:
        if any(all(l in ns for l in ls) for ls in exclude_sets if ls):
            return False
    return True


def _ip_in_cidrs(ip_obj, cidr_list: list) -> bool:
    for cidr in cidr_list:
        try:
            if ip_obj in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            pass
    return False


# ── Record collection ───────────────────────────────────────────────────────────

def _collect_ips(zone: dict) -> list:
    """Return list of {ip, hostname} dicts matching the zone's scope."""
    scope      = zone.get('scope', {})
    inc_labels = scope.get('include_label_sets', [])
    exc_labels = scope.get('exclude_label_sets', [])
    inc_cidrs  = scope.get('include_cidrs', [])
    exc_cidrs  = scope.get('exclude_cidrs', [])
    is_reverse = zone.get('kind') == 'reverse'

    results = []
    for net in all_networks():
        net_labels = get_network_labels(net['id'])

        if not is_reverse:
            if not _label_sets_match(net_labels, inc_labels, exc_labels):
                continue

        for ip_str in r.smembers(net_ips_key(net['id'])):
            addr = get_ip(ip_str)
            if not addr or not addr.get('hostname'):
                continue
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if is_reverse:
                if not inc_cidrs or not _ip_in_cidrs(ip_obj, inc_cidrs):
                    continue
                if exc_cidrs and _ip_in_cidrs(ip_obj, exc_cidrs):
                    continue
            else:
                if exc_cidrs and _ip_in_cidrs(ip_obj, exc_cidrs):
                    continue

            results.append({'ip': ip_obj, 'hostname': addr['hostname']})

    return results


# ── Serial computation ──────────────────────────────────────────────────────────

def _resolve_serial(zone: dict, bump: bool = False) -> tuple:
    """Return (serial_int, updated_zone_dict).

    bump=True persists the new serial. bump=False is preview-safe.
    """
    policy = zone.get('serial_policy', 'date_based')
    zone   = dict(zone)

    if policy == 'manual':
        return zone.get('serial_current', 0), zone

    if policy == 'on_export':
        serial = zone.get('serial_current', 0) + (1 if bump else 0)
        if bump:
            zone['serial_current'] = serial
        return serial, zone

    # date_based: YYYYMMDDnn
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    ckey  = f'{_zone_key(zone["id"])}:serial_date'
    if bump:
        stored = r.get(ckey) or ''
        nn = int(stored[8:]) + 1 if stored.startswith(today) else 1
        r.set(ckey, f'{today}{nn:02d}')
    else:
        stored = r.get(ckey) or ''
        nn     = int(stored[8:]) if stored.startswith(today) else 0
    serial = int(f'{today}{nn:02d}')
    zone['serial_current'] = serial
    return serial, zone


# ── Zone file helpers ───────────────────────────────────────────────────────────

def _emit_soa(zone: dict, serial: int) -> str:
    primary = (zone.get('primary_ns') or f'ns1.{zone["name"]}.').rstrip('.') + '.'
    email   = (zone.get('admin_email') or f'hostmaster.{zone["name"]}.').rstrip('.') + '.'
    return (
        f'$ORIGIN {zone["name"]}.\n'
        f'$TTL {zone.get("default_ttl", 300)}\n'
        f'@\t\tIN SOA\t{primary} {email} (\n'
        f'\t\t\t{serial}\t; serial\n'
        f'\t\t\t{zone.get("refresh", 3600)}\t; refresh\n'
        f'\t\t\t{zone.get("retry", 600)}\t; retry\n'
        f'\t\t\t{zone.get("expire", 1209600)}\t; expire\n'
        f'\t\t\t{zone.get("minimum_ttl", 300)} )\t; minimum TTL\n'
    )


def _ptr_absolute(ip_obj) -> str:
    if ip_obj.version == 4:
        return '.'.join(reversed(str(ip_obj).split('.'))) + '.in-addr.arpa'
    expanded = ip_obj.exploded.replace(':', '')
    return '.'.join(reversed(list(expanded))) + '.ip6.arpa'


def _ptr_label(ip_obj, zone_name: str) -> str | None:
    """Return the PTR record label relative to zone_name, or None if not in zone."""
    abs_name   = _ptr_absolute(ip_obj)
    zone_lower = zone_name.lower().rstrip('.')
    abs_lower  = abs_name.lower()
    if abs_lower == zone_lower:
        return '@'
    suffix = '.' + zone_lower
    if abs_lower.endswith(suffix):
        return abs_name[:len(abs_name) - len(suffix)]
    return None


def _strip_zone(fqdn: str, zone_name: str) -> str:
    """Strip zone suffix to get the relative label for a forward record."""
    fqdn_s = fqdn.rstrip('.')
    zone   = zone_name.rstrip('.')
    if fqdn_s.lower().endswith('.' + zone.lower()):
        return fqdn_s[:-(len(zone) + 1)]
    if fqdn_s.lower() == zone.lower():
        return '@'
    return fqdn_s


# ── Zone file emission ──────────────────────────────────────────────────────────

def emit_zone_file(zone: dict, bump_serial: bool = False) -> str:
    """Generate a BIND-format zone file.

    bump_serial=True increments/persists the serial; use on download.
    bump_serial=False computes but does not persist (safe for preview).
    """
    serial, updated = _resolve_serial(zone, bump=bump_serial)
    if bump_serial:
        save_zone(updated)

    ttl   = zone.get('default_ttl', 300)
    lines = [_emit_soa(zone, serial), '']
    for ns in zone.get('nameservers', []):
        ns = ns.rstrip('.') + '.'
        lines.append(f'@\t\tIN NS\t{ns}')
    if zone.get('nameservers'):
        lines.append('')

    ips = sorted(_collect_ips(zone), key=lambda e: e['ip'])

    if zone.get('kind') == 'forward':
        for entry in ips:
            ip_obj  = entry['ip']
            rr_type = 'A' if ip_obj.version == 4 else 'AAAA'
            label   = _strip_zone(entry['hostname'], zone['name'])
            lines.append(f'{label:<30} {ttl:>5} IN {rr_type:<5} {ip_obj}')
    else:
        for entry in ips:
            ip_obj   = entry['ip']
            hostname = entry['hostname'].rstrip('.') + '.'
            label    = _ptr_label(ip_obj, zone['name'])
            if label is None:
                continue
            lines.append(f'{label:<40} {ttl:>5} IN PTR  {hostname}')

    static = zone.get('static_records', [])
    if static:
        lines.append('')
        for rec in static:
            name  = rec.get('name', '@')
            rtype = rec.get('type', 'A')
            value = rec.get('value', '')
            rtl   = rec.get('ttl', ttl)
            lines.append(f'{name:<30} {rtl:>5} IN {rtype:<5} {value}')

    return '\n'.join(lines) + '\n'


def collect_zone_records(zone: dict) -> list:
    """Return zone records as structured dicts (for JSON export and preview)."""
    ttl     = zone.get('default_ttl', 300)
    records = []
    ips     = sorted(_collect_ips(zone), key=lambda e: e['ip'])

    if zone.get('kind') == 'forward':
        for entry in ips:
            ip_obj  = entry['ip']
            rr_type = 'A' if ip_obj.version == 4 else 'AAAA'
            records.append({
                'name':  _strip_zone(entry['hostname'], zone['name']),
                'ttl':   ttl,
                'type':  rr_type,
                'value': str(ip_obj),
            })
    else:
        for entry in ips:
            ip_obj = entry['ip']
            label  = _ptr_label(ip_obj, zone['name'])
            if label is None:
                continue
            records.append({
                'name':  label,
                'ttl':   ttl,
                'type':  'PTR',
                'value': entry['hostname'].rstrip('.') + '.',
            })

    for rec in zone.get('static_records', []):
        records.append({
            'name':  rec.get('name', '@'),
            'ttl':   rec.get('ttl', ttl),
            'type':  rec.get('type', 'A'),
            'value': rec.get('value', ''),
        })

    return records


# ── Form parsing ────────────────────────────────────────────────────────────────

def _parse_zone_form(form, existing: dict | None = None) -> tuple:
    """Parse and validate zone form data. Returns (zone_dict, errors_dict)."""
    name   = form.get('name', '').strip().lower().rstrip('.')
    kind   = form.get('kind', 'forward')
    errors = form_errors(
        ('name', bool(name), 'Zone name is required.'),
        ('kind', kind in ('forward', 'reverse'), 'Kind must be forward or reverse.'),
    )
    if errors:
        return {}, errors

    # Parse nameservers (one per line)
    ns_raw = form.get('nameservers', '')
    nameservers = [s.strip() for s in ns_raw.splitlines() if s.strip()]

    # Parse include_label_sets (one comma-separated set per line)
    def _parse_label_lines(raw):
        sets = []
        for line in raw.splitlines():
            labels = [l.strip() for l in line.split(',') if l.strip()]
            if labels:
                sets.append(labels)
        return sets

    # Parse include_cidrs / exclude_cidrs (one per line)
    def _parse_cidr_lines(raw):
        return [c.strip() for c in raw.splitlines() if c.strip()]

    # Parse static records (JSON textarea)
    static_raw = form.get('static_records', '[]').strip()
    try:
        static_records = json.loads(static_raw) if static_raw else []
        if not isinstance(static_records, list):
            static_records = []
    except json.JSONDecodeError:
        static_records = []

    zone_id = existing['id'] if existing else new_id()
    zone = {
        'id':             zone_id,
        'kind':           kind,
        'name':           name,
        'enabled':        bool(form.get('enabled')),
        'primary_ns':     form.get('primary_ns', '').strip(),
        'admin_email':    form.get('admin_email', '').strip(),
        'refresh':        int(form.get('refresh', 3600) or 3600),
        'retry':          int(form.get('retry', 600) or 600),
        'expire':         int(form.get('expire', 1209600) or 1209600),
        'minimum_ttl':    int(form.get('minimum_ttl', 300) or 300),
        'default_ttl':    int(form.get('default_ttl', 300) or 300),
        'serial_policy':  form.get('serial_policy', 'date_based'),
        'serial_current': int(existing.get('serial_current', 0) if existing else 0),
        'nameservers':    nameservers,
        'scope': {
            'include_label_sets': _parse_label_lines(form.get('include_label_sets', '')),
            'exclude_label_sets': _parse_label_lines(form.get('exclude_label_sets', '')),
            'include_cidrs':      _parse_cidr_lines(form.get('include_cidrs', '')),
            'exclude_cidrs':      _parse_cidr_lines(form.get('exclude_cidrs', '')),
        },
        'static_records': static_records,
    }
    return zone, {}


def _zone_to_form_values(zone: dict) -> dict:
    """Convert a zone dict back to form field values for edit pre-fill."""
    scope = zone.get('scope', {})

    def _label_sets_to_text(sets):
        return '\n'.join(','.join(ls) for ls in sets)

    def _cidrs_to_text(cidrs):
        return '\n'.join(cidrs)

    return {
        'name':               zone.get('name', ''),
        'kind':               zone.get('kind', 'forward'),
        'enabled':            zone.get('enabled', True),
        'primary_ns':         zone.get('primary_ns', ''),
        'admin_email':        zone.get('admin_email', ''),
        'refresh':            zone.get('refresh', 3600),
        'retry':              zone.get('retry', 600),
        'expire':             zone.get('expire', 1209600),
        'minimum_ttl':        zone.get('minimum_ttl', 300),
        'default_ttl':        zone.get('default_ttl', 300),
        'serial_policy':      zone.get('serial_policy', 'date_based'),
        'serial_current':     zone.get('serial_current', 0),
        'nameservers':        '\n'.join(zone.get('nameservers', [])),
        'include_label_sets': _label_sets_to_text(scope.get('include_label_sets', [])),
        'exclude_label_sets': _label_sets_to_text(scope.get('exclude_label_sets', [])),
        'include_cidrs':      _cidrs_to_text(scope.get('include_cidrs', [])),
        'exclude_cidrs':      _cidrs_to_text(scope.get('exclude_cidrs', [])),
        'static_records':     json.dumps(zone.get('static_records', []), indent=2),
    }


# ── Routes ──────────────────────────────────────────────────────────────────────

@dns_bp.route('/dns')
def dns_list():
    zones = all_zones()
    counts = {}
    for z in zones:
        try:
            counts[z['id']] = len(collect_zone_records(z))
        except Exception:  # pylint: disable=broad-except
            counts[z['id']] = 0
    return render_template('dns/zone_list.html', zones=zones, counts=counts)


@dns_bp.route('/dns/zones/add', methods=['GET', 'POST'])
@editor_required
def add_dns_zone():
    if request.method == 'POST':
        zone, errors = _parse_zone_form(request.form)
        if errors:
            return render_template('dns/zone_form.html', errors=errors,
                                   form_values=request.form, zone=None)
        save_zone(zone)
        flash(f'Zone "{zone["name"]}" created.', 'success')
        return redirect(url_for('dns.zone_detail', zid=zone['id']))
    return render_template('dns/zone_form.html', errors={}, form_values={}, zone=None)


@dns_bp.route('/dns/zones/<zid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_dns_zone(zid):
    zone = get_zone(zid)
    if not zone:
        flash('Zone not found.', 'danger')
        return redirect(url_for('dns.dns_list'))
    if request.method == 'POST':
        updated, errors = _parse_zone_form(request.form, existing=zone)
        if errors:
            return render_template('dns/zone_form.html', errors=errors,
                                   form_values=request.form, zone=zone)
        save_zone(updated)
        flash(f'Zone "{updated["name"]}" updated.', 'success')
        return redirect(url_for('dns.zone_detail', zid=zid))
    return render_template('dns/zone_form.html', errors={},
                           form_values=_zone_to_form_values(zone), zone=zone)


@dns_bp.route('/dns/zones/<zid>/delete', methods=['POST'])
@editor_required
def delete_dns_zone(zid):
    zone = get_zone(zid)
    if zone:
        delete_zone_record(zid)
        flash(f'Zone "{zone["name"]}" deleted.', 'success')
    return redirect(url_for('dns.dns_list'))


@dns_bp.route('/dns/zones/<zid>/toggle', methods=['POST'])
@editor_required
def toggle_dns_zone(zid):
    zone = get_zone(zid)
    if zone:
        zone['enabled'] = not zone.get('enabled', True)
        save_zone(zone)
        state = 'enabled' if zone['enabled'] else 'disabled'
        flash(f'Zone "{zone["name"]}" {state}.', 'success')
    return redirect(url_for('dns.dns_list'))


@dns_bp.route('/dns/zones/<zid>')
def zone_detail(zid):
    zone = get_zone(zid)
    if not zone:
        flash('Zone not found.', 'danger')
        return redirect(url_for('dns.dns_list'))
    records  = collect_zone_records(zone)
    preview  = emit_zone_file(zone, bump_serial=False)
    return render_template('dns/zone_detail.html', zone=zone,
                           records=records, preview=preview)


@dns_bp.route('/dns/zones/<zid>/export.zone')
def export_zone_file_route(zid):
    zone = get_zone(zid)
    if not zone:
        return 'Zone not found', 404
    content = emit_zone_file(zone, bump_serial=True)
    filename = f'{zone["name"].rstrip(".")}.zone'
    return Response(
        content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@dns_bp.route('/dns/zones/<zid>/export.json')
def export_zone_json_route(zid):
    zone = get_zone(zid)
    if not zone:
        return jsonify({'error': 'not found'}), 404
    serial, _ = _resolve_serial(zone, bump=False)
    return jsonify({
        'zone':    zone['name'],
        'kind':    zone['kind'],
        'serial':  serial,
        'records': collect_zone_records(zone),
    })


@dns_bp.route('/dns/export.tar.gz')
def export_all_zones_route():
    """Bundle all enabled zones + a named.conf.local stub into a tarball."""
    zones   = [z for z in all_zones() if z.get('enabled')]
    buf     = io.BytesIO()
    conf_lines = []

    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for zone in zones:
            content  = emit_zone_file(zone, bump_serial=True)
            filename = f'{zone["name"].rstrip(".")}.zone'
            data     = content.encode()
            info     = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            conf_lines += [
                f'zone "{zone["name"]}" {{',
                f'    type master;',
                f'    file "/etc/bind/zones/{filename}";',
                f'}};',
                '',
            ]

        named_conf = '\n'.join(conf_lines)
        data2 = named_conf.encode()
        info2 = tarfile.TarInfo(name='named.conf.local')
        info2.size = len(data2)
        tf.addfile(info2, io.BytesIO(data2))

    buf.seek(0)
    return Response(
        buf.read(),
        mimetype='application/gzip',
        headers={'Content-Disposition': 'attachment; filename="dns-zones.tar.gz"'},
    )
