# CLAUDE-ALLOCATION-MODEL.md

> Companion to the top-level `CLAUDE.md`. Related docs:
> `CLAUDE-NE-HW-BINDING.md` (NE iface ↔ HW port binding modes),
> `CLAUDE-TENANTS-VRF-MAPPING.md` (customer/tenant scoping),
> `CLAUDE-CHECKS.md` (checklist materialization patterns).
>
> **Scope:** Overhaul of the IP allocation model. Replaces the
> per-project supernet, promotes VRF to a first-class entity, introduces
> namespaced labels, redefines iface IP demand as `address_count` per
> family, and adds inventory-aware subnet template rules. Touches
> `ipam.py`, `ne.py`, `hw.py`, the customer blueprint, multiple
> templates, and the requirements engine.
>
> **Status:** Implemented (2026-05-20). See implementation notes below.

---

## Goal

Move from a model where every project owns a private CIDR supernet and
labels are an opaque flat namespace to one where:

- Customers own VRFs; projects belong to customers.
- VRF, site, pod, and rack are entity references on subnets and
  interfaces — not labels.
- Labels carry an explicit `category:value` shape and are only used for
  free-form tags (env, role, criticality, tier).
- Interfaces declare an `address_count` per family rather than a
  prefix length.
- Subnet templates can size themselves from inventory, so the user gets
  the minimum prefix that fits the deployment.
- `compute_requirements` emits one requirement per subnet (per family),
  not per iface, so shared subnets get the right size by construction.

---

## Summary of Changes

| Change | Replaces | Reason |
|---|---|---|
| Drop `project.supernet` | Was used for `carve_next_subnet` and utilization math | Replaced by Label → VRF → Site → Project pool chain |
| Project requires `customer_id` | Optional FK | Customer is the unit of branding, VRFs, billing |
| VRF as entity, scoped per customer | VRF-as-label string | Entity has lifecycle, RD/RT, sentinel for global RIB |
| Label categories (`category:value`) | Flat label namespace | Splits free-form tags from entity references |
| Iface `address_count` per family | Iface `prefix_len` | Demand is "how many IPs", not "what prefix" |
| `per_hw` and `per_ne` template rules | Static-only `from_start`/`from_end`/`range` rules | Subnet size derives from inventory |
| Per-subnet requirements | Per-iface requirements | Shared subnets get sized from sum of contributions |
| Manual delta-only re-materialization | Full re-materialization, often automatic | Stop reshuffling deployed IPs |

---

## Data Model Changes

### Project

```python
{
  'id': '…',
  'name': 'EMEA Rollout',
  'description': '',
  'customer_id': 'cid-acme',          # REQUIRED on create
  'legacy_supernet': '10.0.0.0/8',    # migration-only; never read for allocation
}
```

- `supernet` removed from the form; existing values move to
  `legacy_supernet` during migration and are never read again.
- `customer_id` is required on create; the customer picker on
  `project_form.html` has no "— None —" option for new projects.
  Existing nullable projects keep null until explicitly assigned.
- `project_pool_summary` and `global_pool_summary` no longer divide by
  the supernet. "Total capacity" becomes the sum of subnet capacities
  matched to the project; utilization is sum-of-used over that.

### Customer

No schema changes. Customer is the owner of VRFs and the default
container for template sets, branding, and (in the doc-gen layer)
checklists. Customer deletion blocks while any project or VRF
references it — the existing cascade-preview API extends to VRFs.

### VRF (new entity)

VRFs are scoped **per customer**. Redis keys:

```
vrf:{vid}                    JSON record
vrfs:index                   Set of all vid's
customer:{cid}:vrfs          Set of vid's owned by this customer
```

Record:

```python
{
  'id': 'vrf-internet',
  'customer_id': 'cid-acme',
  'name': 'internet',
  'description': '',
  'rd':  '65000:100',                 # optional, free-form
  'rt_import': ['65000:100'],
  'rt_export': ['65000:100'],
  'site_pool_overrides': {            # per-site IP pool preferences
    'site-lon': {'pool_labels': ['vrf:internet', 'site:lon']},
    # …
  },
}
```

