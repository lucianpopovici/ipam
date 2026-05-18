# CLAUDE-CHECKS.md

> Companion to the top-level `CLAUDE.md`. Read it first.
>
> **Strongly related:** `CLAUDE-DOCUMENT-GENERATION.md`. Checklists are
> artifacts produced by the same pipeline (same context snapshot, same
> Jinja2 environment, same storage). This doc describes the data model
> and lifecycle; the rendering mechanics are shared.
>
> **Scope:** Pre- and post-deployment checks the customer's engineers
> run when applying the design ipam produced. ipam **does not execute**
> anything — it generates checklists, tracks reported results, and
> produces signed-off reports.
>
> **Status:** Design — not yet implemented. Recommended to land after
> `CLAUDE-DOCUMENT-GENERATION.md` since checklists are a kind of
> artifact built on top of that pipeline.

---

## The constraint that shapes everything

ipam does not SSH, does not query devices, does not hold credentials.
Customer engineers apply configurations on their network and run any
verification commands themselves. Therefore:

- A "check" in this system is a **structured instruction**, not an
  automated assertion.
- A "check result" is a **reported outcome**, recorded by your team
  based on what the customer's engineers tell you, not measured.
- The deliverable is a **signed-off report PDF** that says "this is what
  was verified after deployment, here's who said so, here's when."

That constraint is a feature. It eliminates the credential-management
and network-reachability problems that make most check-running tools
operationally painful, and it puts ipam squarely in the design-and-
documentation role where it belongs.

---

## Goal

Today, pre/post deployment checks are written by hand for each
engagement — usually as a Word doc or spreadsheet copied from a previous
project and tweaked. The work is repetitive, the checks drift from the
design over time, and there's no way to tie "we verified BGP came up on
PE-LON-01" back to the source-of-truth design data.

After this work:

- A library of reusable **check templates** lives in ipam, attachable to
  NE types, interfaces, cables, VRFs, or projects.
- For a given deployment, ipam **materializes** the relevant checks per
  the project's actual inventory, producing a per-deployment **checklist**.
- The customer receives a PDF checklist with the design document. Their
  engineers run the checks, report results, your team records them.
- ipam produces a **signed-off report PDF** showing every check, its
  status, who recorded what when, included in the project's artifact
  history.

---

## Three entities

### `check_template:{ctid}` — the reusable definition

```json
{
  "id": "ct-iface-up",
  "name": "Interface administratively up",
  "description": "Verify the configured interface is up at L1 and L2.",
  "phase": "post",                          // 'pre' | 'post'
  "attached_to": "ne_iface",                // see "Attachment points" below
  "attachment_filter": {                    // optional, narrows applicability
    "iface_labels_any": ["data", "uplink"]  // only data or uplink ifaces
  },
  "action_description": "On the device, verify {{ iface.name }} on {{ ne.name }} is administratively up and the line protocol is up.",
  "expected_result": "Interface {{ iface.name }} status: up/up. No errors in the last 60 seconds.",
  "vendor_hints": {
    "cisco_ios":  "show interface {{ iface.name }} | include line protocol",
    "junos":      "show interfaces {{ iface.name }} terse",
    "arista_eos": "show interface {{ iface.name }} status",
    "linux":      "ip -br link show {{ iface.name }}"
  },
  "severity": "critical",                   // 'critical' | 'standard' | 'advisory'
  "tags": ["interface", "l1", "l2"],
  "scope": "global"                         // 'global' | 'customer' | 'project'
}
```

Key fields:

- `phase` — pre or post. A template is always one or the other; a
  symmetric "iface up after, but iface down before" pair is two
  templates.
- `attached_to` + `attachment_filter` — how the template materializes
  against project inventory (see next section).
- `action_description` / `expected_result` — Jinja2-rendered against the
  per-check context at materialization time. The rendered strings end
  up in the PDF.
- `vendor_hints` — non-executable. Provided only so the customer
  engineer can copy-paste the right command. ipam stays out of the
  vendor-syntax business by treating this as opaque text.
- `severity` — affects sorting and the signed-off report's summary
  ("3 critical, 8 standard, 2 advisory").
