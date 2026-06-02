"""DHCP scope export blueprint.

Generates ISC dhcpd.conf and Kea JSON configurations from IPAM subnet records.
Subnets opt in via dhcp_enabled=True; options are stored in dhcp_options.
"""
import io
import ipaddress
import json
import re
import tarfile

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response,
)

import db
from auth import editor_required
from ipam import (
    new_id, all_networks, get_network, save_network,
    net_ips_key, get_ip,
)

dhcp_bp = Blueprint('dhcp', __name__, url_prefix='')
r = db.r

# ── MAC validation ──────────────────────────────────────────────────────────────

_MAC_RE = re.compile(r'^([0-9a-f]{2}[:\-]){5}[0-9a-f]{2}$', re.IGNORECASE)


def normalize_mac(mac: str) -> str | None:
    """Return lowercase-colon MAC (aa:bb:cc:11:22:33) or None if invalid."""
    m = mac.strip().lower().replace('-', ':')
    return m if _MAC_RE.match(m) else None


# ── DHCP options helpers ────────────────────────────────────────────────────────

def _default_options() -> dict:
    return {
        'gateway':             '',
        'domain_name':         '',
        'domain_name_servers': [],
        'ntp_servers':         [],
        'interface_mtu':       0,
        'lease_default':       3600,
        'lease_max':           7200,
        'passthrough':         '',
    }


def _parse_options_form(form) -> dict:
    def _split(v):
        return [s.strip() for s in v.replace(',', '\n').splitlines() if s.strip()]

    return {
        'gateway':             form.get('gateway', '').strip(),
        'domain_name':         form.get('domain_name', '').strip(),
        'domain_name_servers': _split(form.get('domain_name_servers', '')),
        'ntp_servers':         _split(form.get('ntp_servers', '')),
        'interface_mtu':       int(form.get('interface_mtu', 0) or 0),
        'lease_default':       int(form.get('lease_default', 3600) or 3600),
        'lease_max':           int(form.get('lease_max', 7200) or 7200),
        'passthrough':         form.get('passthrough', '').strip(),
    }


# ── Scope building ──────────────────────────────────────────────────────────────

def _collapse_ranges(ip_strings: list) -> list:
    """Collapse a list of IP strings into contiguous (start, end) pairs."""
    if not ip_strings:
        return []
    sorted_ips = sorted(ip_strings, key=lambda x: int(ipaddress.ip_address(x)))
    ranges, start, prev = [], sorted_ips[0], int(ipaddress.ip_address(sorted_ips[0]))
    for ip in sorted_ips[1:]:
        curr = int(ipaddress.ip_address(ip))
        if curr != prev + 1:
            ranges.append((start, str(ipaddress.ip_address(prev))))
            start = ip
        prev = curr
    ranges.append((start, str(ipaddress.ip_address(prev))))
    return ranges


def build_dhcp_scope(net: dict) -> dict | None:
    """Build the DHCP scope IR for a single subnet. Returns None if not applicable."""
    if not net.get('dhcp_enabled'):
        return None

    opts = {**_default_options(), **(net.get('dhcp_options') or {})}

    # Collect all IP records for this subnet
    pool_ips, static_hosts, all_ips = [], [], {}
    for ip_str in r.smembers(net_ips_key(net['id'])):
        addr = get_ip(ip_str)
        if not addr:
            continue
        all_ips[ip_str] = addr
        status = addr.get('status', 'allocated')
        if status == 'dhcp':
            pool_ips.append(ip_str)
        elif status == 'allocated' and addr.get('mac_address'):
            static_hosts.append({
                'hostname': addr.get('hostname') or ip_str,
                'mac':      addr['mac_address'],
                'ip':       ip_str,
            })

    # Gateway: explicit option wins; fall back to first reserved IP
    gateway = opts.get('gateway') or ''
    if not gateway:
        reserved_ips = sorted(
            [ip for ip, a in all_ips.items() if a.get('status') == 'reserved'],
            key=lambda x: int(ipaddress.ip_address(x)),
        )
        gateway = reserved_ips[0] if reserved_ips else ''

    net_obj = ipaddress.ip_network(net['cidr'], strict=False)
    return {
        'net_id':       net['id'],
        'cidr':         net['cidr'],
        'network':      str(net_obj.network_address),
        'netmask':      str(net_obj.netmask),
        'prefix_len':   net_obj.prefixlen,
        'gateway':      gateway,
        'pool_ranges':  _collapse_ranges(pool_ips),
        'static_hosts': sorted(static_hosts, key=lambda h: int(ipaddress.ip_address(h['ip']))),
        'options':      opts,
        'description':  net.get('description', ''),
        'name':         net.get('name', net['cidr']),
        'version':      net_obj.version,
    }


