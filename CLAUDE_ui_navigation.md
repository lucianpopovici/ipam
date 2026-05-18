## Planned UI Improvements — Navigation & Responsiveness

> **Status:** Design agreed, not yet implemented. New templates should follow
> this direction; existing pages migrate in phases.
> Stack stays **Bootstrap 5.3 + Jinja2** — no SPA rewrite, no JS framework.

### Design principles (and what we explicitly rejected)

| Rejected approach | Why |
|---|---|
| Rewrite UI as React/Vue SPA | Multiplies build complexity, breaks the "Flask + Jinja + Bootstrap" mental model, no current pain that justifies it. |
| Replace Bootstrap with Tailwind/shadcn | Same reason. Bootstrap 5.3 is already there, well-known, sufficient. |
| One mega-page per project (current style, just polished) | The "long scroll of cards" pattern is the root navigation problem — polishing it doesn't fix it. |
| Modal-driven UX for sub-entities | Hides URLs (no deep-linking, no back-button), poor on mobile. |

What we **did** commit to:

1. **Two-tier navigation.** A clean separation between *global config*
   (Schemas, HW Templates, Connectors, VMware) and *project-scoped work*
   (Subnets, Sites, PODs, NE Types, BoM…).
2. **Breadcrumbs everywhere.** A single Jinja macro, used on every page below
   the homepage. Replaces the ad-hoc `← Project Name` link at the top of
   each subpage.
3. **Project-context sidebar/tab strip** when inside a project — replaces the
   "scroll past Hardware card to find Network Elements card" pattern.
4. **Real mobile navbar.** Fix the current `navbar-expand-lg` that is
   neutered by an inner `flex-row`. Add a proper `navbar-toggler`.
5. **Active-state highlighting** on the navigation so users can see where
   they are.

### Current state — what's wrong

**`templates/base.html`** (top navbar):

- 10 top-level links mixing global config and primary entities in one flat row.
- `navbar-expand-lg` is set, but the inner `<div class="navbar-nav … flex-row">`
  overrides the collapse — links overflow horizontally on small screens
  instead of collapsing into a hamburger.
