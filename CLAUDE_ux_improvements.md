## Planned UX Improvements — Trust, Feedback, Empty States

> **Status:** Fully implemented (2026-06-03). All redirect-on-error patterns
> fixed in hw.py forms (add/edit template, add instance, BoM save). Mobile e2e
> from nav spec remains low-priority.
> Stack stays **Bootstrap 5.3 + Jinja2** — same as the navigation doc.
> Some items depend on the schema-flexibility and navigation plans; see
> "Dependencies" at the end.

### Design principles (and what we explicitly rejected)

| Rejected approach | Why |
|---|---|
| Visual redesign (theming, icon overhaul, animations) | Pure polish; doesn't move the needle until information architecture is fixed. Cheap to do once everything else is solid. |
| Replacing flash alerts with a toast system | Bootstrap flash alerts already work. Toasts solve a problem we don't have. |
| Client-side form library (react-hook-form, alpine, etc.) | Adds a JS framework dependency for a problem solved with `request.form` round-tripping. |
| Long feature list ("30 things that sound reasonable") | Easy to generate, hard to ship. We picked 6 items with high felt-difference and low cost. |
| Auth UX work | There is no auth today. Separate concern, separate doc when it exists. |

What we **did** commit to (in priority order):

1. **Preserve user input on validation errors.** Re-render forms with
   submitted values + inline errors instead of redirecting and losing data.
2. **Replace generic delete confirmations with impact previews.**
   Show *what* will be deleted/orphaned, not just "Delete?".
3. **Empty states that teach.** First-time users see context, not just
   "No sites yet."
4. **Bulk operations exposed from list views**, not hidden behind
   separate URLs.
5. **Real progress feedback** for slow operations (push-to-IPAM, generate
   instances). No more "is it broken or working?"
6. **Inline field validation** on the obvious inputs (CIDR, prefix
   length, port count).

### Current state — what's wrong

**Lost form input.** Multiple routes use the pattern:

```python
if invalid_json:
    flash('Invalid interfaces JSON', 'danger')
    return redirect(request.url)   # ← user's data is gone
```

Found in `add_ne_type`, `edit_ne_type`, and elsewhere. The redirect drops
the entire form state. The user has to retype.

**Generic confirmations.** Every delete looks like:

```html
<form onsubmit="return confirm('Delete?')">
```

No information about cascade effects. Deleting a site silently detaches
its PODs. Deleting an NE type silently orphans pod slots that reference it.
Users can't tell "Delete?" apart from "Delete and lose everything connected
to this?".

**Bare empty states.** `sites_list.html`:

```
No sites yet. Add one or bulk create.
```

The user landing here for the first time doesn't know what a site *is* in
this app, what data a site needs, whether to use bulk-create, or that
schemas determine what fields they'll be filling in. Same pattern repeats
in pods, NE types, BoM, racks, cables.

**Hidden bulk operations.** Bulk-add subnets and bulk-add sites exist as
separate URLs reached via header buttons. There's no concept of "select
several rows in this list and act on them" — which is what users expect
after using any other modern app.

**Synchronous slow ops.** "Push 47 requirements to IPAM" runs as one
request. No progress, no feedback, no way to know if it stalled. Users
either click again (creating duplicates risk) or assume it's broken.

**No client-side validation.** Type `10.0.0.0/33` into a supernet field
→ submit → flash error → redirect → start over. The browser already
knows it's invalid before the request leaves.

### Target structure

#### 1. Form input preservation

Replace the redirect-on-error pattern with re-render:

```python
# Before
if errors:
    flash(errors[0], 'danger')
    return redirect(request.url)

# After
if errors:
    return render_template('ne/ne_type_form.html',
                           ne=None, form_values=request.form,
                           errors=errors,
                           ne_schema=ne_schema, ...)
```

Templates read from `form_values` (falls back to model values for edit
forms) and render inline `errors` next to each field:

```jinja
<input name="name" value="{{ form_values.name or ne.name or '' }}"
       class="form-control {% if errors.name %}is-invalid{% endif %}">
{% if errors.name %}
  <div class="invalid-feedback">{{ errors.name }}</div>
{% endif %}
```

A small helper `flask_form_state(request, model=None)` in `core/forms.py`
returns the right value-source dict so views don't repeat the logic.

