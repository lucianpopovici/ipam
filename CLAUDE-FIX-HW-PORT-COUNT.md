# CLAUDE-FIX-HW-PORT-COUNT.md

> Companion to the top-level `CLAUDE.md`. Related docs:
> `CLAUDE-NAME-PATTERNS.md` (the canonical name-expansion convention) and
> `CLAUDE-NE-HW-BINDING.md` (the consumer of expanded ports).
>
> **Scope:** Make the `count` field on hardware-template ports actually
> create multiple distinct ports, with names that follow the same
> `{a..b}` / `{N}` / count-appended convention the NE-Type interface
> builder already uses. Stop the four ad-hoc `for n in range(count)`
> expansions scattered across the codebase from inventing a parallel
> (and broken) naming/identity scheme.
>
> **Status:** Implemented (2026-06-02).

---

## The bug

Today a hardware-template port row carries a `count` field intended to
mean "N identical ports." It is wired through inconsistently:

1. **Half the read sites expand it, half don't.** Expanded:
   `hw.hw_instance_detail`, `ne.api_hw_free_ports`,
   `ne.api_autoresolve_preview`, `rules.materialize_binding` /
   `rules.preview_binding`. **Not** expanded: `hw.api_instance_ports`
   (the cable-form port dropdown), `hw_logic._get_port`,
   `hw_logic._build_tmpl_port_map`, `hw_logic._check_ne_bindings`. So
   the same template renders 48 ports on one page and 1 port on the
   next.
2. **All expanded sub-ports share the same `port_id`.** The four
   call-sites use:
   ```python
   for n in range(count):
       pid_  = port['id']                                # ← same id for all N
       pname = port['name'] if count == 1 else f"{port['name']}-{n}"
   ```
   This makes `port_id` ambiguous in cables, NE bindings, `port_overrides`,
   the `hw:port_bound:{iid}:{port_id}` index, and every validation code
   that keys on it. `tests/unit/test_rule_materializer.py::test_materialize_port_count_expansion`
   explicitly documents this (`# but port_id is the same 'eth0' for all`).
3. **The HW template form has no pattern support.** `templates/hw/template_form.html`
   treats Name as a literal. There's no `{0..7}` range, no `{N}`
   placeholder, no live preview, no server-side validation. This
   contradicts `CLAUDE.md → Name Expansion Patterns`, which mandates
   the same convention for every bulk-create form.
4. **Naming style disagrees with the NE iface builder.** HW expansion
   produces `eth-0, eth-1, …` (hyphen-prefixed, zero-based). The NE
   builder produces `eth0, eth1, …` (index appended directly) — see
   `templates/ne/ne_type_form.html::expandPattern`. The two builders
   are conceptually the same operation; they must agree.

---

## Design

**One change, applied at save time.** The HW template form expands the
port list before it persists to Redis. After save, every stored port row
is a single physical port with a unique `id` and a fully resolved `name`.
The `count` field stops being an input that consumers must re-expand and
becomes purely a UX shortcut in the form.

This removes the four `for n in range(count)` loops, removes the
`port_id` collision, and makes the rest of the system mode-agnostic.

The expansion rules — both client-side in the form and server-side at
save — are exactly the rules from `CLAUDE.md → Name Expansion Patterns`:

| Input (Name, Count)         | Stored ports                                      |
|-----------------------------|---------------------------------------------------|
| `Eth1/{0..47}`, ignored     | `Eth1/0`, `Eth1/1`, …, `Eth1/47`                  |
| `Eth1/{N}`, count=48        | `Eth1/0`, `Eth1/1`, …, `Eth1/47`                  |
| `Eth`, count=4              | `Eth0`, `Eth1`, `Eth2`, `Eth3`                    |
| `iLO`, count=1              | `iLO`                                             |
| `port-{01..10}-bmc`, ignored | `port-01-bmc`, …, `port-10-bmc` (padding kept)   |

Range syntax always wins; when present, count is derived from the range
and the count input is disabled in the UI.

### `port_id` after expansion

Stored ids are derived from the row's pre-expansion id:

- `count == 1` and no range: id stays as-is. Backward compatible.
- Otherwise: ids become `{original_id}-{n}` for `n = 0..N-1`, zero-based,
  no padding. E.g. row id `p1` with count=4 → `p1-0`, `p1-1`, `p1-2`,
  `p1-3`.

