# CLAUDE-GENERATE-COMMITS.md

> Companion to the top-level `CLAUDE.md`. Read it first.
>
> **Strongly related:**
> - `CLAUDE-NE-HW-BINDING.md` — auto-rule rematerialization is reframed as
>   a commit-producing event by this design.
> - `CLAUDE-DOCUMENT-GENERATION.md` — the per-artifact `context_snapshot`
>   pattern is the precedent reused here at the project level.
> - The planned audit-log work package — **complementary, not redundant**.
>   See "Relationship to the audit log" below.
>
> **Scope:** Give the `generate` step git-style semantics. Every call to
> the generate function (HW from BoM, NE instance creation, NE auto-rule
> rematerialization) produces a **commit** that freezes the current
> template+BoM state, materializes elements from it, and records who/what/
> when so every generated element knows which commit gave it which value.
>
> **Status:** Implemented (2026-06-03). generate_commits.py module, hw.py integration, commit log/preview/detail templates, per-instance history + merge_mode, 19 unit tests. Full 3-way merge for existing instances on template changes is in apply_template_changes(); qty-shrink deletion deferred to a follow-up.

---

## The concept in one line

`generate` becomes `git commit`. Template and BoM edits accumulate as
**pending** changes against the project's `HEAD` commit. The user
previews the impact, then `generate` atomically commits the change set
and propagates it to generated elements with a commit ID.

---

## Mapping to git

| git                  | ipam                                              |
|----------------------|---------------------------------------------------|
| working tree         | live templates + BoM (mutable)                    |
| index / staging      | pending change set (computed: current vs HEAD)    |
| HEAD                 | latest `generate_commit` for this project         |
| commit               | one `generate` run, with attribution + diff       |
| object hash          | sha256 of canonicalised snapshot                  |

The mapping breaks down in one important place: generated instances
**aren't files reconstructed from a commit**. They carry instance-local
state on top of what the template gave them — asset tags, status, rack
placement, IPs in `port_overrides`, NE bindings pointing at their ports.
Regenerate is therefore a three-way merge, not a checkout.

---

## Decisions (locked in)

1. **Three merge modes**, configurable per project (default) with
   per-instance override:
   - `3way` *(default)* — only overwrite fields still at their
     as-generated value; keep local edits with a warning.
   - `frozen` — skip the instance entirely on regenerate.
   - `overwrite` — always take the new template value.

   No BoM-line-level cascade in v1. Project default + per-instance
   override is enough surface area.

2. **Qty shrink destroys.** A BoM qty decrease from 10 → 5 deletes the
   five excess instances. "Delete" means:
   - Remove from project inventory (soft-delete: status set to
     `deleted`, kept readable from the commit log; see open question 2).
   - Release any allocated IPs back to their pool.
   - Delete any cable records connecting the instance.
   - Unbind from any NE ifaces: clean `iface_bindings.ports` entries and
     the `hw:port_bound:{hw_instance_id}:{port_id}` index keys.
   - Selection rule: highest-numbered tag first.
   - Always shown in the preview before commit.

3. **Commit scope is the project.** Template changes can be local
   (project-scoped) or global; **both** are frozen into a per-project
   snapshot at commit time. A global template edit becomes pending in
   every project that references it; each project decides when to
   absorb it by calling generate.

4. **Provenance is rich.** Each generated instance carries a
   `history: [{commit_id, ts, field, old, new, reason}]` list. Rendered
   as a timeline on the element detail page.

5. **Pending state is computed**, not stored. At view time, diff
   (normalised current templates+BoM) against (snapshot embedded in
   HEAD). No drift bugs, reuses the artifact pipeline's snapshot
   pattern.

6. **Scope: HW + NE.** HW instances from BoM lines, NE instances, and
   NE auto-rule iface bindings all go through the same commit
   machinery.

