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
> HW ports map to which NE interfaces, with a **filter-driven picker**
> (port type, connector, port labels) where **cabling status is not a
> binding constraint**. The binding can be a single port, a LAG of N
> ports, an active/passive pair, or a rule that auto-materializes against
> current inventory. Each binding attaches the NE iface's subnet to the
> bound port and propagates it one hop through any attached cable.
> Ports that require their own IP get one allocated from the same subnet.
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

Clicking [+ Bind] opens a modal. The picker is **filter-driven, not
inventory-driven** — the user describes the kind of port they want and
the candidate list narrows accordingly. **Cabling status is not a binding
constraint:** a port can be bound to an NE iface regardless of whether
it has a cable. Cabling is a separate physical concern; binding is a
logical assignment.

```
Bind iface 'mgmt'
────────────────────────────────────────────────────────────
Bind mode               (•) single  ( ) LAG  ( ) active-passive
HW instance scope       [▼ all in project ──]   (filter typeahead)

Port filters
  Port type             [☑ mgmt]  [▢ data]  [▢ console]  [▢ power]  [▢ usb]
  Connector             [▼ any (or pick: RJ45, SFP+, SFP28, …)         ]
  Port labels           [▼ any (or any-of: ipmi, uplink, console, …)   ]
  Exclude               [☑] bound to another NE iface (default on, recommended)
                        [▢] cabled to a non-bound port

Candidate ports (12 matched)
   [▢] srv-001 / iLO       mgmt  RJ45  labels: ipmi
   [☑] srv-001 / iDRAC     mgmt  RJ45  labels: ipmi
   [▢] srv-001 / mgmt0     mgmt  RJ45                cabled → tor-01:Eth1/47
   [▢] srv-002 / iLO       mgmt  RJ45  labels: ipmi
   [▢] sw-tor-01 / mgmt0   mgmt  RJ45                bound by NE oob-mgmt
   ...
                                                          [Cancel] [Bind]
```

Critical UX rules:

- **Filters narrow; they don't gate.** Every filter is "show me ports
  matching X" — clearing them all shows every port in the scope. No
  filter is mandatory.
- Filters compose with **AND between categories, OR within a category**
  (e.g. port type = `mgmt` AND connector ∈ {`RJ45`, `SFP+`}).
- The **port_type filter defaults sensibly per NE iface**: an iface
  labeled `mgmt` opens the modal with port_type=`mgmt` pre-checked; a
  `data` iface with port_type=`data`. The user can clear to see all
  types. This is convenience, not enforcement.
- The **"bound to another NE iface" exclude is on by default** — the
  one occupancy concept that actually matters for binding. Turning it
  off shows already-bound ports as informational rows (greyed out with
  the owning NE name).
- **Cabling is shown as a per-row badge** ("cabled → tor-01:Eth1/47" or
  "uncabled"), purely informational. It is **not** a default exclusion
  filter; the second exclude checkbox above lets the user opt in to
  hiding cabled-but-unbound ports if they want to.
- `bind_mode='single'` greys out all checkboxes after the first is
  ticked. `lag` allows multi-select.
- The **HW instance scope** dropdown lists every HW instance in the
  project by default, with a textual filter — not just instances in the
  same POD. Real deployments cable across pods.

The candidate-list query is one call to a new endpoint
`GET /api/projects/<pid>/ports/search?…` (see Routes below) that
evaluates the filters server-side; the modal pages through results when
the project is large.

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

## Port-state visibility

A common operational question: "of this 48-port switch, which ports are
bound, cabled, and to what subnets?" Surface it on the HW instance detail
page. **Cabling and binding are independent dimensions** — a port can be
in any combination of cabled/uncabled and bound/unbound — so display them
as separate badges, not a single status:

