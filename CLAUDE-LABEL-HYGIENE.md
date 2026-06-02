# CLAUDE-LABEL-HYGIENE.md

> Companion to the top-level `CLAUDE.md` — read that first for the
> label / pool resolution model.
>
> **Feature:** Lint for the label namespace — unused labels, unmatched
> label queries, likely typos, and inconsistent capitalization or
> hyphenation. Sibling to `CLAUDE-CROSS-PROJECT-LINT.md`.
> **Scope:** add to the existing `lint.py` blueprint (or new
> `label_lint.py`), one section on the fleet lint page, one JSON API.
> **Status:** Design — not yet implemented.

---

## Goal

Labels are how the pool resolver finds the right pool, how the BGP
advertisement rules pick what to advertise, and how DNS / DHCP exports
scope their outputs. Drift in the label namespace ("prod" vs "prdo",
"oob" vs "OOB" vs "out-of-band") quietly breaks all of them.

Five detection rules, all read-only:

1. **Orphan labels** — defined on a pool, never carried by any
   subnet (or vice versa). Dead code in the namespace.
2. **Unmatched pool queries** — a pool whose label query matches zero
   subnets in its resolution scope.
3. **Probable typos** — labels within Levenshtein distance 1–2 of a
   much more common label.
4. **Case / separator drift** — labels that differ only in case or
   `-` vs `_` (heuristic; not always a bug).
5. **Overloaded labels** — same label used with semantically
   incompatible co-occurrence vectors (already proposed as Rule 4 in
   `CLAUDE-CROSS-PROJECT-LINT.md`; cross-referenced here).

Each rule is cheap; together they catch a class of bugs that's hard
to spot manually past a handful of projects.

---

## Non-goals

- **No auto-rename.** Labels are referenced in many places (subnets,
  pools, BGP rules, DNS scopes). Auto-rename without confirmation is a
  good way to corrupt state. The lint flags; the operator runs an
  explicit rename action through the existing label-edit form.
- **No enforced naming convention.** No "all labels must be
  lowercase-hyphen." Some shops have other conventions; the lint
  *surfaces* drift without imposing a style.
- **No ML / semantic similarity.** Levenshtein + co-occurrence is
  enough signal. Embedding-based "these labels mean the same thing"
  would require ground-truth training data nobody has.
- **No retroactive cleanup tool.** The lint surfaces issues; users
  fix them through the existing CRUD. A bulk-rename action could
  come later but lives outside this doc.

---

## Detection rules

### Rule 1: Orphan labels

```python
def find_orphan_labels() -> list[dict]:
    """Labels referenced in pool queries with no matching subnet, or
    on subnets with no matching pool query."""
    labels_on_subnets = set()
    for net_id in r.smembers('networks:index'):
        net = get_network(net_id)
        labels_on_subnets.update(net.get('labels', []))

    labels_in_queries = set()
    for pool_id in r.smembers('pools:index'):
        pool = get_pool(pool_id)
        for ls in pool.get('include_label_sets', []):
            labels_in_queries.update(ls)
        for ls in pool.get('exclude_label_sets', []):
            labels_in_queries.update(ls)

    findings = []
    for lbl in labels_in_queries - labels_on_subnets:
        findings.append({
            'rule':     'orphan_label_in_query',
            'label':    lbl,
            'pools':    [p for p in pools_using_label_in_query(lbl)],
            'severity': 'warning',
        })
    for lbl in labels_on_subnets - labels_in_queries:
        findings.append({
            'rule':     'orphan_label_on_subnet',
            'label':    lbl,
            'subnets':  [n for n in subnets_with_label(lbl)],
            'severity': 'info',
        })
    return findings
```

`orphan_label_in_query` is a warning — the pool will resolve to
nothing. `orphan_label_on_subnet` is info — labels people put on
subnets for documentation purposes are common and fine.

### Rule 2: Unmatched pool queries

A pool whose full query (including VRF, site, project scope) resolves
to zero subnets. Distinct from Rule 1 because all the *individual*
labels might exist; it's the *combination* that matches nothing.