The `null` VRF (no record) represents the **global routing table**. It
is a valid, first-class value in all matching logic — never a wildcard.

VRF picker UX is three-state:

- `— Global routing table —` (vrf_id = null)
- One of the customer's defined VRFs
- Unset (transient only; rejected on save)

### Labels — categories

Labels move from flat strings to namespaced `category:value` form. The
category prefix is mandatory for new labels; bare labels are accepted
on read for backward compat and rewritten to `tag:<label>` during
migration.

Declared categories live in `config/app_config.yaml`:

```yaml
label_categories:
  env:         {scope: [subnet, ne, hw],  multi_valued: false}
  role:        {scope: [subnet, ne, hw],  multi_valued: true}
  tier:        {scope: [subnet, ne],      multi_valued: false}
  criticality: {scope: [subnet],          multi_valued: false}
  tag:         {scope: [subnet, ne, hw],  multi_valued: true}  # catch-all
```

- `scope` restricts which entity types can carry a label of that
  category. Out-of-scope labels rejected at save.
- `multi_valued` controls whether multiple values are allowed on one
  entity (`env:prod` + `env:staging` invalid; `role:master` +
  `role:etcd` valid).

**What is no longer a label:**

- VRF — use `vrf_id` (entity FK).
- Site — use `site_id`.
- Pod — use `pod_id`.
- Rack — use `rack_id`.

The OOB synthetic-label convention (`rack:R-01`, etc.) from
`CLAUDE-NE-HW-BINDING.md` survives as an **internal plumbing artifact**
for the auto-rule materializer's bucket keys. It is not user-authored
and does not appear in the labels UI.

### Network / Subnet

```python
{
  'id': '…',
  'cidr': '10.0.0.0/24',
  'family': 4,                        # NEW — explicit, not inferred
  'vrf_id': 'vrf-internet' | None,    # null = global RIB
  'site_id': 'site-lon' | None,
  'pod_id':  'pod-a' | None,
  'project_id': 'pid-emea',
  'labels': ['env:prod', 'role:transit'],
  'name': '', 'description': '', 'vlan': '',
  'pending_slots': [...],
  'phantom_slots': [...],             # NEW — reserved-for-future, see below
}
```

### NE-type interface

```python
{
  'id': 'i1',
  'name': 'mgmt',
  'labels': ['role:mgmt'],
  'sharing': 'project',               # project | site | pod | ne | interface
  'vrf_id': 'vrf-mgmt' | None,        # default for new instances
  'ipv4': {'address_count': 1, 'min_prefix': None} | None,
  'ipv6': {'address_count': 1, 'min_prefix': None} | None,
  'params': {},
  # bind mode fields per CLAUDE-NE-HW-BINDING.md
}
```

- `prefix_len` is removed. `address_count` is the demand; the subnet
  is sized to fit (see "Minimum prefix calculation").
- Both `ipv4` and `ipv6` default to `None`. Save-time validation:
  **at least one of `ipv4` and `ipv6` must be non-null.** Dual-stack
  ifaces populate both; counts can differ legitimately (e.g. 3 v4
  for HSRP, 1 v6 for SLAAC).