7. **Preview is critical-path UI.** Given decision 2, generate without
   preview is a footgun. The "Pending changes" view must show, before
   commit, the full destructive footprint: instances to be created /
   updated / deleted, cables to be deleted, IPs to be released, NE
   ifaces to be unbound, and per-instance merge outcomes ("srv-001:
   template port renamed Eth1 → Eth1/data, instance has manual edit
   `Eth1/uplink`, merge_mode=3way → keeping local").

8. **Initial commit on migration.** Each existing project gets a
   synthetic `kind='initial'` commit at deploy time with current
   instance values and empty per-instance history. All future commits
   diff against that baseline.

---

## Data model

### `generate_commit:{cid}`

```json
{
  "id": "c-a1b2c3d4",
  "project_id": "abc123",
  "created_at": "2026-05-26T14:23:11+00:00",
  "user": "lucian",

  "kind": "bom-all | bom-line | ne-instances | ne-rematerialize | initial",
  "trigger": {
    "bom_line_id": "...",
    "ne_instance_id": "..."
  },

  "parent_commit": "c-fedcba98",
  "snapshot_key":  "snapshot:c-a1b2c3d4",

  "diff_summary": {
    "templates_added":   ["tmpl-x"],
    "templates_edited":  [{"id": "tmpl-y", "fields": ["ports", "u_size"]}],
    "templates_deleted": [],
    "bom_added":   ["bom-line-z"],
    "bom_edited":  [{"id": "bom-line-w", "fields": ["qty"]}],
    "bom_deleted": []
  },

  "impact": {
    "hw_created":  ["hw-101", "hw-102"],
    "hw_updated":  [{"id": "hw-100", "fields": ["ports"], "merge": "3way-clean"}],
    "hw_deleted":  [
      {"id": "hw-099", "released_ips": ["10.0.0.5"],
       "deleted_cables": ["cbl-7"], "unbound_from": ["ne-001:iface-mgmt"]}
    ],
    "ne_created":  [],
    "ne_updated":  ["ne-001"],
    "ne_deleted":  [],
    "ne_rematerialized": [
      {"ne_id": "ne-001", "iface_id": "iface-mgmt",
       "added": ["hw-101:iLO"], "removed": []}
    ],
    "conflicts": [
      {"instance_id": "hw-088", "field": "ports.Eth1.name",
       "base": "Eth1", "local": "Eth1/uplink", "remote": "Eth1/data",
       "merge_mode": "3way", "resolution": "kept-local"}
    ]
  },

  "merge_mode_default": "3way",
  "notes": "Q3 wave 2 deployment"
}
```

### `snapshot:{cid}` (separate key — can be large)

The frozen template+BoM state from *this project's perspective* at
commit time:

```json
{
  "commit_id": "c-a1b2c3d4",
  "hw_templates":     {"tmpl-x": { ...full body... }},
  "ne_types":         {"ne-router-pe": { ... }},
  "ne_schemas":       {"sch-1": { ... }},
  "bom":              [ { ...all bom lines, deep-copied... } ],
  "subnet_templates": { ... },
  "labels":           { ... },
  "vrfs":             { ... }
}
```

Stored separately from the commit metadata because snapshots are large
and rarely read (only by preview computation and the future revert/
"regenerate this old artifact" workflows). Content hash on
`canonical_json(snapshot)` is stored in the commit for fast equality
checks.

### Per-instance fields (added to both `hw_instance` and `ne_instance`)

```json
{
  "id": "hw-101",
  // ... existing fields ...

  "created_in_commit":     "c-a1b2c3d4",
  "last_touched_in_commit": "c-a1b2c3d4",

  // The values that came from the template the last time generate
  // touched this instance. Baseline for 3-way merge.
  "template_snapshot": {
    "ports": [ ... ],
    "u_size": 1,
    "category": "server"
  },

  // null means inherit project default
  "merge_mode": null,

  "history": [
    {
      "commit_id": "c-a1b2c3d4",
      "ts":        "2026-05-26T14:23:11+00:00",
      "field":     null,
      "old":       null,
      "new":       null,
      "reason":    "created"
    },
    {
      "commit_id": "c-b2c3d4e5",
      "ts":        "2026-05-27T09:10:00+00:00",
      "field":     "ports.Eth1.name",
      "old":       "Eth1",
      "new":       "Eth1/data",
      "reason":    "template-edit-applied"
    }
  ]
}
```

### Redis key conventions

```
generate_commit:{cid}              JSON   — commit metadata
snapshot:{cid}                     JSON   — frozen template + BoM state
project:{pid}:commits              List   — cid's, newest-first
project:{pid}:head_commit          String — current HEAD cid (= commits[0])
project:{pid}:initial_commit       String — first cid for the project
```

The audit log (when it lands) lives in a separate keyspace and remains
the source of truth for *every* edit; this layer is for the *coarse
semantic moments* at which they get applied.

---

## Snapshot & diff algorithm

### Building a snapshot at commit time

1. Walk the project's BoM and NE instances. Collect every referenced
   template/type/schema/subnet-template/label/vrf id.
2. For each, deep-copy the live body into the corresponding bucket of
   the snapshot.
3. Globals referenced by this project are copied; globals not
   referenced are skipped.
4. Compute `snapshot_hash = sha256(canonical_json(snapshot))`.

### Computing pending changes for the preview

1. Build `snapshot_now` from the live state using the same rules.
2. Load `snapshot:{head_commit}`.
3. Diff field-by-field:
   - Added / removed templates and BoM lines: whole-object reporting.
   - Retained ones: structural walk, per-field diff. Ports lists: set
     diff by port `name`. BoM: ordered diff by line id.
4. Compute per-instance impact:
   - For each affected template, find instances with that `template_id`.
   - Run the merge logic (below) in dry-run mode → bucket as
     `created` / `updated` / `unchanged` / `deleted` / `conflicted`.
5. Render in the preview UI before the user clicks Commit.

Diff cost is `O(template_count + bom_size + affected_instance_count)`.
For large projects, cache `snapshot:{head_commit}` per request.

---

## Merge modes — the 3-way logic

For each tracked field on each affected instance:

```python
BASE   = instance.template_snapshot[field]          # what template gave us last time
LOCAL  = instance[field]                            # current instance value
REMOTE = derive_from_new_template(...)              # what new template would give

if LOCAL == BASE:
    instance[field] = REMOTE                        # clean: no local edits
elif LOCAL == REMOTE:
    pass                                            # converged: nothing to do
else:
    # CONFLICT — local edit and incoming change diverge
    mode = instance.merge_mode or project.merge_mode_default
    if   mode == "3way":      keep LOCAL  → record conflict + warning
    elif mode == "frozen":    skip whole instance entirely
    elif mode == "overwrite": instance[field] = REMOTE → warning

# After merge, BASE is advanced so next-time conflict detection works:
if mode != "frozen":
    instance.template_snapshot[field] = REMOTE
```

### Tracked vs instance-local fields

These tables are the **actual contract** of the feature. Lists below
are illustrative; the full review pass (every field currently on the
two object types) is open question 1.

| HW instance — tracked from template     | HW instance — instance-local |
|------------------------------------------|-------------------------------|
| `ports[]` structure (name, type, count)  | `port_overrides` (IP allocations) |
| `u_size`, `depth`, `weight_kg`, `power_w`| `asset_tag`, `status`, `location` |
| `category`, `vendor`, `model`            | `serial`, `description`, custom labels |
| `form_factor`, `cable_type`              | |

| NE instance — tracked from NE type       | NE instance — instance-local |
|------------------------------------------|-------------------------------|
| `iface[]` structure                      | `iface_bindings` (bind_mode, rule, ports[]) |
| schema-driven field *shape*              | schema-driven field *values* |
| derived field definitions                | manual ne-instance-level labels |
| `default_bind_rule` per iface            | `name`, instance-level overrides |

NE iface_bindings deserve special handling: `bind_mode='auto-rule'`
bindings have a `ports[]` that is *also* generated from another rule
applied to live HW. Rematerialising those is a sub-step of the
commit and shows up in `impact.ne_rematerialized`.

---

## Generate flow (the commit)

```
1. Compute pending changes (snapshot_now vs snapshot:{head_commit}).
2. If empty AND no new BoM rows to materialize → no-op,
   return "nothing to commit".
3. Build impact plan: created / updated / deleted / conflicted instances,
   freed IPs, deleted cables, NE rebinding deltas.
4. Render preview. User confirms (button: "Commit changes (N)").
5. Open atomic transaction (Redis MULTI/EXEC where possible,
   app-level compensating writes where not):
   a. Save snapshot:{new_cid}.
   b. For each instance to be DELETED:
      - Release IPs (pool returns).
      - Delete cable records.
      - Clean hw:port_bound:* keys.
      - For affected NE bindings: drop the port entry from ports[],
        record in impact.ne_rematerialized.
      - Set instance.status='deleted', append final history entry.
   c. For each instance to be UPDATED:
      - Run merge logic per tracked field.
      - Write new template_snapshot.
      - Append per-field history entries (one per changed field).
      - Update last_touched_in_commit.
   d. For each instance to be CREATED:
      - Standard generation path.
      - Set created_in_commit + last_touched_in_commit = new_cid.
      - Append history entry {reason: 'created'}.
   e. For each affected NE auto-rule binding:
      - Rematerialise, diff against previous binding.ports.
      - Record delta in impact.ne_rematerialized.
      - Update the NE instance's history.
   f. Save generate_commit:{new_cid}.
   g. LPUSH project:{pid}:commits new_cid.
   h. SET project:{pid}:head_commit = new_cid.
6. Commit transaction. Flash success with cid and impact counts.
```

### Async path

`generate_all_from_bom` already runs async via `core.jobs`. The commit
finalisation (steps 5f–5h above) runs at the **end** of the job, so
the user sees one commit per "Generate All" click, regardless of how
many BoM lines were processed. Per-line `generate_from_bom` produces
one commit per click.

---

## Initial commit (migration)

On first deploy of this feature, for each existing project:

```python
for pid in r.smembers('projects:index'):
    cid = new_id()
    snap = build_snapshot(pid)
    save_generate_commit({
        'id': cid, 'project_id': pid,
        'created_at': now_iso(), 'user': 'system',
        'kind': 'initial', 'parent_commit': None,
        'snapshot_key': f'snapshot:{cid}',
        'diff_summary': {}, 'impact': {},
        'merge_mode_default': '3way',
        'notes': 'Synthetic initial commit — pre-existing state.',
    })
    save_snapshot(cid, snap)
    r.lpush(f'project:{pid}:commits', cid)
    r.set(f'project:{pid}:head_commit', cid)
    r.set(f'project:{pid}:initial_commit', cid)

    for inst in project_instances(pid) + project_ne_instances(pid):
        inst['created_in_commit']     = cid
        inst['last_touched_in_commit'] = cid
        inst['template_snapshot']     = derive_current_template_values(inst)
        inst['merge_mode']            = None
        inst['history'] = [{
            'commit_id': cid, 'ts': now_iso(),
            'field': None, 'old': None, 'new': None,
            'reason': 'initial-import',
        }]
        save_instance(inst)
```

Idempotent — re-running detects an existing `project:{pid}:initial_commit`
and skips.

---

## UI surfaces

1. **Project page** — "Pending changes" badge with count, link to
   preview. Suppressed when count is zero.
2. **Pending changes / Preview**
   (`/projects/<pid>/commits/preview`) — full impact plan with
   explicit destructive listing; "Commit" button performs the generate.
3. **Commit log** (`/projects/<pid>/commits`) — newest-first list,
   summary line per commit ("23 created, 4 updated, 2 deleted").
4. **Commit detail** (`/projects/<pid>/commits/<cid>`) — diff_summary
   + impact + link to snapshot view.
5. **Element detail pages** — "Provenance" / "History" tab with the
   per-field timeline, each entry linking to its commit.

---

## Relationship to the audit log

Both ship; they don't overlap.

| | Audit log | Commit log |
|---|---|---|
| What | every CRUD op on every entity | the moments generate runs |
| Volume | high | low (a few per project per day) |
| Question it answers | "who edited template X at 14:03?" | "which design version produced srv-001?" |
| Source of truth for | raw edit history | semantic design versions |

The commit log can reference the audit log: "between commits A and B,
these audit events occurred on these entities" — useful for diagnosing
*why* pending changes accumulated.

---

## Acceptance criteria

- [ ] First deploy creates an `initial` commit per project; all
      instances backfilled with `template_snapshot` and history.
- [ ] Editing a BoM line qty from 10 → 5 and clicking Generate
      produces a commit that deletes 5 instances, releases their IPs,
      deletes their cables, cleans `hw:port_bound:` keys, and rebinds
      affected NE ifaces.
- [ ] The Preview page shows that destructive footprint before commit;
      cancelling Preview leaves all data unchanged.
- [ ] Editing a HW template port name and clicking Generate updates
      all instances of that template under 3-way merge; instances
      with manual edits to that port name keep their local value and
      appear under `impact.conflicts`.
- [ ] Switching a single instance's `merge_mode` to `frozen` causes
      the next generate to skip that instance entirely.
- [ ] Element detail page shows the commit history with per-field
      changes; each entry links to the commit detail page.
- [ ] Commit list renders newest-first; commit detail shows the full
      diff_summary + impact.
- [ ] Auto-rule rematerialisation (`CLAUDE-NE-HW-BINDING.md`) runs
      through this commit machinery; its diff appears in
      `impact.ne_rematerialized`.
- [ ] Per-line `generate_from_bom` produces one commit per click;
      `generate_all_from_bom` produces one commit per click (not one
      per line).
- [ ] A global HW template edit creates a pending change in every
      project that references it; each project absorbs it independently
      by calling generate.

---

## Out of scope

- **Revert / checkout a previous commit.** Snapshots make it possible
  later, but the merge semantics with current local edits are non-
  trivial; defer.
- **Branching / parallel design versions.** No.
- **Cross-project diff.** Each project's commit history is independent.
- **Diff visualisation beyond field-level lists.** No structural visual
  diff for nested template ports in v1 — text/list rendering is enough.
- **Cherry-pick a single template change across multiple projects.**
  Global edits become pending in each project naturally; explicit
  cherry-pick is unnecessary.
- **External event sources** (webhooks, git push integration). No.

---

## Open questions to resolve before implementation

1. **Tracked-field lists.** Review every existing field on
   `hw_instance` and `ne_instance` and bucket it as tracked-from-template
   or instance-local. Likely 30–60 fields total. The tables in this doc
   are illustrative, not exhaustive.
2. **Soft vs hard delete** for qty-shrink removals. Soft (this doc's
   recommendation) keeps history coherent — old commit detail pages
   can still link to the deleted instance. Hard is simpler but
   "what was hw-099?" stops working. Confirm soft.
3. **Selection rule for which instances to delete** when qty shrinks.
   Default: highest-numbered tag first (LIFO over tag sequence).
   Alternative: oldest-touched first (LRU). Recommend tag-LIFO so the
   behaviour is predictable from the tag scheme.
4. **Conflict surfacing in `3way` mode.** Block the commit or apply
   with warnings? Recommend warnings + per-instance conflict markers;
   the user can flip merge_mode and re-run if they want a hard pass.
5. **History size cap.** Bound the per-instance `history[]` list?
   Suggested: keep all entries until 100, then collapse older ones
   into a single "history summary" pointer to the older commits.
   Could defer to v2.
6. **Snapshot storage growth.** Each commit holds a deep copy of all
   referenced templates + BoM. For large template libraries, content-
   addressed snapshots (shared by hash when unchanged) would cut
   storage. Optimisation — defer until measured.
7. **Per-instance `merge_mode` UX.** Where does the toggle live —
   instance edit form, or dedicated "merge behaviour" widget on the
   detail page? Probably the latter, to keep it discoverable.
