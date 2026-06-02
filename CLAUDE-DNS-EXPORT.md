# CLAUDE-DNS-EXPORT.md

> Companion to the top-level `CLAUDE.md` — read that first for tech stack,
> blueprint layout, Redis key conventions.
>
> **Feature:** Read-only export of BIND-format forward and reverse DNS
> zones derived from existing IPAM data. No DNS server, no zone hosting —
> just file generation and an API.
> **Scope:** new `dns.py` blueprint, a Zone object in Redis, two
> templates, one route per output format.
> **Status:** Implemented (2026-06-02).

---

## Goal

The IPAM already knows every hostname, IP, subnet, and label in the
fleet. Emitting BIND-format zone files from that data is a pure read
function, and it covers the single highest-leverage IPAM integration:
any DNS implementation (BIND, PowerDNS, Knot, Route53, AdGuard, NSD)
can ingest zone files or convert them trivially.

Three end-state capabilities:

1. **Forward zones** — `example.com` → A/AAAA records from NE and
   hw-instance hostnames + their allocated IPs.
2. **Reverse zones** — `10.in-addr.arpa`, `0.0.10.in-addr.arpa`,
   `0.8.b.d.0.1.0.0.2.ip6.arpa` etc. → PTR records covering allocated
   IPs.
3. **Zone export route** producing valid RFC 1035 zone file syntax,
   downloadable per zone and as a fleet bundle.

---

## Non-goals

- **No DNS server.** No serving, no zone transfer (AXFR/IXFR), no
  DNSSEC signing. The IPAM emits files; an actual DNS server consumes
  them.
- **No push integrations in v1.** PowerDNS API, Route53, Cloudflare etc.
  are out of scope. The zone file is the universal interchange. Add a
  push adapter later if a specific deployment wants it.
- **No automatic serial bumping on every export.** Serial bump is
  declarative (see Zone object). An export is idempotent for the same
  IPAM state.
- **No view-based DNS (split-horizon)** in v1. One zone = one set of
  records. Operators who need internal/external views run two zones.

---

## Data model

### Zone object

New Redis keyspace:

| Key                       | Type | Purpose                                |
|---------------------------|------|----------------------------------------|
| `dns:zone:{id}`           | hash | Zone record (JSON-serialized dict)     |
| `dns:zones:index`         | set  | All zone IDs                           |
| `dns:zones:by_kind:{k}`   | set  | Zones grouped by `forward` / `reverse` |

```python
{
  'id':           'zone-...',
  'kind':         'forward',           # or 'reverse'
  'name':         'corp.example.com',  # zone apex; for reverse: '10.in-addr.arpa'
  'enabled':      True,

  # SOA fields
  'primary_ns':   'ns1.corp.example.com.',
  'admin_email':  'hostmaster.corp.example.com.',   # @ → . per RFC 1035
  'refresh':      3600,
  'retry':        600,
  'expire':       1209600,
  'minimum_ttl':  300,

  # Serial bump policy: 'manual' | 'on_export' | 'date_based'
  'serial_policy': 'date_based',
  'serial_current': 2026052500,

  # Authoritative nameservers (NS records)
  'nameservers':  ['ns1.corp.example.com.', 'ns2.corp.example.com.'],

  # Default TTL for records emitted into this zone
  'default_ttl':  300,

  # Scope — what subnets / NEs feed this zone
  'scope': {
    'include_label_sets': [['prod']],   # forward: filter hostnames
    'include_vrfs':       [],
    'include_cidrs':      ['10.0.0.0/8'],   # reverse: which space this zone covers
    'exclude_label_sets': [],
    'exclude_cidrs':      [],
  },

  # Static records the operator wants appended verbatim (MX, TXT, etc.)
  'static_records': [
    {'name': '@', 'type': 'MX', 'value': '10 mail.corp.example.com.', 'ttl': 3600},
    {'name': '@', 'type': 'TXT', 'value': '"v=spf1 -all"', 'ttl': 3600},
  ],
}
```

