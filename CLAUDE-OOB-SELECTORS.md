# CLAUDE-OOB-SELECTORS.md

> ⚠️ **SUPERSEDED — kept for historical context only.**
>
> This design has been folded into `CLAUDE-NE-HW-BINDING.md` as the
> **`auto-rule` bind mode** — one of four unified bind modes alongside
> `single`, `lag`, and `active-passive`. The mechanism described here
> still exists, but as a property of an NE-instance iface binding rather
> than as a separate parallel system attached to NE-type interfaces.
>
> What carries over verbatim from this doc:
> - The rule predicates (`port_types`, `name_regex`, `categories`).
> - The `group_by` bucketing logic and synthetic labels (`rack:R-01`, …).
> - Per-bucket IP requirement emission for per-rack pools.
> - Validation around unracked ports.
>
> What changed:
> - The selector lives on the binding, not on the NE type's iface.
> - Explicit (`single`/`lag`) bindings on a port take precedence; rules
>   skip those ports during materialization.
> - A small new field `default_bind_rule` on NE-type ifaces pre-populates
>   the binding for new instances, preserving the old "every router
>   auto-binds its mgmt iface" ergonomics.
>
> **Do not implement from this doc.** Use `CLAUDE-NE-HW-BINDING.md`.
> The content below is preserved only so that PRs/commits referencing
> "the selector design" remain meaningful.
>
> ---

# CLAUDE-OOB-SELECTORS.md

> Companion to the top-level `CLAUDE.md`. Read that first — it covers the
> tech stack, blueprint layout, and Redis key conventions assumed throughout
> this document.
>
> **Feature:** Hardware-port-based OOB IP allocation via interface selectors.
> **Scope:** `ne.py`, `hw_logic.py`, `hw.py`, `ipam.py`, several templates,
> three new test modules. No new blueprints, no new dependencies.
> **Status:** Design — not yet implemented.

---

## Goal

Allow an NE interface (typically belonging to an "OOB" NE) to **automatically
bind to every hardware port** matching a selector — and emit **one IP
allocation requirement per bucket** (rack, hw-template, site, etc.), so that
each rack or hardware family can draw from a **separate OOB subnet** via the
existing label-based pool resolver.

End-state user flow:

1. Define a `OOB-mgmt` NE type with one interface, sharing `project`, with a
   `port_selector` that matches all `port_type='mgmt'` ports grouped by `rack`.
2. The requirements engine emits N requirements — one per rack — each tagged
   with a synthetic label `rack:R-01`, `rack:R-02`, …
3. The user defines pools / subnets labeled `rack:R-01`, `rack:R-02`, …
4. The existing pool resolver matches them. Push allocates the subnets.
5. The "Allocate OOB IPs" action pops one IP per matched port and writes it to
   `hw:instance:{iid}.port_overrides[port_id].ip`.

---

## Concept: Selector → Bucket → Synthetic Label → Pool

```
                                              ┌──────────────────────┐
                                              │  pool/subnet labels  │
                                              │  rack:R-01           │
                                              │  rack:R-02           │
                                              └──────────┬───────────┘
                                                         │ existing
                                                         │ resolver
                                                         ▼
NE iface.port_selector ──► resolve_port_selector ──► buckets ──► requirements
                                                         │
                                                         │ each requirement
                                                         │ carries synthetic
                                                         │ labels = bucket keys
                                                         ▼
                                              ┌──────────────────────┐
                                              │ existing             │
                                              │ compute_requirements │
                                              └──────────────────────┘
```

The **synthetic labels are the only new contract** between this feature and
the rest of the system. Everything downstream (pool resolution, subnet push,
IP popping) is unchanged.

---

## Data Model Changes

### 1. NE interface — new optional field `port_selector`

Location: each entry in `ne_type:{tid}.interfaces`.