#### 2. Impact-aware confirmation modals

One generic confirmation pattern, replacing every `onsubmit="return
confirm(...)"`. Two pieces:

**An impact endpoint per entity** (or one generic endpoint once the
relation graph lands — see Dependencies):

```
GET /api/sites/<sid>/impact
→ { "label": "LON-1",
    "cascades": [
      {"kind": "detach", "count": 3, "what": "PODs will be detached"},
      {"kind": "orphan", "count": 12, "what": "NE slots will lose context"}
    ] }
```

**One reusable Jinja macro** `confirm_modal(target_url, label, impact_url)`
that:

- Fetches `impact_url` when the modal opens.
- Renders the cascade list in plain English.
- Posts to `target_url` on confirm.

Replaces a dozen ad-hoc `confirm('Delete?')` calls. The impact endpoint
also doubles as a useful API for future audit/UI work.

#### 3. Teaching empty states

A single macro in `templates/_macros.html`:

```jinja
{% macro empty_state(icon, title, body, primary_action, secondary=[]) %}
<div class="text-center py-5 text-muted">
  <div style="font-size:2.5rem">{{ icon }}</div>
  <h6 class="mt-2">{{ title }}</h6>
  <p class="small mx-auto" style="max-width:30rem">{{ body }}</p>
  <a href="{{ primary_action.url }}" class="btn btn-primary btn-sm">
    {{ primary_action.label }}
  </a>
  {% for s in secondary %}
    <a href="{{ s.url }}" class="btn btn-outline-secondary btn-sm ms-1">{{ s.label }}</a>
  {% endfor %}
</div>
{% endmacro %}
```

Per-entity usage explains the concept and offers next steps:

```jinja
{{ empty_state(
  icon='📍',
  title='No sites yet',
  body='A site is a physical location that hosts one or more PODs.
        Fields are configured under Schemas — define what you need
        (region, country, datacenter…) before adding sites.',
  primary_action={'label': '+ Add Site', 'url': url_for('ne.add_site', pid=proj.id)},
  secondary=[
    {'label': '⚡ Bulk Create', 'url': url_for('ne.bulk_add_sites', pid=proj.id)},
    {'label': '⚙ Configure Schema', 'url': url_for('ne.project_schemas', pid=proj.id)},
  ]
) }}
```

Each empty state is one block, written once, surfaced where it matters.

#### 4. List-view bulk operations

Convention for every list template:

- Each row carries `<input type="checkbox" class="row-select" value="{{ id }}">`.
- A `<div class="bulk-action-bar">` at the top, hidden by default.
- A tiny `bulk-actions.js` enables/disables the bar and counts selected.

```html
<div class="bulk-action-bar bg-light border rounded p-2 mb-2 d-none">
  <span class="selected-count">0</span> selected ·
  <button data-action="delete">Delete</button>
  <button data-action="label">Add labels</button>
  <button data-action="export">Export</button>
</div>
```

Actions post to existing bulk endpoints with the selected IDs. The
already-exists `/projects/<pid>/subnet/bulk` and `bulk_add_sites` routes
are stepping stones — we generalise to `/<entity>/bulk/<action>` over
time.

#### 5. Async progress for slow operations

For any operation expected to take >2 seconds (push requirements, generate
BoM instances, bulk imports), the pattern becomes:

1. POST kicks off the job, returns `{job_id}`.
2. Job state stored in Redis:
   `job:{id}` → JSON `{status, progress, total, message, result}`.
3. Client polls `GET /api/jobs/<id>` every 500 ms.
4. A reusable `<progress-bar data-job-id="…">` Jinja macro handles the UI.

A small `core/jobs.py` module with `start_job(fn, *args)` and
`update_progress(job_id, done, total, message)` covers the pattern.
No Celery, no broker — just Redis-backed bookkeeping. Fine for the
operation sizes this app deals with.

#### 6. Inline field validation

One JS file `static/js/validate.js`, one endpoint per validator type:

```
GET /api/validate/cidr?v=10.0.0.0/24       → {ok: true}
GET /api/validate/cidr?v=10.0.0.0/33       → {ok: false, error: 'Invalid prefix'}
GET /api/validate/prefix-in-supernet?...   → {ok: false, error: 'Outside supernet'}
```

