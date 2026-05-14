# IPAM — Codebase Guide for AI Assistants

## Project Overview

A Flask-based IP Address Management (IPAM) web application backed by Redis. It
manages IP addressing, network element topology, hardware resources, and external
integrations (currently VMware).

---

## Architecture

### Tech Stack

| Layer       | Technology                                        |
|-------------|---------------------------------------------------|
| Web         | Flask ≥ 3.0, Jinja2, Bootstrap 5.3               |
| Storage     | Redis ≥ 5.0 (key–value, all data stored as JSON) |
| Tests       | pytest, fakeredis, Playwright                     |
| Server      | Runs on `192.168.56.107:5000` (debug mode)        |

### Module Structure

```
app.py              Flask app factory — blueprints, context processors, before_request hooks
db.py               Shared Redis client (imported as `r` in every blueprint)
auth.py             Authentication: User model, login/logout, Redis backend
ipam.py             IPAM blueprint: projects, subnets, IPs, labels, templates, validate API
ne.py               Network Element blueprint: NE types, sites, PODs, requirements, impact API
hw.py               Hardware blueprint: templates, BoM, instances, racks, cables
hw_logic.py         Business logic for hardware management (validation, placement)
health_logic.py     Health Engine: subnet utilization, requirement gaps, cabling
vmware.py           VMware connector: REST API + UI for IP allocation to VMware

config/
  app_config.yaml   Enum values (ne_kinds, field_types, sharing_levels, entity_types)
  relations.yaml    Declared many-to-many relations (loaded for documentation; storage is in core/relations.py)

core/
  __init__.py       Empty package marker
  relations.py      Generic relation store: relate/unrelate/related/clear_entity
  forms.py          form_errors() helper for per-field validation errors
  jobs.py           Async job tracking: create_job/update_job/get_job/run_job

static/
  js/
    validate.js     Client-side field validation (data-validate="cidr" convention)
    bulk-actions.js List-view bulk selection and bulk-delete

templates/          Jinja2 HTML templates, one sub-folder per blueprint
  _macros.html      Shared macros: breadcrumbs, empty_state, confirm_delete, job_progress,
                    label_pill(s), utilization_bar, summary_card
  _project_nav.html Project context strip (auto-included by base.html when g.current_project is set)
  base.html         Base layout: navbar, project nav, shared delete modal, scripts

tests/
  conftest.py         Shared fixtures (fake_redis, Flask client, seed helpers)
  unit/               Pure helper-function tests (no I/O)
  api/                Flask test-client tests (fakeredis, no browser)
  e2e/                Playwright browser tests (requires live server)
```

### Blueprint Registration

Each blueprint is registered in `app.py` with `url_prefix=''`; routes carry
their own prefixes manually.

```python
from ipam   import ipam_bp
from ne     import ne_bp
from hw     import hw_bp
from vmware import vmware_bp

app.register_blueprint(ipam_bp)
app.register_blueprint(ne_bp)
app.register_blueprint(hw_bp)
app.register_blueprint(vmware_bp)
```

### app.py Hooks and Context Processors

```python
# Injects is_active(*endpoints) into every template
@app.context_processor
def inject_nav_helpers(): ...

# Sets g.current_project and g.all_projects when a `pid` URL arg is present
@app.before_request
def attach_project_context(): ...
```

`is_active(*endpoints)` accepts exact endpoint names or dot-suffix prefixes
(e.g. `is_active('ne.list_sites')` or `is_active('vmware.')`).

---

## Redis Data Model

### Key Naming Convention

