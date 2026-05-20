#!/usr/bin/env python3
"""
Migration 006 — reshape iface_bindings to the unified list model.

Old shape (one dict per iface):
    "iface_bindings": {
        "iface-id": {"hw_instance_id": "...", "port_id": "..."}
    }

New shape:
    "iface_bindings": {
        "iface-id": {
            "bind_mode": "single",
            "ports": [{"hw_instance_id": "...", "port_id": "...",
                       "role": "primary", "bucket": []}]
        }
    }

Also rebuilds hw:port_bound: index from scratch.

Safe to re-run — already-migrated records are detected and skipped.
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402

with app.app_context():
    from db import r

    migrated_insts    = 0
    migrated_ifaces   = 0
    skipped_insts     = 0
    rebuilt_idx_entries = 0

    # ── Step 1: rebuild hw:port_bound index from scratch ─────────────────────
    # Delete all existing port-bound keys to avoid stale data
    for key in r.scan_iter('hw:port_bound:*'):
        r.delete(key)
    for key in r.scan_iter('hw:instance:*:bound_ports'):
        r.delete(key)
    print('Cleared stale port-bound index.')

    # ── Step 2: reshape iface_bindings ────────────────────────────────────────
    all_nids = r.smembers('ne:instances:index')
    print(f'Found {len(all_nids)} NE instance(s) to inspect.')

    for nid in all_nids:
        raw = r.get(f'ne:instance:{nid}')
        if not raw:
            continue
        try:
            inst = json.loads(raw)
        except json.JSONDecodeError:
            print(f'  WARN: could not parse ne:instance:{nid} — skipped')
            continue

        old_bindings = inst.get('iface_bindings', {})
        if not old_bindings:
            skipped_insts += 1
            continue

        new_bindings = {}
        inst_changed = False

        for iface_id, binding in old_bindings.items():
            # Already in new shape: has 'bind_mode' key
            if 'bind_mode' in binding:
                new_bindings[iface_id] = binding
                continue

            # Old shape: flat dict with hw_instance_id + port_id at top level
            hw_iid  = binding.get('hw_instance_id', '')
            port_id = binding.get('port_id', '')

            if not hw_iid or not port_id:
                # Malformed — preserve as-is so data isn't lost
                new_bindings[iface_id] = binding
                print(f'  WARN: ne:instance:{nid} iface {iface_id} has no hw/port — preserved verbatim')
                continue

            new_bindings[iface_id] = {
                'bind_mode': 'single',
                'ports': [{
                    'hw_instance_id': hw_iid,
                    'port_id':        port_id,
                    'role':           'primary',
                    'bucket':         [],
                }],
            }
            inst_changed    = True
            migrated_ifaces += 1

        if inst_changed:
            inst['iface_bindings'] = new_bindings
            r.set(f'ne:instance:{nid}', json.dumps(inst))
            migrated_insts += 1
        else:
            skipped_insts += 1

    # ── Step 3: rebuild hw:port_bound index from new shape ────────────────────
    for nid in all_nids:
        raw = r.get(f'ne:instance:{nid}')
        if not raw:
            continue
        try:
            inst = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for iface_id, binding in inst.get('iface_bindings', {}).items():
            bind_mode = binding.get('bind_mode', 'single')
            for port in binding.get('ports', []):
                hw_iid  = port.get('hw_instance_id', '')
                port_id = port.get('port_id', '')
                if not hw_iid or not port_id:
                    continue
                payload = json.dumps({
                    'ne_instance_id': nid,
                    'iface_id':       iface_id,
                    'bind_mode':      bind_mode,
                })
                r.set(f'hw:port_bound:{hw_iid}:{port_id}', nid)
                r.hset(f'hw:instance:{hw_iid}:bound_ports', port_id, payload)
                rebuilt_idx_entries += 1

    print(
        f'\nDone.\n'
        f'  NE instances reshaped : {migrated_insts}\n'
        f'  Iface bindings updated: {migrated_ifaces}\n'
        f'  NE instances skipped  : {skipped_insts} (already current shape)\n'
        f'  Port-bound index keys : {rebuilt_idx_entries}'
    )