```python
def find_unmatched_pool_queries() -> list[dict]:
    findings = []
    for pool_id in r.smembers('pools:index'):
        pool = get_pool(pool_id)
        matched = run_pool_query(pool)
        if not matched:
            findings.append({
                'rule':     'unmatched_pool_query',
                'pool_id':  pool_id,
                'query':    pool_summary_for_display(pool),
                'severity': 'warning',
            })
    return findings
```

### Rule 3: Probable typos

For each label, find labels within Levenshtein distance ≤ 2 that
are at least `typo_ratio_threshold` (default 5×) more common in the
fleet. The threshold filters out legitimate distinct labels that
happen to be similar (`prod`/`pred` if both are common) from
likely typos (`prod` used 200×, `prdo` used 1×).

```python
def find_probable_typos(typo_ratio: int = 5) -> list[dict]:
    counts = label_usage_counts()  # {label: count_across_fleet}
    findings = []
    labels = list(counts)
    for i, a in enumerate(labels):
        for b in labels[i+1:]:
            if abs(len(a) - len(b)) > 2:
                continue
            d = levenshtein(a, b)
            if 1 <= d <= 2 and max(counts[a], counts[b]) >= \
                    typo_ratio * min(counts[a], counts[b]):
                rare, common = sorted([a, b], key=lambda x: counts[x])
                findings.append({
                    'rule':         'probable_typo',
                    'rare_label':   rare,
                    'rare_count':   counts[rare],
                    'common_label': common,
                    'common_count': counts[common],
                    'edit_distance': d,
                    'severity':     'warning',
                })
    return findings
```

The "rare vs common" framing in the finding output gives the operator
an obvious direction for the rename without the lint deciding.

### Rule 4: Case / separator drift

```python
def find_case_drift() -> list[dict]:
    counts = label_usage_counts()
    groups = defaultdict(list)
    for lbl in counts:
        key = lbl.lower().replace('-', '_')
        groups[key].append(lbl)

    findings = []
    for key, variants in groups.items():
        if len(variants) > 1:
            findings.append({
                'rule':     'label_case_drift',
                'variants': sorted(variants, key=lambda v: counts[v],
                                   reverse=True),
                'counts':   {v: counts[v] for v in variants},
                'severity': 'info',
            })
    return findings
```

Info-level because some teams deliberately distinguish `oob` (project
label) from `OOB` (network class). The lint surfaces the variation
without insisting it's wrong.

### Rule 5: Overloaded labels

Cross-reference to `CLAUDE-CROSS-PROJECT-LINT.md` Rule 4. Implemented
there, surfaced in both places via the shared finding payload.

---

## Storage

Same cache pattern as `CLAUDE-CROSS-PROJECT-LINT.md` — compute on
demand, cache 60 seconds in `lint:findings:cache:labels`, invalidate
on any subnet/pool write that touches labels.

Findings hash for ack persistence: rule + sorted involved labels +
involved pool/subnet IDs. Acks GC'd after 30 days of not appearing.

---

## Routes

```
GET  /lint/labels                          — label lint findings
GET  /api/lint/labels.json                 — JSON output

POST /lint/labels/findings/<fid>/ack       — acknowledge a finding
```

Integrated into the fleet lint page (`/lint`) as a tabbed section
alongside the cross-project lint.

### Label usage report

```
GET /lint/labels/usage
```

Not a finding surface — a sortable table of every label, its usage
count on subnets, count in pool queries, and which projects/VRFs
reference it. Useful for the "let's audit our label namespace"
conversation.

---

## UI

### Label lint tab on `/lint`

Grouped by rule:

```
Orphan labels in pool queries (3)
  "prdo" — referenced by pool "transit-prdo" in proj-A
    Did you mean "prod" (used 47 times)? [view pool] [ack…]

Unmatched pool queries (2)
  Pool "transit-old" in proj-B matches 0 subnets
    Query: vrf=prod-underlay, labels=[transit, legacy]
    [view pool] [ack…]

Probable typos (1)
  "prdo" (1 use) is very close to "prod" (47 uses)
    [view "prdo" usage] [view "prod" usage] [ack…]

Case / separator drift (2)
  "OOB" (12), "oob" (143), "Oob" (1) — looks like the same label
    [view all] [ack…]

Overloaded labels (1)
  See [Rule 4 in cross-project lint](/lint#rule-4-...)
```