### Where hostnames come from

Audit existing model: NE-instance and hw-instance dicts likely carry
`name` or `hostname` already (the project uses these for the UI). For
DNS export, add an explicit `fqdn` field that, if empty, falls back to
`{name}.{zone_default_suffix}` resolved at export time.

If multi-interface NEs need per-iface DNS names (router-id loopback as
`r1.corp.example.com`, mgmt iface as `r1-mgmt.corp.example.com`), add
an optional `dns_name` on the iface dict. Empty → no record for that
iface.

---

## Algorithm

### Forward zone generation

```python
def emit_forward_zone(zone: dict) -> str:
    """Return zone-file content for a forward zone."""
    lines = [_emit_soa(zone), *_emit_ns(zone), '']

    # A/AAAA records from instances
    for inst in instances_matching_scope(zone['scope']):
        fqdn = inst.get('fqdn') or f"{inst['name']}.{zone['name']}"
        for ip in primary_ips(inst):
            rr_type = 'A' if ip.version == 4 else 'AAAA'
            short   = strip_zone_suffix(fqdn, zone['name'])
            lines.append(f"{short:<30} {zone['default_ttl']:>5} IN {rr_type} {ip}")

    # Per-iface dns_name records
    for inst, iface, ip in iface_ips_matching_scope(zone['scope']):
        if not iface.get('dns_name'):
            continue
        ...

    # Static records
    for r in zone.get('static_records', []):
        lines.append(f"{r['name']:<30} {r.get('ttl', zone['default_ttl']):>5} "
                     f"IN {r['type']} {r['value']}")

    return '\n'.join(lines) + '\n'
```

### Reverse zone generation

```python
def emit_reverse_zone(zone: dict) -> str:
    """PTR records for a reverse zone. Scope.include_cidrs defines what
    address space this zone is authoritative for."""
    lines = [_emit_soa(zone), *_emit_ns(zone), '']

    for cidr in zone['scope']['include_cidrs']:
        net = ipaddress.ip_network(cidr)
        for ip, fqdn in allocated_ips_in(net):
            ptr_label = _reverse_label(ip, net.version)  # '1.0.0.10' for 10.0.0.1
            lines.append(f"{ptr_label:<30} {zone['default_ttl']:>5} IN PTR {fqdn}.")

    return '\n'.join(lines) + '\n'
```

### Serial number policy

- `manual` — operator sets `serial_current` explicitly; export uses
  that value unchanged.
- `on_export` — serial increments on every export call. Simple but
  noisy (non-idempotent exports).
- `date_based` — `YYYYMMDDNN` per RFC 1912. Recompute at export time
  from current date + a daily counter persisted in `dns:zone:{id}`.
  Default, most operationally friendly.

Serial logic is centralized in `_resolve_serial(zone)` so adding a
future policy is one switch.

### Composability with BGP summarization

When a session in `CLAUDE-BGP-SUMMARIZATION.md` emits aggressive
summary CIDRs, those become candidates for reverse-zone delegation
boundaries. The fleet view of zones can offer "create reverse zones
matching this session's summary" as a one-click — pure UI sugar over
the existing zone-create form.

---

## Route

New `dns.py` blueprint:

```
GET    /dns                              — zone list + add button
GET    /dns/zones/<zid>                  — zone detail (records preview)
POST   /dns/zones/add                    — create
POST   /dns/zones/<zid>/edit             — update
POST   /dns/zones/<zid>/delete           — delete
POST   /dns/zones/<zid>/toggle           — enable/disable
GET    /dns/zones/<zid>/export.zone      — BIND zone file
GET    /dns/zones/<zid>/export.json      — same data, structured (for
                                            consumers like PowerDNS)
GET    /dns/export.tar.gz                — fleet bundle, all enabled
                                            zones + a `named.conf.local`
                                            stub
```

The fleet bundle is what an operator drops onto a BIND host: a tarball
of zone files plus a paste-ready `named.conf.local` stub with zone
declarations pointing at them.

---

## UI