The `id` is the stable handle (used by cables, bindings, overrides). The
`name` is human-facing and can differ from the id (e.g. id `p1-3`,
name `Eth1/3`).

### Cap

1024 ports per row, matching the NE iface cap. The cable, rack, and BoM
flows never need more.

### What stays in the form vs what's stored

In the form (`template_form.html`), each row keeps fields:
`{ id, name, port_type, connector, speed_gbps, count, breakout_fan_out, notes }`.

After `prepareSubmit()` runs, the JSON sent to the server contains the
**already-expanded** flat list with `count: 1` on every entry. The
server re-validates and re-expands defensively (clients can be wrong or
malicious) using a shared helper.

---

## Data model

### Stored port — before

```json
{ "id": "p1", "name": "Eth1", "port_type": "data",
  "connector": "SFP28", "speed_gbps": 25, "count": 48,
  "breakout_fan_out": 1, "notes": "" }
```

### Stored port — after

```json
[
  { "id": "p1-0", "name": "Eth1/0", "port_type": "data",
    "connector": "SFP28", "speed_gbps": 25, "count": 1,
    "breakout_fan_out": 1, "notes": "" },
  { "id": "p1-1", "name": "Eth1/1", "port_type": "data", ... },
  ...
  { "id": "p1-47", "name": "Eth1/47", "port_type": "data", ... }
]
```

`count: 1` is kept on each row for schema stability — existing read
sites that look at it still work, they just see 1 everywhere.

---

## Migration

Existing templates have `count > 1` rows on disk, and existing cables /
NE bindings / `port_overrides` reference the pre-expansion `port_id`
(e.g. `'eth0'` for what should have been one of `eth0-0` … `eth0-47`).

In practice the only sub-port that was ever actually addressable through
the broken expansion was index 0 (because all sub-ports collided on the
same `port_id`). So the migration rule is:

- Expand every stored template's `ports[]` using the same helper the
  form uses.
- The first sub-port keeps the original `id`; subsequent sub-ports get
  `{original_id}-{n}` for `n = 1..N-1`. This preserves existing cable
  endpoints and NE bindings without rewriting them.

The migration is a one-shot script under `scripts/`, idempotent, run by
the operator after deploying the new code:

```
scripts/migrate_009_expand_hw_template_ports.py
```

Idempotency check: a template is already migrated iff every port row
has `count == 1` (or no `count` field) and no `{` appears in any name.

---

## File touch list

```
hw_logic.py                            + expand_template_ports(tmpl) → tmpl
                                       + expand_port_pattern(name, count) → (names, error)
                                       (called from save_hw_template; idempotent)

hw.py                                  no functional change; the four read sites
                                       can stop expanding once migrated, but the
                                       loops are harmless on flat ports (count=1)
                                       so leave them for backward safety in v1

ne.py                                  same — leave the two expansion loops in
                                       api_hw_free_ports and api_autoresolve_preview;
                                       they are no-ops on flat ports

rules.py                               same — materialize_binding and preview_binding
                                       loops become no-ops on flat ports

templates/hw/template_form.html        + port the expandPattern() / preview JS from
                                         ne_type_form.html
                                       + live preview line under each port row
                                       + disable Count when {a..b} present
                                       + prepareSubmit expands before serialising

scripts/migrate_009_expand_hw_template_ports.py   NEW

tests/unit/test_hw_helpers.py          + test_expand_port_pattern_*
                                       + test_expand_template_ports_idempotent
                                       + test_save_expands_count
                                       + test_save_expands_range
                                       + test_save_expands_N_placeholder
                                       + test_port_ids_unique_after_expand
tests/api/test_hw_api.py               + test_instance_ports_api_returns_all_subports
                                       + test_cable_can_bind_to_specific_subport
tests/e2e/test_hw_flows.py             + test_template_form_preview_live
                                       + test_template_form_range_disables_count
```

No changes to:

- `hw.api_instance_ports` — once ports are flat, it returns 48 entries
  for free.
- The four `for n in range(count)` loops in `hw.py`, `ne.py`, `rules.py`
  — they iterate exactly once on flat ports, so they're harmless. Remove
  them in a later cleanup pass once migration is verified in prod.
