# CLAUDE-CROSS-PROJECT-LINT.md

> Companion to the top-level `CLAUDE.md` — read that first for tech
> stack, blueprint layout, Redis key conventions, and the VRF / label
> model.
>
> **Feature:** Fleet-level lint surface that catches CIDR overlaps,
> label collisions, and similar cross-project hazards that escape
> the per-project validators.
> **Scope:** new `lint.py` blueprint, one report page, one JSON API,
> hooks into existing per-project save validators.
> **Status:** Implemented (2026-06-02).

---

## Goal

Per-project validation already prevents overlapping CIDRs within a
single project. Across projects, the system today is silent — and
overlapping CIDRs across projects within the same VRF is one of those
bugs that ships fine and then explodes the day someone merges two
networks or peers two routers.

This doc proposes a read-only lint pass over the global index that
surfaces:

1. **CIDR overlaps within the same VRF, across projects.**
2. **Same-CIDR collisions** (two projects claim the same network in the
   same VRF — strict subset of #1, but worth a distinct error class
   because the fix is different).
3. **Cross-VRF "leaking" subnets** — a subnet whose label set matches
   pools in multiple VRFs, suggesting a likely misconfiguration.
4. **Label collisions** across projects — same label used with
   semantically incompatible meanings (heuristic, not certain).
5. **Reserved-range overlaps** — IPAM subnets that fall inside RFC1918
   blocks the operator has marked off-limits, CGN ranges, or
   documented reservations.

All read-only; nothing in the IPAM auto-resolves these.

---

## Non-goals

- **No auto-remediation.** Lint flags issues; the operator fixes them
  through the existing CRUD forms. Renumbering is not something an
  IPAM should ever do silently.
- **No blocking save on cross-project conflicts.** Existing
  per-project validation stays as the hard gate. Cross-project lint
  is advisory because legitimate overlaps exist (deliberate
  multi-tenant reuse in separate VRFs, lab overlays).
- **No global hard "uniqueness" enforcement.** Different VRFs may
  intentionally use the same CIDR (the entire point of VRFs).
  Uniqueness applies per-VRF only.
- **No external CIDR sources** (RIR registries, BGP looking glass).
  Lint operates on IPAM state only.

---

## Detection rules

### Rule 1: CIDR overlap within VRF, across projects

For each VRF, collect all subnets across all projects. Pairwise
check for overlap using `network.overlaps(other)`.

```python
def find_cross_project_overlaps() -> list[dict]:
    by_vrf = defaultdict(list)
    for net_id in r.smembers('networks:index'):
        net  = get_network(net_id)
        vrf  = net.get('vrf', '_def')
        proj = net.get('project_id')
        by_vrf[vrf].append((proj, net))

    findings = []
    for vrf, entries in by_vrf.items():
        # Sort and walk to detect overlap in O(n log n)
        entries.sort(key=lambda e: int(ipaddress.ip_network(e[1]['cidr'])
                                       .network_address))
        for i, (proj_a, net_a) in enumerate(entries):
            n_a = ipaddress.ip_network(net_a['cidr'])
            for proj_b, net_b in entries[i+1:]:
                n_b = ipaddress.ip_network(net_b['cidr'])
                if int(n_b.network_address) > int(n_a.broadcast_address):
                    break  # sorted; no further overlap possible
                if proj_a == proj_b:
                    continue  # within-project caught elsewhere
                if n_a.overlaps(n_b):
                    findings.append({
                        'rule': 'cross_project_overlap',
                        'vrf':  vrf,
                        'subnet_a': {'cidr': net_a['cidr'],
                                     'project': proj_a},
                        'subnet_b': {'cidr': net_b['cidr'],
                                     'project': proj_b},
                        'severity': 'error',
                    })
    return findings
```

`severity: error` because routing won't work; the operator must
renumber one side.

### Rule 2: Exact-CIDR collisions

Subset of Rule 1 where both CIDRs are identical. Detect in the same
pass; emit as a distinct finding with `rule: 'same_cidr_collision'`.
Different from overlap because the resolution is usually "merge or
delete," not "renumber."