```
project:{pid}               JSON — project record
project:{pid}:networks      Set  — network IDs in project
project:{pid}:labels        Set  — project-scoped labels
project:{pid}:templates     Set  — project-scoped subnet template IDs
project:{pid}:sites         Set  — site IDs
project:{pid}:pods          Set  — POD IDs
project:{pid}:ne_types      Set  — NE type IDs
project:{pid}:bom           JSON — bill of materials line items
project:{pid}:hw:templates  Set  — HW template IDs
project:{pid}:hw:instances  Set  — HW instance IDs
project:{pid}:hw:cables     Set  — cable IDs

user:{username}             JSON — user record (username, hashed password)

network:{nid}               JSON — network/subnet record
network:{nid}:ips           Set  — IP strings allocated in subnet
network:{nid}:labels        Set  — labels on subnet
label:{label}:nets          Set  — network IDs tagged with label

ip:{ip_str}                 JSON — IP address record

template:{tid}              JSON — subnet template record
ne_type:{tid}               JSON — NE type record
site:{sid}                  JSON — site record
pod:{pid_}                  JSON — POD record
pod:{pid_}:slots            JSON — NE slot list

rel:{name}:fwd:{a_id}       Set  — B-ids linked from A  (generic relation store)
rel:{name}:rev:{b_id}       Set  — A-ids linked to B    (generic relation store)
  → pod_of_site: fwd = sites for a pod; rev = pods for a site

hw:template:{tid}           JSON — hardware template
hw:instance:{iid}           JSON — hardware instance
hw:cable:{cid}              JSON — cable record
hw:rack:{rack_iid}:slots    JSON — rack slot list
hw:connectors               Set  — connector type names
hw:compat:{connector}       Set  — compatible connector names

vmware:subnets              Set  — network IDs enabled for VMware allocation
vmware:alloc:{ip}           JSON — VMware allocation metadata per IP
vmware:net:{net_id}:ips     Set  — IPs allocated via VMware per subnet

job:{id}                    JSON — async job state {status, progress, total, message, result}
                                   TTL: 1 hour. status: running | done | error

projects:index              Set  — all project IDs
networks:index              Set  — all network IDs
ne_types:index              Set  — global NE type IDs
templates:global            Set  — global subnet template IDs
labels:global               Set  — global label names
hw:templates:index          Set  — global HW template IDs
hw:instances:index          Set  — all instance IDs
hw:cables:index             Set  — all cable IDs
```

**Note:** `site:{sid}:pods` and `pod:{pid_}:sites` no longer exist. The
site↔POD many-to-many is stored in the generic relation store under the name
`pod_of_site`.

### Core JSON Schemas

**User**
```json
{ "username": "admin", "password": "pbkdf2:sha256:..." }
```

**Project**
```json
{ "id": "abc123", "name": "Production", "supernet": "10.0.0.0/8", "description": "" }
```

**Network (Subnet)**
```json
{
  "id": "net456", "name": "mgmt", "cidr": "10.0.1.0/24",
  "description": "", "vlan": "1001", "project_id": "abc123",
  "template_id": "tmpl789",
  "pending_slots": [{"ip": "10.0.1.1", "role": "gateway", "status": "reserved"}]
}
```

**IP Address**
```json
{
  "ip": "10.0.1.10", "hostname": "vm-01", "description": "VMware: vm-01",
  "status": "allocated",   // "reserved" | "allocated" | "dhcp"
  "network_id": "net456", "from_template": "tmpl789"
}
```

**HW Template**
```json
{
  "id": "tmpl_123", "name": "Standard Server", "u_height": 2, "depth": "full",
  "power_w": 500, "weight_kg": 20,
  "max_power_w": 0, "max_weight_kg": 0,  // Non-zero for Rack templates
  "ports": [{"name": "Eth1", "type": "LC", "direction": "in"}]
}
```

**VMware Allocation**
```json
{
  "ip": "10.0.1.10", "network_id": "net456", "cidr": "10.0.1.0/24",
  "vm_name": "vm-01", "datacenter": "DC-East", "cluster": "Prod",
  "allocated_at": "2026-03-28T10:00:00+00:00"
}
```

---

## Blueprint Reference