- Validation, `port_overrides`, the `hw:port_bound` index, the cable
  trace path — all already correct for flat ports.

---

## Per-file changes

### 1. `hw_logic.py` — helpers

Add near the top of the file, after the existing `CATEGORIES`,
`PORT_TYPES` etc. constants:

```python
import re as _re

PORT_EXPAND_CAP = 1024


def expand_port_pattern(name: str, count: int) -> tuple[list[str] | None, str | None]:
    """
    Expand a port-name pattern into a list of concrete names.

    Rules (must match templates/ne/ne_type_form.html::expandPattern):
      - 'eth{0..7}'        → eth0, eth1, …, eth7 (range wins, count ignored,
                              zero-padding preserved from the literal digits)
      - 'gi-{N}/0', count=3 → gi-0/0, gi-1/0, gi-2/0
      - 'eth', count=4     → eth0, eth1, eth2, eth3 (no pattern: index appended)
      - 'iLO', count=1     → iLO (no expansion)

    Returns (names, None) on success or (None, error_message) on failure.
    Caps result at PORT_EXPAND_CAP.
    """
    name = (name or '').strip()
    if not name:
        return None, 'Port name is required'

    m = _re.search(r'\{(\d+)\.\.(\d+)\}', name)
    if m:
        start_s, end_s = m.group(1), m.group(2)
        start, end = int(start_s), int(end_s)
        if end < start:
            return None, 'Range end must be >= start'
        if end - start + 1 > PORT_EXPAND_CAP:
            return None, f'Range too large (max {PORT_EXPAND_CAP})'
        width  = len(start_s)
        prefix = name[:m.start()]
        suffix = name[m.end():]
        return [f'{prefix}{str(i).zfill(width)}{suffix}'
                for i in range(start, end + 1)], None

    n = int(count) if count else 1
    if n < 1:
        n = 1
    if n > PORT_EXPAND_CAP:
        return None, f'Count too large (max {PORT_EXPAND_CAP})'
    if '{N}' in name:
        return [name.replace('{N}', str(i)) for i in range(n)], None
    if n == 1:
        return [name], None
    return [f'{name}{i}' for i in range(n)], None


def expand_template_ports(tmpl: dict) -> dict:
    """
    Expand any port rows with count>1 or pattern syntax into individual rows.

    Idempotent: a template whose rows already all have count==1 and no '{' in
    any name is returned unchanged.

    Per-row id rule:
      - count==1 and no range: row passes through.
      - otherwise: first sub-port keeps the original id, sub-ports n>=1 get
        f'{original_id}-{n}'.

    Raises ValueError on invalid patterns so save_hw_template can surface a
    flash error.
    """
    rows = tmpl.get('ports') or []
    out = []
    for row in rows:
        name = (row.get('name') or '').strip()
        count = int(row.get('count') or 1)
        if count <= 1 and '{' not in name:
            out.append({**row, 'count': 1})
            continue

        names, err = expand_port_pattern(name, count)
        if err:
            raise ValueError(f'Port "{name}": {err}')
        orig_id = row.get('id') or ''
        for i, n in enumerate(names):
            sub_id = orig_id if i == 0 else f'{orig_id}-{i}'
            out.append({**row, 'id': sub_id, 'name': n, 'count': 1})
    return {**tmpl, 'ports': out}
```

Then in `save_hw_template`, call the helper:

```python
def save_hw_template(tmpl):
    """Save template; expand port-name patterns + count before persisting."""
    tmpl = expand_template_ports(tmpl)   # NEW
    redis_save(_tmpl_key(tmpl['id']), tmpl)
    r.sadd(HW_TMPL_INDEX, tmpl['id'])
    if tmpl.get('project_id'):
        r.sadd(f'project:{tmpl["project_id"]}:hw:templates', tmpl['id'])
```

### 2. `hw.py` — surface the validation error

In `add_hw_template` and `edit_hw_template`, wrap the `save_hw_template`
call so a `ValueError` from `expand_template_ports` becomes a flash:

```python
try:
    save_hw_template(tmpl)
except ValueError as e:
    flash(str(e), 'danger')
    return redirect(request.url)
```

### 3. `templates/hw/template_form.html` — JS port + preview

