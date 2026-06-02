"""
One-shot migration: apply the allocation model changes.

  1. Projects  — supernet → legacy_supernet
  2. Labels    — bare labels → tag:<label>; VRF/site/pod/rack labels → entity FKs
  3. NE-type ifaces — prefix_len → address_count per family
  4. NE instances   — initialise vrf_overrides = {}
  5. Networks  — set explicit family; lift entity-ref labels to FK fields

Run:
    python migrations/alloc_model.py [--dry-run]

Idempotent — safe to run multiple times.
"""
import json
import sys
import ipaddress
import argparse

sys.path.insert(0, __file__.split('migrations')[0])

from db import r, redis_get, redis_save  # pylint: disable=wrong-import-position


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load(key):
    raw = r.get(key)
    return json.loads(raw) if raw else None


def _save(key, obj):
    r.set(key, json.dumps(obj))


def _usable_from_prefix(prefix_len: int, family: int) -> int:
    if family == 6:
        return 1 if prefix_len >= 128 else 2 ** (128 - prefix_len)
    if prefix_len >= 32:
        return 1
    if prefix_len == 31:
        return 2
    return max(1, 2 ** (32 - prefix_len) - 2)


# ── Step 1: Projects ──────────────────────────────────────────────────────────

def migrate_projects(dry_run: bool) -> list:
    report = []
    proj_ids = r.smembers('projects:index')
    for pid in proj_ids:
        proj = _load(f'project:{pid}')
        if not proj:
            continue
        if 'supernet' in proj and 'legacy_supernet' not in proj:
            report.append(f'  project {proj["id"]} ({proj.get("name","")!r}): '
                          f'supernet={proj["supernet"]} → legacy_supernet')
            if not dry_run:
                proj['legacy_supernet'] = proj.pop('supernet')
                _save(f'project:{pid}', proj)
        elif 'supernet' in proj and 'legacy_supernet' in proj:
            report.append(f'  project {proj["id"]}: already has legacy_supernet, removing supernet key')
            if not dry_run:
                proj.pop('supernet')
                _save(f'project:{pid}', proj)
        if not proj.get('customer_id'):
            report.append(f'  WARNING project {proj["id"]} ({proj.get("name","")!r}): '
                          f'no customer_id — assign one via the UI')
    return report


# ── Step 2: Labels ────────────────────────────────────────────────────────────

def _known_entity_names() -> dict:
    """Build a map of lowercased entity-name → (entity_type, id) for lifting."""
    mapping = {}
    for vid in r.smembers('vrfs:index'):
        v = _load(f'vrf:{vid}')
        if v:
            mapping[v['name'].lower()] = ('vrf', vid)
    for sid in r.smembers('sites:index') if r.exists('sites:index') else []:
        s = _load(f'site:{sid}')
        if s:
            mapping[s['name'].lower()] = ('site', sid)
    # collect from project site sets
    for pid in r.smembers('projects:index'):
        for sid in r.smembers(f'project:{pid}:sites'):
            s = _load(f'site:{sid}')
            if s:
                mapping[s['name'].lower()] = ('site', sid)
        for pod_id in r.smembers(f'project:{pid}:pods'):
            p = _load(f'pod:{pod_id}')
            if p:
                mapping[p['name'].lower()] = ('pod', pod_id)
    return mapping


def migrate_labels(dry_run: bool) -> list:
    """Rewrite bare labels on global and project label sets."""
    report = []
    entity_map = _known_entity_names()

    def _rewrite(label: str):
        if ':' in label:
            return label, None  # already categorised
        lower = label.lower()
        if lower in entity_map:
            etype, eid = entity_map[lower]
            return None, (etype, eid, label)  # lift to entity FK
        return f'tag:{label}', None  # rename to tag:

    sets_to_migrate = [('labels:global', None)]
    for pid in r.smembers('projects:index'):
        sets_to_migrate.append((f'project:{pid}:labels', pid))

    for key, pid in sets_to_migrate:
        labels = list(r.smembers(key))
        for label in labels:
            new_label, lift = _rewrite(label)
            if new_label == label:
                continue
            if lift:
                report.append(f'  label {label!r} in {key}: lifted to {lift[0]}={lift[1]}')
            else:
                report.append(f'  label {label!r} in {key}: rewritten → {new_label!r}')
            if not dry_run:
                r.srem(key, label)
                if new_label:
                    r.sadd(key, new_label)

    return report


