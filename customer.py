"""
Customer blueprint — manages customer entities and their template sets.

Customers own projects and template sets. This is a lightweight entity:
no per-customer auth, no URL routing isolation — just a dropdown on the
project form and an admin page.
"""
import json
import os
import tarfile
import tempfile
import shutil

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort
)
from auth import editor_required
from db import r, new_id

customer_bp = Blueprint('customer', __name__, url_prefix='')

CUSTOMERS_INDEX   = 'customers:index'
TEMPLATE_SETS_ROOT = os.environ.get(
    'IPAM_TEMPLATE_SETS_ROOT',
    os.path.join(os.path.dirname(__file__), 'var', 'ipam', 'template-sets'),
)


# ── Key helpers ───────────────────────────────────────────────────────────────

def _customer_key(cid):
    return f'customer:{cid}'

def _customer_projects_key(cid):
    return f'customer:{cid}:projects'

def _ts_key(tsid):
    return f'template_set:{tsid}'

def _customer_ts_index_key(cid):
    return f'customer:{cid}:template_sets'


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def get_customer(cid):
    """Return customer dict or None."""
    raw = r.get(_customer_key(cid))
    return json.loads(raw) if raw else None


def all_customers():
    """Return sorted list of all customer dicts."""
    cids = r.smembers(CUSTOMERS_INDEX)
    custs = [c for cid in cids if (c := get_customer(cid))]
    return sorted(custs, key=lambda c: c.get('name', ''))


def save_customer(customer):
    """Persist a customer dict and add to index."""
    cid = customer['id']
    r.set(_customer_key(cid), json.dumps(customer))
    r.sadd(CUSTOMERS_INDEX, cid)
    return customer


def get_template_set(tsid):
    """Return template set metadata dict or None."""
    raw = r.get(_ts_key(tsid))
    return json.loads(raw) if raw else None


def customer_template_sets(cid):
    """Return all template sets for a customer, sorted newest first."""
    tsids = r.smembers(_customer_ts_index_key(cid))
    sets = [ts for tsid in tsids if (ts := get_template_set(tsid))]
    return sorted(sets, key=lambda t: t.get('uploaded_at', ''), reverse=True)


# ── Routes ────────────────────────────────────────────────────────────────────

@customer_bp.route('/customers')
def customers_list():
    """List all customers."""
    customers = all_customers()
    return render_template('customers/customers_list.html', customers=customers)


@customer_bp.route('/customers/add', methods=['GET', 'POST'])
@editor_required
def add_customer():
    """Create a new customer."""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip().lower().replace(' ', '-')
        if not name:
            flash('Name is required.', 'warning')
            return render_template('customers/customer_form.html',
                                   form_values=request.form, customer=None)
        cid = f'cust-{new_id()}'
        customer = {
            'id':               cid,
            'name':             name,
            'slug':             slug or name.lower().replace(' ', '-'),
            'primary_contact':  {
                'name':  request.form.get('contact_name', ''),
                'email': request.form.get('contact_email', ''),
                'phone': request.form.get('contact_phone', ''),
            },
            'billing_ref':      request.form.get('billing_ref', ''),
            'template_set_id':  None,
            'branding': {
                'logo_path':    '',
                'primary_color': request.form.get('primary_color', '#003366'),
                'footer_line':  request.form.get('footer_line', ''),
            },
            'default_locale':   request.form.get('default_locale', 'en-GB'),
            'notes':            request.form.get('notes', ''),
            'created_at':       r.time()[0],
        }
        save_customer(customer)
        flash(f'Customer {name!r} created.', 'success')
        return redirect(url_for('customer.customer_detail', cid=cid))

    return render_template('customers/customer_form.html',
                           form_values={}, customer=None)


@customer_bp.route('/customers/<cid>')
def customer_detail(cid):
    """Customer detail page — shows template sets and linked projects."""
    customer = get_customer(cid)
    if not customer:
        abort(404)
    template_sets = customer_template_sets(cid)

    # Linked projects
    proj_ids = r.smembers(_customer_projects_key(cid))
    from ipam import get_project
    projects = [p for pid in proj_ids if (p := get_project(pid))]
    projects.sort(key=lambda p: p.get('name', ''))

    return render_template('customers/customer_detail.html',
                           customer=customer,
                           template_sets=template_sets,
                           projects=projects)