```python
{
  'id': 'i1',
  'name': 'oob',
  'labels': ['oob'],
  'sharing': 'project',                # required: 'project' | 'site' | 'pod'
  'ipv4': {'prefix_len': 24},
  'ipv6': None,
  'params': {},

  # NEW — entirely optional, absence = legacy behaviour
  'port_selector': {
    'port_types':  ['mgmt'],                              # subset of PORT_TYPES
    'name_regex':  '^(iLO|iDRAC|BMC|mgmt|ME)\\d*$',       # default '.*'
    'categories':  ['server', 'switch', 'router'],        # subset of CATEGORIES
    'group_by':    ['rack'],                              # see "Group keys" below
  }
}
```

**Group keys** (any combination, evaluated in order, produces nested buckets):

| Key           | Bucket value source                                    |
|---------------|--------------------------------------------------------|
| `rack`        | `inst.location.rack_id`, or `'unracked'` if absent     |
| `hw_template` | `inst.template.name`                                   |
| `category`    | `inst.template.category` (`server`, `switch`, …)       |
| `site`        | site that owns the pod that "uses" this hw instance¹   |
| `pod`         | pod that "uses" this hw instance¹                      |

¹ See "Open question: site/pod inference" below.

### 2. HW instance — new optional field `labels: []`

`hw_instance` currently has no labels (only the template does). Adding labels
to instances lets the user define **rack groups** (e.g. `rack-group:floor-1`)
that span multiple physical racks without renaming asset tags.

Migration: existing instances are read with `inst.get('labels', [])` — no
upgrade script needed. Add the input on `templates/hw/instance_form.html`.

This field is consumed by `_bucket_key` when `group_by` includes `rack` —
if the rack has labels, they take precedence over the raw `rack_id`.

### 3. Port overrides — already has `ip`

No change. `inst.port_overrides[port_id]` is already
`{notes, mac, ip}`. The allocator writes here.

### 4. New Redis keys

```
ne_iface:{ne_type_id}:{iface_id}:bindings   JSON list
                                            [{hw_instance_id, port_id, port_name,
                                              ip, bucket}, ...]
```

This is a denormalized reverse index. It's rebuilt every time the OOB allocator
runs, so it can be regenerated from `hw:instance` records if lost. Keep it
because UI lookups ("show me everything this NE iface is bound to") would
otherwise scan every hw instance.

---

## Core Helpers

All live in `ne.py` (selector/resolver is NE-domain) and call into
`hw_logic.py` for hardware data access. **No circular import:** the resolver
calls `hw_logic.project_instances` directly; do not move it to `hw.py`.

### `resolve_port_selector(pid, selector) -> dict[tuple, list]`

```python
import re
from hw_logic import project_instances, CATEGORIES, PORT_TYPES, get_hw_instance

def resolve_port_selector(pid: str, selector: dict) -> dict:
    """
    Return {bucket_key: [(hw_instance_id, port_id, port_name), ...]}.
    bucket_key is a tuple of (group_kind, group_value) pairs in selector
    group_by order — used directly to derive synthetic labels.
    """
    port_types = set(selector.get('port_types') or PORT_TYPES)
    categories = set(selector.get('categories') or CATEGORIES)
    name_re    = re.compile(selector.get('name_regex') or '.*', re.IGNORECASE)
    group_by   = selector.get('group_by') or []

    buckets: dict = {}
    for inst in project_instances(pid):
        tmpl = inst.get('template')
        if not tmpl or tmpl.get('category') not in categories:
            continue
        for port in tmpl.get('ports', []):
            if port.get('port_type') not in port_types:
                continue
            count = int(port.get('count', 1))
            for n in range(count):
                pname = port['name'] if count == 1 else f"{port['name']}-{n}"
                if not name_re.match(pname):
                    continue
                key = _bucket_key(inst, tmpl, group_by)
                buckets.setdefault(key, []).append((inst['id'], port['id'], pname))
    return buckets


def _bucket_key(inst: dict, tmpl: dict, group_by: list) -> tuple:
    parts = []
    loc = inst.get('location', {})
    for g in group_by:
        if g == 'rack':
            rack_id = loc.get('rack_id')
            if rack_id:
                rack_inst = get_hw_instance(rack_id)
                # prefer first instance label, else asset_tag, else id
                val = (rack_inst.get('labels', [None])[0]
                       if rack_inst and rack_inst.get('labels')
                       else rack_inst.get('asset_tag', rack_id) if rack_inst
                       else rack_id)
            else:
                val = 'unracked'
            parts.append(('rack', val))
        elif g == 'hw_template':
            parts.append(('hw-tmpl', tmpl.get('name', 'unknown')))
        elif g == 'category':
            parts.append(('category', tmpl.get('category', 'other')))
        # site / pod: see "Open question" below
    return tuple(parts) if parts else (('all', 'all'),)


def selector_synthetic_labels(bucket_key: tuple) -> list:
    """['rack:R-01', 'hw-tmpl:Dell-R650']  — kind:value, alpha-sorted."""
    return sorted(f'{k}:{v}' for (k, v) in bucket_key)
```