### Rule 3: Cross-VRF leaking subnets

A subnet whose label set resolves to pools in multiple VRFs (rare and
usually unintentional). Detect by running each subnet's labels through
the pool resolver scoped to *every* VRF and checking if more than one
matches.

`severity: warning` — sometimes deliberate (intentional leak), so
allow an `acknowledged_leak: true` flag on the subnet to silence the
finding.

### Rule 4: Label collisions

Heuristic: a label used across many projects with very different
co-occurring labels in each is probably overloaded.

```python
def find_label_collisions(threshold=0.2) -> list[dict]:
    """Labels whose co-occurrence vectors differ sharply across projects."""
    # For each label, build a per-project co-occurrence vector
    # (other labels seen on subnets carrying this label).
    # Compute pairwise cosine similarity across projects;
    # flag labels where min(sim) < threshold.
```

`severity: info` — pure heuristic. The fix is usually "rename one of
them" but the IPAM can't decide which.

### Rule 5: Reserved-range overlaps

Operator-declared reserved ranges (stored in a small `reserved:cidrs`
keyspace) — IPAM subnets falling inside any of them get flagged.

```python
{
    'cidr':        '100.64.0.0/10',
    'name':        'CGN — do not use',
    'severity':    'error',           # or 'warning' / 'info'
    'description': '...',
}
```

A subnet may legitimately fall inside RFC1918; this rule is opt-in via
explicit reservation declarations. Default reservation set ships with
documentation-only entries (TEST-NET, CGN, link-local) — informational,
not errors.

---

## Storage and recomputation

Findings are computed on demand, not stored. The full pass over a
fleet with thousands of subnets runs in well under a second
(O(n log n) for sort, near-linear for overlap detection after
sorting).

Cache findings for ~60 seconds in Redis (`lint:findings:cache`) to
avoid recomputing on every page hit; invalidate on any subnet write.

```python
def get_findings(force: bool = False) -> list[dict]:
    if not force:
        cached = r.get('lint:findings:cache')
        if cached:
            return json.loads(cached)
    findings = run_all_rules()
    r.setex('lint:findings:cache', 60, json.dumps(findings))
    return findings
```

