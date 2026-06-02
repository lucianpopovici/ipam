"""
VRF blueprint — VRF entities scoped per customer.

Routes:
  /customers/<cid>/vrfs                         List VRFs
  /customers/<cid>/vrfs/add                     Create VRF (GET/POST)
  /customers/<cid>/vrfs/<vid>/edit              Edit VRF (GET/POST)
  /customers/<cid>/vrfs/<vid>/delete            Delete VRF (POST)
  /api/customers/<cid>/vrfs                     JSON list (GET)
  /api/vrfs/<vid>/impact                        Impact preview (GET)
"""
import json
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
)
from auth import editor_required
from db import r, new_id

vrf_bp = Blueprint('vrf', __name__, url_prefix='')

VRF_INDEX = 'vrfs:index'


# ── Key helpers ───────────────────────────────────────────────────────────────

def _vrf_key(vid):
    return f'vrf:{vid}'

def _customer_vrfs_key(cid):
    return f'customer:{cid}:vrfs'


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def get_vrf(vid):
    """Return VRF dict or None."""
    raw = r.get(_vrf_key(vid))
    return json.loads(raw) if raw else None


def save_vrf(vrf):
    """Persist a VRF dict; maintain customer and global indices."""
    r.set(_vrf_key(vrf['id']), json.dumps(vrf))
    r.sadd(VRF_INDEX, vrf['id'])
    r.sadd(_customer_vrfs_key(vrf['customer_id']), vrf['id'])
    return vrf


def delete_vrf(vid):
    """Remove a VRF and all its index entries."""
    vrf = get_vrf(vid)
    if not vrf:
        return
    r.srem(VRF_INDEX, vid)
    r.srem(_customer_vrfs_key(vrf['customer_id']), vid)
    r.delete(_vrf_key(vid))


def customer_vrfs(cid):
    """Return all VRFs for a customer, sorted by name."""
    vids = r.smembers(_customer_vrfs_key(cid))
    vrfs = [v for vid in vids if (v := get_vrf(vid))]
    return sorted(vrfs, key=lambda v: v.get('name', ''))


def all_vrfs():
    """Return all VRFs across all customers."""
    return [v for vid in r.smembers(VRF_INDEX) if (v := get_vrf(vid))]


def vrfs_for_project(pid):
    """Return VRFs available for a project (via its customer)."""
    from ipam import get_project  # pylint: disable=import-outside-toplevel
    proj = get_project(pid)
    if not proj or not proj.get('customer_id'):
        return []
    return customer_vrfs(proj['customer_id'])


# ── Impact ────────────────────────────────────────────────────────────────────

def vrf_impact(vid):
    """Return cascade preview for VRF deletion."""
    from ipam import all_networks  # pylint: disable=import-outside-toplevel
    nets = [n for n in all_networks() if n.get('vrf_id') == vid]
    return {
        'label': 'VRF',
        'cascades': [
            {'kind': 'subnet', 'count': len(nets),
             'what': f'{len(nets)} subnet{"s" if len(nets) != 1 else ""} reference this VRF'},
        ],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@vrf_bp.route('/customers/<cid>/vrfs')
def vrfs_list(cid):
    from customer import get_customer  # pylint: disable=import-outside-toplevel
    customer = get_customer(cid)
    if not customer:
        abort(404)
    return render_template('vrf/vrfs_list.html', customer=customer,
                           vrfs=customer_vrfs(cid))


@vrf_bp.route('/customers/<cid>/vrfs/add', methods=['GET', 'POST'])
@editor_required
def add_vrf(cid):
    from customer import get_customer  # pylint: disable=import-outside-toplevel
    customer = get_customer(cid)
    if not customer:
        abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('VRF name is required.', 'warning')
            return render_template('vrf/vrf_form.html', customer=customer,
                                   vrf=None, form_values=request.form)
        rt_import = [x.strip() for x in request.form.get('rt_import', '').split(',') if x.strip()]
        rt_export = [x.strip() for x in request.form.get('rt_export', '').split(',') if x.strip()]
        vrf = {
            'id':          f'vrf-{new_id()}',
            'customer_id': cid,
            'name':        name,
            'description': request.form.get('description', ''),
            'rd':          request.form.get('rd', '').strip(),
            'rt_import':   rt_import,
            'rt_export':   rt_export,
            'site_pool_overrides': {},
        }
        save_vrf(vrf)
        flash(f'VRF {name!r} created.', 'success')
        return redirect(url_for('vrf.vrfs_list', cid=cid))

    return render_template('vrf/vrf_form.html', customer=customer,
                           vrf=None, form_values={})


@vrf_bp.route('/customers/<cid>/vrfs/<vid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_vrf(cid, vid):
    from customer import get_customer  # pylint: disable=import-outside-toplevel
    customer = get_customer(cid)
    vrf = get_vrf(vid)
    if not customer or not vrf or vrf.get('customer_id') != cid:
        abort(404)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('VRF name is required.', 'warning')
            return render_template('vrf/vrf_form.html', customer=customer,
                                   vrf=vrf, form_values=request.form)
        rt_import = [x.strip() for x in request.form.get('rt_import', '').split(',') if x.strip()]
        rt_export = [x.strip() for x in request.form.get('rt_export', '').split(',') if x.strip()]
        vrf['name']        = name
        vrf['description'] = request.form.get('description', '')
        vrf['rd']          = request.form.get('rd', '').strip()
        vrf['rt_import']   = rt_import
        vrf['rt_export']   = rt_export
        save_vrf(vrf)
        flash(f'VRF {name!r} updated.', 'success')
        return redirect(url_for('vrf.vrfs_list', cid=cid))

    return render_template('vrf/vrf_form.html', customer=customer,
                           vrf=vrf, form_values=vrf)


@vrf_bp.route('/customers/<cid>/vrfs/<vid>/delete', methods=['POST'])
@editor_required
def delete_vrf_route(cid, vid):
    from customer import get_customer  # pylint: disable=import-outside-toplevel
    customer = get_customer(cid)
    vrf = get_vrf(vid)
    if not customer or not vrf or vrf.get('customer_id') != cid:
        abort(404)
    impact = vrf_impact(vid)
    if any(c['count'] > 0 for c in impact.get('cascades', [])):
        flash('Cannot delete VRF: subnets still reference it.', 'danger')
        return redirect(url_for('vrf.vrfs_list', cid=cid))
    delete_vrf(vid)
    flash(f'VRF {vrf["name"]!r} deleted.', 'info')
    return redirect(url_for('vrf.vrfs_list', cid=cid))


# ── API ───────────────────────────────────────────────────────────────────────

@vrf_bp.route('/api/customers/<cid>/vrfs')
def api_customer_vrfs(cid):
    return jsonify(customer_vrfs(cid))


@vrf_bp.route('/api/vrfs/<vid>/impact')
def api_vrf_impact(vid):
    vrf = get_vrf(vid)
    if not vrf:
        abort(404)
    return jsonify(vrf_impact(vid))
