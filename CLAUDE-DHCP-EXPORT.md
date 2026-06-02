# CLAUDE-DHCP-EXPORT.md

> Companion to the top-level `CLAUDE.md` — read that first for tech
> stack, blueprint layout, Redis key conventions, and the template-rule
> model that marks slots as DHCP/reserved/gateway.
>
> **Feature:** Emit ISC dhcpd.conf and Kea JSON configuration from IPAM
> records. Pure read path; no DHCP daemon control.
> **Scope:** new `dhcp.py` blueprint (or share with `dns.py`), two
> serializers, MAC-address linkage on iface records.
> **Status:** Design — not yet implemented.

---

## Goal

Every subnet in the IPAM that's intended as a DHCP scope already carries
the data a DHCP server needs: the CIDR, the gateway, the DHCP pool
range, the static reservations. Generate the config from that data, on
demand, in the format the operator's DHCP daemon understands.

Two outputs from one read function:

1. **ISC dhcpd.conf** — the de facto format; works with ISC dhcpd 4.x
   and most clones (Kea reads a converted form).
2. **Kea JSON config** — modern ISC successor, used standalone or via
   the Kea REST API.

The OOB-per-rack pattern from `CLAUDE-OOB-SELECTORS.md` is the obvious
fit: each rack's management subnet becomes one DHCP scope, all rendered
from existing template-rule data with no extra modeling.

---

## Non-goals

- **No DHCP daemon control.** No restart, no `omshell`, no Kea control
  socket. The IPAM emits config; the operator's deployment pipeline
  applies it.
- **No DHCPv6 dynamic prefix delegation.** Static v6 reservations via
  `host` blocks are fine; PD is a separate, much hairier feature.
- **No failover/high-availability config.** Pair config is per-site
  policy and lives in a wrapper template, not the IPAM.
- **No DHCP option editor.** A small fixed set of options (gateway,
  DNS, NTP, domain, MTU) covers the realistic majority. More exotic
  options use a free-form passthrough block, not first-class fields.
- **No subnet leases / current bindings.** Lease state lives in the
  DHCP server, not the IPAM. Importing it back creates a sync problem
  with no clean answer.

---

## Data model

### MAC-address linkage on iface records

DHCP host reservations require a MAC. Iface records gain:

```python
{
  ...existing fields...,
  'mac_address': 'aa:bb:cc:11:22:33',   # optional
}
```

When set, the iface becomes eligible for `host` block emission in the
generated config. When unset, the iface gets nothing (no static
reservation) and lives entirely in the pool range.

MAC is validated at save time (six hex octets, colon or hyphen
separator, normalized to lowercase-colon canonical form).

### Per-subnet DHCP options

Subnet records gain an optional `dhcp_options` dict:

```python
{
  ...existing fields...,
  'dhcp_enabled':   True,             # default: False
  'dhcp_options': {
      'domain_name':         'lab.example.net',
      'domain_name_servers': ['10.0.0.53', '10.0.0.54'],
      'ntp_servers':         ['10.0.0.123'],
      'interface_mtu':       1500,
      'lease_default':       3600,
      'lease_max':           7200,
      'passthrough':         '',       # raw text appended to scope
  },
}
```

Defaults inherit from the project (`project.dhcp_defaults`) and from the
site (`site.dhcp_defaults`). Resolution chain: subnet → site → project
→ global. Mirrors the pool resolver philosophy.

### Template rule reuse

The template-rule model already marks IP roles within a subnet:

- `reserved` — never offered by DHCP, never auto-allocated.
- `gateway` — emit as `option routers` in the scope.
- `dhcp` — included in the DHCP pool range.
- `allocated` — explicit IP assigned to a NE-iface; if iface has MAC,
  emit as static `host` reservation.

The serializer reads this directly; no new fields needed.

---

## Generation algorithm

### Intermediate representation