### Zone list (`/dns`)

Table: zone name, kind, scope (label/CIDR summary), record count,
last serial, enabled toggle, download buttons.

### Zone detail

Two panels:

1. **Settings** — SOA, NS, scope, static records. Edit-in-place.
2. **Preview** — first N rendered records with "show full zone file"
   expansion. Highlight diffs vs. the last exported version
   (`dns:zone:{id}:last_export` cached blob, refreshed on download).

The preview pane runs the generator live so operators see the effect
of scope changes before saving.

### Fleet bundle button

`/dns` page has a prominent "Export all zones" button producing the
tarball. Linked from the project pages as well.

---

## Edge cases

- **An NE has no FQDN.** Skip; emit a warning row in the preview ("3
  instances lack hostnames; no records emitted").
- **An IP is allocated but unbound** (manual allocation with no
  hostname). Emit no record; the reverse zone simply has a gap. Don't
  invent a name.
- **CNAME collisions.** v1 only emits A/AAAA/PTR. CNAMEs come from
  `static_records` — if operator adds one that collides with a derived
  A record, refuse at save time.
- **Zones that overlap in CIDR space.** Allowed but flagged in the
  zone-list view. The export still works; semantics is "first match
  wins per record."
- **Hostnames containing invalid DNS chars.** Validate at instance
  save time (existing model probably allows freer characters); add a
  `valid_dns_name` lint that flags offenders before they end up in a
  zone.
- **`@` and root-label quoting** in `static_records.value`. Validate
  at zone save time; reject obviously-malformed values rather than
  catching them on import into the DNS server.
- **IPv6 reverse zones.** The `ip6.arpa` nibble format is verbose; the
  generator handles it. Reverse zone `name` is stored canonically
  (e.g. `0.8.b.d.0.1.0.0.2.ip6.arpa` for `2001:db8::/32`).
- **Subdomain delegation.** A forward zone for `corp.example.com` may
  delegate `lab.corp.example.com` to a different IPAM zone via NS
  records in `static_records`. Encourage this explicitly in docs;
  don't try to auto-delegate.

---

## Observability

Extends `CLAUDE-POOL-OBSERVABILITY.md`:

### `dns.export`

```json
{
  "event": "dns.export",
  "ts": "...",
  "zone_id": "zone-...",
  "zone_name": "corp.example.com",
  "kind": "forward",
  "serial": 2026052500,
  "record_count": 247,
  "trigger": "ui" | "api" | "bundle"
}
```

Useful for "what was the state of DNS on date X" alongside the
audit-log work package.

---

## Testing

- `tests/unit/test_dns_emit.py` — SOA formatting, A/AAAA/PTR emission,
  IPv6 reverse-label arithmetic, static record passthrough, serial
  policies.
- `tests/unit/test_dns_scope.py` — scope filter semantics (labels,
  VRFs, CIDR inclusion/exclusion).
- `tests/integration/test_dns_routes.py` — CRUD + export + bundle,
  fakeredis + `--real-redis` parity.
- **Round-trip test.** Pipe the emitted zone through `named-checkzone`
  (BIND tool) if available in the test environment; mark `skipif` when
  not.

---

## Implementation order

1. **Zone object + CRUD routes + UI list/detail (no emission yet).**
2. **Forward zone emission + `.zone` route.** Standalone, testable.
3. **Reverse zone emission + IPv6 nibble support.**
4. **Static records + scope exclusions.**
5. **Fleet bundle + `named.conf.local` stub.**
6. **Composability button** — "create reverse zones from BGP summary"
   on the BGP session detail page.
7. **Observability event.**

Each step ships independently; no data migration at any step.

---

## Related docs

- `CLAUDE.md` — top-level model, NE/hw instance schemas (hostname
  fields).
- `CLAUDE-BGP-SUMMARIZATION.md` — composability hook for reverse-zone
  boundaries.
- `CLAUDE-DHCP-EXPORT.md` — sibling export feature; same template
  rules drive both.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy this doc extends.
