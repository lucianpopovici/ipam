# CLAUDE-SERVICES.md

> Companion to the top-level `CLAUDE.md`. Read it first.
>
> **Strongly related:** `CLAUDE-NE-HW-BINDING.md` (the binding model
> that service values attach to) and `CLAUDE_schema_flexibility.md`
> (the schema and derivation primitives this feature builds on).
>
> **Scope:** Customer-scoped service definitions (oob, oam, diameter, …)
> with a mandatory-fields schema. Services link to NE-type interfaces;
> per-binding the user resolves each service field via one of three
> input modes (text / list / dynamic template). Extends the requirements
> page with non-IP rows alongside the existing IP rows.
>
> **Status:** Design — not yet implemented. One new blueprint, one new
> entity, one new declared relation, extensions to `compute_requirements`
> and the requirements UI. No new dependencies.

---

## The problem

IP allocation is one mandatory NE-side input among many. A real deployment
also needs hostnames, DNS suffixes, OAM addresses, Diameter realm strings,
NTP source identifiers, and so on. Today these live in spreadsheets and
tribal knowledge. There is no link between "this iDRAC port" and "the
hostname that should run behind it"; nothing on the requirements page
tells the user that they still owe 12 hostnames before the iDRAC binding
is shippable.

The right shape is one ipam already has for IP: declare the requirement at
the NE-type-interface level, resolve it at the NE-instance binding level,
and surface unresolved items on the requirements page. The only new ideas
are the **service** container (a named bundle of required fields like
"oob") and three **input modes** for supplying values (one shared text, an
explicit list keyed by target, or a Jinja template evaluated per target).

Services are customer-scoped because the catalogue of services and the
rules around their fields (which can be sensitive — realm strings, naming
conventions) travel with the customer, not with a single project. Two
projects under the same customer share the same service catalogue.

---

## Resolved decisions

| Decision | Resolution |
|---|---|
| **Scope** | Customer-scoped. Lives under `/customers/<cid>/services`. Available to every project belonging to that customer. |
| **Link granularity** | NE-type interface level. All instances of the NE type inherit the linked services. |
| **Per-binding storage** | Values stored on `iface_bindings[iface_id].service_values`. Inputs only — dynamic templates evaluated at read time. |
| **Cardinality source** | Inherits the iface's `sharing` level by default. Per-service-field override available — user explicitly picks scope (`project`/`site`/`pod`/`ne`/`interface`) when defining the service field. |
| **List mode keying** | Keyed by target id (port_id, ne_instance_id, pod_id, …) — survives binding edits. |
| **Dynamic mode UX** | One template textarea plus four `custom_*` text slots referenceable from the template (e.g. `{{ custom_suffix }}`). |
| **Late linking** | Linking a service to an iface with existing bindings creates `mode=null` rows on the requirements page (flagged red). No backfill, no block. |

---

## Data model

### `service:{sid}` — the customer-scoped definition

```json
{
  "id": "svc-oob-001",
  "name": "oob",
  "description": "Out-of-band management.",
  "customer_id": "cust-acme",
  "schema": [
    {
      "id": "f-hostname",
      "name": "hostname",
      "label": "Hostname",
      "field_type": "text",
      "required": true,
      "options": [],
      "default": "",
      "scope_override": null
    },
    {
      "id": "f-domain",
      "name": "domain",
      "label": "Cloud domain name",
      "field_type": "text",
      "required": true,
      "options": [],
      "default": "",
      "scope_override": "project"
    }
  ]
}
```

