# CLAUDE-FEAT-CABLE-TEMPLATE.md

> Companion to the top-level `CLAUDE.md`. Related docs:
> `CLAUDE-FIX-HW-PORT-COUNT.md` (port-row expansion this builds on),
> `CLAUDE-NE-HW-BINDING.md` (the binding model that will eventually
> auto-create cables from bindings).
>
> **Scope:** Turn cable templates from a thin metadata wrapper into a
> proper SKU. A cable template now declares the connector at each end,
> whether the cable is a breakout assembly, and the patterns used to
> auto-derive `asset_tag` and `label` on cable instances. The cable
> form pre-fills from those patterns and the validator gains two new
> codes.
>
> **Status:** Design — ready to implement.

---

## The gap

Today a cable template carries `cable_type` (`DAC`/`AOC`/`fiber-patch`/…),
vendor, and model. That's enough to unlock `CABLE_PORT_TYPE_MISMATCH` and
the DAC/AOC `SPEED_MISMATCH` checks, but it leaves three things on the
table:

1. **Connectors live on ports, not the cable.** A "QSFP28-to-4×SFP28
   breakout DAC" can't be distinguished from a "QSFP28-to-QSFP28 DAC" at
   the template level. Validation has no way to catch wiring an SFP28
   cable into an RJ45 port via the template's intent — it can only catch
   port-vs-port connector mismatches after the cable is plugged in.
2. **Breakout is an instance-level afterthought.** `cable.breakout` and
   `cable.breakout_fan_out` are set on each cable record, even though a
   "QSFP28 → 4×SFP28" cable is fundamentally a SKU property. Users have
   to re-tick the box on every cable.
3. **Asset tag and label are typed by hand.** Real deployments have
   conventions like `data-compute01` (the data link from compute01) or
   `mgmt-tor04` (mgmt cable on TOR4). Forcing the operator to type
   these means typos and inconsistency. Likewise the label, which
   almost always wants to be a deterministic `host:port ↔ host:port`
   string.

---

## Design

Four additive fields on the cable template, plus two new validation
codes, plus a generation step in the cable form. Existing templates
(no new fields) keep their current behaviour. No migration script is
required.

### New template fields

```json
{
  ...existing cable-template fields...

  "connector_a":          "QSFP28",
  "connector_b":          "SFP28",
  "breakout":             true,
  "breakout_fan_out":     4,
  "asset_tag_pattern":    "{a.port_type}-{a.asset_tag}",
  "label_pattern":        "{a.asset_tag}:{a.port} ↔ {b.asset_tag}:{b.port}"
}
```

All four are **optional**. Their semantics:

- `connector_a` / `connector_b` — connector types from `hw:connectors`.
  Empty string means "any" (current behaviour, no new check).
- `breakout` — boolean, default `false`. When `true`, end A is the
  single-connector side and end B is the fan-out side. `breakout_fan_out`
  is the number of legs on end B (>=2 when `breakout=true`, else 1).
- `asset_tag_pattern` — pattern string evaluated to fill
  `cable.asset_tag` on instance creation. Empty = no auto-fill.
- `label_pattern` — same, for `cable.label`.

### End-A convention

A cable has two ends. For patterns and breakout, **end A is the
"originating" or "single-connector" end**. In practice:

- Server-to-switch DAC: end A = server, end B = switch.
- Breakout: end A = the QSFP-side, end B = each SFP leg.
- Patch cable (symmetric): order doesn't matter; either choice is fine.

