## Planned Architecture — Schema Flexibility

> **Status:** Phases 1–4 implemented (2026-06-03): app_config.yaml, core/relations.py, core/derivations.py, core/entity_blueprint.py all present. Phase 5 (generalise compute_requirements) deferred.
> The goal is **declarative entity definitions and links**, not a full
> meta-model engine.

### Design principles (and what we explicitly rejected)

We chose a **narrow declarative design** over a full generic meta-model.

| Rejected approach | Why |
|---|---|
| Fully generic entity engine (zero code to add entities) | Generic UI never fits complex cases (NE interfaces, POD slots). Two code paths emerge anyway. High upfront cost, ongoing tax on every feature, "framework instead of an app" risk. |
| Per-project blueprint (each project picks its type) | Multiplies validation surface, complicates the requirements engine, no current use case. |
| YAML blueprint editable via UI | Adds a "broken blueprint kills the app" failure mode. Files-in-repo + git history is sufficient for the actual editors (network engineers). |

What we **did** commit to:

1. **Configuration over constants.** Hardcoded enums move into YAML.
2. **Generic relation graph.** Replace per-pair `site:X:pods` / `pod:Y:sites`
   storage with a uniform relation store.
3. **Derivation engine.** Fields can be declared as Jinja expressions over
   linked entities (e.g. hostname derived from site name + NE kind + index).
4. **Adding a new entity remains a small code recipe**, not zero code —
   an `EntityBlueprint` base class with ~100 LOC subclasses per new entity.

### What's hardcoded today (to be migrated)

In `ne.py`:

```python
ENTITY_TYPES   = ('site', 'pod', 'ne', 'interface')
NE_KINDS       = ('CNF', 'VNF', 'PNF', 'VM', 'Container')
SHARING_LEVELS = ('project', 'site', 'pod', 'ne', 'interface')
FIELD_TYPES    = ('text', 'number', 'textarea', 'dropdown', 'multi-select', 'checkbox')
```

Plus the implicit hardcoding:

- The hierarchy **Project → Site ↔ POD → NE slot → Interface** baked into
  Redis key helpers (`_site_key`, `_pod_key`, `_proj_sites_key`,
  `_pod_sites_key`, …)
- The site↔POD many-to-many association as a one-off pattern
- `compute_requirements` walking that exact tree with sharing-level dedup keys

### Target structure

#### 1. `config/app_config.yaml`

All the loose enums live here. Loaded once at startup.

```yaml
ne_kinds:       [CNF, VNF, PNF, VM, Container]
field_types:    [text, number, textarea, dropdown, multi-select, checkbox]
sharing_levels: [project, site, pod, ne, interface]
```

#### 2. `config/relations.yaml`

Replaces the per-pair Redis sets. One generic store, declarative graph.

```yaml
relations:
  - { name: pod_of_site,  from: pod, to: site, kind: many-to-many }
  - { name: ne_in_pod,    from: ne,  to: pod,  kind: many-to-one }
  # New relations land here — no Python changes for storage.
```

Storage convention (in `core/relations.py`):

```
rel:{name}:fwd:{a_id}   Set — B ids linked from A
rel:{name}:rev:{b_id}   Set — A ids linked to B
```

API:

```python
relate(rel_name, a_id, b_id)
unrelate(rel_name, a_id, b_id)
related(rel_name, a_id, direction='fwd') -> set
```

#### 3. `config/derivations.yaml`

Declarative computed fields. Evaluated at **read time** with
per-request memoization (no write-time cache — invalidation is not worth it).
Jinja `SandboxedEnvironment` only.

```yaml
ne_instance:
  hostname:
    template: "{{ site.name | lower }}-{{ ne.kind | lower }}-{{ index | pad(2) }}"
    resolve:
      site: "via pod via site"      # walks declared relations
      ne:   "ne_type"
```

**M:N path rule:** when a derivation path traverses a many-to-many relation,
the resolver fans out and produces one result per endpoint (matches how
`compute_requirements` already handles site↔POD). Document this explicitly
in any new derivation.

#### 4. `core/entity_blueprint.py`

Base class for new entity types. Provides CRUD + key helpers; subclass
overrides 2–3 methods. Concrete subclasses live in their own module
and register a blueprint as today.

### Migration phasing

| Phase | Scope | Risk | Unlocks |
|---|---|---|---|
| 1 | Extract enums to `config/app_config.yaml`. No behaviour change. | Low | Cleanup; single source of truth. |
| 2 | Build `core/relations.py`. Migrate `site↔POD` onto it as pilot. Tests get simpler. | Medium | Generic relation store. |
| 3 | Build `core/derivations.py` + `derivations.yaml`. Wire the hostname use case. | Medium | The actual user-facing flexibility. |
| 4 | (Only if needed) `EntityBlueprint` base class. Add a real entity (e.g. Zone) and validate the recipe. | Higher | Faster new-entity onboarding. |
| 5 | (Out of current scope) Generalize `compute_requirements` to walk declared relations. | High | Arbitrary hierarchies. |

Phases 1–3 deliver ~90% of practical value. Phase 5 is deferred until
there's a concrete second hierarchy to support — don't generalise on
speculation.

### Rules for new code (effective immediately)

- **Don't add new hardcoded enums** to `ne.py` or elsewhere. Add to
  `config/app_config.yaml` (create the file if Phase 1 hasn't landed).
- **Don't add new per-pair relation key helpers** (`_foo_bars_key` /
  `_bar_foos_key`). Use `core/relations.py` once it lands; until then,
  flag the addition in the PR description so it can be migrated.
- **Don't write new computed fields inline** in templates or views.
  They belong in `derivations.yaml` once Phase 3 lands.
- **Don't introduce write-time caching** of derived values. Read-time
  with request-scoped memoization is the chosen model.
- **Blueprint files are repo-managed, not UI-editable.** Don't add
  routes that mutate them.

### Open questions (resolve before implementation)

- Naming for the derivation path syntax. `"via pod via site"` is readable
  but parsing is ad-hoc; a structured list (`[pod_of_site, site_self]`) may
  be safer. Decide before writing the resolver.
- Where derivations attach for entities that don't yet exist (e.g.
  `ne_instance` is implicit — POD slots × NE type × index). The hostname
  example assumes an instance-level abstraction; we may need to make
  instances first-class before Phase 3 lands.