@customer_bp.route('/customers/<cid>/edit', methods=['GET', 'POST'])
@editor_required
def edit_customer(cid):
    """Edit customer metadata."""
    customer = get_customer(cid)
    if not customer:
        abort(404)

    if request.method == 'POST':
        customer['name']           = request.form.get('name', '').strip()
        customer['slug']           = request.form.get('slug', '').strip()
        customer['billing_ref']    = request.form.get('billing_ref', '')
        customer['notes']          = request.form.get('notes', '')
        customer['default_locale'] = request.form.get('default_locale', 'en-GB')
        customer['primary_contact'] = {
            'name':  request.form.get('contact_name', ''),
            'email': request.form.get('contact_email', ''),
            'phone': request.form.get('contact_phone', ''),
        }
        customer['branding'] = {
            'logo_path':     customer.get('branding', {}).get('logo_path', ''),
            'primary_color': request.form.get('primary_color', '#003366'),
            'footer_line':   request.form.get('footer_line', ''),
        }
        if not customer['name']:
            flash('Name is required.', 'warning')
            return render_template('customers/customer_form.html',
                                   form_values=request.form, customer=customer)
        save_customer(customer)
        flash('Customer updated.', 'success')
        return redirect(url_for('customer.customer_detail', cid=cid))

    return render_template('customers/customer_form.html',
                           form_values=customer, customer=customer)


@customer_bp.route('/customers/<cid>/template-set', methods=['POST'])
@editor_required
def upload_template_set(cid):
    """Upload a tarball as a new template set for the customer."""
    customer = get_customer(cid)
    if not customer:
        abort(404)

    f = request.files.get('tarball')
    if not f or not f.filename:
        flash('No file uploaded.', 'warning')
        return redirect(url_for('customer.customer_detail', cid=cid))

    tsid = f'ts-{new_id()}'
    ts_dir = os.path.join(TEMPLATE_SETS_ROOT, tsid)
    os.makedirs(ts_dir, exist_ok=True)

    # Save upload to temp file then extract
    with tempfile.NamedTemporaryFile(delete=False, suffix='.tar') as tmp:
        f.save(tmp.name)
        tmp_path = tmp.name

    try:
        if not tarfile.is_tarfile(tmp_path):
            flash('Uploaded file is not a valid tar archive.', 'danger')
            os.unlink(tmp_path)
            shutil.rmtree(ts_dir, ignore_errors=True)
            return redirect(url_for('customer.customer_detail', cid=cid))

        with tarfile.open(tmp_path) as tar:
            # Security: strip leading path components, no absolute paths
            def _safe_member(m):
                m.name = os.path.normpath(m.name).lstrip('/')
                return m
            members = [_safe_member(m) for m in tar.getmembers()
                       if not os.path.isabs(m.name) and '..' not in m.name]
            tar.extractall(path=ts_dir, members=members)  # nosec
    except (tarfile.TarError, Exception) as exc:
        flash(f'Error extracting archive: {exc}', 'danger')
        shutil.rmtree(ts_dir, ignore_errors=True)
        os.unlink(tmp_path)
        return redirect(url_for('customer.customer_detail', cid=cid))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Enumerate extracted files
    files = []
    for root, _, fnames in os.walk(ts_dir):
        for fname in fnames:
            full = os.path.join(root, fname)
            rel  = os.path.relpath(full, ts_dir)
            files.append(rel)

    from datetime import datetime, timezone
    version = len(customer_template_sets(cid)) + 1
    ts_label = request.form.get('label', f'v{version}')

    ts_meta = {
        'id':                  tsid,
        'customer_id':         cid,
        'name':                ts_label,
        'version':             version,
        'context_schema_min':  1,
        'context_schema_max':  1,
        'uploaded_at':         datetime.now(timezone.utc).isoformat(),
        'uploaded_by':         'system',
        'files':               files,
    }

    # Write metadata file into the set dir
    with open(os.path.join(ts_dir, 'metadata.json'), 'w', encoding='utf-8') as mf:
        json.dump(ts_meta, mf, indent=2)

    r.set(_ts_key(tsid), json.dumps(ts_meta))
    r.sadd(_customer_ts_index_key(cid), tsid)

    # Make this the active template set
    customer['template_set_id'] = tsid
    save_customer(customer)

    flash(f'Template set {ts_label!r} uploaded and activated.', 'success')
    return redirect(url_for('customer.customer_detail', cid=cid))


@customer_bp.route('/customers/<cid>/activate-template-set/<tsid>', methods=['POST'])
@editor_required
def activate_template_set(cid, tsid):
    """Switch the customer's active template set."""
    customer = get_customer(cid)
    if not customer:
        abort(404)
    ts = get_template_set(tsid)
    if not ts or ts.get('customer_id') != cid:
        abort(404)
    customer['template_set_id'] = tsid
    save_customer(customer)
    flash(f'Template set {ts["name"]!r} is now active.', 'success')
    return redirect(url_for('customer.customer_detail', cid=cid))