```python
@dataclass
class DhcpScope:
    cidr:         str
    netmask:      str
    gateway:      str | None
    pool_ranges:  list[tuple[str, str]]   # contiguous DHCP ranges
    static_hosts: list['DhcpHost']
    options:      dict
    description:  str

@dataclass
class DhcpHost:
    hostname:     str
    mac:          str
    ip:           str
    options:      dict
```

The build function walks subnets where `dhcp_enabled=True`:

```python
def build_dhcp_scopes(filters: dict) -> list[DhcpScope]:
    scopes = []
    for net in networks_matching(filters):
        if not net.get('dhcp_enabled'):
            continue

        # Walk per-IP roles from template rules + allocations
        roles = compute_ip_roles(net)
        gateway = next((ip for ip, r in roles.items() if r == 'gateway'),
                       None)

        # Collapse adjacent dhcp-role IPs into contiguous ranges
        pool_ranges = _collapse_role(roles, 'dhcp')

        # Static reservations from allocated IPs with a MAC on the iface
        static = []
        for ip, role in roles.items():
            if role != 'allocated':
                continue
            alloc = get_allocation(net['id'], ip)
            iface = get_iface(alloc['iface_id'])
            ne    = get_ne(alloc['ne_id'])
            if iface and iface.get('mac_address'):
                static.append(DhcpHost(
                    hostname=ne['name'],
                    mac=iface['mac_address'],
                    ip=ip,
                    options={}))

        scopes.append(DhcpScope(
            cidr=net['cidr'],
            netmask=str(ipaddress.ip_network(net['cidr']).netmask),
            gateway=gateway,
            pool_ranges=pool_ranges,
            static_hosts=static,
            options=resolve_dhcp_options(net),
            description=net.get('description', '')))
    return scopes
```

`_collapse_role` is the same shape as the BGP summarizer's lossless
mode — find runs of consecutive IPs sharing a role and emit them as
`(start, end)` tuples.

### Serializers

ISC dhcpd format is whitespace-tolerant text:

```
subnet 10.0.0.0 netmask 255.255.255.0 {
  option routers           10.0.0.1;
  option domain-name       "lab.example.net";
  option domain-name-servers 10.0.0.53, 10.0.0.54;
  default-lease-time 3600;
  max-lease-time     7200;
  range 10.0.0.100 10.0.0.200;

  host spine-01 {
    hardware ethernet aa:bb:cc:11:22:33;
    fixed-address    10.0.0.10;
  }
}
```

Kea JSON is straightforward — same data, JSON schema per the Kea docs.
Both serializers consume the same `DhcpScope` list; adding a new format
is one more function.

---

## Routes

```
GET  /dhcp                                 — scope list
GET  /dhcp/scopes/<net_id>                 — per-subnet preview
POST /dhcp/scopes/<net_id>/toggle          — enable/disable
POST /dhcp/scopes/<net_id>/options         — edit options

GET  /dhcp/export.conf?filter=...          — ISC dhcpd.conf text
GET  /dhcp/export.json?format=kea          — Kea JSON config
GET  /dhcp/export/bundle.tar.gz            — split per-site files
```

The bundle endpoint groups scopes by site (one file per DHCP relay
domain), useful for deployments with per-site servers.

Filter params: `site=`, `project=`, `vrf=`, `label=`. Multiple
combine with AND.

---

## UI

### DHCP-enabled subnet badge

On the subnet detail page (existing), add a "DHCP" badge with an
enable/disable toggle and a small options editor (the seven fields
above plus a passthrough block).

### Per-subnet preview panel

Renders the generated scope for that subnet — gateway, pool ranges,
static host count, full options. Helps debug "why is host X not getting
an IP?" — usually answered by "iface has no MAC" or "subnet not
dhcp-enabled."

### Fleet view (`/dhcp`)

Table of all dhcp-enabled subnets across projects with scope size,
host-reservation count, last-export timestamp. Filter by site / VRF /
project. Bulk-toggle and bulk-edit-options actions for the
"enable DHCP on all OOB-mgmt subnets at this site" case.