Convention: any input with `data-validate="cidr"` (etc.) gets a
debounced blur handler that calls the endpoint and toggles
`is-invalid`/`is-valid` Bootstrap classes. No framework, ~50 LOC.

Reuses the server-side validators that already exist — single source of
truth for what counts as valid.

### Migration phasing

| Phase | Scope | Risk | Unlocks |
|---|---|---|---|
| 1 | `core/forms.py` helper + migrate `add_ne_type`, `edit_ne_type`, project add/edit, site add/edit to re-render-on-error. | Low | Stops the worst trust-killer. |
| 2 | `empty_state()` macro + adopt on sites, PODs, NE types, BoM, racks, cables list pages. | Very low | First-time UX. Visible immediately. |
| 3 | `confirm_modal()` macro + impact endpoints for site, pod, NE type, project. Replace every `confirm('Delete?')`. | Medium | No more silent cascades. |
| 4 | `core/jobs.py` + adopt for `push_requirements` and `generate_all_from_bom`. | Medium | Real feedback on slow ops. |
| 5 | `static/js/validate.js` + adopt on CIDR/prefix-length fields across the app. | Low | Catch errors before submit. |
| 6 | List-view bulk action bar. Adopt on subnets, sites, pods first. | Medium | Power-user efficiency, surfaces hidden ops. |

Phases 1, 2, and 5 are each a few hours and ship independently. Phase 3
is the most user-visible; Phase 4 is the most architectural and worth
batching with any other "long operation" work.

### Rules for new code (effective immediately)

- **Never `redirect(request.url)` after a validation error.** Re-render
  the form with `form_values=request.form` and inline errors.
- **Never use `onsubmit="return confirm(...)"`** on new templates. Use
  the `confirm_modal()` macro (Phase 3) — write it if it doesn't exist
  yet.
- **Never write a bare "No X yet."** Use the `empty_state()` macro with
  at minimum: what the entity is, primary action, one secondary path.
- **Don't add new synchronous endpoints for operations that touch >10
  records.** Use `core/jobs.py` and the polling pattern.
- **Server-side validators are the source of truth.** Client-side
  validation calls them via `/api/validate/...` rather than reimplementing
  the rules in JS.
- **Every new list view supports row selection** with the
  `bulk-action-bar` pattern, even if the only initial action is delete.

### Dependencies on other plans

A few items here get materially easier once the other docs land — order
matters:

- **Impact previews (Phase 3)** are per-entity today; once the generic
  **relation graph** ships (schema-flexibility doc, Phase 2), the impact
  endpoint becomes one generic resolver that walks the declared relations
  instead of one endpoint per entity. Don't over-build per-entity impact
  endpoints if the relation graph is imminent.
- **Auto-fill / derived field hints in forms** (e.g. "hostname will be
  computed as `lon-vnf-01`") need the **derivation engine** (schema
  doc, Phase 3). UX win is large; deferred until the engine lands.
- **"Where am I?" / project context** is part of the **navigation doc**
  (Phase 4). Don't band-aid it here.

Realistic sequencing, end to end across all three docs:

```
nav Phase 1–2   →  ux Phase 1 (forms)
nav Phase 3     →  ux Phase 2 (empty states)
schema Phase 2  →  ux Phase 3 (impact previews — generic)
                   ux Phase 4 (jobs)
                   ux Phase 5 (validation)
nav Phase 4     →  ux Phase 6 (bulk actions, leverages new nav)
schema Phase 3  →  derivation-driven form hints (future)
```

### Open questions (resolve before implementation)

- **Where do `errors` live in the form-render contract?** Per-field dict
  (`errors.name = "..."`) or flat list? Per-field is better UX but
  requires every validator to return field-keyed errors. Decide before
  Phase 1 lands.
- **Job retention.** How long does `job:{id}` live in Redis after
  completion? Short (5 min) is enough for UI polling; longer is useful
  for an audit trail. Probably 1 hour with a Redis TTL.
- **Bulk action authorization.** No auth today, but bulk-delete is the
  kind of operation that should be auth-gated once auth lands. Don't
  ship bulk-delete without flagging this for review when auth arrives.
- **Confirmation modal complexity ceiling.** A delete that affects 200
  downstream items shouldn't render 200 list items in the modal. Cap
  at "first 5 + 'and 195 more'" with optional expand.