### ipam.py — Routes

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/projects/add` | Create project |
| GET | `/projects/<pid>` | Project detail |
| POST | `/projects/<pid>/delete` | Delete project |
| GET/POST | `/projects/<pid>/subnet/add` | Add subnet (manual or auto-carved) |
| POST | `/projects/<pid>/subnet/bulk` | Bulk add subnets (JSON body) |
| POST | `/projects/<pid>/subnets/bulk-delete` | Bulk delete subnets (JSON body `{ids:[...]}`) |
| GET/POST | `/networks/<net_id>/edit` | Edit subnet |
| POST | `/networks/<net_id>/delete` | Delete subnet |
| GET | `/networks/<net_id>` | Network detail + IP list |
| GET/POST | `/networks/<net_id>/ip/add` | Add IP to subnet |
| GET/POST | `/ip/<ip>/edit` | Edit IP record |
| POST | `/ip/<ip>/delete` | Delete IP |
| GET | `/api/networks/<net_id>/next` | Next available IP (JSON) |
| GET | `/api/pool` | Pool query by labels (JSON) |
| GET | `/api/networks/export` | Export all subnets (CSV) |
| GET | `/api/validate/cidr` | Validate CIDR string → `{ok, error}` |
| GET | `/api/validate/ip-interface` | Validate IP/prefix → `{ok, error}` |
| GET | `/api/validate/prefix-in-supernet` | Validate CIDR fits within `?supernet=` → `{ok, error}` |
| GET | `/api/jobs/<job_id>` | Poll async job state → `{status, progress, total, message, result}` |
| GET | `/api/projects/<pid>/impact` | Cascade preview for project delete → `{label, cascades}` |
| GET | `/dashboard` | Visual analytics and health alerts |
| GET | `/pool` | Pool query UI |
| GET | `/search` | Global search (IP, hostname, HW, Projects) |
| GET | `/overview` | Global utilisation overview |
| GET | `/labels` | Manage global labels |
| GET/POST | `/templates` | Manage global subnet templates |
| POST | `/networks/<net_id>/slots/confirm` | Confirm pending slot |
| POST | `/networks/<net_id>/slots/confirm_all` | Confirm all pending slots |
| POST | `/networks/<net_id>/slots/dismiss` | Dismiss pending slot |
| POST | `/networks/<net_id>/slots/dismiss_all` | Dismiss all pending slots |

### ne.py — Routes

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/admin/schemas` | Manage global entity schemas |
| GET/POST | `/projects/<pid>/schemas` | Manage project-specific entity schemas |
| GET | `/ne-types` | List global NE types |
| GET/POST | `/ne-types/add` | Add global NE type |
| GET/POST | `/projects/<pid>/ne-types/add` | Add project-scoped NE type |
| GET/POST | `/ne-types/<tid>/edit` | Edit NE type |
| POST | `/ne-types/<tid>/delete` | Delete NE type |
| GET | `/projects/<pid>/ne-types` | List project NE types |
| GET | `/projects/<pid>/sites` | List project sites |
| GET/POST | `/projects/<pid>/sites/add` | Add site to project |
| GET/POST | `/projects/<pid>/sites/bulk` | Bulk add sites (pattern textarea) |
| GET/POST | `/projects/<pid>/sites/<sid>/edit` | Edit site |
| POST | `/projects/<pid>/sites/<sid>/delete` | Delete site |
| POST | `/projects/<pid>/sites/bulk-delete` | Bulk delete sites (JSON `{ids:[...]}`) |
| POST | `/projects/<pid>/sites/<sid>/assign-pod` | Assign POD to site |
| POST | `/projects/<pid>/sites/<sid>/unassign-pod` | Unassign POD from site |
| GET | `/projects/<pid>/sites/<sid>` | Site detail (params + PODs) |
| GET | `/projects/<pid>/pods` | List project PODs |
| GET/POST | `/projects/<pid>/pods/add` | Add POD to project |
| GET/POST | `/projects/<pid>/pods/<pod_id>/edit` | Edit POD |
| POST | `/projects/<pid>/pods/<pod_id>/delete` | Delete POD |
| POST | `/projects/<pid>/pods/bulk-delete` | Bulk delete PODs (JSON `{ids:[...]}`) |
| GET | `/projects/<pid>/pods/<pod_id>` | POD detail (params + sites + slots) |
| POST | `/projects/<pid>/pods/<pod_id>/slots` | Update POD NE slots |
| POST | `/projects/<pid>/pods/<pod_id>/assign-site` | Assign site to POD |
| POST | `/projects/<pid>/pods/<pod_id>/unassign-site` | Unassign site from POD |
| GET | `/projects/<pid>/topology` | Visual topology map (Sites/PODs/HW) |
| GET | `/api/projects/<pid>/topology` | Topology data API (Cytoscape.js) |
| GET | `/projects/<pid>/requirements` | View subnet requirements for project |
| POST | `/projects/<pid>/requirements/push` | Push selected requirements to IPAM (JSON) |
| POST | `/projects/<pid>/requirements/push-all` | Push all requirements to IPAM (JSON) |
| GET | `/api/sites/<sid>/impact` | Cascade preview for site delete → `{label, cascades}` |
| GET | `/api/pods/<pod_id>/impact` | Cascade preview for POD delete → `{label, cascades}` |
| GET | `/api/ne-types/<tid>/impact` | Cascade preview for NE type delete → `{label, cascades}` |