- `min_prefix` is an optional ops-policy floor ("no transit smaller
  than /30 regardless of count"). Defaults to None.
- `vrf_id` on the type is the **generic VRF** for this iface. At
  instance time it is mapped to a concrete customer VRF via
  `vrf_overrides`.

### NE instance

```python
{
  'id': '…',
  'ne_type_id': '…',
  'project_id': '…',
  'vrf_overrides': {                  # NEW
    'iface-id-1': 'vrf-acme-internet',
    'iface-id-2': None,               # explicit override to global RIB
  },
  'iface_bindings': { ... },          # per CLAUDE-NE-HW-BINDING.md
}
```

Effective VRF for an iface:

```python
def effective_vrf(ne_inst, ne_type, iface_id):
    overrides = ne_inst.get('vrf_overrides', {})
    if iface_id in overrides:        # explicit override (may be null)
        return overrides[iface_id]
    iface = find_iface(ne_type, iface_id)
    return iface.get('vrf_id')       # type default (may be null)
```

The `in` check matters — a key with value `None` is "override to global
RIB", not "no override".

### Subnet template — new rule types

Existing rules (`from_start`, `from_end`, `range`) keep their shape.
Two new rule types are added.

**`per_hw`** — count from HW inventory, ordered selectors:

```python
{
  'type': 'per_hw',
  'offset': {'kind': 'fixed', 'value': 10},
  'selectors': [
    {'labels_any': ['role:master'], 'role': 'master', 'sort_by': 'asset_id'},
    {'labels_any': ['role:worker'], 'role': 'worker', 'sort_by': 'asset_id'},
  ],
  'max_count': 50,                   # hard ceiling for prefix calc
  'status': 'reserved',
}
```

HW labels are **additive**: effective label set on an HW instance is
`hw_template.labels ∪ hw_instance.labels`. Selectors match the effective
set. `asset_id` is a first-class field on `hw_instance` (not
schema-driven), so `sort_by` reads it directly.

**`per_ne`** — count from NE inventory with a per-NE multiplier:

```python
{
  'type': 'per_ne',
  'offset': {'kind': 'after_previous', 'gap': 2},
  'ne_filter':    {'ne_type_id': '…', 'labels_any': ['role:edge']},
  'iface_filter': {'labels_any': ['role:cluster']},
  'per_ne_count': 3,
  'max_count': 10,                   # max number of NEs to budget for
  'role': 'cluster',
  'status': 'reserved',
}
```

**Offsets** can be fixed or relative:

```python
{'kind': 'fixed',          'value': 10}     # at position 10
{'kind': 'after_previous', 'gap': 2}        # 2 positions after previous rule's last
```

The first rule's offset **must** be `fixed` (no previous to chain from)
— enforced at save time as `TMPL_FIRST_RULE_RELATIVE`.

Example mixed template:

```
rule[0] = from_start, fixed 0            → position 0 (gateway)
rule[1] = per_hw,     after_previous 2   → starts at position 3
rule[2] = per_ne,     after_previous 1   → starts at rule[1].end + 2
```

---

## Algorithm Changes

### Pool resolver

```
resolve(family, vrf_id, label_set, site_id=None, pod_id=None) → [subnet]
```

Match conditions (all must hold):

1. `subnet.family == family`
2. `subnet.vrf_id == vrf_id`  *(null == null, not a wildcard)*
3. `label_set ⊆ subnet.labels`
4. If `site_id` given: `subnet.site_id == site_id` OR `subnet.site_id is None`
5. Same logic for `pod_id`.

Scoring priority remains Label → VRF → Site → Project, but VRF and site
are now exact entity matches, not string matches against label values.

### `compute_requirements` — per subnet, not per iface

Group key:

```python
key = (family, effective_vrf(...), frozenset(labels), sharing_scope_value)
```

where `sharing_scope_value` is:

| `sharing`   | value             |
|-------------|-------------------|
| `project`   | `'project'`       |
| `site`      | site_id           |
| `pod`       | pod_id            |
| `ne`        | ne_instance_id    |
| `interface` | iface_instance_id |

Group iteration:

```python
for key, members in grouped_ifaces:
    family, vrf, labels, _ = key
    total = sum(contribution(iface, family, applied_template)
                for iface in members)
    requirement = {
        'family': family,
        'vrf_id': vrf,
        'labels': sorted(labels),
        'sharing_scope': sharing_scope_value,
        'address_count': total,
        'min_prefix': max(min_prefix_floors(members, family)),
        'iface_refs': [m.ref for m in members],
    }
    yield requirement
```

`contribution`:

```python
def contribution(iface, family, applied_template):
    rule = template_rule_for_iface(applied_template, iface)
    if rule and rule.type in ('per_ne', 'per_hw'):
        return rule.per_unit_count
    return iface[family]['address_count']
```

Cross-`ne_type` merging is **intentional**: two ifaces with matching
(family, vrf, labels, sharing) merge even if they belong to different
NE types. That's the point of shared transit segments.

### Minimum prefix calculation

**IPv4:**

```python
def min_prefix_v4(address_count: int) -> int:
    if address_count <= 1: return 32
    if address_count == 2: return 31           # RFC 3021 P2P
    return 32 - ceil_log2(address_count + 2)   # +2 for net+bcast
```

| address_count | min prefix | usable |
|---|---|---|
| 1 | /32 | 1 |
| 2 | /31 | 2 |
| 3–6 | /29 | 6 |
| 7–14 | /28 | 14 |

Note: /30 is never the answer — same usable count as /31 (2) but uses
4 addresses. The model picks /31.

**IPv6:**

```python
def min_prefix_v6(address_count: int) -> int:
    if address_count <= 1: return 128
    return 128 - ceil_log2(address_count)      # no net/bcast subtraction
```

| address_count | min prefix | usable |
|---|---|---|
| 1 | /128 | 1 |
| 2 | /127 | 2 |
| 3–4 | /126 | 4 |
| 5–8 | /125 | 8 |

### Template materialization

**Apply (initial):**

1. Resolve `per_hw` and `per_ne` rule counts from current inventory.
2. Compute each rule's absolute start position via fixed /
   after_previous chain.
3. Reserve `max_count` positions per dynamic rule (phantom slots),
   tagged `phantom_source = 'template:<tid>:rule:<rid>:slot:<n>'`.
4. Compute `min_prefix` from final high-water mark.
5. Carve subnet of that size.
6. As real hosts arrive, claim phantom slots in selector-defined order
   (sorted by `asset_id` by default). New hosts take only **free
   (unreserved)** addresses — they cannot displace a claimed slot.

**Re-materialize (manual, delta-only):**

Triggered by a button on the subnet detail page. Computes:

- `current_inventory_matches`: what selectors match today.
- `claimed_slots`: phantom slots already bound to an asset.
- `delta`:
  - **Adds:** matches in current that aren't in claimed.
  - **Removes:** claimed slots whose asset no longer matches selectors.

For adds, assign the next free phantom slot. For removes, mark the
slot free (return it to the phantom pool) and surface the released IP
in the UI for operator confirmation.

**Never** re-sort claimed slots. An IP on a deployed host never moves
during re-materialization — that is the whole reason for going
delta-only. New instances are not regenerated each time the way the
current code does it; the existing implementation is wrong and must
be replaced.

If `max_count` is exceeded by current inventory, fail with
`TMPL_SLOT_OVERFLOW` — operator must raise the cap (and re-carve a
larger subnet, manual) or split.

---

## Migration (one-shot script)

Executed once during deploy. Idempotent. Dry-run mode required and
must produce a full diff report before the destructive pass.

**Run order:**

1. **Projects**
   - `supernet` → `legacy_supernet`.
   - Projects without `customer_id` flagged in the report; admin
     assigns before the supernet code path is removed in a follow-up.

2. **Labels**
   - For each label without a `category:` prefix, look up the label
     string against existing VRF, site, pod, rack names.
     - **Match** → drop the label, set the corresponding entity FK
       (`vrf_id` / `site_id` / etc.) on the carrying entity.
     - **No match** → rewrite to `tag:<label>`.
   - Report: lifted-to-entity counts per category, residual `tag:`
     counts, ambiguous matches needing manual review.

3. **NE-type ifaces**
   - `prefix_len` → `address_count`:
     - /32→1, /31→2, /30→2 *(see note below)*, /29→6, /28→14, /27→30, …
     - The /30→2 mapping is a judgement call (matches usable host
       count). HSRP-style links where the operator actually meant
       "3 addresses" are flagged for follow-up; no silent conversion.
   - Family inferred from address family of any prior allocation; if
     ambiguous, default to `ipv4` populated and `ipv6` None.

4. **NE instances**
   - Initialize `vrf_overrides = {}` on every instance.
   - For ifaces where the instance-level VRF label disagrees with the
     type's default, set `vrf_overrides[iface_id] = vrf_id_lookup(...)`.

5. **Networks**
   - Set `family` explicitly from CIDR.
   - Lift VRF/site/pod/rack labels into entity FK fields.
   - Subnets that fail validation (e.g. conflicting VRF labels) listed
     in the report for manual cleanup.

---

## Validation Codes

| Code | Severity | Condition |
|---|---|---|
| `PROJ_NO_CUSTOMER` | error | New project without `customer_id`. |
| `VRF_CROSS_CUSTOMER` | error | Iface references a VRF whose `customer_id` ≠ project's customer. |
| `VRF_MISMATCH` | error | IP allocated from a pool with different `vrf_id` (incl. null mismatch). |
| `IFACE_NO_FAMILY` | error | Iface has both `ipv4` and `ipv6` set to None. |
| `IFACE_BAD_LABEL_SCOPE` | warning | Label category not declared for this entity scope. |
| `LABEL_UNCATEGORIZED` | warning | Bare label without `category:` prefix (migration leftover). |
| `TMPL_FIRST_RULE_RELATIVE` | error | First rule in a template uses `after_previous` offset. |
| `TMPL_SLOT_OVERLAP` | error | Two rules in one template produce overlapping positions. |
| `TMPL_SLOT_OVERFLOW` | error | Computed high-water exceeds subnet capacity. |
| `REQ_GROUP_TEMPLATE_CONFLICT` | error | Ifaces in one requirement group reference different applied templates. |
| `REQ_FAMILY_MISSING` | warning | Iface declares a family but no requirement was emitted (resolver gap). |

---

## File Touch List

```
ipam.py                          - remove supernet from add_project/edit_project
                                 - rewrite project_pool_summary, global_pool_summary
                                 - new pool resolver entry point taking
                                   (family, vrf_id, labels, site_id, pod_id)
                                 - per_hw / per_ne rule evaluation
                                 - manual re-materialization route + delta calc
customer.py                      - VRF CRUD route hooks
                                 - cascade-preview includes VRFs
vrf.py                           NEW — EntityBlueprint subclass, CRUD,
                                 site_pool_overrides editor
ne.py                            - compute_requirements rewrite
                                   (per-subnet grouping, family branch)
                                 - iface schema: address_count per family
                                 - vrf_overrides on NE instance
                                 - effective_vrf helper
hw.py                            - hw_instance labels editor (additive)
templates/project_form.html      - drop supernet field, customer required
templates/subnet_form.html       - vrf picker (3-state), site/pod pickers
templates/ne/*                   - iface form per-family blocks
templates/templates_*.html       - new rule UI (per_hw, per_ne, offset kind)
config/app_config.yaml           + label_categories block
migrations/202X_allocation_model.py    NEW — one-shot script + dry-run
tests/unit/test_address_count.py       NEW
tests/unit/test_pool_resolver_v2.py    NEW
tests/unit/test_template_rules.py      NEW
tests/unit/test_rematerialize.py       NEW
tests/api/test_vrf_routes.py           NEW
tests/api/test_project_no_supernet.py  NEW
tests/e2e/test_allocation_flow.py      NEW
```

---

## Out of Scope (v1)

- IPv6 EUI-64 derivation. Allocator just pops next free.
- VRF route leaking / import-export evaluation at runtime. RT fields
  are stored for documentation only.
- DHCP integration. IPs land in subnet records; consumption is separate.
- Multi-fabric / multi-underlay scoping when `vrf_id = null`. One global
  RIB per project assumed.
- Cross-customer VRF references. Strictly rejected.
- Auto re-materialization. Manual button only.
- Silent /30 → address_count=3 conversion for HSRP-style links;
  flagged for human review.

---

## Open Questions

None blocking. To revisit post-v1:

- Should `label_categories` be editable in the UI, or stay file-only?
  File-only is simpler; UI is friendlier. v2.
- Per-VRF site overrides currently store label tuples; consider
  pinning a specific subnet instead once usage patterns settle.
- An interactive review tool in `migrations/` for the /30 → 2-vs-3
  ambiguity may be worth building if the count of affected links is
  high.