```
hw-tor-01 — Dell-N9336C — 48 ports
─────────────────────────────────────────────────────────────────────
Port      Type  Connector  Binding                  Cable               Subnets attached
Eth1/1    data  SFP+       NE pe-lon-01 / wan       → srv-001/Eth1/1    10.1.0.0/24 direct
Eth1/2    data  SFP+       NE access-01 / access:0  → srv-002/Eth1/1    10.2.0.0/24 direct
Eth1/3    data  SFP+       NE access-01 / access:1  uncabled            10.2.0.0/24 direct
Eth1/24   data  SFP+       NE access-01 / access:23 → srv-024/Eth1/1    10.2.0.0/24 direct
Eth1/25   data  SFP+       —                        → srv-009/Eth1/1    10.5.0.0/24 propagated
Eth1/26   data  SFP+       —                        uncabled            —
Eth1/47   data  QSFP28     NE pe-lon-01 / uplink (LAG)  → spine-01/Eth1/1  10.99.0.0/30 direct
Eth1/48   data  QSFP28     NE pe-lon-01 / uplink (LAG)  → spine-02/Eth1/1  10.99.0.0/30 direct
iLO       mgmt  RJ45       NE oob-mgmt / mgmt (auto-rule) uncabled       10.0.0.0/24 direct (IP: 10.0.0.5)
mgmt0     mgmt  RJ45       —                        → tor-mgmt-sw/Eth1/3 —
```

Four independent badge columns:

- **Binding**: empty, or `NE … / iface (mode)`. Auto-rule, single, lag,
  active-passive each get a small badge.