### hw.py — Routes

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/admin/hw/connectors` | Manage connector types and compatibility |
| GET | `/hw/templates` | List global HW templates |
| GET/POST | `/hw/templates/add` | Add global HW template |
| GET/POST | `/projects/<pid>/hw/templates/add` | Add project-scoped HW template |
| GET/POST | `/hw/templates/<tid>/edit` | Edit HW template |
| POST | `/hw/templates/<tid>/delete` | Delete HW template |
| GET | `/projects/<pid>/hw/templates` | List project HW templates |
| GET/POST | `/projects/<pid>/bom` | Manage Project Bill of Materials (BoM) |
| POST | `/projects/<pid>/bom/generate` | Generate instances from BoM line item |
| POST | `/projects/<pid>/bom/generate-all` | Generate all instances (form→sync redirect; JSON→async job) |
| GET | `/projects/<pid>/hw/inventory` | List HW instances in project |
| GET/POST | `/projects/<pid>/hw/instances/add` | Add HW instance |
| GET/POST | `/projects/<pid>/hw/instances/<iid>/edit` | Edit HW instance |
| POST | `/projects/<pid>/hw/instances/<iid>/delete` | Delete HW instance |
| GET | `/projects/<pid>/hw/racks` | List racks in project |
| GET | `/projects/<pid>/hw/racks/<rack_iid>` | Rack detail (visual elevation) |
| POST | `/projects/<pid>/hw/racks/<rack_iid>/place` | Place instance in rack slot |
| POST | `/projects/<pid>/hw/racks/<rack_iid>/remove` | Remove instance from rack slot |
| POST | `/api/projects/<pid>/hw/racks/<rack_iid>/place` | API: Place instance in rack slot |
| GET | `/projects/<pid>/hw/inventory/export` | Export HW inventory (CSV) |
| GET | `/projects/<pid>/hw/cables/<cid>/trace` | Visual end-to-end cable trace |
| GET/POST | `/projects/<pid>/hw/rack-table` | Batch rack placement table |
| GET | `/projects/<pid>/hw/cables` | List project cables |
| GET/POST | `/projects/<pid>/hw/cables/add` | Add/Edit cable |
| GET/POST | `/projects/<pid>/hw/cables/<cid>/edit` | Edit cable |
| POST | `/projects/<pid>/hw/cables/<cid>/delete` | Delete cable |
| GET | `/api/projects/<pid>/hw/instance-ports/<iid>` | API: List ports for instance |
| GET | `/projects/<pid>/hw/validate` | HW validation report |
| GET | `/api/projects/<pid>/hw/validate` | API: HW validation report (JSON) |

### vmware.py — Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/vmware` | VMware connector dashboard |
| POST | `/vmware/networks/<net_id>/enable` | Enable subnet for VMware |
| POST | `/vmware/networks/<net_id>/disable` | Disable subnet for VMware |
| GET | `/api/vmware/networks` | List enabled subnets (JSON) |
| POST | `/api/vmware/networks/<net_id>/allocate` | Allocate next IP (JSON) |
| GET | `/api/vmware/networks/<net_id>/ips` | List VMware-allocated IPs (JSON) |
| DELETE | `/api/vmware/ip/<path:ip_str>/release` | Release IP back to pool (JSON) |

**Allocate request body (all fields optional):**
```json
{ "vm_name": "prod-vm-01", "datacenter": "DC-East", "cluster": "Cluster-01" }
```

### auth.py — Authentication

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/login` | Login page |
| GET | `/logout` | Logout and redirect to login |

**User Model:** Implements `UserMixin`. Users are stored in Redis as `user:{username}` with hashed passwords (PBKDF2). A default `admin/admin` account is created on first start.

---

## Hardware & Health Validation

The application performs continuous health checks:
- **Power/Weight:** Rack capacity vs. device consumption.
- **Utilization:** Alert if subnets are >90% full.
- **Requirement Gap:** Missing subnets required by the logical POD/Site design.
- **Connectivity:** Half-connected cables or connector mismatches.
- **Space:** Standard unit height (`u_height`) and slot availability checks.

---

## Core Library Modules

### `core/relations.py` — Generic Relation Store

Replaces per-pair Redis key helpers (no more `_site_pods_key` / `_pod_sites_key`).
Uses `import db; db.r` so test monkeypatching of `db.r` propagates automatically.

```python
relate(rel_name, a_id, b_id)           # add link (bidirectional)
unrelate(rel_name, a_id, b_id)         # remove link
related(rel_name, entity_id, direction='fwd') -> set   # fwd or rev
clear_entity(rel_name, entity_id)      # remove all links for this entity
```

Storage: `rel:{name}:fwd:{a_id}` (B-ids) and `rel:{name}:rev:{b_id}` (A-ids).

**Don't add new per-pair key helpers** (`_foo_bars_key` / `_bar_foos_key`).
New many-to-many relations go in `config/relations.yaml` and use this module.

### `core/forms.py` — Form Validation Helper

```python
errors = form_errors(
    ('name',     bool(name),              'Name is required.'),
    ('supernet', validate_ip_interface(v), 'Invalid CIDR.'),
)
if errors:
    return render_template('...', errors=errors, form_values=request.form)