The form should hint this in the field labels ("End A — originating
device") and in the help text on the template form ("End A is the
single-connector side for breakout cables").

This is **not enforced in storage** — the user can swap ends freely;
the convention only affects pattern output and breakout interpretation.

### Pattern grammar

A pattern is a string with `{…}` placeholders. Recognised placeholders:

| Placeholder        | Resolves to                                            |
|--------------------|--------------------------------------------------------|
| `{a.asset_tag}`    | end A device asset tag                                 |
| `{a.port}`         | end A port name (resolved through HW template)         |
| `{a.port_type}`    | end A port type (`data`, `mgmt`, `power`, `console`, `usb`) |
| `{a.connector}`    | end A port connector                                   |
| `{b.asset_tag}`    | end B device asset tag                                 |
| `{b.port}`         | end B port name                                        |
| `{b.port_type}`    | end B port type                                        |
| `{b.connector}`    | end B port connector                                   |
| `{cable_type}`     | template's `cable_type`                                |
| `{seq}`            | running counter, zero-padded to 3 digits, scoped per project + pattern; assigned at save time |

Unknown placeholders are left literal (so `{notes}` survives in output).
Missing fields (e.g. end is unconnected) render as the empty string,
not `None`.

Worked examples (template patterns shown next to the rendered output):

| Pattern                                              | Result                                  |
|------------------------------------------------------|-----------------------------------------|
| `{a.port_type}-{a.asset_tag}`                        | `data-compute01`                        |
| `{cable_type}-{a.asset_tag}-{b.asset_tag}-{seq}`     | `DAC-compute01-tor04-001`               |
| `{a.asset_tag}:{a.port} ↔ {b.asset_tag}:{b.port}`    | `compute01:eth0 ↔ tor04:Eth1/3`         |

### When generation runs

Three points, in this order:

1. **In the cable form, live.** When the template is selected and both
   endpoints are picked, render the previewed asset tag and label below
   the manual input fields. The manual fields stay editable; if they
   are empty the preview value is used at submit.
2. **In the cable form, on `[🔄 Regenerate]`.** A small button next to
   each pattern-driven field overwrites the manual value with the
   freshly rendered pattern. Editing an existing cable does not
   regenerate automatically — that would surprise operators who hand-
   edited a value.
3. **Server-side on `add_cable` / `edit_cable`.** Defensive backstop: if
   `asset_tag` or `label` are blank on submit and a template with a
   pattern is selected, fill them in. `{seq}` is resolved server-side
   only, since it depends on what's already in the project.

### `{seq}` mechanics

Counter is per-(project, pattern) so two templates can each have their
own `001…N`. Stored at:

```
project:{pid}:cable_seq:{md5(pattern)}    int
```

On every server-side render with `{seq}` present, `INCR` the key and
zero-pad to 3 digits (`f'{n:03d}'`). The hash of the literal pattern
string is fine for v1 — collisions are harmless (worst case two
templates share a counter), and the key only matters if both end up
in use.

`{seq}` in the live preview is rendered as `???` (deferred to server).

---

## Validation

Two new codes, both extending `_check_cable_port_type`'s neighbour
block in `hw_logic.validate_project`:

| Code                          | Severity | Trigger                                                                |
|-------------------------------|----------|------------------------------------------------------------------------|
| `CABLE_CONNECTOR_MISMATCH`    | error    | Template declares `connector_a` and end A's port `connector` is not in the connector compat set of `connector_a` (or symmetrically for B). |
| `CABLE_BREAKOUT_MISMATCH`     | warning  | `cable.breakout` ≠ `template.breakout`, or `cable.breakout_fan_out` ≠ `template.breakout_fan_out`. Warning because operators may have a legitimate reason to override. |

`CABLE_CONNECTOR_MISMATCH` uses the existing `connectors_compatible`
helper, not strict equality — a `connector_a='SFP28'` template still
mates with an `SFP+` port if the compat matrix allows it. Strict
equality would force operators to add bespoke templates for every
compatible-but-not-identical assembly.

Add the two codes to `templates/hw/validation.html`'s
`code_descriptions` map.

---

## Form behaviour

### `templates/hw/template_form.html` (cable category)

When `category == 'cable'` the existing fields stay, plus a new
"Connectors & Breakout" card:

```
┌─ Connectors & Breakout ──────────────────────────────┐
│ End A connector  [▼ QSFP28 ── ]    (any if blank)   │
│ End B connector  [▼ SFP28 ─── ]    (any if blank)   │
│ ☑ Breakout cable                                     │
│   Fan-out  [4]  legs at end B                        │
│                                                       │
│ ℹ End A is the single-connector side for breakouts. │
└──────────────────────────────────────────────────────┘

┌─ Auto-generation patterns ────────────────────────────┐
│ Asset tag pattern  [{a.port_type}-{a.asset_tag}    ] │
│ Label pattern      [{a.asset_tag}:{a.port} ↔ {b... ] │
│                                                        │
│ Available placeholders:                               │
│   {a.asset_tag}, {a.port}, {a.port_type},            │
│   {a.connector}, {b.asset_tag}, {b.port},            │
│   {b.port_type}, {b.connector}, {cable_type}, {seq}  │
└──────────────────────────────────────────────────────┘
```

The Connectors fields are populated from `all_connectors()` (same as
the port builder). When `breakout` is unchecked, the fan-out input is
disabled and ignored on submit. The two cards are only rendered when
`category === 'cable'` — the existing `onCatChange()` toggle in the
template form handles this.

For non-cable categories, the existing `connector` field on each
**port row** is unchanged. The new `connector_a` / `connector_b` are
template-level fields that only exist for cable templates.

### `templates/hw/cable_form.html`

Five changes:

1. When a template is selected, fetch the template via a new
   `/api/hw/templates/{tid}` endpoint (or pass templates in the
   render context, since they're already enumerated). Populate the
   `breakout` / `breakout_fan_out` inputs from the template; keep
   them editable.
2. Below the Asset Tag field, a small preview line: `Preview: data-compute01`.
   Greyed out if any pattern placeholder couldn't resolve.
3. Same below the Label field.
4. A `🔄` button next to each previewed field that copies preview →
   input.
5. JS reacts to changes on the template select, end_a select, end_a
   port select, end_b select, end_b port select. Each change recomputes
   the preview client-side.

Server-side fallback (in `add_cable` and `edit_cable` route handlers):
if `asset_tag` is blank and the chosen template has `asset_tag_pattern`,
render it and use the result. Same for `label` / `label_pattern`.

---

## Data model — full updated cable template

```json
{
  "id":                "...",
  "name":              "DAC25G Breakout",
  "vendor":            "Mellanox",
  "model":             "MC2609130",
  "category":          "cable",
  "form_factor":       "N/A",
  "u_size":            0,
  "power_w":           0,
  "weight_kg":         0,
  "max_power_w":       0,
  "max_weight_kg":     0,
  "cable_type":        "DAC",
  "description":       "",
  "ports":             [],
  "scope":             "global",
  "project_id":        "",

  "connector_a":       "QSFP28",
  "connector_b":       "SFP28",
  "breakout":          true,
  "breakout_fan_out":  4,
  "asset_tag_pattern": "{cable_type}-{a.asset_tag}-{seq}",
  "label_pattern":     "{a.asset_tag}:{a.port} ↔ {b.asset_tag}:{b.port}"
}
```

`ports` stays empty for cable templates (unchanged).

---

## File touch list

```
hw_logic.py                            + render_pattern(pattern, ctx) → str
                                       + cable_pattern_context(cable, pid) → dict
                                       + next_cable_seq(pid, pattern) → str
                                       + _check_cable_template_constraints(cable, tmpl, issues)
                                       (called from validate_project)

hw.py                                  + /api/hw/templates/<tid>  (JSON, for cable form JS)
                                       + add_cable / edit_cable: server-side pattern fallback

templates/hw/template_form.html        + connectors + breakout card (cable category only)
                                       + asset-tag / label pattern inputs (cable category only)
                                       + placeholder cheat-sheet below the pattern inputs

templates/hw/cable_form.html           + template-driven prefill of breakout fields
                                       + live preview lines + 🔄 buttons
                                       + JS that recomputes preview on change

templates/hw/validation.html           + 2 new code_descriptions entries

tests/unit/test_hw_helpers.py          + test_render_pattern_* (all placeholders, missing
                                         fields, unknown placeholder, {seq} server-side)
                                       + test_cable_connector_mismatch_uses_compat_matrix
                                       + test_cable_breakout_mismatch_warning
tests/api/test_hw_api.py               + test_cable_form_uses_template_patterns
                                       + test_api_hw_template_returns_cable_fields
                                       + test_seq_increments_per_project_per_pattern
tests/e2e/test_hw_flows.py             + test_cable_form_preview_live_updates
                                       + test_regenerate_button_overwrites_field
```

No changes to `save_hw_template` itself — the new fields are passed
through Redis as-is.

---

## Per-file changes — key snippets

### `hw_logic.py` — pattern rendering

```python
import hashlib
import re as _re

# Allowed placeholder dotted-paths in patterns
_PATTERN_PLACEHOLDER = _re.compile(
    r'\{(a\.asset_tag|a\.port|a\.port_type|a\.connector'
    r'|b\.asset_tag|b\.port|b\.port_type|b\.connector'
    r'|cable_type|seq)\}'
)


def render_pattern(pattern: str, ctx: dict) -> str:
    """
    Render a cable pattern using ctx.

    Unknown placeholders pass through literally so user notes like
    {tenant} survive. Missing values render as empty strings.

    ctx shape:
        { 'a': {'asset_tag', 'port', 'port_type', 'connector'},
          'b': {'asset_tag', 'port', 'port_type', 'connector'},
          'cable_type': str,
          'seq':        str | None,   # already zero-padded; None defers
        }
    """
    if not pattern:
        return ''

    def sub(m):
        key = m.group(1)
        if '.' in key:
            side, attr = key.split('.', 1)
            return str(ctx.get(side, {}).get(attr, '') or '')
        if key == 'seq':
            return ctx.get('seq') or ''
        return str(ctx.get(key, '') or '')

    return _PATTERN_PLACEHOLDER.sub(sub, pattern)


def cable_pattern_context(cable: dict, *, defer_seq: bool = False) -> dict:
    """Build a render-context dict for a cable's endpoints."""
    def side(end: dict) -> dict:
        iid = end.get('instance_id') or ''
        pid = end.get('port_id') or ''
        inst = get_hw_instance(iid) if iid else None
        port = _get_port(iid, pid) if iid and pid else None
        return {
            'asset_tag': inst.get('asset_tag', '') if inst else '',
            'port':      port.get('name', '') if port else '',
            'port_type': port.get('port_type', '') if port else '',
            'connector': port.get('connector', '') if port else '',
        }

    tmpl = get_hw_template(cable.get('template_id')) if cable.get('template_id') else None
    return {
        'a':          side(cable.get('end_a', {})),
        'b':          side(cable.get('end_b', {})),
        'cable_type': tmpl.get('cable_type', '') if tmpl else '',
        'seq':        None if defer_seq else '',   # resolved by next_cable_seq
    }


def next_cable_seq(pid: str, pattern: str) -> str:
    """Atomic per-(project, pattern) counter. Returns 3-digit zero-padded."""
    h = hashlib.md5(pattern.encode()).hexdigest()[:8]
    n = r.incr(f'project:{pid}:cable_seq:{h}')
    return f'{n:03d}'
```

### `hw_logic.py` — validation additions

In `validate_project`, inside the per-cable loop, after the existing
`_check_cable_port_type` call:

```python
if tmpl:
    _check_cable_template_constraints(cable, tmpl, port_a, port_b, issues)
```

```python
def _check_cable_template_constraints(cable, tmpl, port_a, port_b, issues):
    """Connector and breakout checks against the cable template."""
    expect_a = tmpl.get('connector_a', '')
    expect_b = tmpl.get('connector_b', '')
    actual_a = port_a.get('connector', '')
    actual_b = port_b.get('connector', '')

    if expect_a and actual_a and not connectors_compatible(expect_a, actual_a):
        issues.append(_issue('error', 'CABLE_CONNECTOR_MISMATCH',
            f'Cable {cable["asset_tag"]}: template expects {expect_a} at end A '
            f'but port is {actual_a}',
            {'cable': cable['id']}))
    if expect_b and actual_b and not connectors_compatible(expect_b, actual_b):
        issues.append(_issue('error', 'CABLE_CONNECTOR_MISMATCH',
            f'Cable {cable["asset_tag"]}: template expects {expect_b} at end B '
            f'but port is {actual_b}',
            {'cable': cable['id']}))

    t_brk = bool(tmpl.get('breakout'))
    c_brk = bool(cable.get('breakout'))
    if t_brk != c_brk:
        issues.append(_issue('warning', 'CABLE_BREAKOUT_MISMATCH',
            f'Cable {cable["asset_tag"]}: template breakout={t_brk} '
            f'but cable breakout={c_brk}',
            {'cable': cable['id']}))
    elif t_brk:
        t_fan = int(tmpl.get('breakout_fan_out') or 1)
        c_fan = int(cable.get('breakout_fan_out') or 1)
        if t_fan != c_fan:
            issues.append(_issue('warning', 'CABLE_BREAKOUT_MISMATCH',
                f'Cable {cable["asset_tag"]}: template fan-out={t_fan} '
                f'but cable fan-out={c_fan}',
                {'cable': cable['id']}))
```

### `hw.py` — server-side fallback in `add_cable` / `edit_cable`

Just before `save_cable(cable)`:

```python
tmpl = get_hw_template(cable.get('template_id')) if cable.get('template_id') else None
if tmpl:
    if not cable.get('asset_tag') and tmpl.get('asset_tag_pattern'):
        ctx = cable_pattern_context(cable, defer_seq=True)
        if '{seq}' in tmpl['asset_tag_pattern']:
            ctx['seq'] = next_cable_seq(pid, tmpl['asset_tag_pattern'])
        cable['asset_tag'] = render_pattern(tmpl['asset_tag_pattern'], ctx)
    if not cable.get('label') and tmpl.get('label_pattern'):
        ctx = cable_pattern_context(cable, defer_seq=True)
        if '{seq}' in tmpl['label_pattern']:
            ctx['seq'] = next_cable_seq(pid, tmpl['label_pattern'])
        cable['label'] = render_pattern(tmpl['label_pattern'], ctx)
```

### `hw.py` — new template API endpoint

```python
@hw_bp.route('/api/hw/templates/<tid>')
def api_hw_template(tid):
    """Return a single HW template as JSON (used by the cable form)."""
    tmpl = get_hw_template(tid)
    if not tmpl:
        return jsonify({'error': 'not found'}), 404
    return jsonify(tmpl)
```

### `templates/hw/cable_form.html` — JS pattern preview

```js
const TEMPLATES_BY_ID = {};  // populated lazily via /api/hw/templates/<tid>

async function getTemplate(tid) {
  if (!tid) return null;
  if (!TEMPLATES_BY_ID[tid]) {
    const resp = await fetch(`/api/hw/templates/${tid}`);
    if (!resp.ok) return null;
    TEMPLATES_BY_ID[tid] = await resp.json();
  }
  return TEMPLATES_BY_ID[tid];
}

async function refreshPreviews() {
  const tid = document.getElementById('cableTmplSelect').value;
  const tmpl = await getTemplate(tid);
  if (!tmpl) { setPreview('asset_tag_preview', ''); setPreview('label_preview', ''); return; }

  // Apply breakout defaults if instance fields are blank
  if (tmpl.breakout && !document.getElementById('breakoutCb').dataset.touched) {
    document.getElementById('breakoutCb').checked = true;
    document.getElementById('fanOutInput').value  = tmpl.breakout_fan_out || 1;
  }

  const ctx = await buildContext();  // reads end-a/end-b selects, calls instance-ports API
  setPreview('asset_tag_preview', render(tmpl.asset_tag_pattern, ctx));
  setPreview('label_preview',     render(tmpl.label_pattern,     ctx));
}

function render(pattern, ctx) {
  if (!pattern) return '';
  return pattern.replace(/\{(a|b)\.(asset_tag|port|port_type|connector)\}/g,
                         (_, side, attr) => (ctx[side] && ctx[side][attr]) || '')
                .replace(/\{cable_type\}/g, ctx.cable_type || '')
                .replace(/\{seq\}/g, '???');   // server resolves
}
```

Wire `refreshPreviews()` to the `onchange` of: template select, end_a
instance select, end_a port select, end_b instance select, end_b port
select. The 🔄 buttons just copy the preview text into the input.

Mark the breakout checkbox `dataset.touched = '1'` on user `change` so
re-selecting the template doesn't clobber a manual override.

---

## Tests

### Unit — `tests/unit/test_hw_helpers.py`

```python
class TestRenderPattern:
    def test_all_placeholders(self):
        ctx = {'a': {'asset_tag': 'compute01', 'port': 'eth0',
                     'port_type': 'data', 'connector': 'SFP28'},
               'b': {'asset_tag': 'tor04', 'port': 'Eth1/3',
                     'port_type': 'data', 'connector': 'SFP28'},
               'cable_type': 'DAC', 'seq': '042'}
        out = render_pattern(
            '{cable_type}-{a.asset_tag}:{a.port} -> {b.asset_tag}:{b.port} #{seq}', ctx)
        assert out == 'DAC-compute01:eth0 -> tor04:Eth1/3 #042'

    def test_missing_field_renders_empty(self):
        ctx = {'a': {}, 'b': {}, 'cable_type': '', 'seq': ''}
        assert render_pattern('{a.asset_tag}-{b.port}', ctx) == '-'

    def test_unknown_placeholder_passes_through(self):
        ctx = {'a': {'asset_tag': 'x'}}
        assert render_pattern('{a.asset_tag}-{tenant}', ctx) == 'x-{tenant}'


class TestNextCableSeq:
    def test_seq_increments(self, fake_redis):
        s1 = next_cable_seq('p1', '{a.asset_tag}-{seq}')
        s2 = next_cable_seq('p1', '{a.asset_tag}-{seq}')
        assert (s1, s2) == ('001', '002')

    def test_seq_isolated_per_project(self, fake_redis):
        next_cable_seq('p1', 'pat')
        next_cable_seq('p1', 'pat')
        s = next_cable_seq('p2', 'pat')
        assert s == '001'

    def test_seq_isolated_per_pattern(self, fake_redis):
        next_cable_seq('p1', 'pat-A')
        s = next_cable_seq('p1', 'pat-B')
        assert s == '001'


class TestCableTemplateValidation:
    def test_connector_mismatch_via_template_emits_error(self):
        # template says end A is QSFP28; port at end A is RJ45
        ...
        assert any(i['code'] == 'CABLE_CONNECTOR_MISMATCH' for i in issues)

    def test_connector_compatible_via_matrix_no_error(self):
        # template says SFP28, port is SFP+ → matrix says OK
        ...
        assert not any(i['code'] == 'CABLE_CONNECTOR_MISMATCH' for i in issues)

    def test_breakout_mismatch_warning(self):
        # template breakout=true, cable breakout=false
        ...
        assert any(i['code'] == 'CABLE_BREAKOUT_MISMATCH'
                   and i['severity'] == 'warning' for i in issues)
```

### API — `tests/api/test_hw_api.py`

```python
def test_cable_form_uses_template_patterns(self, client):
    """Submitting a cable with blank asset_tag pulls it from the template pattern."""
    pid = _create_project(client)
    srv_t = _server_tmpl(); save_hw_template(srv_t)
    sw_t  = _switch_tmpl(); save_hw_template(sw_t)
    cab_t = {**_cable_tmpl(),
             'asset_tag_pattern': '{a.port_type}-{a.asset_tag}',
             'label_pattern':     '{a.asset_tag}:{a.port} ↔ {b.asset_tag}:{b.port}'}
    save_hw_template(cab_t)
    srv = _make_instance(pid, srv_t)
    sw  = _make_instance(pid, sw_t)
    resp = client.post(f'/projects/{pid}/hw/cables/add', data={
        'template_id':     cab_t['id'],
        'asset_tag':       '',   # blank — should be filled
        'label':           '',   # blank — should be filled
        'length_m':        '1',
        'end_a_instance':  srv['id'], 'end_a_port': 'sfp0',
        'end_b_instance':  sw['id'],  'end_b_port': 'swp0',
    }, follow_redirects=False)
    assert resp.status_code == 302
    cables = project_cables(pid)
    assert any(c['asset_tag'] == f'data-{srv["asset_tag"]}' for c in cables)
    assert any('↔' in c['label'] for c in cables)


def test_api_hw_template_returns_cable_fields(self, client):
    cab_t = {**_cable_tmpl(),
             'connector_a': 'QSFP28', 'connector_b': 'SFP28',
             'breakout': True, 'breakout_fan_out': 4,
             'asset_tag_pattern': '{cable_type}-{seq}',
             'label_pattern':     ''}
    save_hw_template(cab_t)
    resp = client.get(f'/api/hw/templates/{cab_t["id"]}')
    body = json.loads(resp.data)
    assert body['connector_a'] == 'QSFP28'
    assert body['breakout'] is True
    assert body['breakout_fan_out'] == 4
```

### E2E

Cover: type a pattern in the template form, save, then add a cable
and verify the preview line updates as you change endpoints. Click 🔄
and verify the input is filled.

---

## Backward compatibility

- **Templates without the new fields** keep working. `tmpl.get('connector_a', '')`
  returns `''` → no `CABLE_CONNECTOR_MISMATCH` check fires. Empty patterns →
  no auto-fill. Empty `breakout` → falsey → no `CABLE_BREAKOUT_MISMATCH`.
- **Existing cables** keep their hand-typed `asset_tag` and `label`. The
  server-side fallback only fills *blank* fields, never overwrites.
- **No migration script** required.

---

## Open questions

1. **End-A convention.** This doc assumes end A is the originating /
   single-connector side. If your convention is different (e.g. end A
   is always the switch / fan-out side), the doc still works — just
   flip the placeholder names mentally. Worth a one-line note in
   `CLAUDE.md`'s Hardware Conventions section once we settle on one.

2. **Multi-leg breakout cables.** The current cable record stores one
   `end_a` and one `end_b`. A QSFP28 → 4×SFP28 cable today is
   represented as 4 separate cable records, each sharing the same
   parent QSFP28 sub-port (via `breakout_fan_out` on the port template).
   The `breakout` flag on the cable record is purely advisory in that
   model. This doc treats it the same way; we're not redesigning
   breakout topology here. If you eventually want one cable record
   with `ends_b[]: [...]`, that's a separate feature.

3. **Pattern errors.** If a pattern references `{c.asset_tag}` (typo:
   neither `a` nor `b`), `_PATTERN_PLACEHOLDER` won't match and the
   literal `{c.asset_tag}` survives. Question: should the template-save
   route validate patterns against the allowed-placeholder list and
   reject typos? Recommendation: yes, with a warning rather than a hard
   error — operators sometimes use literal `{notes}` etc. as documentation.

4. **Seq scope.** Per-(project, pattern) is one option; per-template
   would be another (i.e., counters scoped to `tid`). Pattern-scoped
   means changing the pattern resets the counter. Template-scoped means
   editing the pattern keeps the counter. The latter is probably more
   intuitive in practice; switch to it if `{seq}` ergonomics turn out
   annoying.

5. **`{seq}` rendered as `???` in live preview.** Acceptable for v1.
   A nicer touch is to show `next` as a tooltip via a `/api/projects/{pid}/cable-seq-peek?pattern=…`
   endpoint (without incrementing). Defer.

---

## Acceptance criteria

- [ ] Cable templates accept `connector_a`, `connector_b`, `breakout`,
      `breakout_fan_out`, `asset_tag_pattern`, `label_pattern`.
- [ ] The template form shows the connector / breakout / patterns cards
      only when `category == 'cable'`.
- [ ] The cable form previews asset tag and label live as endpoints
      change.
- [ ] The 🔄 buttons overwrite the manual input with the rendered
      pattern.
- [ ] Submitting a cable with blank `asset_tag` / `label` and a
      template with patterns fills them server-side.
- [ ] `{seq}` zero-pads to 3 digits and increments atomically per
      project per pattern.
- [ ] `CABLE_CONNECTOR_MISMATCH` fires when the template's connector
      doesn't match the port's, using the compat matrix (not strict
      equality).
- [ ] `CABLE_BREAKOUT_MISMATCH` fires when template and cable disagree
      on breakout or fan-out.
- [ ] Templates without the new fields behave exactly as before — no
      new errors or warnings on existing data.
- [ ] All existing unit, API, and E2E tests pass unchanged.