Per-page hint integration (e.g., a "this project has 3 cross-project
overlaps" badge on the project page) reads from the cache; the full
lint page can force-refresh.

---

## Routes

```
GET  /lint                          — fleet-wide findings, all rules
GET  /lint/projects/<pid>           — findings touching this project
GET  /lint/vrfs/<vrf>               — findings in this VRF
GET  /api/lint/findings.json        — JSON output for CI integration

POST /lint/reserved/add             — declare a reserved range
POST /lint/reserved/<rid>/delete    — remove
GET  /lint/reserved                 — list declared ranges

POST /lint/findings/<fid>/ack       — acknowledge (silence) a finding
                                      with a free-text reason
```

Acknowledgement state is stored per finding-identity (a hash of the
finding's stable fields — rule + sorted involved-CIDRs + VRF). Acks
persist across recomputation; if the finding stops appearing, the ack
becomes stale and is GC'd after 30 days.

---

## UI

### Fleet lint page (`/lint`)

Grouped table by severity:

```
Errors (3)
  Cross-project overlap in VRF "prod-underlay":
    10.0.0.0/24 (proj-A)  ↔  10.0.0.0/16 (proj-B)
    [view proj-A] [view proj-B] [acknowledge…]

Warnings (7)
  Cross-VRF leak: subnet 10.50.0.0/24 (proj-A) resolves in 2 VRFs
    [view subnet] [mark as deliberate leak] [acknowledge…]

Info (12)
  Label "transit" used inconsistently across 4 projects
    [details…]
```

Each finding links to the relevant detail pages. Severities are
collapsible so the operator can focus on errors first.

### Per-project lint badge

Project list view gains a small badge per project: count of findings
involving that project, color-coded by max severity. Click to filter
the lint page to that project.

### Save-time hint

When saving a subnet, the per-project validator runs as today (hard
block on within-project overlap). After save, if the new subnet
introduces a cross-project overlap, flash a yellow toast with a link
to the new finding. Doesn't block the save — operator may be
deliberately accepting the overlap — but ensures they see it.

---

## Edge cases

- **The /0 subnet.** A subnet of `0.0.0.0/0` (rare but legal) overlaps
  everything. Treat as a special case: emit one finding for the /0 with
  every other subnet in its VRF, collapse the UI presentation to "this
  /0 overlaps every subnet in VRF X — likely a mistake."
- **Foreign / external CIDRs.** Subnets marked as `foreign: true`
  (received from elsewhere, not allocated by this IPAM) participate in
  overlap detection. Worth knowing about, even if the IPAM can't
  renumber them.
- **Default VRF (`_def`).** Treated as just another VRF for overlap
  detection. Many sites end up with everything in `_def`; this rule is
  most valuable for them.
- **Reserved ranges with severity `info`.** Documentation-only; not a
  finding. Useful for "this CIDR is officially documented as for
  example/test use" without flagging it as broken.
- **Acknowledged findings vs unack'd.** UI defaults to hiding acks
  (with a count of hidden findings); toggle to show. Acks include the
  acknowledging user (from auth, once available) and a free-text
  reason.
- **Cache invalidation on bulk writes.** Don't recompute on every
  individual write within a bulk operation — debounce by clearing the
  cache and letting the next read recompute lazily.

---

## Composability with other docs

- **`CLAUDE-POOL-OPTIMIZATION.md`** — the pool-shadowing lint
  proposed there is naturally a Rule 6 in this framework. Move it
  into the same surface.
- **`CLAUDE-LABEL-HYGIENE.md`** — overlaps on the label dimension;
  defined in its own doc, surfaces here.
- **`CLAUDE-TOPOLOGY-GRAPH.md`** — cross-VRF leaks visualized in the
  L3 view; same data, two presentations.
- **`CLAUDE-POOL-OBSERVABILITY.md`** — add `lint.compute` events:
  rule, finding_count, duration_ms, trigger.

---

## Testing

- `tests/unit/test_lint_overlap.py` — pairwise overlap detection
  across a synthetic multi-project fleet, including the /0 edge case
  and same-CIDR collisions.
- `tests/unit/test_lint_cross_vrf_leak.py` — leak detection across a
  fleet with deliberate and accidental leaks.
- `tests/unit/test_lint_label_collision.py` — co-occurrence vector
  math, threshold tuning.
- `tests/unit/test_lint_reserved.py` — reserved-range matching.
- `tests/unit/test_lint_acks.py` — ack persistence, stale ack GC.
- `tests/integration/test_lint_routes.py` — route end-to-end, cache
  behavior, invalidation on write.

Targets: full fleet lint completes in <500ms for 5k subnets. Profile
and add an index if not.

---

## Implementation order

1. **Rule 1 (overlap) + Rule 2 (same-CIDR) + fleet lint page.**
   Highest signal, simplest implementation.
2. **Per-project lint badge** on project list. Reuses the cache.
3. **Save-time hint** with toast on cross-project conflict.
4. **Rule 5 (reserved ranges)** + reserved-range CRUD UI.
5. **Acknowledgement system** + stale-ack GC.
6. **Rule 3 (cross-VRF leak)** — requires the pool resolver to be
   callable per-VRF, easy lift.
7. **Rule 4 (label collision)** — heuristic; tune threshold against
   real fleets before exposing.
8. **JSON API for CI integration.** Trivial after the above.

Each step is independently shippable; later rules are pure adds to the
lint surface, no schema changes.

---

## Related docs

- `CLAUDE.md` — top-level reference, VRF model.
- `CLAUDE-LABEL-HYGIENE.md` — sibling lint, label-dimension issues.
- `CLAUDE-POOL-OPTIMIZATION.md` — pool-shadowing lint folds in here.
- `CLAUDE-TOPOLOGY-GRAPH.md` — visualizes findings touched by Rule 3.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy for `lint.compute`.