```

Returns `{}` on success, `{field: message}` on failure. In templates:

```jinja
<input name="name" class="form-control {% if errors.name %}is-invalid{% endif %}"
       value="{{ form_values.name or entity.name or '' }}">
{% if errors.name %}<div class="invalid-feedback">{{ errors.name }}</div>{% endif %}
```

**Never `redirect(request.url)` after a validation error.** Re-render with
`errors=errors, form_values=request.form`.

### `core/jobs.py` — Async Job Tracking

For operations expected to take >2 seconds. Jobs are stored in Redis with a 1-hour TTL.

```python
job_id = create_job()                          # allocates Redis key, status='running'
update_job(job_id, done, total, message,       # called periodically from worker
           status='running', result=None)
job = get_job(job_id)                          # returns dict or None
run_job(app, job_id, fn, *args, **kwargs)      # starts daemon thread with app context
```

**Pattern for async endpoints:**

```python
if request.is_json:                            # JSON POST → async
    job_id = create_job()
    run_job(app, job_id, _work_fn, pid, ...)
    return jsonify({'job_id': job_id})
# form POST → sync (tests use this path)
```

Client polls `GET /api/jobs/<job_id>` every 500 ms. The `job_progress()` macro
in `_macros.html` renders the progress bar; `base.html` contains the polling
script for elements that have `data-job-id` set at load time.

**Don't add new synchronous endpoints for operations touching >10 records.**
Use `core/jobs.py` and the JSON/form dual-path.

---

## Configuration

### `config/app_config.yaml` — Enum Values

Loaded at import time in `ne.py` with hardcoded fallbacks:

```yaml
ne_kinds:       [CNF, VNF, PNF, VM, Container]
field_types:    [text, number, textarea, dropdown, multi-select, checkbox]
sharing_levels: [project, site, pod, ne, interface]
entity_types:   [site, pod, ne, interface]
```

**Don't add new hardcoded enums** to `ne.py` or elsewhere. Add to this file.

### `config/relations.yaml` — Declared Relations

Documents which many-to-many relations exist. Storage is handled by
`core/relations.py`; this file is the human-readable declaration:

```yaml
relations:
  - { name: pod_of_site, from: pod, to: site, kind: many-to-many }
```

New relations land here. No Python changes needed for storage — call
`relate()` / `unrelate()` / `related()` with the relation name.

---

## Development Conventions

### Adding a New Blueprint

1. Create `<name>.py` with `<name>_bp = Blueprint('<name>', __name__, url_prefix='')`
2. Import and register in `app.py`
3. If the blueprint uses its own `r = db.r` reference, add it to the
   monkeypatch loop in `tests/conftest.py`
4. Create `templates/<name>/` for Jinja2 templates
5. New global config links go in the `⚙ Admin` dropdown in `base.html` — **don't add top-level navbar links**
6. Write `tests/unit/test_<name>_helpers.py` and `tests/api/test_<name>_api.py`

### Redis Access Pattern

Every blueprint imports a module-level `r`:

```python
from db import r
```

`core/` modules use `import db; db.r` so test monkeypatching propagates without
needing a conftest entry.

**Critical for tests:** Never bind `r` as a local name outside a function (e.g.
`from db import r as _r` at module level in test helpers). Monkeypatching
replaces the *module attribute* `vmware.r`, `ipam.r`, etc. A cached local
reference still points to the real Redis. Access `r` via the module dynamically:

```python
# WRONG in test helpers — bypasses monkeypatch
from db import r as _r
_r.sadd(key, val)