### Per-label detail (`/lint/labels/<label>`)

For any label, show:

- Total usage count, broken down by subnet and pool query.
- List of subnets carrying it (with project / VRF / site).
- List of pools querying it.
- Suggested similar labels (Levenshtein ≤ 2) for quick navigation.

This page doubles as the "before I rename, let me see everything that
references it" pre-rename checklist.

---

## Edge cases

- **The empty-label `''`.** Should never exist; if found, surface as
  a high-severity finding with link to the offending subnet/pool.
  Validators should reject it at save time; if it slipped in, fix the
  validators and the data.
- **Unicode labels.** Lowercase normalization for case-drift detection
  uses `str.casefold()`, not `lower()`, to handle locale-specific
  cases. Edit distance is over Unicode codepoints; works fine for
  realistic label content.
- **Very short labels** (1–2 chars). Levenshtein 1 catches everything
  similar; the typo rule's count-ratio gate filters out the noise.
  No special case needed.
- **Acknowledged drift.** Some teams use `oob` and `OOB` deliberately
  (one for project label, one for label that flows to BGP). Ack the
  finding with a reason; it stays silenced until the data changes.
- **Bulk relabeling.** Cache invalidation on every label write would
  thrash. Debounce: clear once per write burst, recompute lazily on
  next read.

---

## Composability with other docs

- **`CLAUDE-CROSS-PROJECT-LINT.md`** — sibling. Shares the lint page,
  cache, and ack infrastructure. Overloaded-label rule is defined
  there, surfaced here.
- **`CLAUDE-POOL-OPTIMIZATION.md`** — the pool-shadowing lint folds
  into the cross-project lint, not here. Labels are about names;
  shadowing is about resolver precedence.
- **`CLAUDE-BGP-SESSIONS.md`** — advertisement rules query by label.
  Orphan-label warnings on BGP sessions feed back here.
- **`CLAUDE-DNS-EXPORT.md`** / **`CLAUDE-DHCP-EXPORT.md`** — both
  scope by label. Same lint applies; same surface.
- **`CLAUDE-POOL-OBSERVABILITY.md`** — adds `lint.labels.compute`
  event.

---

## Testing

- `tests/unit/test_label_orphans.py` — orphan detection across
  pools and subnets.
- `tests/unit/test_label_unmatched.py` — unmatched pool query
  detection.
- `tests/unit/test_label_typos.py` — Levenshtein math + ratio
  threshold behavior, including unicode and short-label edge cases.
- `tests/unit/test_label_case_drift.py` — grouping and casefold
  semantics.
- `tests/integration/test_label_lint_routes.py` — Flask routes,
  cache, ack persistence.

Target: full label lint completes in well under a second for fleets
with ~500 distinct labels. Levenshtein is O(n²) over distinct labels;
500² × bounded-string-length is fine. Add a coarse pre-filter
(first-character match) if profiling shows pain.

---

## Implementation order

1. **`label_usage_counts` helper** — used by every other rule.
2. **Rule 1 (orphans)** + label lint tab on `/lint`.
3. **Rule 2 (unmatched pool queries).**
4. **Rule 3 (typos)** — tune `typo_ratio` against a real fleet
   before exposing prominently.
5. **Rule 4 (case drift).**
6. **Per-label detail page** + label usage report.
7. **Acknowledgement integration** (reuses cross-project lint's
   ack system).
8. **JSON API** for CI integration.

Each step is independently shippable. Defaults are conservative
(typo threshold high, case drift severity info) so the lint is
useful from day one without crying wolf.

---

## Related docs

- `CLAUDE.md` — top-level reference, label model.
- `CLAUDE-CROSS-PROJECT-LINT.md` — sibling lint, shared
  infrastructure, Rule 4 (overloaded labels) defined there.
- `CLAUDE-POOL-OPTIMIZATION.md` — pool-shadowing lint folds into
  cross-project, not here.
- `CLAUDE-BGP-SESSIONS.md` / `CLAUDE-DNS-EXPORT.md` /
  `CLAUDE-DHCP-EXPORT.md` — label consumers; surface same findings.
- `CLAUDE-POOL-OBSERVABILITY.md` — event taxonomy for
  `lint.labels.compute`.