Lift `expandPattern` from `ne_type_form.html` verbatim (it already
returns `{ names | error }`) and wire it into the port row renderer.

Key UI additions per row:

- A small preview line under each row showing the first and last
  expanded name plus the total count, e.g. `48 ports: Eth1/0 … Eth1/47`.
- The Count input is disabled (and visually muted) when the Name field
  contains `{a..b}`. Use `oninput` on Name to recompute.
- `prepareSubmit()` is replaced with a version that expands every row
  before writing `portsJson`:

```js
function prepareSubmit() {
  const flat = [];
  for (const p of ports) {
    const r = expandPattern(p.name, p.count);
    if (r.error) { alert(`Port row "${p.name}": ${r.error}`); return false; }
    r.names.forEach((nm, i) => {
      flat.push({
        ...p,
        id:   i === 0 ? p.id : `${p.id}-${i}`,
        name: nm,
        count: 1,
      });
    });
  }
  document.getElementById('portsJson').value = JSON.stringify(flat);
  return true;
}
```

Update the submit button handler to short-circuit on `prepareSubmit()`
returning false (`onclick="return prepareSubmit()"` plus `<form
onsubmit="return prepareSubmit()">` as a belt-and-braces).

Also update the card-footer text:

```
Each port row represents a port group.
- Name supports {0..47} ranges and {N} placeholders.
- Count is used when Name has no range. Disabled when {0..N} is present.
```

### 4. `scripts/migrate_009_expand_hw_template_ports.py`

```python
"""
One-shot migration: expand all HW templates so every port row has count=1
and a fully resolved name. Idempotent — safe to run multiple times.
"""
import sys
from db import r, redis_get, redis_save
from hw_logic import _tmpl_key, expand_template_ports, HW_TMPL_INDEX


def needs_migration(tmpl: dict) -> bool:
    for p in tmpl.get('ports') or []:
        if int(p.get('count') or 1) > 1:
            return True
        if '{' in (p.get('name') or ''):
            return True
    return False


def main() -> int:
    migrated = 0
    failed   = 0
    for tid in r.smembers(HW_TMPL_INDEX):
        tmpl = redis_get(_tmpl_key(tid))
        if not tmpl or not needs_migration(tmpl):
            continue
        try:
            new_tmpl = expand_template_ports(tmpl)
        except ValueError as e:
            print(f'  ✗ {tid} ({tmpl.get("name", "?")}): {e}', file=sys.stderr)
            failed += 1
            continue
        redis_save(_tmpl_key(tid), new_tmpl)
        n_before = len(tmpl.get('ports') or [])
        n_after  = len(new_tmpl.get('ports') or [])
        print(f'  ✓ {tid} ({tmpl.get("name", "?")}): {n_before} → {n_after} ports')
        migrated += 1
    print(f'\nMigrated: {migrated}   Failed: {failed}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
```

Run inside the container:

```
podman exec -it ipam python scripts/migrate_009_expand_hw_template_ports.py
```

---

## Tests

### `tests/unit/test_hw_helpers.py`

