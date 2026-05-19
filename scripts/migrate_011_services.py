#!/usr/bin/env python3
"""
Migration 011 — Services feature.

No-op for empty or existing deployments.
Validates customer index integrity and ensures no orphan service keys.
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db

r = db.r


def run():
    print("Migration 011: Services feature — validating customer index integrity...")

    cids = r.smembers('customers:index')
    orphan_services = 0
    for cid in cids:
        svc_ids = r.smembers(f'customer:{cid}:services')
        for sid in svc_ids:
            raw = r.get(f'service:{sid}')
            if raw is None:
                print(f"  WARNING: orphan service ID {sid!r} in customer {cid!r} — removing from index")
                r.srem(f'customer:{cid}:services', sid)
                orphan_services += 1

    if orphan_services:
        print(f"  Removed {orphan_services} orphan service index entries.")
    else:
        print(f"  Checked {len(cids)} customer(s) — no orphans found.")

    print("Migration 011 complete.")


if __name__ == '__main__':
    run()
