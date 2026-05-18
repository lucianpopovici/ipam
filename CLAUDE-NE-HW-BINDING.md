# CLAUDE-NE-HW-BINDING.md

> Companion to the top-level `CLAUDE.md`. Related docs:
> `CLAUDE-NAME-PATTERNS.md` (port and iface pattern expansion) and
> `CLAUDE-TENANTS-VRF-MAPPING.md` Phase 3 (NE instance reification).
>
> **Supersedes** `CLAUDE-OOB-SELECTORS.md`. The selector mechanism is
> retained here as the `auto-rule` bind mode — one of four unified bind
> modes — instead of a separate parallel system. Read that older doc only
> for historical context.
>
> **Scope:** The unified model for mapping NE interfaces to hardware
> ports. An NE rarely uses every port of a device. The user picks which
> HW ports map to which NE interfaces; the binding can be a single port,
> a LAG of N ports, an active/passive pair, or a rule that auto-materializes
> against current inventory.
>
> **Status:** Design — not yet implemented. Supersedes the simplistic
> 1:1 `iface_bindings` shown in `CLAUDE-TENANTS-VRF-MAPPING.md` Phase 3.

---

## The problem

A 48-port ToR switch might host:

- 4 uplinks to spine, owned by NE `spine-uplink` (1 iface, 4 ports as a LAG).
- 24 server-facing ports, owned by NE `access` (1 iface group `access{1..24}`).
- 2 OOB ports that should auto-bind to whichever OOB-NE exists in the project.
- 18 unused ports.

This is not "bind the whole switch to one NE." It's three NEs sharing one
HW instance, with non-overlapping port sets, one binding being a LAG, and
one binding being declarative ("every iLO/iDRAC, grouped per rack").

The model must support:

