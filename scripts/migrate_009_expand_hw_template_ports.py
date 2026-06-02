"""
One-shot migration: expand all HW templates so every port row has count=1
and a fully resolved name. Idempotent — safe to run multiple times.

Run inside the container:
    podman exec -it ipam python scripts/migrate_009_expand_hw_template_ports.py
"""
import sys
from db import r, redis_get, redis_save
from hw_logic import _tmpl_key, expand_template_ports, HW_TMPL_INDEX


def needs_migration(tmpl: dict) -> bool:
    for p in tmpl.get('ports') or []:
        if int(p.get('count') or 1) > 1:
            return True
        if '{' in (p.get('name') or ''):
            return True
    return False


def main() -> int:
    migrated = 0
    failed   = 0
    for tid in r.smembers(HW_TMPL_INDEX):
        tmpl = redis_get(_tmpl_key(tid))
        if not tmpl or not needs_migration(tmpl):
            continue
        try:
            new_tmpl = expand_template_ports(tmpl)
        except ValueError as e:
            print(f'  ✗ {tid} ({tmpl.get("name", "?")}): {e}', file=sys.stderr)
            failed += 1
            continue
        redis_save(_tmpl_key(tid), new_tmpl)
        n_before = len(tmpl.get('ports') or [])
        n_after  = len(new_tmpl.get('ports') or [])
        print(f'  ✓ {tid} ({tmpl.get("name", "?")}): {n_before} → {n_after} ports')
        migrated += 1
    print(f'\nMigrated: {migrated}   Failed: {failed}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