---

## Integration: `compute_requirements`

In the existing `compute_requirements` loop in `ne.py`, when iterating
`for iface in ne_type.get('interfaces', [])`, branch on
`iface.get('port_selector')`:

```python
sel = iface.get('port_selector')
if sel:
    # Selector mode: emit one requirement per bucket
    buckets = resolve_port_selector(pid, sel)
    for bucket_key, matches in buckets.items():
        synth_labels = selector_synthetic_labels(bucket_key)
        all_labels   = sorted(set(iface.get('labels', [])) | set(synth_labels)
                              | site_labels | pod_labels | ne_labels | slot_labels)
        for ip_ver in ('ipv4', 'ipv6'):
            spec = iface.get(ip_ver)
            if not spec:
                continue
            rec = {
                'site_id': site['id'], 'site_name': site['name'],
                'pod_id':  pod['id'],  'pod_name':  pod['name'],
                'ne_type_id': ne_type['id'], 'ne_type_name': ne_type['name'],
                'ne_kind': ne_type['kind'],
                'iface_id': iface['id'],
                'iface_name': f"{iface['name']} [{','.join(synth_labels)}]",
                'ip_version': ip_ver,
                'prefix_len': spec['prefix_len'],
                'sharing':    iface.get('sharing', 'interface'),
                'labels':     all_labels,
                'count':      len(matches),     # one IP per matched port
                'key':        f'sel:{ne_type["id"]}:{iface["id"]}:'
                              f'{":".join(synth_labels)}:{ip_ver}',
                'bucket':     list(bucket_key),
                'matches':    matches,          # carried for allocator step
                'pushed':     False,
            }
            reqs.append(rec)
    continue   # selector path is exclusive; don't fall through to legacy branch

# else: existing sharing-based logic unchanged
```

**Sharing semantics with selectors:**

| `sharing`   | Behaviour with selector                                              |
|-------------|----------------------------------------------------------------------|
| `project`   | one requirement per bucket, project-wide. Recommended for OOB.       |
| `site`      | one requirement per bucket per site. Resolve buckets per-site.       |
| `pod`       | one requirement per bucket per pod.                                  |
| `ne`        | rejected at save time — selectors don't make sense per-NE-instance.  |
| `interface` | rejected at save time — selector already produces N matches.         |

Validate this in the NE type save route (`add_ne_type` / `edit_ne_type`).

---

## IP Allocation Action

New route on the subnet detail page (already exists, just add a button when
the subnet was pushed from a selector requirement):

```
POST /projects/<pid>/subnets/<nid>/allocate-oob
```

Algorithm:

1. Look up the requirement record whose subnet matches `nid` (via the existing
   pushed-requirement linkage).
