# CLAUDE-TOPOLOGY-GRAPH.md

> Companion to the top-level `CLAUDE.md` — read that first for tech
> stack, blueprint layout, Redis key conventions, and the NE/HW/iface
> data model.
>
> **Feature:** Render network topology diagrams from IPAM data as
> Mermaid graphs (and optionally GraphViz DOT for larger views).
> **Scope:** new `topology.py` blueprint, one render module, panel
> integrations on NE / project / BGP session pages.
> **Status:** Implemented (2026-06-02).

---

## Goal

The IPAM holds enough relational data — NE↔iface↔subnet, HW↔port↔NE,
NE↔BGP session↔NE — to render genuinely useful diagrams without
asking the user to maintain a separate Visio file. Mermaid is the
format of choice: text-based, version-controllable, no JS runtime,
renders inline in markdown viewers, GitHub, and a one-line CDN include
in templates.

Three view planes from the same data:

1. **L1 physical** — hardware instances, ports, cables (where modeled).
2. **L3 logical** — NEs, ifaces, subnets, allocations.
3. **BGP** — NEs, sessions, peers, behind-set counts.

Each scoped by project / site / VRF as the user picks.

---

## Non-goals

- **No interactive editing.** Mermaid is render-only. Editing
  topology happens via the existing CRUD forms; the graph reflects
  state, doesn't manipulate it.
- **No vendor topology auto-discovery.** No LLDP/CDP polling, no
  SNMP. The IPAM renders what was entered. Bringing in discovery is a
  whole different tool category.
- **No live status overlay.** No "is this link up right now."
  Operationally tempting; doesn't belong in IPAM.
- **No 3D / fancy WebGL views.** Mermaid covers 95% of useful cases.
  GraphViz DOT export covers the remaining 5% (large fleets where
  Mermaid layout breaks down).
- **No real-time graph updates.** Graphs render on page load.
  Refresh by reloading the page.

---

## Data sources per view

### L1 physical

Inputs:

- `hw_instance` records (rack, slot, hostname).
- Port records on hw_instances (existing model from
  `CLAUDE-NE-HW-BINDING.md`).
- Port-to-NE-iface bindings.
- Optional cable records (if present; otherwise omit).

Output edges: rack containers (subgraphs), hw_instance nodes, port
labels, dashed lines between connected ports.

### L3 logical

Inputs:

- NE instances + their ifaces.
- IP allocations per iface.
- Subnet records.
- VRF labels.

Output edges: NE nodes, iface labels, lines to subnet nodes annotated
with the assigned IP. Subnets shared by multiple NEs (point-to-point
links, transit LANs) become hubs.

### BGP

Inputs:

- `bgp_session` records (`CLAUDE-BGP-SESSIONS.md`).

Output edges: NE nodes, lines to peer NEs (or "external AS N" stubs
for unmodeled peers), annotated with peer AS and behind-set count.
Different line styles for ebgp vs ibgp.

---

## Rendering

### Mermaid generator

Each view is a function `(scope_filters) -> str` returning a Mermaid
graph definition. No state.

```python
def render_l3_mermaid(scope: dict) -> str:
    nes      = nes_matching(scope)
    edges    = []
    subnets  = set()

    lines = ['graph LR']
    for ne in nes:
        lines.append(f'  {_id(ne)}["{ne["name"]}"]')
        for iface in ifaces_of(ne):
            for alloc in allocations_for(iface):
                net = get_network(alloc['network_id'])
                subnets.add(net['id'])
                edges.append(
                    f'  {_id(ne)} -- "{iface["name"]}<br/>'
                    f'{alloc["ip"]}" --> {_id(net)}')

    for sid in subnets:
        net = get_network(sid)
        lines.append(f'  {_id(net)}(("{net["cidr"]}"))')

    lines.extend(edges)
    return '\n'.join(lines)
```

`_id` produces Mermaid-safe identifiers (no dots, no hyphens) from
record IDs.

L1 uses subgraphs for racks:

```
graph TB
  subgraph rack_R01 ["Rack R-01"]
    hw_a["spine-01<br/>U24-25"]
    hw_b["leaf-01<br/>U10"]
  end
  hw_a -- "Eth1/1 ↔ Eth1/1" --- hw_b
```

BGP uses class definitions for line styling:

```
graph LR
  ne_a["spine-01"]
  ne_b["leaf-01"]
  ne_c["external<br/>AS65002"]
  ne_a -- "ibgp<br/>3 prefixes" --> ne_b
  ne_a -. "ebgp AS65002<br/>147 prefixes" .-> ne_c
  classDef external fill:#f5f5f5,stroke-dasharray:5
  class ne_c external
```

### GraphViz fallback

For >50 nodes Mermaid layout gets messy. Emit DOT format from the same
data:

```python
def render_l3_dot(scope: dict) -> str:
    ...
```

Operators run `dot -Tsvg` themselves; the IPAM just emits the source.
No graphviz dependency in the container.

---

## Routes

```
GET /topology                              — fleet overview, project picker
GET /topology/projects/<pid>?view=l3       — project-scoped L3
GET /topology/projects/<pid>?view=l1
GET /topology/projects/<pid>?view=bgp

GET /topology/sites/<sid>?view=l1
GET /topology/ne/<ne_id>?view=neighborhood — 1-hop around one NE

GET /topology/projects/<pid>/render.mermaid — raw text
GET /topology/projects/<pid>/render.dot     — DOT text
GET /topology/projects/<pid>/render.svg     — pre-rendered SVG (if pyppeteer / mermaid-cli is available, else 501)
```

### Filters

Query parameters narrow what's drawn:

- `vrf=<name>` — only edges/nodes in this VRF (L3 + BGP views).
- `labels=a,b` — only NEs / subnets carrying all listed labels.
- `site=<id>` — only nodes at this site (L1 view).
- `max_nodes=N` — cap rendered nodes; surface a "view truncated" badge
  if exceeded. Default 80 for Mermaid, no cap for DOT.

Filters compose; the graph reflects exactly the filtered set.

---

## UI

### Topology overview (`/topology`)

Landing page: project picker, recent topologies, links to per-site
L1 views. Tiny page; mostly a directory.

### Per-project topology page

Three tabs (L1 / L3 / BGP). Each tab shows:

1. **Filter bar** — VRF, labels, site, max_nodes.
2. **Rendered graph** — Mermaid via the standard
   `<pre class="mermaid">` block + the Mermaid JS CDN include.
3. **Legend** — small inline key for line styles and node shapes.
4. **Export buttons** — `.mermaid` and `.dot` downloads.

### Embedded views on other pages

- **NE detail page** — small "1-hop neighborhood" L3 graph showing
  this NE, its ifaces, the subnets they connect to, and the other
  NEs on those subnets.
- **BGP session detail page** — minimal BGP-view graph showing
  this session's two endpoints plus the session's behind-set as a
  cluster of subnet nodes attached to the local endpoint.
- **Subnet detail page** — small graph: this subnet at the center,
  every NE-iface on it as a spoke.

Each embedded view is the same render function with tightly-scoped
filters.

---

## Edge cases

- **Large graphs.** Mermaid breaks down past ~80 nodes. `max_nodes`
  truncation drops the lowest-priority nodes first (defined as: leaf
  NEs furthest from the scope center, or sparsely-connected subnets).
  The truncated count and a "see DOT export for full graph" hint go in
  the page header.
- **Cycles.** L3 with shared transit subnets has cycles; Mermaid
  handles them fine. BGP iBGP full mesh creates dense cycles; consider
  collapsing iBGP-mesh into a single "iBGP mesh: N peers" cluster node
  when peer count exceeds 6.
- **Orphan nodes.** NEs with no ifaces or no allocations still appear
  on the L3 graph as floating nodes — useful for spotting incomplete
  data. Mark with a dashed border.
- **Cross-VRF edges.** A subnet in VRF A connecting NEs in VRF A and
  VRF B is unusual but valid (typically a leaking transit). Render the
  edge in a distinct style; surface as a lint warning in
  `CLAUDE-CROSS-PROJECT-LINT.md`.
- **External-AS stubs in BGP view.** A session whose `peer_ip` doesn't
  match any IPAM-known NE renders a stub node "AS{peer_as}" with a
  distinct style. Multiple sessions to the same peer AS share one stub
  node.
- **Mermaid identifier collisions.** Different records can have IDs
  that produce the same Mermaid-safe slug. Use a hash suffix when
  collisions detected; bake into `_id`.
- **Empty graphs.** Empty filter result renders a placeholder with
  "no nodes match these filters" and a "clear filters" button rather
  than a broken Mermaid block.

---

## Composability with other docs

- **`CLAUDE-BGP-SESSIONS.md`** — provides BGP view input.
- **`CLAUDE-NE-HW-BINDING.md`** — provides L1 view input (port
  bindings).
- **`CLAUDE-OOB-SELECTORS.md`** — orthogonal; OOB networks render as
  normal subnets in L3, just commonly per-rack.
- **`CLAUDE-CROSS-PROJECT-LINT.md`** — cross-VRF edges flagged in
  L3 view also surface in the lint.
- **`CLAUDE-POOL-OBSERVABILITY.md`** — add `topology.render` events:
  view, scope, node_count, edge_count, truncated (bool).

---

## Testing

- `tests/unit/test_topology_render_l3.py` — render output over a
  synthetic project, including shared transit subnets, multi-VRF, and
  truncation behavior.
- `tests/unit/test_topology_render_l1.py` — rack subgraphs, port
  bindings, orphan hw_instances.
- `tests/unit/test_topology_render_bgp.py` — ibgp/ebgp line styles,
  external-AS stubs, mesh collapse threshold.
- `tests/unit/test_topology_filters.py` — VRF / label / site filter
  composition.
- `tests/unit/test_mermaid_validity.py` — round-trip the rendered
  output through a Mermaid parser (the `mermaid-py` package or just
  syntax-check with a regex grammar) to catch malformed output.

Outputs are pure strings; no Redis writes. Golden files are fine for
the render tests as long as `_id` is deterministic.

---

## Implementation order

1. **L3 Mermaid renderer + raw export route.** Simplest; uses
   data the IPAM already has.
2. **Per-project topology page** with L3 tab, filter bar, Mermaid
   inline.
3. **L1 renderer** for racks + ports.
4. **BGP renderer** — depends on `CLAUDE-BGP-SESSIONS.md` landing
   first.
5. **Embedded views** on NE / subnet / BGP session detail pages.
6. **DOT export** for large graphs.
7. **Pre-rendered SVG** if/when a Mermaid CLI is acceptable as a
   container dependency.

Each step is independently shippable. The Mermaid CDN include is a
single `<script>` tag; no new build pipeline.

---

## Related docs

- `CLAUDE.md` — top-level reference.
- `CLAUDE-BGP-SESSIONS.md` — BGP view inputs.
- `CLAUDE-NE-HW-BINDING.md` — L1 view inputs.
- `CLAUDE-CROSS-PROJECT-LINT.md` — surfaces issues the L3 view also
  visualizes.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy for
  `topology.render`.