```python
from hw_logic import expand_port_pattern, expand_template_ports

class TestExpandPortPattern:
    def test_count_one_no_pattern(self):
        names, err = expand_port_pattern('iLO', 1)
        assert err is None and names == ['iLO']

    def test_count_append(self):
        names, err = expand_port_pattern('eth', 4)
        assert err is None and names == ['eth0', 'eth1', 'eth2', 'eth3']

    def test_N_placeholder(self):
        names, err = expand_port_pattern('gi-{N}/0', 3)
        assert err is None and names == ['gi-0/0', 'gi-1/0', 'gi-2/0']

    def test_range_wins_over_count(self):
        names, err = expand_port_pattern('Eth1/{0..3}', 99)
        assert err is None and names == ['Eth1/0', 'Eth1/1', 'Eth1/2', 'Eth1/3']

    def test_range_preserves_zero_padding(self):
        names, err = expand_port_pattern('p-{01..03}', 1)
        assert err is None and names == ['p-01', 'p-02', 'p-03']

    def test_range_inverted_errors(self):
        names, err = expand_port_pattern('eth{5..1}', 1)
        assert names is None and 'end' in err.lower()

    def test_cap_exceeded(self):
        names, err = expand_port_pattern('eth{0..9999}', 1)
        assert names is None and 'max' in err.lower()


class TestExpandTemplatePorts:
    def _tmpl(self, ports):
        return {'id': 't', 'name': 'T', 'ports': ports}

    def test_idempotent_on_flat(self):
        t = self._tmpl([{'id': 'p1', 'name': 'eth0', 'count': 1}])
        assert expand_template_ports(t)['ports'] == [
            {'id': 'p1', 'name': 'eth0', 'count': 1}
        ]

    def test_count_expansion_keeps_first_id(self):
        t = self._tmpl([{'id': 'p1', 'name': 'eth', 'count': 3}])
        out = expand_template_ports(t)['ports']
        assert [p['id'] for p in out] == ['p1', 'p1-1', 'p1-2']
        assert [p['name'] for p in out] == ['eth0', 'eth1', 'eth2']

    def test_port_ids_unique_after_expand(self):
        t = self._tmpl([{'id': 'p1', 'name': 'Eth1/{0..47}', 'count': 1}])
        out = expand_template_ports(t)['ports']
        ids = [p['id'] for p in out]
        assert len(ids) == 48 and len(set(ids)) == 48

    def test_invalid_pattern_raises(self):
        t = self._tmpl([{'id': 'p1', 'name': 'eth{5..1}', 'count': 1}])
        with pytest.raises(ValueError):
            expand_template_ports(t)
```

### `tests/api/test_hw_api.py`

```python
def test_instance_ports_api_returns_all_subports(self, client):
    """48-port switch must surface 48 entries in the cable-form picker."""
    pid = _create_project(client)
    tmpl = {
        'id': new_id(), 'name': 'ToR', 'category': 'switch',
        'form_factor': '19"', 'u_size': 1, 'cable_type': '', 'description': '',
        'ports': [{'id': 'p1', 'name': 'Eth1/{0..47}', 'port_type': 'data',
                   'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                   'breakout_fan_out': 1, 'notes': ''}],
        'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    inst = _make_instance(pid, tmpl)
    resp  = client.get(f'/api/projects/{pid}/hw/instance-ports/{inst["id"]}')
    ports = json.loads(resp.data)
    assert len(ports) == 48
    assert {p['name'] for p in ports} == {f'Eth1/{i}' for i in range(48)}
    assert len({p['id'] for p in ports}) == 48   # ← regression guard

def test_cable_can_bind_to_specific_subport(self, client):
    """Cables must distinguish between p1-3 and p1-4 on the same template."""
    ...
```

### `tests/e2e/test_hw_flows.py`

Add a check that the template form's preview updates live and that
typing `{0..3}` disables the Count input.

---

## Acceptance criteria

- [ ] Saving a template with one row `Eth1/{0..47}` produces 48 stored
      port rows with unique ids.
- [ ] Saving a template with one row `eth`, count=4 produces 4 stored
      port rows: `eth0, eth1, eth2, eth3` with ids `p1, p1-1, p1-2, p1-3`.
- [ ] The cable form's port dropdown lists every sub-port for a 48-port
      switch.
- [ ] Each sub-port can be bound to a different NE iface without
      collision, and `hw:port_bound:{iid}:{port_id}` reflects exactly
      one binding per `port_id`.
- [ ] `port_overrides` keyed by `port_id` no longer collides across
      sub-ports of the same group.
- [ ] Re-running the migration script is a no-op (idempotent).
- [ ] All existing unit, API, and E2E tests pass unchanged.
- [ ] The HW template form's preview matches the NE-Type interface
      builder's preview wording.

---

## Out of scope (v1)

- Removing the four `for n in range(count)` blocks in `hw.py`, `ne.py`,
  and `rules.py`. They become no-ops on flat ports and stay as a
  belt-and-braces fallback for any template missed by migration. A
  separate cleanup task can delete them once prod data is verified.
- Re-introducing a compact "group" representation for breakout fan-out
  (where one physical port becomes N logical ports). `breakout_fan_out`
  stays on the row, untouched.
- Renaming `port_id` references in existing cables / bindings beyond
  the first-sub-port rule. Operators who need other sub-ports of a
  legacy template can re-bind in the UI after migration.