---

## Edge cases

- **Subnet too small for a pool.** A /30 with gateway has 1 host slot;
  a /31 P2P has none. Skip with a warning ("subnet 10.0.0.0/30: no
  pool range possible after gateway"). Static reservations within
  small subnets are still emitted.
- **DHCP pool overlaps a static reservation.** Template rules should
  prevent this at save time, but if it slips through (legacy data),
  serializer raises a clear error: "static host X at 10.0.0.100 falls
  inside DHCP range 10.0.0.100-200." Don't silently emit a broken
  config.
- **MAC on multiple ifaces.** Two ifaces with the same MAC, both
  asking for static reservations in scopes that touch the same DHCP
  server, would emit duplicate `host` blocks. Detect at export time;
  emit the higher-priority one (allocated more recently) and warn on
  the rest.
- **IPv6 scopes.** v6 DHCP differs structurally from v4 — the format
  is different, PD is its own world. v1 emits v6 scopes as
  `subnet6 ... { range6 ...; }` static-only (no PD). Document the
  limit clearly; users who need v6 PD will know.
- **Options with no schema match.** Free-form `passthrough` text gets
  appended verbatim inside the scope braces. The IPAM doesn't validate
  it; the operator owns the syntax. Useful as an escape valve;
  documented as such.
- **Subnet not in a site.** Site grouping for the bundle endpoint
  uses `_unsited` as the fallback group. Surfaces orphan subnets the
  operator probably wants to assign.

---

## Composability with other docs

- **`CLAUDE-OOB-SELECTORS.md`** — per-rack OOB subnets are the most
  natural DHCP scopes. The selector model already gives a tight 1:1
  between rack and subnet; flipping `dhcp_enabled` on all of them
  via the fleet view's bulk action is a one-click setup for OOB DHCP.
- **`CLAUDE-DNS-EXPORT.md`** — DNS forward records benefit from DHCP
  static reservations (predictable IPs deserve predictable names).
  The two exports share zero code but compose at the operator level.
- **`CLAUDE-POOL-OPTIMIZATION.md`** — irrelevant; dhcp_enabled is a
  per-subnet flag regardless of how the subnet was carved.
- **`CLAUDE-POOL-OBSERVABILITY.md`** — adds `dhcp.export` event:
  scope_count, host_count, format, trigger.

---

## Testing

- `tests/unit/test_dhcp_build.py` — IR construction over a synthetic
  project: gateway detection, pool range collapsing across reserved
  gaps, MAC-driven host emission, options resolution chain.
- `tests/unit/test_dhcp_serializers.py` — ISC and Kea outputs against
  golden files. ISC must pass `dhcpd -t -cf <file>` if dhcpd is
  available in CI (skip if not).
- `tests/unit/test_dhcp_collisions.py` — pool/static overlap
  detection, MAC duplication.
- `tests/integration/test_dhcp_routes.py` — CRUD, filters, bundle
  generation.

---

## Implementation order

1. **MAC field on iface + validation.** Surface in iface edit form.
   No DHCP yet; just data plumbing.
2. **`dhcp_enabled` + `dhcp_options` on subnets**, with the
   resolution chain. Subnet detail page badge.
3. **`build_dhcp_scopes` + ISC serializer.** Validate against real
   `dhcpd -t`.
4. **Export route + bundle endpoint.**
5. **Kea JSON serializer** (small once IR is solid).
6. **Fleet view with bulk actions.**
7. **IPv6 scopes** (v6 unicast only, no PD).

---

## Related docs

- `CLAUDE.md` — top-level reference, template-rule model.
- `CLAUDE-OOB-SELECTORS.md` — per-rack OOB subnets, natural DHCP
  scopes.
- `CLAUDE-DNS-EXPORT.md` — sibling export; same read-only
  philosophy, IR pattern, and operator workflow.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy for `dhcp.export`.