- Search bar is always inline, eating horizontal space on mobile.
- No active-state highlighting.
- Inconsistent emoji prefixing (some links have them, some don't).

**`templates/project_detail.html`:**

- Project actions in the header (`d-flex gap-2 flex-wrap`) — uses
  `flex-wrap`, so it survives narrow screens, but still cramped.
- The "Hardware" card and "Network Elements" card give 6 + 5 sub-links
  respectively. Finding "Inventory" or "Sites" requires scrolling and reading.
- No persistent project navigation — every subpage relies on the tiny
  `← Project Name` breadcrumb-as-link to get back.

**Subpages (sites, PODs, NE types, BoM, requirements…):**

- Each has its own `← Project Name` link at the top — consistent in pattern
  but ad-hoc per template, no central breadcrumb component.
- No way to jump *between* sibling sections (e.g. from Sites to PODs) without
  going up to project root first.
- "Where am I?" requires reading the page title.

**Duplicated concepts in the navbar:**

- "Schemas" appears as a global link and again per-project — same name,
  different scopes. Same for HW Templates and NE Types. Easy to confuse.

### Target structure

#### 1. Two-tier navigation

**Top bar** — only truly global concerns (compact):

```
🗺 IPAM   Projects   Pool Query   Search   ⚙ Admin ▾
                                            ├─ Global Labels
                                            ├─ Global Schemas
                                            ├─ Global NE Types
                                            ├─ Global HW Templates
                                            ├─ Connectors
                                            └─ VMware
```

The Admin dropdown collects everything that's "global config the user
rarely touches". The top bar stays short.

**Project context bar** (rendered when `pid` is in the URL, sits *below*
the navbar):

```
[Project: Production ▾]   Subnets · Sites · PODs · NE Types · Schemas · Requirements · BoM · Inventory · Racks · Cables · Templates · Validate
```

- A project picker dropdown on the left (jump to any project without
  going home).
- Section links across — the same set today's project_detail.html
  scatters across two cards. Active section is highlighted.
- Collapses into an offcanvas drawer on mobile.

Implementation: one Jinja include `templates/_project_nav.html` that reads
`g.current_project` (set in a `before_request` hook when the URL contains
`pid`). No per-template duplication.

#### 2. Breadcrumb macro

Single source of truth in `templates/_macros.html`:

```jinja
{% macro breadcrumbs(items) %}
<nav aria-label="breadcrumb" class="mb-2">
  <ol class="breadcrumb small mb-0">
    {% for item in items %}
      {% if loop.last %}
        <li class="breadcrumb-item active">{{ item.label }}</li>
      {% else %}
        <li class="breadcrumb-item">
          <a href="{{ item.url }}">{{ item.label }}</a>
        </li>
      {% endif %}
    {% endfor %}
  </ol>
</nav>
{% endmacro %}
```

Used on every subpage:

```jinja
{{ breadcrumbs([
  {'label': 'Projects', 'url': url_for('ipam.index')},
  {'label': proj.name, 'url': url_for('ipam.project_detail', pid=proj.id)},
  {'label': 'Sites', 'url': url_for('ne.list_sites', pid=proj.id)},
  {'label': site.name}
]) }}
```

Replaces the dozen ad-hoc `← Project Name` snippets. Pages can drop
this in two lines.

#### 3. Real mobile navbar

Fix `templates/base.html`:

- Add a `<button class="navbar-toggler">` with the standard Bootstrap collapse.
- Remove `flex-row` from the inner `.navbar-nav` so it actually collapses.
- Move the search box inside the collapse so it stacks below on mobile.
- Add `aria-current="page"` (and a CSS rule) for active-state highlighting,
  driven by `request.endpoint`.

#### 4. Active-state helper

A tiny context processor in `app.py`:

```python
@app.context_processor
def inject_nav_helpers():
    def is_active(endpoint_prefix):
        return (request.endpoint or '').startswith(endpoint_prefix)
    return {'is_active': is_active}
```

Used in templates:

```jinja
<a class="nav-link {% if is_active('ne.') %}active{% endif %}"
   href="{{ url_for('ne.list_sites', pid=proj.id) }}">Sites</a>
```

#### 5. Project context in `g`

`app.py` registers a `before_request` that detects `pid` in `request.view_args`
and loads the project once:

```python
@app.before_request
def attach_project_context():
    pid = (request.view_args or {}).get('pid')
    g.current_project = get_project(pid) if pid else None
```

Templates can read `g.current_project` without every view passing `proj=`
explicitly (existing `proj=` keeps working too).

### Responsiveness — concrete fixes

| Problem | Fix |
|---|---|
| Navbar doesn't collapse on mobile | Add `navbar-toggler`, remove inner `flex-row`. |
| Search bar steals horizontal space | Move inside the collapse, or behind a 🔍 icon button on `<md`. |
| Project header button row is dense | Already uses `flex-wrap` — keep, but split primary (Add Subnet) from secondary (Labels, Templates) into a `dropdown` on `<md`. |
| Tables overflow on mobile | Already use `table-responsive` — verify everywhere, add to `tests/e2e`. |
| No mobile coverage in e2e | Add a Playwright fixture with `viewport={width: 390, height: 844}` (iPhone-ish) running the smoke flows. |

### Migration phasing

| Phase | Scope | Risk | Unlocks |
|---|---|---|---|
| 1 | Breadcrumb macro + adopt on all subpages. Pure additive. | Very low | Consistent navigation up the tree. |
| 2 | Fix mobile navbar (toggler, collapse, search inside). Add `is_active` helper. | Low | Real mobile usability, active states. |
| 3 | Group global admin links into a single `⚙ Admin ▾` dropdown. | Low | Top bar uncluttered. |
| 4 | Project context bar (`_project_nav.html`) + `g.current_project`. Adopt on project_detail and all `/projects/<pid>/…` pages. | Medium | Replaces "scroll-of-cards" with persistent in-project navigation. |
| 5 | Mobile e2e viewport fixture; smoke flows green at 390px wide. | Low | Prevents regression. |
| 6 | (Optional) Offcanvas drawer for the project nav on mobile. | Low | Polish. |

Phases 1–3 are each a few hours and ship independently. Phase 4 is the
biggest user-visible win and depends on Phase 1 (breadcrumbs replace the
ad-hoc back-links it would otherwise duplicate).

### Rules for new code (effective immediately)

- **Don't add new top-level navbar links.** If it's global config, put it
  in the `⚙ Admin ▾` dropdown once that lands. Until then, flag the
  addition in the PR description for migration.
- **Don't write ad-hoc `← Project Name` headers** on new pages. Use the
  `breadcrumbs()` macro (Phase 1) — create it if it doesn't exist yet.
- **Don't introduce new modals for sub-entity CRUD.** Keep URLs
  navigable; modals are reserved for confirmations and tiny inline edits.
- **Every new template's primary actions must survive at 390px wide.**
  Use `flex-wrap` on button rows, `table-responsive` on tables, and
  push secondary actions into a `dropdown` rather than a long inline row.
- **Every new template must use `is_active(...)`** on the relevant
  navigation link so the user can see where they are.
- **Don't bind navigation to `request.path` string matching.** Use the
  endpoint name via `is_active('ne.')`-style prefixes; routes can change.

### Open questions (resolve before Phase 4)

- **Tabs vs. sidebar vs. horizontal section strip for the project context
  bar.** Horizontal strip is mobile-friendliest and matches the current
  visual density; sidebar gives more room but eats horizontal space on
  desktop and looks worse on mobile. Recommendation: horizontal strip
  with offcanvas on mobile. Decide before building.
- **What counts as "primary" project sections vs. "advanced".** Subnets,
  Sites, PODs, Requirements are clearly primary. Schemas, Templates,
  Validate are arguably config — they could move into a per-project
  `⚙ Settings ▾` dropdown instead of being top-level tabs. Decide which
  goes where.
- **Should the project picker support search?** A `<select>` works fine
  up to ~50 projects; beyond that a typeahead is needed. Probably defer
  until someone has >50 projects.