2. For each `(hw_instance_id, port_id, port_name)` in `req['matches']`:
   - If `inst.port_overrides[port_id].ip` is already set and is in this subnet,
     skip (idempotent).
   - Pop next free IP from `network:{nid}:ips` (use existing allocator).
   - Set `inst.port_overrides[port_id] = {..., 'ip': <ip>}`.
   - Append to `ne_iface:{ne_type_id}:{iface_id}:bindings`.
3. Flash `Allocated N IPs across M devices`.

**Idempotency rule:** re-running the action must not double-allocate. The
`port_overrides[port_id].ip in subnet` check handles this.

**Stale-binding cleanup:** when an `hw_instance` is deleted
(`delete_hw_instance` in `hw_logic.py`), iterate its `port_overrides`,
remove each IP from its `network:{nid}:ips` set, and prune
`ne_iface:*:bindings` entries referencing the instance. Add this to the
existing delete path.

---

## UI Changes

### `templates/ne/ne_type_form.html`

In the interface card, add a collapsed **"Port Selector"** section below the
IPv4/IPv6 checkboxes:

- Toggle: "Bind to hardware ports by selector" (off by default).
- When on:
  - Multi-select: port types (default `['mgmt']`).
  - Multi-select: device categories (default `['server','switch','router']`).
  - Text input: name regex (default `^(iLO|iDRAC|BMC|mgmt|ME)\d*$`).
  - Multi-select: group by (default `['rack']`).
- **Live preview pane** (right side), refreshed via a new endpoint
  `GET /api/projects/<pid>/selector-preview?selector=<urlencoded-json>`:

  ```
  Currently matches 27 ports across 3 buckets:
    rack:R-01      → 12 ports (4 servers × 3 iLO/iDRAC/mgmt)
    rack:R-02      → 12 ports
    unracked       →  3 ports   ⚠
  ```

The preview pays for itself the first time someone forgets to assign a rack —
the "unracked" warning surfaces immediately.

### `templates/hw/instance_form.html`

Add a `labels` text field (comma-separated, same UX as site/pod labels).
Save into the instance dict; the field is referenced by `_bucket_key`.

### Subnet detail page

When the subnet was pushed from a selector requirement, show an
**"Allocate OOB IPs"** button that POSTs to the action above, plus a table:

```
Bound Ports                              IP
──────────────────────────────────────────────────
srv-001 / iLO                            10.10.1.5
srv-001 / iDRAC                          10.10.1.6
sw-tor-01 / mgmt                         10.10.1.7
```

---

## Validation

New codes for `templates/hw/validation.html`:

| Code                       | Severity | Trigger                                                        |
|----------------------------|----------|----------------------------------------------------------------|
| `OOB_UNRACKED_PORT`        | warning  | Selector with `group_by:['rack']` matched a port on an instance with no `location.rack_id`. |
| `OOB_SELECTOR_NO_MATCH`    | warning  | Selector defined but matches zero ports project-wide.          |
| `OOB_PORT_DOUBLE_BOUND`    | error    | The same `(hw_instance, port)` is matched by selectors on two different NE interfaces. |
| `OOB_IP_OUTSIDE_SUBNET`    | error    | `port_overrides.ip` does not fall inside the bound subnet.     |

Add these descriptions to the `code_descriptions` dict in `validation.html`.

---

## Open Question: site/pod inference

`group_by=['site']` requires knowing which site a *hardware instance* belongs
to. Today, hardware doesn't carry a `site_id` — only racks do (implicitly,
via the project). Two options:

1. **Add `site_id` and `pod_id` directly to `hw:instance`** (preferred). The
   "place in rack" action already knows the rack; if racks are
   site-scoped, set the instance's site at placement time. Backfill is
   trivial: for each instance, look up its rack's project's sites.
2. **Skip `site` and `pod` group keys for v1.** They're not needed for the
   user's stated case (per-rack and per-hw-template). Defer.

Recommendation: **defer**. Ship v1 with `rack`, `hw_template`, `category` only.

---

## Migration / Backwards Compatibility