1. One NE iface ↔ **one** HW port (today's default).
2. One NE iface ↔ **N** HW ports as a LAG/bond (new).
3. One NE iface group (pattern) ↔ N HW ports by position (new).
4. One NE iface ↔ **a rule** that materializes against current inventory
   (new — was `port_selector` in the now-superseded OOB doc).
5. **Partial coverage** — most HW ports remain unbound, free for other NEs
   or for non-NE use (e.g., server access ports cabled directly to other
   devices).
6. **Multiple NEs on one HW instance**, with mutual exclusion enforced at
   the port level.

---

## Concept: bindings are lists, not pairs

The Phase 3 sketch had:

```json
"iface_bindings": {
  "iface-mgmt": {"hw_instance_id": "hw-srv-001", "port_id": "p-iLO"}
}
```

Replace with:

```json
"iface_bindings": {
  "iface-mgmt": {
    "bind_mode": "single",                       // 'single' | 'lag' | 'active-passive' | 'auto-rule'
    "ports": [
      {"hw_instance_id": "hw-tor-01", "port_id": "Eth1:0", "role": "primary"}
    ]
  },
  "iface-spine-uplink": {
    "bind_mode": "lag",
    "lag_id": 1,                                 // for vendor config rendering
    "ports": [
      {"hw_instance_id": "hw-tor-01", "port_id": "Eth1:47", "role": "member"},
      {"hw_instance_id": "hw-tor-01", "port_id": "Eth1:46", "role": "member"},
      {"hw_instance_id": "hw-tor-02", "port_id": "Eth1:47", "role": "member"},
      {"hw_instance_id": "hw-tor-02", "port_id": "Eth1:46", "role": "member"}
    ]
  },
  "iface-oob": {
    "bind_mode": "auto-rule",
    "rule": {
      "port_types":  ["mgmt"],
      "name_regex":  "^(iLO|iDRAC|BMC|mgmt)\\d*$",
      "categories":  ["server", "switch", "router"],
      "group_by":    ["rack"]                     // emits per-rack synthetic labels
    },
    "ports": [                                    // materialized snapshot, refreshed on rematerialize
      {"hw_instance_id": "hw-srv-001", "port_id": "iLO", "role": "primary", "bucket": ["rack:R-01"]},
      {"hw_instance_id": "hw-srv-002", "port_id": "iLO", "role": "primary", "bucket": ["rack:R-01"]}
    ]
  }
}
```

Key shape decisions:

- `ports` is **always** a list — single-port bindings are a list of one.
  Saves a `isinstance(..., dict)` branch everywhere.
- `bind_mode` lives on the binding, not on each port — every port in a LAG
  shares the mode.
- `role` per port: `primary` / `member` / `active` / `standby`. Used by
  config rendering (Phase 5 of roadmap); ignored in v1 for `single`.
- Multi-chassis LAGs work naturally — `ports` may reference different
  `hw_instance_id`s. (See MC-LAG note in Out of Scope.)
- For `auto-rule`, `ports[]` is a **materialized cache**, refreshed by the
  rematerialize action (see Workflow D). The `rule` is the source of truth;
  `ports[]` is what the rest of the system (IP allocator, validation,
  picker exclusion) reads. Each materialized entry carries its `bucket`
  (the synthetic labels from `group_by`) so per-rack pool resolution still
  works as it did in the superseded selector doc.

For pattern-expanded NE iface groups (see `CLAUDE-NAME-PATTERNS.md`), each
sub-iface (`data:0`, `data:1`, …) has its own entry in `iface_bindings` —
the expansion is materialized at binding time, not stored as a pattern.

---

## Four binding workflows (all from the NE side)

The user's entry point is always the **NE instance detail page**, never the
HW instance. (HW instance has a read-only "bound to NE" panel; see below.)
Every workflow ends with the same persisted shape — `bind_mode` + `ports[]`
— so downstream code (IP allocation, validation, picker exclusion) is mode-agnostic.

### Workflow A — Manual per-iface picker (the default)

For NE instances with a small number of interfaces, e.g., a router with
`mgmt`, `wan`, `lan`.

```
NE instance: pe-lon-01            [Edit bindings]
─────────────────────────────────────────────────
Interface       Bound to                          Status
mgmt            (none)                            [+ Bind]
wan             srv-01 / Eth1/1                   [Edit] [Unbind]
lan             srv-01 / Eth1/2,3,4,5  (LAG)      [Edit] [Unbind]
```

Clicking [+ Bind] opens a modal:

```
Bind iface 'mgmt'
─────────────────────────────────────────
Hardware instance       [▼ pick one ── ]
   ↓ once selected:
Bind mode               (•) single  ( ) LAG  ( ) active-passive
Ports                   [▢] Eth1/1   data   in use by NE wan-router
                        [▢] Eth1/2   data
                        [▢] Eth1/3   data
                        [☑] iLO      mgmt   ← selected
                        [▢] mgmt0    mgmt
                        ...
Filter                  [port_type: mgmt ▼]  [show: free ▼]
                                                      [Cancel] [Bind]
```

Critical UX rules:

- The port list **shows in-use ports greyed out** with the NE/iface that
  owns them. Lets the user see context, but prevents accidental overlap.
- A `port_type` filter dropdown defaults sensibly per iface (`mgmt` iface
  defaults to filtering `mgmt` ports; `data` iface to `data`). User can
  clear the filter.
- `bind_mode='single'` greys out all checkboxes after the first is ticked.
  `lag` allows multi-select.
- The HW dropdown lists **all HW instances in the project** by default,
  with a textual filter — not just instances in the same POD. Real
  deployments cable across pods (e.g., to spine).

### Workflow B — Pattern-to-pattern bulk bind

For NE iface groups like `data{1..24}` paired with HW port groups like
`Eth1/{1..48}`. Triggered from the iface group row, not the per-iface row.

```
Iface group: data{1..24}          [Bulk bind]
─────────────────────────────────────────
Hardware instance       [▼ hw-tor-01 ──── ]
Hardware port group     [▼ Eth1/{1..48}     48 ports ]
Use indices             [ 0   ] to [ 23  ]    → Eth1/1 … Eth1/24
                        Skip step [ 1 ]        (e.g. 2 = every other port)
Bind mode               (•) single per iface  ( ) LAG (one iface, N ports)
                                                          [Cancel] [Bind]
```

Preview pane (live):

```
data:0   →  hw-tor-01 / Eth1/1
data:1   →  hw-tor-01 / Eth1/2
data:2   →  hw-tor-01 / Eth1/3
...
data:23  →  hw-tor-01 / Eth1/24
```

With the LAG option, the preview is:

```
data (all 24 sub-ifaces collapse into one logical LAG)
  → hw-tor-01 / Eth1/1, Eth1/2, ..., Eth1/24
```

Implementation notes:

- The index range applies to the **HW port expansion order** (0-based, as
  defined in `CLAUDE-NAME-PATTERNS.md`). Not to port names — names can have
  arbitrary numbering.
- If `len(ne_subifaces) != len(hw_indices)` and mode is `single per iface`,
  refuse with a clear error showing the count mismatch.
- The skip step covers the common "even ports to A, odd ports to B" wiring
  patterns. v1 keep it simple (positive integers only).

### Workflow C — Auto-resolve against a whole device (convenience)

For users who want one-click full-device binding, optional, behind a button:

```
NE instance: pe-lon-01
[+ Bind to whole device]
```

Modal: pick an HW instance, then preview the auto-mapping:

```
Auto-resolve will create:
  ✓ mgmt        → hw-srv-001 / iLO         (name match)
  ✓ wan         → hw-srv-001 / Eth1        (name match: 'wan' in description)
  ✗ lan         → ?                        (no match; manual binding required)
  ✗ console     → ?                        (no match)
                                              [Cancel] [Apply matched]
```

Auto-resolution algorithm (deterministic, no fuzzy AI matching):

1. Exact case-insensitive match between iface `name` and port `name`.
2. Fallback: `name` substring match in port `notes`.
3. Tie-break: port_type match (NE iface labeled `mgmt` prefers `mgmt` port).
4. Anything unresolved → user must bind manually.

Each resolved iface is saved as `bind_mode='single'` with `ports=[…]` of
length one. Unmatched ifaces stay unbound; the user then uses Workflow A
for those. This is a *convenience over Workflow A* — not a separate
mechanism.

### Workflow D — Auto-rule binding (declarative, fleet-aware)

For "this NE iface owns every port of type X across the project, bucketed
per Y" — the canonical OOB-management case, plus console aggregators and
PDU management.

Triggered from the per-iface row, same as Workflow A. Modal:

```
Bind iface 'mgmt' (auto-rule)
─────────────────────────────────────────
Bind mode               ( ) single  ( ) LAG  ( ) active-passive  (•) auto-rule

Port types              [☑ mgmt]  [▢ data]  [▢ console]  [▢ power]
Name regex              [ ^(iLO|iDRAC|BMC|mgmt)\d*$               ]
Device categories       [☑ server]  [☑ switch]  [☑ router]  [▢ pdu]
Group by                [☑ rack]  [▢ hw_template]  [▢ category]  [▢ site]

Preview                 27 ports across 3 buckets:
                        rack:R-01     → 12 ports (4 servers × 3 mgmt ports)
                        rack:R-02     → 12 ports
                        unracked      →  3 ports   ⚠
                                                  [Cancel] [Save rule]
```

On save, the rule is stored and the materializer runs immediately:

1. For each HW port in the project, evaluate the rule predicates
   (`port_types`, `name_regex`, `categories`).
2. **Skip any port already in another iface's `ports[]`** — explicit
   bindings (single/lag/active-passive) win. Skipped ports surface as
   `NE_RULE_EXPLICIT_OVERLAP` info records, not errors.
3. For each surviving port, compute its `bucket` from `group_by` (same
   semantics as the superseded selector doc — `rack:R-01`, `hw-tmpl:R650`, …).
4. Write all surviving ports into `ports[]` with `role='primary'` and
   their `bucket`.

**Rematerialization** happens automatically on:

- HW instance create/delete (project-scoped).
- HW instance rack assignment change (affects `rack` bucket).
- HW template port edits (affects which ports match).
- Manual button: NE instance page → `[↻ Re-evaluate rules]`.

The audit log records each materialization with diff (`+ Eth1/1 added to
mgmt iface, − iLO-old removed`). This is critical for "why did this port
get an IP overnight" questions.

**Per-bucket IP requirements** work the same as in the selector doc: when
`compute_requirements` walks an auto-rule binding, it groups `ports[]`
entries by `bucket` and emits one requirement per bucket, tagged with the
synthetic labels (`rack:R-01`, …). The existing label-based pool resolver
then picks the right per-rack subnet. **No change** to the requirement
engine beyond reading `binding.ports` grouped by `bucket` instead of
calling a separate selector resolver.

---

## Data model

### `ne_instance` — extended `iface_bindings`

```json
{
  "id": "ne-001",
  "ne_type_id": "ne-router-pe",
  "name": "pe-lon-01",
  ...

  "iface_bindings": {
    "iface-id-1": {
      "bind_mode": "single|lag|active-passive|auto-rule",
      "lag_id":    1,                  // optional, free-form, for lag mode
      "rule": {                        // present iff bind_mode='auto-rule'
        "port_types":  ["mgmt"],
        "name_regex":  "^(iLO|iDRAC|BMC)\\d*$",
        "categories":  ["server"],
        "group_by":    ["rack"]
      },
      "rule_materialized_at": "...",   // for auto-rule: ISO timestamp of last refresh
      "ports": [
        {"hw_instance_id": "...", "port_id": "...", "role": "primary",
         "bucket": ["rack:R-01"]}      // bucket only present for auto-rule entries
      ]
    }
  }
}
```

`ports[]` is the canonical "what's bound" answer regardless of mode. For
`auto-rule`, it's the cached materialization output; for everything else
it's the user's direct entry. Downstream code reads `ports[]` and never
needs to distinguish.

### `hw_instance` — derived "bound to" index

No new persistent field. Add a derived helper:

```python
def hw_instance_bindings(iid: str) -> list:
    """
    Return [{port_id, ne_instance_id, ne_name, iface_id, iface_name, bind_mode}, ...]
    for every port on this HW instance that is bound to an NE iface.
    Result is computed by scanning NE instances; cache in
    'hw:instance:{iid}:bound_ports' if perf becomes an issue.
    """
```

Used by:

- HW instance detail page: read-only "Bound interfaces" table.
- Cable form: when picking a port, show "in use by NE foo" alongside the
  cable status.
- Validation: see codes below.

### Lookup index for explicit bindings (perf)

Avoid scanning every NE instance on every cable-form load. New Redis key:

```
hw:port_bound:{hw_instance_id}:{port_id}   String — ne_instance_id (or absent)
```

Updated atomically on binding save/unbind. Used by:

- The "in use by ..." display in port pickers.
- The auto-rule materializer to exclude explicitly-bound ports from rule matches.
- Validation `PORT_DOUBLE_BOUND` check (now O(1) per port).

---

## Free-port visibility

A common operational question: "of this 48-port switch, which ports are
free?" Surface it on the HW instance detail page:

```
hw-tor-01 — Dell-N9336C — 48 ports
────────────────────────────────────
Eth1/1     data    bound: NE pe-lon-01 / wan
Eth1/2     data    bound: NE access-01 / access:0
Eth1/3     data    bound: NE access-01 / access:1
...
Eth1/24    data    bound: NE access-01 / access:23
Eth1/25    data    cabled (no NE) to srv-009
Eth1/26    data    free
...
Eth1/47    data    LAG member (NE pe-lon-01 / spine-uplink)
Eth1/48    data    LAG member (NE pe-lon-01 / spine-uplink)
iLO        mgmt    bound: NE oob-mgmt / mgmt (via auto-rule)
```

Three port states, color-coded:
- **bound** (any mode) → muted blue, shows NE name and the binding mode badge
- **cabled but unbound** → grey, shows cable peer
- **free** → green

This view is the antidote to "I have no idea what's left on this switch."

---

## Validation

Extend `templates/hw/validation.html` with binding-specific codes:

| Code                          | Severity | Trigger                                                  |
|-------------------------------|----------|----------------------------------------------------------|
| `NE_PORT_DOUBLE_BOUND`        | error    | Same `(hw_instance, port)` in two NE iface `ports[]`.    |
| `NE_PORT_NOT_FOUND`           | error    | Binding references a port_id not in the HW template (after expansion). |
| `NE_PORT_TYPE_MISMATCH`       | warning  | NE iface labeled `data` bound to `mgmt` port (or vice versa). |
| `NE_LAG_SINGLE_PORT`          | warning  | `bind_mode='lag'` with only one port in `ports`.         |
| `NE_LAG_SPEED_MISMATCH`       | warning  | LAG members have different `speed_gbps`.                 |
| `NE_LAG_MIXED_HOSTS`          | info     | LAG spans multiple HW instances (probably MC-LAG; fine). |
| `NE_RULE_EXPLICIT_OVERLAP`    | info     | Auto-rule match excluded due to an explicit binding on the same port. |
| `NE_RULE_NO_MATCH`            | warning  | Auto-rule defined but materialized to zero ports.        |
| `NE_RULE_UNRACKED_PORT`       | warning  | Auto-rule with `group_by:['rack']` matched a port whose HW instance has no rack. |
| `NE_RULE_STALE`               | warning  | HW changed since the rule was last materialized — rematerialize to refresh. |

The `NE_PORT_DOUBLE_BOUND` check uses the `hw:port_bound:` index — O(1).

---

## Integration with VRFs and IP allocation

Each iface binding carries (transitively, via the NE iface) a `vrf_id`.
When `compute_requirements` runs for this NE instance, the emitted
requirement is per-iface (not per-port) — multiple ports in a LAG share
one logical iface and therefore one IP.

The "Allocate IPs" action writes the IP into `port_overrides[port_id].ip`
for **every port in the binding** (so it shows up on each port's view),
but the IP is one and the same. Add a `port_overrides.shared_with: []` to
make this auditable:

```json
"port_overrides": {
  "Eth1:47": {"ip": "10.0.0.1", "shared_with": ["Eth1:46"], "lag_id": 1},
  "Eth1:46": {"ip": "10.0.0.1", "shared_with": ["Eth1:47"], "lag_id": 1}
}
```

For single-port bindings, `shared_with` is empty.

---

## Migration

Two-step, idempotent.

### Step 1: shape change for existing bindings

Any existing `iface_bindings` field with the old 1:1 dict shape:

```python
# old: {"iface-id": {"hw_instance_id": "...", "port_id": "..."}}
# new: {"iface-id": {"bind_mode": "single", "ports": [{"hw_instance_id": "...", "port_id": "...", "role": "primary"}]}}
```

Trivial rewrite; no risk.

### Step 2: build the `hw:port_bound:` index

```python
for nid in r.smembers('ne_instances:index'):
    ne = get_ne_instance(nid)
    for iface_id, binding in ne.get('iface_bindings', {}).items():
        for port in binding['ports']:
            r.set(f'hw:port_bound:{port["hw_instance_id"]}:{port["port_id"]}', nid)
```

After this, the lookup index is the source of truth for "is this port
bound." Don't read iface_bindings to answer that question — always use
the index.

### Step 3: migrate any pre-existing `port_selector` data

If the superseded OOB selector design ever shipped, NE *types* may carry
`iface[].port_selector` fields. These move to NE *instance* `iface_bindings`
as `bind_mode='auto-rule'` entries:

```python
for tid in r.smembers('ne_types:index'):
    ne_type = get_ne_type(tid)
    for iface in ne_type.get('interfaces', []):
        sel = iface.pop('port_selector', None)
        if not sel:
            continue
        # For each NE instance of this type, create an auto-rule binding
        for inst_id in r.smembers(f'ne_type:{tid}:instances'):
            inst = get_ne_instance(inst_id)
            inst.setdefault('iface_bindings', {})[iface['id']] = {
                'bind_mode': 'auto-rule',
                'rule': sel,
                'ports': [],   # materialize on next access
            }
            save_ne_instance(inst)
        # Also stash the rule as a type-level default for future instances
        iface['default_bind_rule'] = sel
    save_ne_type(ne_type)
```

`default_bind_rule` on the NE type is a small new field: when a fresh NE
instance is materialized from a POD slot, any iface with a
`default_bind_rule` gets a pre-populated `auto-rule` binding (the user
can edit or remove it). This preserves the "every router auto-binds its
mgmt iface" ergonomics without making rules a property of the type.

If you never shipped the old selector design, skip step 3 entirely.

---

## UI files touched

```
templates/ne/ne_instance_detail.html             — list ifaces + bind buttons,
                                                    [↻ Re-evaluate rules] button
templates/ne/_bind_modal.html                    NEW — Workflow A modal
templates/ne/_bulk_bind_modal.html               NEW — Workflow B modal
templates/ne/_autoresolve_modal.html             NEW — Workflow C modal
templates/ne/_rule_modal.html                    NEW — Workflow D modal (rule editor + preview)
templates/hw/instance_detail.html                — free-port visibility table
static/js/ne_bindings.js                         NEW — picker + preview JS (shared by A/B/D)

ne.py                                            — new routes for all four workflows
hw_logic.py                                      — hw_instance_bindings helper,
                                                    hw:port_bound index ops
rules.py                                         NEW — rule materializer (lifted from the
                                                    superseded OOB resolver); single helper
                                                    materialize_binding(binding, project) → ports[]
templates/hw/validation.html                     — 10 new code descriptions
scripts/migrate_006_iface_bindings.py            NEW — steps 1–3

tests/unit/test_iface_bindings.py                NEW — covers all four bind modes
tests/unit/test_rule_materializer.py             NEW — covers Workflow D + bucket logic
tests/api/test_bind_routes.py                    NEW
tests/e2e/test_ne_hw_binding.py                  NEW
```

---

## Routes

| Method | Path                                                       | Description              |
|--------|------------------------------------------------------------|--------------------------|
| POST   | `/ne-instances/<nid>/bindings/<iface_id>`                  | Create/update a binding (any mode) |
| POST   | `/ne-instances/<nid>/bindings/<iface_id>/delete`           | Unbind                   |
| POST   | `/ne-instances/<nid>/bindings/<iface_id>/rematerialize`    | Refresh auto-rule's `ports[]` |
| POST   | `/ne-instances/<nid>/bindings/bulk`                        | Workflow B               |
| POST   | `/ne-instances/<nid>/bindings/autoresolve`                 | Workflow C (whole-device)|
| POST   | `/projects/<pid>/rematerialize-rules`                      | Refresh every auto-rule binding in the project (e.g. after bulk HW import) |
| GET    | `/api/projects/<pid>/hw/<hwid>/free-ports?type=mgmt`       | Picker datasource        |
| GET    | `/api/projects/<pid>/hw/<hwid>/bindings`                   | Read-only HW-side view   |
| POST   | `/api/projects/<pid>/rules/preview`                        | Workflow D preview (rule → buckets + counts, no save) |

---

## Acceptance criteria

- [ ] A user can bind NE iface `mgmt` to a single HW port via the modal,
      seeing only that NE's relevant ports; binding persists across reload.
- [ ] A user can bind NE iface `uplink` as a LAG of 4 ports across 2 HW
      instances; the binding shows correctly on both HW instance detail
      pages.
- [ ] Pattern-to-pattern bind: NE iface `data{1..24}` ↔ HW port group
      `Eth1/{1..48}` with index range `[0..23]` produces 24 individual
      bindings; preview shows them before commit.
- [ ] An attempt to bind a port already used by another NE instance is
      refused at save time with a clear error citing the conflicting NE.
- [ ] The HW instance detail page shows three port states (bound / cabled
      / free) with correct counts.
- [ ] The OOB use case from the superseded selector doc works end-to-end:
      an `auto-rule` binding with `port_types=['mgmt']`, `group_by=['rack']`
      materializes per-rack `ports[]` entries and emits one IP requirement
      per rack with synthetic `rack:R-NN` labels for pool resolution.
- [ ] Explicit `single`/`lag` bindings take precedence over `auto-rule`:
      a port in an explicit binding is excluded from rule materialization
      and an `NE_RULE_EXPLICIT_OVERLAP` info record surfaces.
- [ ] Adding a new HW instance and clicking `[↻ Re-evaluate rules]` adds
      its matching ports to the relevant `auto-rule` binding without
      manual intervention.
- [ ] Allocating an IP for a LAG iface writes the same IP into every
      bound port's `port_overrides`, with correct `shared_with` linkage.
- [ ] Unbinding releases the port back to "cabled" or "free" state and
      cleans the `hw:port_bound:` index entry atomically.
- [ ] Auto-resolve correctly matches `mgmt` ↔ `iLO`/`iDRAC`/`BMC` by the
      port_type fallback when names don't match exactly.

---

## Out of scope

- **True MC-LAG semantics** (peer-link, system-id sync). The data model
  supports LAGs spanning hosts; vendor-specific MC-LAG config is a
  rendering concern, not a binding concern.
- **Sub-interfaces / VLAN tagging** on bound ports. A single port carrying
  multiple tagged VLANs that each terminate on a different NE iface is a
  real requirement but needs its own design — likely a `vlan_id` on each
  port entry in the binding's `ports` list. Defer to a follow-up.
- **Port-channel hashing config** (L2/L3/L4, src/dst). Render-time concern.
- **Binding history / audit replay.** Covered by the audit log work
  package; the binding routes just need to emit audit events.
- **Cross-tenant bindings.** Bindings are tenant-scoped; an NE in tenant A
  cannot bind to HW in tenant B. Enforced via the existing
  `@require_tenant` decorator on the binding routes.