- **Cable**: `uncabled`, or `→ peer-instance/peer-port`.
- **Subnets attached**: one or more rows showing the network and its
  source (`direct` from this port's binding, or `propagated` via cable).
  For ports with `requires_ip=true` and an allocated IP, the IP itself is
  shown.

A summary row at the top: `48 ports — 30 bound, 12 cabled-only, 6 free`.

Filters at the top let the user narrow by any combination ("show only
bound ports", "show only ports attached to subnet 10.1.0.0/24", "show
only uncabled ports"). The filters are URL parameters so the view is
linkable from elsewhere.

---

## Validation

Extend `templates/hw/validation.html` with binding-specific codes:

| Code                                | Severity | Trigger                                                  |
|-------------------------------------|----------|----------------------------------------------------------|
| `NE_PORT_DOUBLE_BOUND`              | error    | Same `(hw_instance, port)` in two NE iface `ports[]`.    |
| `NE_PORT_NOT_FOUND`                 | error    | Binding references a port_id not in the HW template (after expansion). |
| `NE_PORT_TYPE_MISMATCH`             | warning  | NE iface labeled `data` bound to `mgmt` port (or vice versa). |
| `NE_LAG_SINGLE_PORT`                | warning  | `bind_mode='lag'` with only one port in `ports`.         |
| `NE_LAG_SPEED_MISMATCH`             | warning  | LAG members have different `speed_gbps`.                 |
| `NE_LAG_MIXED_HOSTS`                | info     | LAG spans multiple HW instances (probably MC-LAG; fine). |
| `NE_RULE_EXPLICIT_OVERLAP`          | info     | Auto-rule match excluded due to an explicit binding on the same port. |
| `NE_RULE_NO_MATCH`                  | warning  | Auto-rule defined but materialized to zero ports.        |
| `NE_RULE_UNRACKED_PORT`             | warning  | Auto-rule with `group_by:['rack']` matched a port whose HW instance has no rack. |
| `NE_RULE_STALE`                     | warning  | HW changed since the rule was last materialized — rematerialize to refresh. |
| `NE_SUBNET_CONFLICT_AT_CABLE_FAR_END` | error    | A cable's two ports have different attached subnets — a true L2 design error. |
| `NE_PORT_NEEDS_IP_NO_SUBNET`        | error    | Port has `requires_ip=true` and is bound, but the NE iface has no allocated subnet to draw from. |
| `NE_PORT_IP_OUTSIDE_SUBNET`         | error    | Port's `port_overrides.ip` is not within the bound NE iface's subnet (drift from earlier allocation, or manual edit). |
| `NE_PORT_LABEL_FILTER_NO_MATCH`     | info     | A binding picker was filtered by labels yielding zero candidates — surfaced only in the picker, not stored. |

The `NE_PORT_DOUBLE_BOUND` check uses the `hw:port_bound:` index — O(1).
`NE_SUBNET_CONFLICT_AT_CABLE_FAR_END` is computed lazily by the
validation route per cable, comparing `port_attached_subnets` at both
ends.

---

## Subnet attachment, propagation, and port IPs

A binding has three downstream effects that flow from the NE iface's
allocated subnet to the physical world.

### 1. Subnet attaches to the bound port

When an NE iface with an allocated network (subnet) is bound to a HW
port, that subnet is considered **attached** to the port. This is
**derived state**, not stored:

```python
def port_attached_subnets(hw_instance_id, port_id, pid) -> list[dict]:
    """
    Return the subnets attached to this port, with their source.
    [{network_id, network_cidr, source: 'direct'|'propagated',
      ne_instance_id, iface_id}, ...]
    """
```

A port can have **multiple** subnets attached when it's a member of
LAGs across address families (rare but legal) or via cable propagation
(below).

### 2. Subnet propagates one hop through cables

When the bound port has a cable, the **far-end port** also gets the same
subnet attached, with `source='propagated'`. Single-hop only; ipam does
not chase chains of switches. The reasoning: the far-end port is the
device-side port that physically terminates this L2 domain, and the
customer engineer needs to see "this subnet lands on switch X port Y" to
configure the switch correctly.

```
NE iface 'data' (10.1.0.0/24, has IP 10.1.0.1)
   │
   │ bound to (direct)
   ▼
srv-001 / Eth1/1                 ← subnets attached: [10.1.0.0/24 direct via NE 'pe-lon-01' iface 'data']
   │
   │ cable
   ▼
tor-01 / Eth1/24                 ← subnets attached: [10.1.0.0/24 propagated via cable C-042]
```

If `tor-01:Eth1/24` is itself the *source* of another binding (e.g.
it's also a member of a switch-management NE iface), both subnets
appear on the port — both with their own `source` and origin.

**Two different subnets meeting at the two ends of one cable is a
design error** and surfaces as `NE_SUBNET_CONFLICT_AT_CABLE_FAR_END`
(see Validation).

### 3. IP allocation for ports that require one

HW template ports carry an optional `requires_ip: bool` flag (see "Port
data model additions" below). When a port with `requires_ip=true` is
bound — directly or as a LAG member — ipam **automatically allocates an
IP** for that port from the bound NE iface's subnet.

Common cases:

| Port           | port_type | requires_ip | Why                                                    |
|----------------|-----------|-------------|--------------------------------------------------------|
| `iLO`, `iDRAC`, `BMC` | `mgmt`    | true        | Each board has its own IP independent of the OS.       |
| `mgmt0` (Linux server) | `mgmt`    | false       | OS-managed; the NE iface itself carries the IP.        |
| `mgmt0` (Cisco switch) | `mgmt`    | true        | Switch's own management IP, distinct from any hosted NE.|
| `console`              | `console` | false       | Serial console; no IP.                                  |
| `Eth1/1` (data port)   | `data`    | false       | The NE/host owns the IP, not the port itself.           |

The allocation is one IP per port-that-requires-one, stored in
`port_overrides[port_id].ip`. This is **distinct** from the NE iface's
primary IP — both come from the same subnet.

**LAG semantics:**

- For a LAG of N ports, the **iface** gets one primary IP (the bond/LAG
  address). The members do not each get their own IP unless their
  `requires_ip=true` (rare for data ports; common only when LAG members
  individually require management addressing, almost never).
- For mixed LAGs where one member requires an IP and another doesn't,
  allocate only for the requiring members.

**Auto-rule semantics:**

- Each materialized port in `ports[]` is treated as an independent
  binding for allocation purposes — i.e. one IP per port — because
  auto-rule typically describes "every iLO across the project, each
  one gets its own IP."
- IPs come from the **bucket's** subnet, not a shared one. With
  `group_by: ['rack']`, rack R-01's iLOs get IPs from the
  `rack:R-01`-labeled subnet, R-02's from its own. This is the
  per-bucket pool resolution already covered earlier.

### Allocation route

```
POST /ne-instances/<nid>/bindings/<iface_id>/allocate-ips
```

Triggered explicitly by the user after binding. Idempotent: re-running
is a no-op if every requires-IP port already has an IP in the subnet.
The "Allocate" button appears on the NE instance detail page once the
iface has a pushed subnet and at least one bound port that needs an IP.

### Reverse view: "which subnets does this device see?"

The HW instance detail page gains a new section listing every subnet
attached to any of its ports, with the source and propagation marker.
This is the answer to "which subnets terminate on tor-01?"

```
Subnets attached to tor-01
──────────────────────────
10.0.1.0/24   propagated   via cable C-042 from srv-001/Eth1/1
10.0.2.0/24   propagated   via cable C-043 from srv-002/Eth1/1
10.0.99.0/24  direct       NE oob-mgmt iface 'mgmt' on this device's mgmt0
```

Same data, computed via `port_attached_subnets` for each port and
deduplicated by network_id.

### Port data model additions

These changes belong on the HW template `port` entry; they're called
out here because the binding semantics depend on them. Coordinate with
the implementation of `CLAUDE-NAME-PATTERNS.md` (which also touches
ports) to land them in one migration:

```json
{
  "id": "iLO",
  "name": "iLO",
  "name_pattern": null,                  // see CLAUDE-NAME-PATTERNS.md
  "port_type": "mgmt",
  "connector": "RJ45",
  "speed_gbps": 1,
  "count": 1,
  "labels": ["ipmi"],                    // NEW: free-form per-port labels
  "requires_ip": true,                   // NEW: triggers automatic IP allocation when bound
  "breakout_fan_out": 1,
  "notes": ""
}
```

**Defaults** during migration: every existing port gets `labels: []`
and `requires_ip` set by port_type heuristic:

- `port_type='mgmt'` and `name` matches `^(iLO|iDRAC|BMC|ME)` → `true`
- everything else → `false`

The heuristic catches the common cases; users edit the few that the
heuristic gets wrong. Conservative default: when in doubt, `false` —
spurious IP allocation is worse than a missing one (it consumes
addresses for nothing).

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
| POST   | `/ne-instances/<nid>/bindings/<iface_id>/allocate-ips`     | Allocate IPs for bound ports with `requires_ip=true` |
| POST   | `/ne-instances/<nid>/bindings/bulk`                        | Workflow B               |
| POST   | `/ne-instances/<nid>/bindings/autoresolve`                 | Workflow C (whole-device)|
| POST   | `/projects/<pid>/rematerialize-rules`                      | Refresh every auto-rule binding in the project (e.g. after bulk HW import) |
| GET    | `/api/projects/<pid>/ports/search`                         | Filter-driven port picker datasource for Workflow A. Params: `port_types[]`, `connectors[]`, `labels_any[]`, `hw_instance_id` (optional scope), `exclude_bound` (bool, default true), `exclude_cabled` (bool, default false), `page`, `page_size`. |
| GET    | `/api/projects/<pid>/hw/<hwid>/bindings`                   | Read-only HW-side view: bindings + cabling + attached subnets per port |
| GET    | `/api/projects/<pid>/ports/<hwid>:<port_id>/subnets`       | Subnets attached to a port (direct + propagated)        |
| GET    | `/api/projects/<pid>/hw/<hwid>/subnets`                    | All subnets attached to any port on this HW instance — deduplicated, for the "subnets attached to tor-01" view |
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
- [ ] The Workflow A picker filters candidates by **port type, connector,
      and port labels** (AND between categories, OR within each); clearing
      every filter shows every port in scope.
- [ ] The picker shows cabled ports as bindable candidates by default —
      cabling is informational, not a binding gate. The optional "exclude
      cabled-but-unbound" toggle removes them when the user wants to.
- [ ] The HW instance detail page shows binding and cabling as independent
      columns (a port can be in any of the four binding × cabling
      combinations); a separate "Subnets attached" column lists every
      subnet attached to the port with its source (direct/propagated) and
      the per-port allocated IP if any.
- [ ] When NE iface `data` (allocated subnet `10.1.0.0/24`) is bound to
      `srv-001/Eth1/1`, that port's "Subnets attached" shows
      `10.1.0.0/24 direct`, **and** the cable's far-end port
      (`tor-01/Eth1/24`) shows `10.1.0.0/24 propagated` — one hop only.
- [ ] Binding an NE iface to a port with `requires_ip=true` and then
      hitting "Allocate IPs" pops an IP from the iface's subnet into
      `port_overrides[port_id].ip`. Re-running the allocate action is
      idempotent.
- [ ] Two NE bindings whose subnets terminate on the two ends of one
      cable trigger `NE_SUBNET_CONFLICT_AT_CABLE_FAR_END` at validation
      time, with both NE/iface origins cited.
- [ ] A binding on a port with `requires_ip=true` when the NE iface has
      no allocated subnet yet produces `NE_PORT_NEEDS_IP_NO_SUBNET` and
      blocks the allocate-ips action with a clear message.
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
- [ ] Unbinding releases the port (clears `port_overrides.ip` if it was
      allocated by this binding) and the `hw:port_bound:` index entry
      atomically; subnet attachments on the port and its cable far end
      disappear from the next read.
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