- `port_selector` absent → existing requirement-emission path runs unchanged.
- `hw_instance.labels` absent → treated as `[]`. No data migration required.
- New Redis keys (`ne_iface:*:bindings`) are created lazily on first
  allocation; absence == "not yet allocated".
- Existing tests must still pass with **zero changes** to NE types that don't
  use selectors. Verify by running the full unit + API suite before merging
  any selector code.

---

## Testing

### Unit (`tests/unit/test_port_selector.py`, new file)

- `resolve_port_selector` with each `group_by` permutation, including empty.
- `_bucket_key` with: rack with labels, rack without labels, unracked, deleted
  rack reference.
- Selector matching: `count > 1` port expansion, regex case-insensitivity,
  port-type filtering, category filtering.
- `compute_requirements` integration:
  - Selector mode emits one requirement per bucket.
  - Synthetic labels appear in `req['labels']`.
  - `req['count']` equals `len(matches)`.
  - Non-selector interfaces on the same NE type still emit via legacy path.

### API (`tests/api/test_oob_allocate.py`, new file)

- Push a selector subnet → allocate OOB IPs → verify each port has an IP.
- Re-run allocator → idempotent (no duplicate pops).
- Delete an HW instance → its IPs return to the pool, bindings pruned.
- `POST` with invalid selector JSON → 400 with flash error.

### E2E (`tests/e2e/test_oob_flow.py`, new file)

Full happy path: create OOB NE type with selector → create 2 racks with 2
servers each → push requirements → confirm 2 subnets created (rack:R-01 +
rack:R-02) → allocate → verify IPs appear on instance detail page.

---

## Acceptance Criteria

- [ ] An OOB NE type can be created with a `port_selector` and saved/loaded
      round-trip without data loss.
- [ ] `compute_requirements` produces N requirements for N buckets, each
      labeled with the synthetic `kind:value` labels.
- [ ] A subnet labeled `rack:R-01` is selected by the existing pool resolver
      for the matching requirement.
- [ ] "Allocate OOB IPs" writes one IP per matched port into
      `port_overrides[port_id].ip` and is idempotent.
- [ ] Deleting an HW instance releases its OOB IPs.
- [ ] The selector preview shows live bucket counts before saving.
- [ ] All four new validation codes fire under their respective conditions.
- [ ] All existing unit + API + E2E tests pass unchanged.

---

## Out of Scope (v1)

- IPv6 EUI-64 derivation from MAC (allocator just pops next free).
- Selector composition / nesting (one selector per interface).
- Cross-project selectors (selectors are project-scoped, resolved against
  `project_instances(pid)`).
- Site/pod `group_by` — see "Open question".
- DHCP integration — the IPs land in `port_overrides`; consumption is a
  separate concern.

---

## File Touch List

```
ne.py                              + resolve_port_selector, _bucket_key,
                                     selector_synthetic_labels,
                                     compute_requirements branch,
                                     /api/projects/<pid>/selector-preview,
                                     save-time sharing validation
hw_logic.py                        + cleanup hook in delete_hw_instance
hw.py                              + POST /projects/<pid>/subnets/<nid>/allocate-oob
                                     (or place in ipam.py — see note)
templates/ne/ne_type_form.html     + selector UI block + preview pane JS
templates/hw/instance_form.html    + labels input
templates/hw/validation.html       + 4 new code_descriptions entries
templates/ipam/subnet_detail.html  + Allocate OOB IPs button + bindings table
tests/unit/test_port_selector.py   NEW
tests/api/test_oob_allocate.py     NEW
tests/e2e/test_oob_flow.py         NEW
```

**Note on route placement:** `allocate-oob` straddles ipam (subnet/IP pool)
and hw (port_overrides). Put it in `ipam.py` since the primary mutation is
the IP allocation; `port_overrides` write is a side-effect via
`save_hw_instance`. Keeps the import direction (`ipam → hw_logic`) consistent
with the existing pattern.
