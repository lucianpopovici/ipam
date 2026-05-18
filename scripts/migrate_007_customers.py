"""
Migration 007: Create cust-internal customer and backfill existing projects.

Run once: python scripts/migrate_007_customers.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db  # noqa: E402 — needs sys.path set first

INTERNAL_CUSTOMER = {
    'id':              'cust-internal',
    'name':            'Internal',
    'slug':            'internal',
    'primary_contact': {'name': '', 'email': '', 'phone': ''},
    'billing_ref':     '',
    'template_set_id': None,
    'branding': {
        'logo_path':     '',
        'primary_color': '#003366',
        'footer_line':   '',
    },
    'default_locale':  'en-GB',
    'notes':           'Default internal customer — created by migration 007.',
    'created_at':      '',
}


def run():
    r = db.r

    # Create internal customer
    cid = INTERNAL_CUSTOMER['id']
    if not r.exists(f'customer:{cid}'):
        r.set(f'customer:{cid}', json.dumps(INTERNAL_CUSTOMER))
        r.sadd('customers:index', cid)
        print(f'Created customer {cid!r}')
    else:
        print(f'Customer {cid!r} already exists — skipping.')

    # Backfill projects that have no customer_id
    proj_ids = r.smembers('projects:index')
    updated = 0
    for pid in proj_ids:
        raw = r.get(f'project:{pid}')
        if not raw:
            continue
        proj = json.loads(raw)
        if not proj.get('customer_id'):
            proj['customer_id'] = cid
            r.set(f'project:{pid}', json.dumps(proj))
            r.sadd(f'customer:{cid}:projects', pid)
            updated += 1
            print(f'  Backfilled project {pid!r} ({proj.get("name","")})')

    print(f'Done — {updated} project(s) backfilled.')


if __name__ == '__main__':
    run()
