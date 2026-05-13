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
app.py          Flask app factory — registers all blueprints
db.py           Shared Redis client (imported as `r` in every blueprint)
auth.py         Authentication: User model, login/logout, Redis backend
ipam.py         IPAM blueprint: projects, subnets, IPs, labels, templates
ne.py           Network Element blueprint: NE types, sites, PODs, requirements
hw.py           Hardware blueprint: templates, BoM, instances, racks, cables
hw_logic.py     Business logic for hardware management (validation, placement)
health_logic.py Health Engine: subnet utilization, requirement gaps, cabling
vmware.py       VMware connector: REST API + UI for IP allocation to VMware
templates/      Jinja2 HTML templates, one sub-folder per blueprint
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
pod:{pid_}:sites            Set  — site IDs associated with POD
site:{sid}:pods             Set  — POD IDs associated with site

hw:template:{tid}           JSON — hardware template
hw:instance:{iid}           JSON — hardware instance
hw:cable:{cid}              JSON — cable record
hw:rack:{rack_iid}:slots    JSON — rack slot list
hw:connectors               Set  — connector type names
hw:compat:{connector}       Set  — compatible connector names

vmware:subnets              Set  — network IDs enabled for VMware allocation
vmware:alloc:{ip}           JSON — VMware allocation metadata per IP
vmware:net:{net_id}:ips     Set  — IPs allocated via VMware per subnet

projects:index              Set  — all project IDs
networks:index              Set  — all network IDs
ne_types:index              Set  — global NE type IDs
templates:global            Set  — global subnet template IDs
labels:global               Set  — global label names
hw:templates:index          Set  — global HW template IDs
hw:instances:index          Set  — all instance IDs
hw:cables:index             Set  — all cable IDs
```

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
| GET/POST | `/networks/<net_id>/edit` | Edit subnet |
| POST | `/networks/<net_id>/delete` | Delete subnet |
| GET | `/networks/<net_id>` | Network detail + IP list |
| GET/POST | `/networks/<net_id>/ip/add` | Add IP to subnet |
| GET/POST | `/ip/<ip>/edit` | Edit IP record |
| POST | `/ip/<ip>/delete` | Delete IP |
| GET | `/api/networks/<net_id>/next` | Next available IP (JSON) |
| GET | `/api/pool` | Pool query by labels (JSON) |
| GET | `/api/networks/export` | Export all subnets (CSV) |
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
| GET/POST | `/projects/<pid>/sites/bulk` | Bulk add sites (CSV-like textarea) |
| GET/POST | `/projects/<pid>/sites/<sid>/edit` | Edit site |
| POST | `/projects/<pid>/sites/<sid>/delete` | Delete site |
| POST | `/projects/<pid>/sites/<sid>/assign-pod` | Assign POD to site |
| POST | `/projects/<pid>/sites/<sid>/unassign-pod` | Unassign POD from site |
| GET | `/projects/<pid>/sites/<sid>` | Site detail (params + PODs) |
| GET | `/projects/<pid>/pods` | List project PODs |
| GET/POST | `/projects/<pid>/pods/add` | Add POD to project |
| GET/POST | `/projects/<pid>/pods/<pod_id>/edit` | Edit POD |
| POST | `/projects/<pid>/pods/<pod_id>/delete` | Delete POD |
| GET | `/projects/<pid>/pods/<pod_id>` | POD detail (params + sites + slots) |
| POST | `/projects/<pid>/pods/<pod_id>/slots` | Update POD NE slots |
| POST | `/projects/<pid>/pods/<pod_id>/assign-site` | Assign site to POD |
| POST | `/projects/<pid>/pods/<pod_id>/unassign-site` | Unassign site from POD |
| GET | `/projects/<pid>/topology` | Visual topology map (Sites/PODs/HW) |
| GET | `/api/projects/<pid>/topology` | Topology data API (Cytoscape.js) |
| GET | `/projects/<pid>/requirements` | View subnet requirements for project |
| POST | `/projects/<pid>/requirements/push` | Push single requirement to IPAM |
| POST | `/projects/<pid>/requirements/push-all` | Push all requirements to IPAM |

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
| POST | `/projects/<pid>/bom/generate-all` | Generate instances for all BoM items |
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

## Development Conventions

### Adding a New Blueprint

1. Create `<name>.py` with `<name>_bp = Blueprint('<name>', __name__, url_prefix='')`
2. Import and register in `app.py`
3. If the blueprint uses its own `r = db.r` reference, add it to the
   monkeypatch loop in `tests/conftest.py`
4. Create `templates/<name>/` for Jinja2 templates
5. Add a navbar link in `templates/base.html`
6. Write `tests/unit/test_<name>_helpers.py` and `tests/api/test_<name>_api.py`

### Redis Access Pattern

Every blueprint imports a module-level `r`:

```python
from db import r
```

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
import ipam, ne, hw, vmware
for mod in (db, ipam, ne, hw, vmware):
    monkeypatch.setattr(mod, 'r', fake_r)
```

**When adding a new blueprint that uses Redis, add it to this list.**

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

- **`from db import r as _r` in unit tests:** The pre-existing unit test files
  use this pattern in some helpers and will fail if no real Redis is reachable.
  New test helpers should use `import ipam; ipam.r.sadd(...)` instead (see
  `tests/unit/test_vmware_helpers.py` for the correct pattern).
- **Secret key:** Loaded from `SECRET_KEY` environment variable. Defaults to a
  development key if not set.