def all_dhcp_scopes() -> list:
    """Return built scopes for all dhcp-enabled subnets."""
    scopes = []
    for net in all_networks():
        scope = build_dhcp_scope(net)
        if scope:
            scopes.append(scope)
    return sorted(scopes, key=lambda s: ipaddress.ip_network(s['cidr']))


# ── ISC dhcpd.conf serializer ───────────────────────────────────────────────────

def emit_isc_conf(scopes: list) -> str:
    """Emit a dhcpd.conf block for a list of scopes."""
    lines = [
        '# Generated by IPAM — do not edit by hand',
        'default-lease-time 3600;',
        'max-lease-time     7200;',
        '',
    ]
    for s in scopes:
        opts = s['options']
        indent = '  '
        lines.append(f'subnet {s["network"]} netmask {s["netmask"]} {{')
        if s['description']:
            lines.append(f'{indent}# {s["description"]}')
        if s['gateway']:
            lines.append(f'{indent}option routers {s["gateway"]};')
        if opts.get('domain_name'):
            lines.append(f'{indent}option domain-name "{opts["domain_name"]}";')
        if opts.get('domain_name_servers'):
            lines.append(f'{indent}option domain-name-servers {", ".join(opts["domain_name_servers"])};')
        if opts.get('ntp_servers'):
            lines.append(f'{indent}option ntp-servers {", ".join(opts["ntp_servers"])};')
        if opts.get('interface_mtu'):
            lines.append(f'{indent}option interface-mtu {opts["interface_mtu"]};')
        lines.append(f'{indent}default-lease-time {opts.get("lease_default", 3600)};')
        lines.append(f'{indent}max-lease-time {opts.get("lease_max", 7200)};')
        if s['pool_ranges']:
            lines.append(f'{indent}pool {{')
            for start, end in s['pool_ranges']:
                lines.append(f'{indent}  range {start} {end};')
            lines.append(f'{indent}}}')
        for host in s['static_hosts']:
            safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '-', host['hostname'])
            lines += [
                f'{indent}host {safe_name} {{',
                f'{indent}  hardware ethernet {host["mac"]};',
                f'{indent}  fixed-address    {host["ip"]};',
                f'{indent}}}',
            ]
        if opts.get('passthrough'):
            for line in opts['passthrough'].splitlines():
                lines.append(f'{indent}{line}')
        lines += ['}', '']
    return '\n'.join(lines)


# ── Kea JSON serializer ─────────────────────────────────────────────────────────

def emit_kea_json(scopes: list) -> dict:
    """Emit a Kea DHCP4 config dict."""
    subnet4 = []
    for s in scopes:
        opts  = s['options']
        entry = {
            'subnet':       s['cidr'],
            'option-data': [],
            'pools':        [],
            'reservations': [],
        }
        if s['gateway']:
            entry['option-data'].append({'name': 'routers', 'data': s['gateway']})
        if opts.get('domain_name'):
            entry['option-data'].append({'name': 'domain-name', 'data': opts['domain_name']})
        if opts.get('domain_name_servers'):
            entry['option-data'].append(
                {'name': 'domain-name-servers', 'data': ', '.join(opts['domain_name_servers'])}
            )
        if opts.get('ntp_servers'):
            entry['option-data'].append(
                {'name': 'ntp-servers', 'data': ', '.join(opts['ntp_servers'])}
            )
        if opts.get('interface_mtu'):
            entry['option-data'].append(
                {'name': 'interface-mtu', 'data': str(opts['interface_mtu'])}
            )
        entry['valid-lifetime']    = opts.get('lease_default', 3600)
        entry['max-valid-lifetime'] = opts.get('lease_max', 7200)

        for start, end in s['pool_ranges']:
            entry['pools'].append({'pool': f'{start} - {end}'})
        for host in s['static_hosts']:
            entry['reservations'].append({
                'hw-address': host['mac'],
                'ip-address': host['ip'],
                'hostname':   host['hostname'],
            })
        subnet4.append(entry)

    return {'Dhcp4': {'subnet4': subnet4}}


# ── Routes ──────────────────────────────────────────────────────────────────────