# ── Step 3: NE-type ifaces ────────────────────────────────────────────────────

def migrate_ne_type_ifaces(dry_run: bool) -> list:
    report = []
    all_tids = list(r.smembers('ne:types:index'))
    for pid in r.smembers('projects:index'):
        all_tids.extend(r.smembers(f'project:{pid}:ne_types'))

    seen = set()
    for tid in all_tids:
        if tid in seen:
            continue
        seen.add(tid)
        ne = _load(f'ne_type:{tid}')
        if not ne:
            continue
        changed = False
        for iface in ne.get('interfaces', []):
            for fam_key, fam_int in (('ipv4', 4), ('ipv6', 6)):
                spec = iface.get(fam_key)
                if not spec:
                    continue
                if 'address_count' not in spec and 'prefix_len' in spec:
                    pl = spec['prefix_len']
                    ac = _usable_from_prefix(pl, fam_int)
                    report.append(f'  ne_type {tid} iface {iface["id"]} {fam_key}: '
                                  f'prefix_len={pl} → address_count={ac}')
                    if not dry_run:
                        iface[fam_key] = {'address_count': ac, 'min_prefix': None}
                    changed = True
        if changed and not dry_run:
            _save(f'ne_type:{tid}', ne)

    return report


# ── Step 4: NE instances ──────────────────────────────────────────────────────

def migrate_ne_instances(dry_run: bool) -> list:
    report = []
    inst_ids = r.smembers('ne:instances:index')
    for nid in inst_ids:
        inst = _load(f'ne:instance:{nid}')
        if not inst:
            continue
        if 'vrf_overrides' not in inst:
            report.append(f'  ne:instance:{nid}: add vrf_overrides={{}}')
            if not dry_run:
                inst['vrf_overrides'] = {}
                _save(f'ne:instance:{nid}', inst)

    return report


# ── Step 5: Networks ──────────────────────────────────────────────────────────

def migrate_networks(dry_run: bool) -> list:
    report = []
    net_ids = r.smembers('networks:index')
    for nid in net_ids:
        net = _load(f'network:{nid}')
        if not net:
            continue
        changed = False

        # 5a. Set explicit family
        if 'family' not in net:
            try:
                fam = ipaddress.ip_network(net['cidr'], strict=False).version
                net['family'] = fam
                report.append(f'  network {nid}: set family={fam}')
                changed = True
            except ValueError:
                report.append(f'  WARNING network {nid}: invalid cidr {net.get("cidr")!r}')

        # 5b. Ensure labels field is populated from Redis set
        if 'labels' not in net:
            lab_key = f'network:{nid}:labels'
            labels = sorted(r.smembers(lab_key))
            if labels:
                net['labels'] = labels
                report.append(f'  network {nid}: populated labels from set: {labels}')
                changed = True

        if changed and not dry_run:
            _save(f'network:{nid}', net)

    return report


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Allocation model migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would change without modifying Redis')
    args = parser.parse_args()

    dry = args.dry_run
    prefix = '[DRY RUN] ' if dry else ''

    print(f'{prefix}=== Step 1: Projects ===')
    for line in migrate_projects(dry):
        print(line)

    print(f'\n{prefix}=== Step 2: Labels ===')
    for line in migrate_labels(dry):
        print(line)

    print(f'\n{prefix}=== Step 3: NE-type ifaces (prefix_len → address_count) ===')
    for line in migrate_ne_type_ifaces(dry):
        print(line)

    print(f'\n{prefix}=== Step 4: NE instances (add vrf_overrides) ===')
    for line in migrate_ne_instances(dry):
        print(line)

    print(f'\n{prefix}=== Step 5: Networks (set family, populate labels) ===')
    for line in migrate_networks(dry):
        print(line)

    print(f'\n{prefix}Migration complete.')


if __name__ == '__main__':
    main()