The `schema` reuses the existing field-definition shape from `get_schema` /
`save_schema` verbatim, with one addition: `scope_override` (null = inherit
iface sharing; otherwise one of `project|site|pod|ne|interface`). Inheriting
is the default because most fields follow the iface's natural granularity —
override is reserved for the cross-cutting case ("one cloud domain regardless
of how many iDRAC ports").

#### Redis keys

```
service:{sid}                       String (JSON)
customer:{cid}:services             Set of service IDs
```

No global services index — services do not exist outside a customer.

### Relation: `iface_of_service`

Declared in `config/relations.yaml`:

```yaml
- { name: iface_of_service, from: ne_iface, to: service, kind: many-to-many }
```

The `ne_iface` side uses a **composite id** `"{ne_type_id}:{iface_id}"`
because interface IDs are only unique within their NE type. A thin helper
hides the composition:

```python
def iface_key(ne_type_id: str, iface_id: str) -> str:
    return f'{ne_type_id}:{iface_id}'
```

All linking/unlinking goes through `services_logic.py`:

```python
def link_service_to_iface(service_id, ne_type_id, iface_id)
def unlink_service_from_iface(service_id, ne_type_id, iface_id)
def services_for_iface(ne_type_id, iface_id) -> list   # → list of service dicts
def ifaces_for_service(service_id) -> list             # → list of (tid, ifid)
```

Storage stays generic in `core/relations.py` — no per-pair helpers.

### Per-binding extension: `iface_bindings[iface_id].service_values`

```json
{
  "service_values": {
    "svc-oob-001": {
      "f-hostname": {
        "mode": "list",
        "values": {
          "port-id-1": "lon-srv-01-idrac",
          "port-id-2": "lon-srv-02-idrac"
        }
      },
      "f-domain": {
        "mode": "text",
        "value": "oob.acme.example"
      }
    },
    "svc-oam-002": {
      "f-hostname": {
        "mode": "dynamic",
        "template": "{{ site.name | lower }}-{{ ne_instance.name | lower }}-oam-{{ index | pad(2) }}",
        "custom": {
          "custom_suffix": "",
          "custom_prefix": "",
          "custom_env": "prod",
          "custom_extra": ""
        }
      }
    }
  }
}
```

Three normalized shapes, one per `mode`. `values` is **always a dict keyed
by target id**, never a list — see Decision: list mode keying.

The keys inside `values` depend on the *effective scope* of that field for
this iface:

| Effective scope | Keys |
|---|---|
| `project` | `"_project"` (single entry — text mode is the natural choice here) |
| `site` | `site_id` |
| `pod` | `pod_id` |
| `ne` | `ne_instance_id` |
| `interface` | `port_id` |

Evaluation results for `dynamic` are **not stored**. They are produced at
read time, exactly as the existing derivations engine does — keeping a
single source of truth (the template + custom slots) and avoiding stale
caches.

---

## Cardinality and scope

Each service field has an **effective scope** computed per (iface, field):

```python
def effective_scope(iface, field) -> str:
    return field.get('scope_override') or iface.get('sharing', 'interface')
```

The effective scope determines how many values the field needs and what
keys they are stored under. The mapping mirrors `compute_requirements`'s
IP sharing semantics, with one extension — `interface` scope expands across
**bindings** to **ports**, because that is the actual unit a user enters
data for:

| Effective scope | Expected values per project |
|---|---|
| `project` | 1 |
| `site` | one per site that hosts an NE of this type with this iface |
| `pod` | one per pod, within each containing site |
| `ne` | one per NE instance of this NE type in the project |
| `interface` | one per port across all bindings of this iface |

The `interface`-scope row is the iDRAC example. The `project`-scope row
is the cloud-domain example. Override on the service field is what lets
`domain` collapse to `project` even when it lives in the same `oob`
service alongside the per-port `hostname` field.

---

## Input modes

Each (iface, service field) requirement row carries an `input_mode` that
the user picks. The mode determines how a value is supplied; the effective
scope determines how many copies of that value are produced.

### `text` — one value, fan-out

The user enters a single string. The same value is applied to every target
id in the effective scope. Useful when the field is genuinely a constant
for the deployment regardless of scope ("the OAM realm is `acme.com`"). The
scope is respected — text-mode at `interface` scope writes the same string
into every port's slot.

```json
{ "mode": "text", "value": "oob.acme.example" }
```

### `list` — one value per target

The user enters N values, one per target id. The UI renders a small grid:

```
Bound port                  Hostname for f-hostname
hw-srv-001 / iLO            [ lon-srv-01-idrac    ]
hw-srv-002 / iLO            [ lon-srv-02-idrac    ]
hw-srv-003 / iLO            [ lon-srv-03-idrac    ]
```

Storage is `values: {port_id: "..."}` — keyed, not positional, so binding
edits (add/remove a port) do not shuffle the user's data. New target ids
appear as empty cells; removed ones leave their value orphaned but visible
(with a "-- removed --" note and a one-click cleanup). Never silently
discard.

### `dynamic` — template per target

The user supplies a Jinja template plus four `custom_*` text slots. The
template is evaluated once per target id with a context (see below). The
evaluated value is **not stored** — recomputed at read time, consistent
with the existing derivations engine.

```json
{
  "mode": "dynamic",
  "template": "{{ site.name | lower }}-{{ ne_instance.name | lower }}-idrac-{{ index | pad(2) }}",
  "custom": {
    "custom_suffix": "",
    "custom_prefix": "",
    "custom_env": "prod",
    "custom_extra": ""
  }
}
```

Why 4 slots: the `custom_*` mechanism is for parameterising a template
across similar deployments without rewriting it. Three is too few when
prefix, suffix, and environment qualifier all come up; five hits diminishing
returns. Slots are always present in the data model (defaulted to `""`) so
the template can reference any of them without conditional logic.

### Mode compatibility matrix

| Mode | `project` | `site` | `pod` | `ne` | `interface` |
|---|---|---|---|---|---|
| `text` | ✓ natural | ✓ (fans out) | ✓ (fans out) | ✓ (fans out) | ✓ (fans out) |
| `list` | ⚠ degenerate (use text) | ✓ | ✓ | ✓ | ✓ natural |
| `dynamic` | ✓ (limited use) | ✓ | ✓ | ✓ | ✓ natural |

`list` at `project` scope is allowed but reduces to a 1-cell grid (the UI
offers `text` instead). `dynamic` at `project` scope is allowed but the
template typically degenerates to a literal.

---

## Dynamic mode context variables

Dynamic mode reuses `core/derivations.py`'s `SandboxedEnvironment` and its
filters (`pad`, the existing default suite). No new engine. The render
context for one evaluation:

| Variable | Type | Notes |
|---|---|---|
| `site` | dict | The site entity (or `{}` for `project`-scope rows) |
| `pod` | dict | The pod entity (or `{}` for `project`/`site`-scope rows) |
| `ne_type` | dict | The NE type definition |
| `ne_instance` | dict | The specific NE instance (or `{}` for shared scopes above `ne`) |
| `iface` | dict | The NE-type iface definition |
| `port` | dict | The bound HW port (only for `interface`-scope rows; carries `hw_instance_id`, `port_id`, `name`, `port_type`) |
| `hw_instance` | dict | The HW instance the port belongs to (only for `interface`-scope) |
| `index` | int | 1-based peer index within the scope (1..N across NE instances for `ne` scope, 1..M across ports for `interface` scope) |
| `custom_*` | str | The four user-supplied custom slots, each defaulting to `""` |

Variables for unavailable scopes are present-but-empty rather than missing,
so a template that references `site.name` while running at project scope
renders to an empty string rather than crashing — same forgiving model the
existing derivations use.

Sort order for `index`: by `name` of the scoped entity for stable peer
indexing, matching the existing `peer_index` resolver semantics.

---

## Requirements page integration

`compute_requirements` (in `ne.py`) gains a second emission path. Existing
rows become `kind: 'ip'`; service rows are `kind: 'service'`. Both flow
through the same persistence (`project:{pid}:requirements`) and the same UI
table, with the `kind` column controlling row rendering.

### New row shape

```python
{
  'kind':            'service',
  'key':             'svc:{sid}:{field_id}:{scope_disambiguator}',
  'service_id':      'svc-oob-001',
  'service_name':    'oob',
  'field_id':        'f-hostname',
  'field_name':      'hostname',
  'field_label':     'Hostname',
  'field_type':      'text',
  'required':        True,
  'ne_type_id':      'nt-server',
  'ne_type_name':    'Server',
  'iface_id':        'ifid-idrac',
  'iface_name':      'iDRAC',
  'effective_scope': 'interface',
  'site_id':         '...',     # present per scope
  'pod_id':          '...',     # present per scope
  'expected_count':  12,
  'resolved_count':  9,
  'missing':         3,
  'input_mode':      'list',    # or None when unresolved
  'mode_payload':    {...},     # mode-specific (value/values/template+custom)
  'pushed':          False,
}
```

`scope_disambiguator` ensures uniqueness when the same field is emitted
multiple times (once per site for `site` scope, etc.).

### Emission algorithm

In the existing `compute_requirements` loop, for each `(site, pod, slot, iface)`:

1. Compute IP rows as today (tagged `kind: 'ip'`).
2. For each `service` linked to `(ne_type_id, iface_id)` via
   `iface_of_service`:
   - Filter by `service.customer_id == project.customer_id` (defense in
     depth — see global NE types note in the UI section).
   - For each field in `service.schema`:
     - Compute `effective_scope`.
     - Compute `expected_count` and target ids per scope.
     - Dedupe by row key — `project` scope emits once across the whole
       project; `site` scope once per site; etc. (Same dedup dictionary
       used for IP rows.)
     - Load the user's saved `input_mode` and payload from
       `iface_bindings[iface_id].service_values[sid][fid]` of the relevant
       binding(s); compute `resolved_count` / `missing` by checking how
       many target ids have non-empty values. (text and dynamic are
       considered fully resolved if `mode` is set and, for text, `value`
       is non-empty; dynamic always renders even for empty custom slots.)

### Persistence of user input

User input lives on the binding, not on the requirements row. Setting a
mode/value on a requirements row writes back to **every binding that
contributes to that row's scope**:

- `interface` scope: write to the one binding owning the port_id.
- `ne` scope: write to that NE instance's binding.
- `pod`/`site`/`project` scope: write the same payload to every binding in
  scope (denormalised — but the only sensible source of truth, since
  bindings live on instances and there is no shared "pod-scope
  service-values" container).

When `compute_requirements` reads back, it reconciles by reading any
binding in scope and verifying they agree. Disagreement (rare; only
possible if a binding was edited directly while a shared-scope row was
set) surfaces as a "values diverge" warning on the row with a one-click
"republish to all bindings" action.

### Save endpoint

The existing "push requirement to IPAM" action is IP-specific. Add a
parallel save endpoint for service rows:

```
POST /projects/<pid>/requirements/service/<row_key>
  form: mode, value | values_json | template + custom_*
```

There is no separate "push" for service rows — they are not allocated
against a pool; they are just persisted onto bindings.

---

## Coverage and validation

A service field with `required: true` is unsatisfied if any target id in
its scope has no value (or if `mode` is unset). The Health Engine learns
one new gap type, `service_field_missing`, with the same structure as
existing gaps:

```python
{
  'kind':       'service_field_missing',
  'severity':   'error' if field.required else 'info',
  'service':    'oob',
  'field':      'hostname',
  'iface':      'iDRAC',
  'ne_type':    'Server',
  'missing':    ['port-id-3', 'port-id-7', 'port-id-12'],
  'project_id': pid,
}
```

The project dashboard's "open issues" count picks this up automatically
via the existing gap aggregation. No new dashboard wiring beyond exposing
the new gap kind.

---

## UI surfaces

### `/customers/<cid>/services` — service catalogue

Card list, similar to the NE types list. Each card shows name, field
count, linked-iface count (via `ifaces_for_service`), description.
Buttons: Edit, Delete (with impact preview), View linked ifaces.

### `/customers/<cid>/services/<sid>/edit` — service editor

Two-pane layout matching the NE type form:

- Left: name, description.
- Right: schema field editor. Reuses `templates/ne/_schema_editor.html` as
  a macro — pass `entity='service'` and the field list. Adds a
  `scope_override` dropdown per field (default "inherit from iface", plus
  the five scope levels).

### NE type form — service linking

On the NE type form (`templates/ne/ne_type_form.html`), each iface row
gains a "Linked services" multi-select populated from the customer's
service catalogue. Saving the NE type writes the relation via
`link_service_to_iface` and removes unchecked entries via
`unlink_service_from_iface`. Since NE types can be global or
project-scoped and services are customer-scoped, the multi-select is
populated from:

- For project-scoped NE types: the project's customer's services.
- For global NE types: a union of all customers' services, **filtered at
  requirements computation time** by customer match. A global NE type can
  legally carry links to multiple customers' services; only the relevant
  ones surface per project.

### Requirements page — service row rendering

Service rows render with the same table chrome as IP rows. The "mode"
column becomes a small dropdown (text / list / dynamic / unresolved).
Selecting a mode opens an inline editor:

- **text:** one input field.
- **list:** the grid of target → input. Header shows count.
- **dynamic:** template textarea (monospace), the four `custom_*` inputs,
  and a live preview box that renders the first 3 evaluations to help the
  user validate the template.

Save buttons are per-row, AJAX, returning the new resolved/missing count
for in-place update.

---

## Routes

| Method | URL | Purpose |
|---|---|---|
| GET    | `/customers/<cid>/services`                                   | List services |
| GET    | `/customers/<cid>/services/add`                               | Service form (new) |
| POST   | `/customers/<cid>/services/add`                               | Create service |
| GET    | `/customers/<cid>/services/<sid>/edit`                        | Service form (edit) |
| POST   | `/customers/<cid>/services/<sid>/edit`                        | Update service |
| POST   | `/customers/<cid>/services/<sid>/delete`                      | Delete service |
| GET    | `/customers/<cid>/services/<sid>/impact`                      | Impact preview (linked ifaces, projects affected) |
| POST   | `/ne-types/<tid>/ifaces/<ifid>/services`                      | Replace linked services for one iface (form: service_ids[]) |
| GET    | `/api/customers/<cid>/services`                               | JSON for NE-type form multi-select |
| GET    | `/api/projects/<pid>/requirements/service/<row_key>/preview`  | Dynamic-template preview (first 3 evaluations) |
| POST   | `/projects/<pid>/requirements/service/<row_key>`              | Save mode + payload for one service requirement row |

All routes live in a new `services.py` blueprint registered in `app.py`.

---

## Acceptance criteria

- [ ] A user can define a service `oob` under a customer with two fields,
      `hostname` (inherit scope) and `domain` (scope override = `project`).
- [ ] On the NE type form, an iface row's "Linked services" multi-select
      shows the customer's services (when the NE type is project-scoped)
      or all customers' services (when global).
- [ ] Linking `oob` to the iDRAC iface, with two NE Server instances each
      having one iDRAC binding of 4 ports, produces on the requirements
      page: one row for `hostname` (interface scope, expected_count=8)
      and one row for `domain` (project scope, expected_count=1).
- [ ] Setting `hostname` to `list` mode and entering 8 values writes them
      into the relevant bindings keyed by port_id; reload shows the same
      values in the same cells.
- [ ] Setting `hostname` to `dynamic` mode with template
      `{{ site.name|lower }}-{{ ne_instance.name|lower }}-idrac-{{ index|pad(2) }}`
      produces evaluations like `lon-srv01-idrac-01` … `lon-srv02-idrac-04`;
      preview shows the first 3.
- [ ] Removing a port from an iDRAC binding leaves the orphaned value
      visible with a `-- removed --` note and a cleanup link.
- [ ] Linking `oob` to an iface that already has populated bindings
      creates the row in `unresolved` mode; saving any mode immediately
      flips the row to resolved status.
- [ ] Setting `domain` (project-scope) to `text="oob.acme.example"` writes
      the same value into every iDRAC binding's `service_values` and
      surfaces a single resolved row.
- [ ] Required service fields with at least one missing target surface
      a `service_field_missing` gap on the project dashboard.
- [ ] Deleting a service refuses (with impact preview) if any iface is
      currently linked; force-delete clears all relations and surfaces
      the affected bindings' values as orphaned.
- [ ] Customer-A's services are invisible from Customer-B's projects'
      NE type forms and requirements pages.

---

## Files touched

```
services.py                                       NEW blueprint
  routes: /customers/<cid>/services/*, /ne-types/<tid>/ifaces/<ifid>/services,
          /api/customers/<cid>/services,
          /api/projects/<pid>/requirements/service/<row_key>/preview,
          /projects/<pid>/requirements/service/<row_key>

services_logic.py                                 NEW
  - get_service / save_service / delete_service / customer_services
  - link_service_to_iface / unlink_service_from_iface
  - services_for_iface / ifaces_for_service
  - effective_scope / expected_targets
  - read_service_values / write_service_values (per binding)
  - evaluate_dynamic (wraps core/derivations Jinja env)
  - service_field_gaps (Health Engine integration)

config/relations.yaml                             EXTEND
  - { name: iface_of_service, from: ne_iface, to: service, kind: many-to-many }

ne.py                                             EXTEND
  - compute_requirements: emit service rows alongside IP rows
  - save_ne_type / delete_ne_type: cascade service link cleanup
    on iface removal

ipam.py                                           EXTEND
  - project_requirements view: render service rows
  - delete_project: cascade clean-up of binding service_values is automatic
    (lives on bindings which the existing path already deletes)

health_logic.py                                   EXTEND
  - Add service_field_missing gap kind

templates/services/
  services_list.html                              NEW
  service_form.html                               NEW
  service_impact.html                             NEW
  _service_link_picker.html                       NEW   (used by ne_type_form.html)
  _requirement_service_row.html                   NEW   (one row + editor)

templates/ne/ne_type_form.html                    EXTEND
  - Per-iface service multi-select widget

templates/_project_nav.html                       EXTEND
  - Requirements page gains a "Services" tab/filter alongside "IP"

templates/base.html                               EXTEND
  - ⚙ Admin dropdown: link to customer services from customer detail page

tests/unit/test_services_helpers.py               NEW
tests/unit/test_service_requirements.py           NEW
tests/api/test_services_routes.py                 NEW
tests/e2e/test_service_flow.py                    NEW

scripts/migrate_011_services.py                   NEW (no-op for empty deployments;
                                                  validates customer index integrity)

app.py                                            EXTEND — register services_bp
```

---

## Out of scope

- **Service templates / global service catalogue.** Each customer curates
  their own services. No federated library. (Same posture as customer
  template sets.)
- **Per-environment overrides** (different `domain` for prod vs staging
  within one customer). v2 — would require either a project-scope service
  override layer or environment as a first-class concept.
- **Validation rules beyond `required` and `field_type`.** No regex,
  length bounds, uniqueness constraints. The schema is for *capture*, not
  *correctness*. Defer richer validation to a follow-up that extends the
  shared schema-field model — it would benefit other entities (NE params,
  site params) the same way.
- **Service values in document generation.** The context snapshot passed
  to Jinja templates should expose `binding.service_values` so PDFs can
  render the resolved values, but the rendering logic itself is the
  document-gen team's concern. This doc only guarantees the data is
  reachable.
- **History / audit of value changes.** Falls under the audit-log work
  package. Routes here just need to emit audit events when that lands.
- **Service-scoped IP pools.** Tempting (one OOB subnet per service), but
  the existing label-based pool resolution already covers this — tag the
  iface with `service:oob` and the subnet with the same label. No new
  mechanism needed.
- **Cross-iface service constraints** ("the `realm` field on `diameter`
  must match the `realm` on `oam`"). Out of scope; would require an
  expression-validation layer. If it becomes a real need, deriving one
  field from another via the existing derivation engine is the path,
  not a constraint system.
- **Per-link scope override.** `scope_override` lives on the service
  field, not on the link. The same field carries the same scope wherever
  the service is linked. Per-link overrides would multiply the validation
  surface for marginal gain; revisit only if a real use case emerges.