@dhcp_bp.route('/dhcp')
def dhcp_list():
    """Fleet view of all DHCP-enabled subnets."""
    scopes = all_dhcp_scopes()
    return render_template('dhcp/scope_list.html', scopes=scopes)


@dhcp_bp.route('/dhcp/scopes/<net_id>')
def scope_detail(net_id):
    """Per-subnet DHCP preview."""
    net = get_network(net_id)
    if not net:
        flash('Subnet not found.', 'danger')
        return redirect(url_for('dhcp.dhcp_list'))
    scope = build_dhcp_scope(net) if net.get('dhcp_enabled') else None
    isc   = emit_isc_conf([scope]) if scope else ''
    kea   = json.dumps(emit_kea_json([scope]), indent=2) if scope else ''
    return render_template('dhcp/scope_detail.html', net=net, scope=scope,
                           isc=isc, kea=kea)


@dhcp_bp.route('/dhcp/scopes/<net_id>/toggle', methods=['POST'])
@editor_required
def toggle_dhcp(net_id):
    net = get_network(net_id)
    if not net:
        flash('Subnet not found.', 'danger')
        return redirect(url_for('dhcp.dhcp_list'))
    net['dhcp_enabled'] = not net.get('dhcp_enabled', False)
    save_network(net)
    state = 'enabled' if net['dhcp_enabled'] else 'disabled'
    flash(f'DHCP {state} for {net["cidr"]}.', 'success')
    return redirect(request.referrer or url_for('dhcp.scope_detail', net_id=net_id))


@dhcp_bp.route('/dhcp/scopes/<net_id>/options', methods=['GET', 'POST'])
@editor_required
def edit_dhcp_options(net_id):
    net = get_network(net_id)
    if not net:
        flash('Subnet not found.', 'danger')
        return redirect(url_for('dhcp.dhcp_list'))
    if request.method == 'POST':
        net['dhcp_enabled'] = bool(request.form.get('dhcp_enabled'))
        net['dhcp_options'] = _parse_options_form(request.form)
        save_network(net)
        flash(f'DHCP options saved for {net["cidr"]}.', 'success')
        return redirect(url_for('dhcp.scope_detail', net_id=net_id))
    opts = {**_default_options(), **(net.get('dhcp_options') or {})}
    return render_template('dhcp/options_form.html', net=net, opts=opts)


@dhcp_bp.route('/dhcp/export.conf')
def export_isc_conf():
    """Export ISC dhcpd.conf for all DHCP-enabled subnets."""
    scopes = all_dhcp_scopes()
    content = emit_isc_conf(scopes)
    return Response(
        content,
        mimetype='text/plain',
        headers={'Content-Disposition': 'attachment; filename="dhcpd.conf"'},
    )


@dhcp_bp.route('/dhcp/export.json')
def export_kea_json():
    """Export Kea DHCP4 JSON config for all DHCP-enabled subnets."""
    scopes = all_dhcp_scopes()
    content = json.dumps(emit_kea_json(scopes), indent=2)
    return Response(
        content,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="kea-dhcp4.json"'},
    )


@dhcp_bp.route('/dhcp/export/bundle.tar.gz')
def export_bundle():
    """Bundle: one dhcpd.conf per site + one kea-dhcp4.json, all in a tarball."""
    from ipam import get_network as _gn  # local import to avoid circular at module level
    scopes     = all_dhcp_scopes()
    buf        = io.BytesIO()

    # Group by site_id (fall back to '_unsited')
    by_site: dict = {}
    for scope in scopes:
        net     = _gn(scope['net_id']) or {}
        site_id = net.get('site_id') or '_unsited'
        by_site.setdefault(site_id, []).append(scope)

    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for site_id, site_scopes in sorted(by_site.items()):
            fname = f'{site_id}/dhcpd.conf'
            data  = emit_isc_conf(site_scopes).encode()
            info  = tarfile.TarInfo(name=fname)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        # Global Kea JSON
        kea_data = json.dumps(emit_kea_json(scopes), indent=2).encode()
        kea_info = tarfile.TarInfo(name='kea-dhcp4.json')
        kea_info.size = len(kea_data)
        tf.addfile(kea_info, io.BytesIO(kea_data))

    buf.seek(0)
    return Response(
        buf.read(),
        mimetype='application/gzip',
        headers={'Content-Disposition': 'attachment; filename="dhcp-bundle.tar.gz"'},
    )