- `scope` — like NE types / HW templates, can be global (curated) or
  customer-specific (one customer's preferred phrasing of "ping the
  management IP").

### `checklist:{cid}` — a per-deployment instance

```json
{
  "id": "cl-q3-wave2-post",
  "project_id": "proj-acme-dc",
  "customer_id": "cust-acme",
  "phase": "post",
  "deployment_label": "Q3 expansion wave 2",
  "status": "in-progress",                  // see lifecycle below
  "generated_at": "2026-05-16T10:00:00Z",
  "generated_by": "u-alice",
  "context_snapshot_path": "/var/ipam/checklists/cl-q3-wave2-post/context.json",
  "checks": [
    {
      "id": "chk-001",
      "check_template_id": "ct-iface-up",
      "subject": {
        "ne_instance_id": "ne-pe-lon-01",
        "iface_id": "wan",
        "context": {                        // pre-rendered Jinja context for this item
          "ne_name": "pe-lon-01",
          "iface_name": "Eth1/1"
        }
      },
      "action_text": "On the device, verify Eth1/1 on pe-lon-01 is...",
      "expected_text": "Interface Eth1/1 status: up/up...",
      "vendor_hint_text": "show interface Eth1/1 | include line protocol",
      "severity": "critical",
      "status": "pending",                  // 'pending' | 'pass' | 'fail' | 'skip' | 'n/a'
      "notes": "",
      "reported_by_external": "",           // free text: who at the customer
      "recorded_by": "",                    // u-id of your team member who entered
      "recorded_at": null,
      "evidence": []                        // optional attachments later
    }
  ],
  "signed_off_at": null,
  "signed_off_by": null,
  "artifact_id": null,                      // set when a PDF snapshot is generated
  "supersedes_checklist_id": null           // for regenerated versions
}
```

Note: each check carries **rendered** `action_text` / `expected_text`,
not Jinja templates. Renders once at materialization; freezes there.
Changing the design later (e.g. renaming an interface) does not retroactively
rewrite an in-progress checklist's text. That's intentional — the
checklist is the contract for that deployment moment.

### Indices

```
check_templates:index            Set of ctid's
checklist:{cid}                  JSON record
project:{pid}:checklists         Ordered list of cid's (newest first)
checklist_template:scope:{key}   Index for filtering, e.g. global/customer/project
```

---

## Attachment points

Where a check template can hook into:

| `attached_to`  | Materializes once per…                                | Subject keys                  |
|----------------|--------------------------------------------------------|-------------------------------|
| `project`      | the project (singleton)                                | —                             |
| `ne_type`      | NE instance of that ne_type                            | `ne_instance_id`              |
| `ne_iface`     | (NE instance, iface) pair with a binding               | `ne_instance_id`, `iface_id`  |
| `cable`        | cable instance                                         | `cable_id`                    |
| `hw_template`  | HW instance of that template                           | `hw_instance_id`              |
| `vrf`          | VRF (when VRF entity exists; see VRF roadmap)          | `vrf_id`                      |

The `attachment_filter` further narrows:

```json
"attachment_filter": {
  "ne_iface_labels_any": ["mgmt"],          // only mgmt ifaces
  "hw_categories_any":   ["server"],        // only on servers
  "binding_modes_any":   ["lag"],           // only LAG bindings
  "phase_only_if_bgp":    true              // skip if no BGP defined
}
```

Filters are AND-combined; lists inside a filter are OR.

A check template attached to `ne_iface` with no filter materializes once
per (NE instance, iface) for every iface in the project — likely too
many. Filters are the usual case, not the exception.

### Materialization algorithm

```python
def materialize_checklist(project_id, phase, deployment_label):
    context = build_context_snapshot(project_id)   # same as doc-gen
    checks = []
    for tmpl in applicable_check_templates(project_id, phase):
        for subject in resolve_subjects(tmpl, context):
            if not passes_filter(tmpl, subject, context):
                continue
            item_ctx = build_item_context(tmpl, subject, context)
            checks.append({
                'id':              new_id(),
                'check_template_id': tmpl['id'],
                'subject':         subject,
                'action_text':     render_jinja(tmpl['action_description'], item_ctx),
                'expected_text':   render_jinja(tmpl['expected_result'], item_ctx),
                'vendor_hint_text': pick_vendor_hint(tmpl, subject, item_ctx),
                'severity':        tmpl['severity'],
                'status':          'pending',
                'notes':           '',
                ...
            })
    return checks
```

The `pick_vendor_hint` step picks one hint based on the device's
declared vendor (from the HW template) and falls back to the first hint
if no match — never errors.

---

## Lifecycle

A checklist moves through five states:

```
draft  ──►  in-progress  ──►  completed  ──►  signed-off  ──►  archived
                                  ▲                ▲
                                  │                │
                              all checks       human action
                              status != pending  by approver
```

- **draft** — just materialized. Engineers can edit subjects, exclude
  checks, change action text, before delivering to customer.
- **in-progress** — delivered. Results being recorded.
- **completed** — every check has a non-pending status; ready for
  signoff.
- **signed-off** — approver clicked the button. Whole record becomes
  read-only.
- **archived** — old signed-off checklists move here automatically (or
  manually) to keep the active list clean.

State transitions are routes; backwards transitions are admin-only and
audited.

> **Decision: editable check text in draft state?**
> Yes. Customer engagements often need last-minute tweaks ("change 'BGP'
> to 'eBGP peering' in this customer's terminology"). Once in-progress,
> the action/expected text is frozen — same reasoning as artifact
> immutability.

---

## Result recording

Two paths, both supported:

### Path A — ipam UI (your team records)

Customer engineer reports back via email/chat. Your team member opens
the checklist in ipam, ticks status per check, adds notes, optionally
records `reported_by_external` ("Sanjay Kapoor, ACME NetEng").

This is the v1 path. Lowest friction, no customer login.

### Path B — shared link (customer fills directly)

Not in v1. Sketched here for forward compatibility:

- A signed, expiring URL grants checklist read+update for one specific
  cid, no login needed.
- Customer engineer ticks results in a stripped-down UI; your team gets
  notified.
- Useful for big deployments where transcription is wasteful.

Save this for v2; the data model already supports it (`recorded_by` is
nullable, `reported_by_external` is free text).

---

## Integration with the document-generation pipeline

A checklist is an artifact source. The same six-stage pipeline runs:

1. **Snapshot** — at checklist materialization, the project context is
   frozen into the checklist's `context_snapshot_path`. Re-rendering the
   PDF months later uses this snapshot — even if the project has since
   changed.
2. **Resolve** — find `checklist.html` template (project → customer →
   default).
3. **Render Jinja** — produce HTML with the checks table, status badges,
   signoff block.
4. **Render PDF** — WeasyPrint.
5. **Package** — single PDF artifact.
6. **Persist** — write to `/var/ipam/artifacts/{aid}/checklist.pdf`,
   link from the checklist's `artifact_id`.

Generation is **lazy and re-runnable**. Users hit "Generate PDF" when
ready; they can regenerate as results come in. Each generation produces
a new artifact (new aid), the old PDFs stay accessible.

### Two render styles, same template engine

- `pre.html` — the pre-deployment checklist. Always all-pending at
  generation time (the engineer fills it in during deployment).
- `post.html` — the post-deployment checklist. May be generated with
  partial or full results.

The default templates ship with sensible layouts: severity grouping,
per-device sections, signoff block at the end. Customer template sets
override these for branding/phrasing.

---

## Combined deliverable: one PDF per deployment

A common ask: "give me one PDF that has the design AND the pre-checklist
AND space for post-results." Support this as a **combined artifact
type** in the doc-gen pipeline:

```
POST /projects/<pid>/generate
  type=combined
  include=[design, pre_checklist, post_checklist]
  deployment_label="Q3 wave 2"
```

The pipeline assembles the three sub-documents into one PDF (multi-page,
proper bookmarks, single download). Saves the customer from juggling
files.

---

## Reusable check library

A handful of patterns recur in every engagement. Ship default check
templates so engineers don't write them from scratch:

| Default template name              | Phase | Attached to | Default action text                                       |
|------------------------------------|-------|-------------|-----------------------------------------------------------|
| `default:iface_admin_down`         | pre   | ne_iface    | "Verify {{ iface }} on {{ ne }} is admin down before reconfiguration." |
| `default:no_existing_routing`      | pre   | project     | "Confirm no live OSPF/BGP/EIGRP processes on target devices." |
| `default:cabling_matches_design`   | pre   | cable       | "Verify cable {{ cable.label }} is physically present between {{ end_a }} and {{ end_b }}." |
| `default:software_version`         | pre   | hw_template | "Verify device {{ hw.asset_tag }} is running expected software version {{ hw.expected_software }}." |
| `default:iface_up`                 | post  | ne_iface    | "Verify {{ iface }} on {{ ne }} is up/up."                |
| `default:bgp_session_up`           | post  | ne_iface    | "Verify BGP session {{ neighbor }} is Established."       |
| `default:ping_reachability`        | post  | ne_iface    | "Verify {{ iface.ip }} on {{ ne }} responds to ICMP from a designated test host." |
| `default:route_present`            | post  | ne_iface    | "Verify expected route {{ expected_route }} is present in the routing table." |

These ship as `scope: 'global'`. Customers can override with their own
`scope: 'customer'` versions (different phrasing, vendor-specific
commands they prefer).

---

## Approval / signoff

Once all checks are non-pending, the **completed** state allows a
sign-off button. The signer is recorded with timestamp; the PDF generated
post-signoff includes:

```
Customer Acceptance
───────────────────
Reported by:   Sanjay Kapoor, ACME NetEng       2026-05-18
Recorded by:   Alice (ipam)                     2026-05-18
Signed off by: Carol (Engineering Manager)      2026-05-19

✓  18 of 19 checks passed
⚠   1 check failed (Eth1/47 LAG member status — see notes)
```

After signoff, the checklist is immutable. Changes require generating a
new checklist (linked via `supersedes_checklist_id`).

---

## UI workflow

### Check templates admin (`/admin/checks/templates`)

- Table of all check templates, filterable by phase, scope, attached_to.
- New / Edit form per template.
- Live preview pane: pick a project, see how many checks this template
  would materialize against current inventory ("would produce 23 checks
  on this project").

### Project checklists (`/projects/<pid>/checklists`)

- List of all checklists for the project, newest first.
- "Create new checklist" button → modal: phase, deployment label, "Apply
  default filter" toggle, "Include drafts only/curated only/all
  applicable" radio.
- Preview pane: shows the materialization count by template before
  committing.

### Single checklist (`/checklists/<cid>`)

- Top: status badge, generated/signed-off metadata, action buttons.
- Filter bar: by status (pending/pass/fail), by severity, by ne_instance.
- Table of checks: action text, expected, vendor hint, status dropdown,
  notes textarea, "reported by" input.
- Bulk action: "Mark selected as Pass" (the most common operation for a
  successful deployment).
- "Generate PDF" button → produces artifact, links from `artifact_id`.

---

## Routes

| Method | Path                                                  | Description                  |
|--------|--------------------------------------------------------|------------------------------|
| GET    | `/admin/checks/templates`                              | List check templates         |
| GET/POST | `/admin/checks/templates/add`                        | Create template              |
| GET/POST | `/admin/checks/templates/<ctid>/edit`                | Edit template                |
| POST   | `/admin/checks/templates/<ctid>/delete`                | Delete (if not used)         |
| POST   | `/admin/checks/templates/<ctid>/preview`               | Preview materialization count for a project |
| GET    | `/projects/<pid>/checklists`                           | List checklists for project  |
| POST   | `/projects/<pid>/checklists/create`                    | Materialize a new checklist  |
| GET    | `/checklists/<cid>`                                    | Single checklist editor      |
| POST   | `/checklists/<cid>/update`                             | Update check status (batch)  |
| POST   | `/checklists/<cid>/transition`                         | State change (draft→in-progress→…) |
| POST   | `/checklists/<cid>/generate-pdf`                       | Render checklist PDF artifact|
| POST   | `/checklists/<cid>/signoff`                            | Approver action              |
| POST   | `/checklists/<cid>/supersede`                          | Create v2 from this checklist|

---

## Validation codes

Reuse the existing validation framework (`templates/hw/validation.html`).
Promote it to a project-wide validation list rather than HW-only; rename
the template to `templates/validation/codes.html` and include from
multiple places.

| Code                          | Severity | Trigger                                                  |
|-------------------------------|----------|----------------------------------------------------------|
| `CHK_TEMPLATE_BAD_JINJA`      | error    | check_template's action/expected has Jinja syntax error. |
| `CHK_TEMPLATE_NO_SUBJECT`     | warning  | Materialization produced zero subjects (filter too tight or no inventory). |
| `CHK_LIST_INCOMPLETE`         | info     | Checklist has pending items beyond the deployment date.  |
| `CHK_LIST_FAILED_CRITICAL`    | error    | Signed-off checklist has any failed critical check.      |
| `CHK_VENDOR_HINT_MISSING`     | info     | NE instance's HW has a vendor with no hint in the template — fallback used. |

---

## Migration

Greenfield feature; no data migration required. First run seeds the
default check templates listed in "Reusable check library" above via:

```bash
flask seed-checks
```

(idempotent — skips templates that already exist by `id`).

---

## Testing

### Unit (`tests/unit/test_check_materialize.py`)

- `materialize_checklist` produces the expected number of checks for a
  given project + template set.
- Filter combinations narrow correctly (OR within, AND between).
- Jinja rendering of action/expected text uses the per-item context;
  templating errors surface as `CHK_TEMPLATE_BAD_JINJA`.
- Vendor hint picker selects by HW template vendor; falls back when no
  match.

### Unit (`tests/unit/test_checklist_lifecycle.py`)

- State transitions allowed/disallowed (draft → in-progress fine; signed-off → in-progress
  refused except by superuser).
- Signoff requires all checks non-pending.
- Supersede creates a new checklist with `supersedes_checklist_id` set.

### API (`tests/api/test_checklist_routes.py`)

- Generate checklist with body, batch-update statuses, transition state,
  generate PDF artifact.

### E2E (`tests/e2e/test_checklist_flow.py`)

Create check templates → create project with NE instances → materialize
post-deployment checklist → record all results → generate PDF → sign off
→ verify artifact in project's artifact history.

---

## Acceptance criteria

- [ ] An admin can create a check_template attached to `ne_iface` with a
      filter on `iface_labels_any: ['mgmt']` and have it materialize once
      per mgmt iface across the project's NE instances.
- [ ] Materialization renders Jinja in the action/expected text using
      per-item context (NE name, iface name appear in the rendered
      strings).
- [ ] A checklist progresses through draft → in-progress → completed →
      signed-off; backward transitions require admin role.
- [ ] Signoff is blocked when any check is still `pending`.
- [ ] After signoff, the checklist is read-only; the only allowed action
      is `supersede`.
- [ ] Generating a PDF produces a stored artifact linked from the
      checklist; regenerating after status changes produces a new
      artifact, both retained.
- [ ] A combined PDF (design + pre + post) is one downloadable file
      with proper bookmarks per section.
- [ ] The default check library seeds on first run and is reusable
      across projects.
- [ ] Vendor hints render based on the NE instance's bound HW vendor;
      fallback works when vendor unknown.

---

## Files touched

```
checks.py                                    NEW blueprint
  routes: /admin/checks/*, /projects/<pid>/checklists/*, /checklists/*

checks_logic.py                              NEW
  - materialize_checklist
  - applicable_check_templates
  - resolve_subjects (per attached_to)
  - passes_filter
  - state-transition rules

templates/checks/
  templates_list.html                        NEW
  template_form.html                         NEW
  checklist_list.html                        NEW
  checklist_detail.html                      NEW                  (the main editor UI)
  _check_row.html                            NEW                  (one row, reused)

templates/default/                           (extends doc-gen defaults)
  pre.html                                   NEW                  (pre-deployment PDF template)
  post.html                                  NEW                  (post-deployment PDF template)
  combined.html                              NEW                  (design + pre + post)
  print.css                                  EXTEND               (status badge styles)

scripts/seed_checks.py                       NEW
core/check_seed_data.py                      NEW                  (default check library, importable)

tests/unit/test_check_materialize.py         NEW
tests/unit/test_checklist_lifecycle.py       NEW
tests/api/test_checklist_routes.py           NEW
tests/e2e/test_checklist_flow.py             NEW

app.py                                       — register checks_bp
templates/_project_nav.html                  — add "Checklists" nav link
templates/dashboard.html                     — show pending checklist count
```

---

## Out of scope

- **Live execution.** ipam doesn't run commands. Vendor hints are
  copy-paste fodder for the customer engineer; ipam does not assert
  whether the device's response matches the expected result.
- **Result parsing from device output.** Same reason. If the customer
  engineer reports "Eth1/1 is up", that's the recorded value.
- **Customer self-service result entry.** v2. Shared signed links
  bypassing login are sketched but not built.
- **Cross-project check libraries.** Each customer has their own scope;
  global templates are curated by your team. No federated/public library
  of community checks.
- **Time-bounded checks.** "BGP session uptime > 24 hours" requires
  measurement over time. v1 checks are point-in-time. Defer.
- **Severity-driven gating.** Currently any failed critical check
  *blocks signoff status* is just a warning. A hard gate (cannot sign off
  while critical fails) is a one-line policy change later if desired.

---

## What this does NOT need

- Auth changes beyond what's already in `auth.py`. The editor/viewer
  roles cover this — editors can create/edit/record; viewers see-only.
- New entity in the relations framework (`core/relations.py`). The
  `check_template` ↔ `attached_to` relationship is a stored field, not
  a many-to-many. Same for `checklist.checks` (embedded array, not
  related entities).
- VRF or BGP entities. Checks reference them when present (via the
  context snapshot), but the check_template attachment model works
  against the data model that exists today. If/when VRFs land, the
  `vrf` attachment type lights up automatically.
