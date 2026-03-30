"""
VMware External Connector — allocates IP addresses from designated IPAM subnets
to VMware environments via a REST API, and provides a management UI.

Redis keys introduced:
  vmware:subnets              — set of network IDs enabled for VMware allocation
  vmware:alloc:{ip}           — JSON blob with VMware-specific metadata per IP
  vmware:net:{net_id}:ips     — set of IPs allocated via this connector per subnet
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
import ipaddress, json
from datetime import datetime, timezone
from db import r
from ipam import (
    get_network, save_ip, get_ip,
    net_ips_key, ip_key,
    all_projects, project_networks,
)

vmware_bp = Blueprint('vmware', __name__, url_prefix='')

# ══════════════════════════════════════════════════════════════════════════════
# Key helpers
# ══════════════════════════════════════════════════════════════════════════════

VMWARE_SUBNETS_KEY = 'vmware:subnets'

def _alloc_key(ip):       return f'vmware:alloc:{ip}'
def _net_ips_key(net_id): return f'vmware:net:{net_id}:ips'

# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def enable_network(net_id: str):
    """Mark a subnet as available for VMware IP allocation."""
    r.sadd(VMWARE_SUBNETS_KEY, net_id)


def disable_network(net_id: str):
    """Remove a subnet from VMware allocation pool."""
    r.srem(VMWARE_SUBNETS_KEY, net_id)


def is_enabled(net_id: str) -> bool:
    return bool(r.sismember(VMWARE_SUBNETS_KEY, net_id))


def enabled_network_ids() -> set:
    return r.smembers(VMWARE_SUBNETS_KEY)


def enabled_networks() -> list:
    """Return network dicts for all VMware-enabled subnets, sorted by CIDR."""
    nets = []
    for nid in enabled_network_ids():
        net = get_network(nid)
        if net:
            nets.append(net)
    return sorted(nets, key=lambda n: ipaddress.ip_network(n['cidr']))


def save_vmware_alloc(alloc: dict):
    r.set(_alloc_key(alloc['ip']), json.dumps(alloc))
    r.sadd(_net_ips_key(alloc['network_id']), alloc['ip'])


def get_vmware_alloc(ip: str):
    raw = r.get(_alloc_key(ip))
    return json.loads(raw) if raw else None


def delete_vmware_alloc(ip: str, net_id: str):
    r.delete(_alloc_key(ip))
    r.srem(_net_ips_key(net_id), ip)


def network_vmware_ips(net_id: str) -> list:
    """Return all VMware allocation records for a subnet, sorted by IP."""
    allocs = []
    for ip in r.smembers(_net_ips_key(net_id)):
        alloc = get_vmware_alloc(ip)
        if alloc:
            allocs.append(alloc)
    return sorted(allocs, key=lambda a: ipaddress.ip_address(a['ip']))


def _find_next_available(net_id: str):
    """Return the first host address that is not yet allocated or pending."""
    net = get_network(net_id)
    if not net:
        return None
    used    = r.smembers(net_ips_key(net_id))
    pending = {s['ip'] for s in net.get('pending_slots', [])}
    for host in ipaddress.ip_network(net['cidr'], strict=False).hosts():
        ip_str = str(host)
        if ip_str not in used and ip_str not in pending:
            return ip_str
    return None


def allocate_ip(net_id: str, vm_name: str = '', datacenter: str = '', cluster: str = '') -> dict:
    """
    Allocate the next available IP from a VMware-enabled subnet.

    Saves the IP into the standard IPAM data model (status='allocated') so that
    utilisation stats remain accurate, and stores VMware-specific metadata
    separately under the vmware:alloc: namespace.

    Raises ValueError when the network is not found, not enabled, or exhausted.
    """
    if not is_enabled(net_id):
        raise ValueError(f'Network {net_id} is not enabled for VMware allocation')
    net = get_network(net_id)
    if not net:
        raise ValueError(f'Network {net_id} not found')
    ip_str = _find_next_available(net_id)
    if not ip_str:
        raise ValueError(f'No available addresses in {net["cidr"]}')
    save_ip({
        'ip':          ip_str,
        'hostname':    vm_name,
        'description': f'VMware: {vm_name}' if vm_name else 'VMware allocation',
        'status':      'allocated',
        'network_id':  net_id,
    })
    alloc = {
        'ip':           ip_str,
        'network_id':   net_id,
        'cidr':         net['cidr'],
        'vm_name':      vm_name,
        'datacenter':   datacenter,
        'cluster':      cluster,
        'allocated_at': datetime.now(timezone.utc).isoformat(),
    }
    save_vmware_alloc(alloc)
    return alloc


def release_ip(ip_str: str) -> bool:
    """
    Release a VMware-allocated IP back to the pool.

    Removes the IP from both the standard IPAM store and the VMware metadata
    store.  Returns False when the IP has no VMware allocation record.
    """
    alloc = get_vmware_alloc(ip_str)
    if not alloc:
        return False
    net_id = alloc['network_id']
    r.delete(ip_key(ip_str))
    r.srem(net_ips_key(net_id), ip_str)
    delete_vmware_alloc(ip_str, net_id)
    return True

# ══════════════════════════════════════════════════════════════════════════════
# Routes — UI
# ══════════════════════════════════════════════════════════════════════════════

@vmware_bp.route('/vmware')
def vmware_index():
    """Dashboard: list all subnets grouped by project, showing VMware status."""
    rows = []
    for proj in sorted(all_projects(), key=lambda p: p['name']):
        for net in project_networks(proj['id']):
            net['project_name']    = proj['name']
            net['project_id']      = proj['id']
            net['vmware_enabled']  = is_enabled(net['id'])
            net['vmware_allocs']   = network_vmware_ips(net['id'])
            rows.append(net)
    rows.sort(key=lambda n: ipaddress.ip_network(n['cidr']))
    return render_template('vmware/index.html', networks=rows)


@vmware_bp.route('/vmware/networks/<net_id>/enable', methods=['POST'])
def enable_network_route(net_id):
    net = get_network(net_id)
    if not net:
        abort(404)
    enable_network(net_id)
    flash(f'Subnet {net["cidr"]} enabled for VMware allocation.', 'success')
    return redirect(url_for('vmware.vmware_index'))


@vmware_bp.route('/vmware/networks/<net_id>/disable', methods=['POST'])
def disable_network_route(net_id):
    net = get_network(net_id)
    if not net:
        abort(404)
    disable_network(net_id)
    flash(f'Subnet {net["cidr"]} disabled for VMware allocation.', 'info')
    return redirect(url_for('vmware.vmware_index'))

# ══════════════════════════════════════════════════════════════════════════════
# Routes — REST API
# ══════════════════════════════════════════════════════════════════════════════

@vmware_bp.route('/api/vmware/networks')
def api_list_networks():
    """
    GET /api/vmware/networks

    Returns all subnets currently enabled for VMware allocation.
    """
    nets = []
    for nid in enabled_network_ids():
        net = get_network(nid)
        if not net:
            continue
        n_obj = ipaddress.ip_network(net['cidr'], strict=False)
        nets.append({
            'id':            nid,
            'cidr':          net['cidr'],
            'name':          net.get('name', ''),
            'total_hosts':   max(n_obj.num_addresses - 2, 1),
            'allocated':     r.scard(net_ips_key(nid)),
            'vmware_allocs': r.scard(_net_ips_key(nid)),
        })
    nets.sort(key=lambda n: ipaddress.ip_network(n['cidr']))
    return jsonify({'networks': nets})


@vmware_bp.route('/api/vmware/networks/<net_id>/allocate', methods=['POST'])
def api_allocate(net_id):
    """
    POST /api/vmware/networks/<net_id>/allocate

    Request body (JSON, all fields optional):
      { "vm_name": "...", "datacenter": "...", "cluster": "..." }

    Returns the allocation record with the assigned IP (HTTP 201) or an error.
    """
    body       = request.get_json(silent=True) or {}
    vm_name    = body.get('vm_name', '')
    datacenter = body.get('datacenter', '')
    cluster    = body.get('cluster', '')
    try:
        alloc = allocate_ip(net_id, vm_name=vm_name, datacenter=datacenter, cluster=cluster)
        return jsonify(alloc), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@vmware_bp.route('/api/vmware/ip/<path:ip_str>/release', methods=['DELETE'])
def api_release(ip_str):
    """
    DELETE /api/vmware/ip/<ip>/release

    Releases the IP and removes its VMware allocation record.
    Returns 404 when the IP has no VMware allocation.
    """
    if not release_ip(ip_str):
        return jsonify({'error': f'{ip_str} is not a VMware allocation'}), 404
    return jsonify({'released': ip_str})


@vmware_bp.route('/api/vmware/networks/<net_id>/ips')
def api_network_ips(net_id):
    """
    GET /api/vmware/networks/<net_id>/ips

    Lists all IPs allocated via VMware in the given subnet.
    """
    net = get_network(net_id)
    if not net:
        abort(404)
    if not is_enabled(net_id):
        return jsonify({'error': f'Network {net_id} is not enabled for VMware'}), 400
    return jsonify({
        'network_id':  net_id,
        'cidr':        net['cidr'],
        'allocations': network_vmware_ips(net_id),
    })