# RIGHT — accesses monkeypatched attribute at call time
import ipam
ipam.r.sadd(key, val)
```

### ID Generation

All entities use 8-character UUID prefixes:

```python
def new_id() -> str:
    return str(uuid.uuid4())[:8]
```

### Labels

- Labels are plain strings stored in Redis sets
- Two scopes: global (`labels:global`) and project-scoped (`project:{pid}:labels`)
- Networks are tagged via `add_labels_to_network(net_id, labels)` which maintains
  the reverse index `label:{label}:nets`

### Subnet Templates

Templates contain `rules` lists. Rule types:
- `from_start` — allocate at offset from first host
- `from_end` — allocate from last host(s)
- `range` — allocate a range by host index

Rules produce `pending_slots` on a network until confirmed or dismissed.

### Name Expansion Patterns

Forms that create multiple entities at once support a `{start..end}` range
syntax in the name field, with zero-padding preserved from the literal digits.

| Input | Result |
|-------|--------|
| `ran{0001..1200}` | `ran0001`, `ran0002`, …, `ran1200` |
| `site-{01..10}-prod` | `site-01-prod`, …, `site-10-prod` |
| `gi-{N}/0` (count=3) | `gi-0/0`, `gi-1/0`, `gi-2/0` |
| `eth` (count=4) | `eth0`, `eth1`, `eth2`, `eth3` |

**Used in:**
- Site bulk create: `expand_site_pattern(pattern)` in `ne.py` (cap: 10,000)
- NE Type interface builder "Add Interface" panel (cap: 1,024)

When adding a new "bulk create" form, reuse `expand_site_pattern` server-side
and the same `{start..end}` / `{N}` / count convention client-side.

---

## Template Conventions

### Navigation

**Top navbar** contains only truly global concerns: Projects, Overview, Dashboard,
Pool Query, and the `⚙ Admin` dropdown (Labels, Templates, Schemas, NE Types,
HW Templates, Connectors, VMware). The `is_active(*endpoints)` context processor
drives active-state highlighting.

**Project context strip** (`_project_nav.html`) renders automatically below the
navbar whenever `g.current_project` is set (i.e. any URL with `<pid>`). It shows:
- A project-picker dropdown (populated from `g.all_projects`)
- Section tabs: Subnets · Sites · PODs · NE Types · Schemas · Requirements ·
  Topology · BoM · Inventory · Racks · Cables · HW Templates · Validate

Active tab detection is via `request.endpoint` membership, defined in
`_project_nav.html`. **Don't bind navigation to `request.path` string matching.**

### Jinja Macros (`templates/_macros.html`)

Import what you need per template:

```jinja
{% from '_macros.html' import breadcrumbs, empty_state, confirm_delete, job_progress %}
{% from '_macros.html' import label_pill, label_pills, utilization_bar, summary_card %}
```

**`breadcrumbs(items)`** — Bootstrap breadcrumb nav. Every page below the
homepage uses this. Replace any ad-hoc `← Name` link with it:

```jinja
{{ breadcrumbs([
  {'label': 'Projects', 'url': url_for('ipam.index')},
  {'label': proj.name,  'url': url_for('ipam.project_detail', pid=proj.id)},
  {'label': 'Sites',    'url': url_for('ne.list_sites', pid=proj.id)},
  {'label': site.name}   {# last item has no url — rendered as active #}
]) }}
```

**`empty_state(icon, title, body, primary_action, secondary=[])`** — Full-page
empty state with context and action buttons. **Never write a bare "No X yet."**

```jinja
{{ empty_state(
  icon='📍',
  title='No sites yet',
  body='A site is a physical location that hosts one or more PODs. ...',
  primary_action={'label': '+ Add Site', 'url': url_for('ne.add_site', pid=proj.id)},
  secondary=[{'label': '⚡ Bulk Create', 'url': url_for('ne.bulk_add_sites', pid=proj.id)}]
) }}
```

**`confirm_delete(url, label, impact_url='')`** — Replaces `onsubmit="confirm(...)"`.
Triggers the shared `#deleteModal` in `base.html`, which optionally fetches
impact from `impact_url` before the user confirms:

```jinja
{{ confirm_delete(
     url_for('ne.delete_site_route', pid=proj.id, sid=site.id),
     site.name,
     url_for('ne.site_impact', sid=site.id)
   ) }}
```

**Never use `onsubmit="return confirm(...)"` on new templates.**

**`job_progress(job_id)`** — Renders a progress bar that `base.html`'s polling
script auto-updates until the job completes.

### Client-Side Validation

Add `data-validate="<type>"` to any input to get debounced blur validation
via `static/js/validate.js`. Supported types: `cidr`, `ip-interface`,
`prefix-in-supernet` (also requires `data-supernet="<supernet_cidr>"`).

```html
<input name="cidr" class="form-control" data-validate="prefix-in-supernet"
       data-supernet="{{ proj.supernet }}">
```

Server-side validators in `ipam.py` are the source of truth; the JS calls them.

### Bulk List Operations

Any list template that supports row selection follows this convention:

- Row checkbox: `<input type="checkbox" class="row-select" value="{{ id }}">`
- Select-all: `<input type="checkbox" id="selectAll">`
- Action bar: `<div class="bulk-action-bar d-none ...">` with
  `<button data-bulk-action="delete" data-action-url="...">` inside

`static/js/bulk-actions.js` handles the show/hide logic and POSTs
`{ids: [...]}` JSON to the `data-action-url`. Bulk-delete endpoints must
validate that each ID belongs to the project before deleting.

### Responsive / Mobile Rules

- **Every new list view:** `table-responsive` wrapper on tables.
- **Every new button row:** `flex-wrap` so it survives narrow viewports.
- **Secondary actions:** push into a `dropdown` rather than a long inline row on `<md`.
- **Every new template's primary actions must survive at 390px wide.**

---

## Testing

### Test Layers

| Layer | Command | Description |
|-------|---------|-------------|
| Unit | `make unit` | Helper functions only, no Redis/HTTP |
| API | `make api` | Flask test client + fakeredis |
| E2E | `make e2e` | Playwright (requires live server + Chromium) |
| Fast | `make fast` | unit + api (CI-friendly, no browser) |
| All | `make test` | All three layers |
| Coverage | `make coverage` | unit + api with HTML report |

### Fake Redis Fixture

`tests/conftest.py` provides an `autouse` fixture that replaces `r` in every
blueprint module with a fresh `fakeredis.FakeRedis` instance per test:

```python
import ipam, ne, hw_logic, vmware, auth
for mod in (db, ipam, ne, hw_logic, vmware, auth):
    monkeypatch.setattr(mod, 'r', fake_r)
```

`core/relations.py` and `core/jobs.py` use `import db; db.r` — the `db`
module is in the list above, so they automatically see the fake Redis without
needing a separate conftest entry.

**When adding a new blueprint that uses Redis directly (`from db import r`),
add it to this list.**

### Async Endpoint Testing

`generate_all_from_bom` and similar async endpoints detect `request.is_json`
to choose the async path. Tests send **form POSTs** (not JSON), so they
exercise the synchronous path. No special test handling is needed.

### Test Markers

```
@pytest.mark.unit   # pure logic, no I/O
@pytest.mark.api    # Flask test client
@pytest.mark.e2e    # Playwright
```

### Seed Fixtures

`conftest.py` provides reusable fixtures:
- `seeded_project` — creates a project via HTTP POST, returns `{id, name, supernet}`
- `seeded_subnet` — creates a `/24` in the seeded project, returns network dict
- `seeded_hw_template`, `seeded_rack_template`, `seeded_cable_template` — hardware fixtures

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-key-for-local-use-only` | Flask secret key (load from env) |
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_DB` | `0` | Redis database index |
| `REDIS_PASSWORD` | `None` | Redis auth password |

---

## Known Issues / Notes

- **`from db import r as _r` in unit tests:** Pre-existing unit test files use
  this pattern in some helpers and will fail if no real Redis is reachable.
  New test helpers should use `import ipam; ipam.r.sadd(...)` instead (see
  `tests/unit/test_vmware_helpers.py` for the correct pattern).
- **Secret key:** Loaded from `SECRET_KEY` environment variable. Defaults to a
  development key if not set.
- **Bulk delete auth:** Bulk-delete endpoints use `@editor_required` but have no
  per-row ownership check beyond `project_id` comparison. Flag for review when
  finer-grained auth is added.
- **Job threading and tests:** `run_job()` starts daemon threads. Tests never
  trigger the async path (they send form POSTs), so no race conditions occur
  between threads and monkeypatching.
