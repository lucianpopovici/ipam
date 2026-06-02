# CLAUDE-FIX-CONNECTOR-MATRIX-TOGGLE.md

> Task file for **Claude Code** (or any agent following step-by-step
> instructions). Single focused bug fix. Not a design document.
>
> **Status:** Implemented (2026-06-02).
>
> **Bug:** On `/admin/hw/connectors`, clicking a compatibility-matrix cell
> only ever enables compatibility (❌ → ✅), never disables it (✅ → ❌).
>
> **Root cause:** `toggleCompat` reads the cell's current state by sniffing
> `element.style.background` for the substring `'d1e7dd'`. Browsers
> normalize inline color values to `rgb()` when read back via JS, so the
> substring never matches and `isCompat` is always `false`. That makes
> `compatVal` always `'1'` (enable).

---

## Files to change

1. `templates/hw/connectors.html` — replace the cell rendering and the
   `toggleCompat` JS function.
2. `hw.py` — add a guard against disabling self-compatibility.
3. `tests/e2e/test_hw_flows.py` — extend `test_toggle_compat_cell` to
   actually verify the round-trip toggle.

Do all three. Do not touch any other file.

---

## Change 1 — `templates/hw/connectors.html`

### 1a. Cell rendering

Find this block inside the matrix `<tbody>` loop:

```html
              <td class="p-0" style="cursor:pointer"
                  onclick="toggleCompat('{{ ca }}','{{ cb }}',this)"
                  title="{{ ca }} ↔ {{ cb }}">
                <div class="d-flex align-items-center justify-content-center"
                     style="height:32px;background:{% if compat %}#d1e7dd{% else %}#f8d7da{% endif %}">
                  {% if compat %}✅{% else %}❌{% endif %}
                </div>
              </td>
```

Replace with:

```html
              <td class="p-0" style="cursor:pointer"
                  data-compat="{{ '1' if compat else '0' }}"
                  onclick="toggleCompat('{{ ca }}','{{ cb }}',this)"
                  title="{{ ca }} ↔ {{ cb }}">
                <div class="d-flex align-items-center justify-content-center"
                     style="height:32px;background:{% if compat %}#d1e7dd{% else %}#f8d7da{% endif %}">
                  {% if compat %}✅{% else %}❌{% endif %}
                </div>
              </td>
```

(Only one line added: `data-compat="{{ '1' if compat else '0' }}"`.)

### 1b. JS function

Find:

```javascript
function toggleCompat(a, b, cell){
  const isCompat = cell.querySelector('div').style.background.includes('d1e7dd');
  document.getElementById('compatA').value   = a;
  document.getElementById('compatB').value   = b;
  document.getElementById('compatVal').value = isCompat ? '0' : '1';
  document.getElementById('compatForm').submit();
}
```

Replace with:

```javascript
function toggleCompat(a, b, cell){
  const isCompat = cell.dataset.compat === '1';
  document.getElementById('compatA').value   = a;
  document.getElementById('compatB').value   = b;
  document.getElementById('compatVal').value = isCompat ? '0' : '1';
  document.getElementById('compatForm').submit();
}
```

(One line changed: state is read from `dataset.compat` instead of sniffing
the computed background colour.)

---

## Change 2 — `hw.py`

In the `hw_connectors()` route, the `'compat'` action currently calls
`set_compat(conn_a, conn_b, val)` unconditionally. Self-compatibility
(`RJ45 ↔ RJ45`) should never be disabled — a connector must always mate
with itself. Add a guard.

Find:

```python
        elif action == 'compat':
            conn_a = request.form.get('conn_a', '')
            conn_b = request.form.get('conn_b', '')
            val = request.form.get('compatible') == '1'
            if conn_a and conn_b:
                set_compat(conn_a, conn_b, val)
                flash(f'Compatibility {conn_a} ↔ {conn_b} updated.', 'success')
```

Replace with:

```python
        elif action == 'compat':
            conn_a = request.form.get('conn_a', '')
            conn_b = request.form.get('conn_b', '')
            val = request.form.get('compatible') == '1'
            if conn_a and conn_b:
                if conn_a == conn_b and not val:
                    flash(f'Cannot disable self-compatibility ({conn_a} ↔ {conn_a}).',
                          'warning')
                else:
                    set_compat(conn_a, conn_b, val)
                    flash(f'Compatibility {conn_a} ↔ {conn_b} updated.', 'success')
```

---

## Change 3 — `tests/e2e/test_hw_flows.py`

The existing `test_toggle_compat_cell` only checks the page reloads
without error. That's why the bug shipped. Extend it to verify a true
round-trip.

Find:

```python
    def test_toggle_compat_cell(self, page_base):
        """Click a cell to toggle compatibility and verify the POST fires."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        # Click the RJ45 ↔ RJ45 cell (should be compatible = ✅)
        # Find the first td in the matrix body and click it
        first_cell = page.locator('table tbody tr:first-child td:nth-child(2)')
        if first_cell.count() > 0:
            first_cell.click()
            # Page should reload (form submit) without error
            expect(page).to_have_url(re.compile(r'/admin/hw/connectors'))
```

Replace with:

```python
    def test_toggle_compat_cell(self, page_base):
        """Click a cell to toggle compatibility off, then on — verify round-trip."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')

        # Pick a non-self cell that is currently compatible.
        # SFP+ ↔ SFP28 are compatible in the default matrix.
        # The matrix has one header row plus one td per connector per row.
        # We look up the cell by its data-compat attribute and the connector
        # pair encoded in the onclick handler.
        cell = page.locator("td[onclick*=\"'SFP+','SFP28'\"]").first
        expect(cell).to_have_attribute('data-compat', '1')

        # Click 1: enabled → disabled
        cell.click()
        expect(page).to_have_url(re.compile(r'/admin/hw/connectors'))
        cell_after = page.locator("td[onclick*=\"'SFP+','SFP28'\"]").first
        expect(cell_after).to_have_attribute('data-compat', '0')

        # Click 2: disabled → enabled (round-trip)
        cell_after.click()
        expect(page).to_have_url(re.compile(r'/admin/hw/connectors'))
        cell_final = page.locator("td[onclick*=\"'SFP+','SFP28'\"]").first
        expect(cell_final).to_have_attribute('data-compat', '1')

    def test_self_compat_cannot_be_disabled(self, page_base):
        """RJ45 ↔ RJ45 (self-compat) must refuse the disable click."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')

        cell = page.locator("td[onclick*=\"'RJ45','RJ45'\"]").first
        expect(cell).to_have_attribute('data-compat', '1')
        cell.click()
        # After the guard fires, the cell should remain compatible
        cell_after = page.locator("td[onclick*=\"'RJ45','RJ45'\"]").first
        expect(cell_after).to_have_attribute('data-compat', '1')
        # And a warning flash should be visible
        expect(page.locator('.alert')).to_contain_text('self-compatibility')
```

---

## Verification

After all three changes, run:

```bash
# Unit + API tests should still pass with zero changes
pytest tests/unit tests/api -x -q

# Specifically run the affected E2E tests
pytest tests/e2e/test_hw_flows.py::TestE2EConnectors -v
```

Manual smoke check:

1. Start the app, go to `/admin/hw/connectors`.
2. Click any green ✅ cell that is not on the diagonal.
3. The page reloads and the cell is now red ❌.
4. Click it again — green ✅ returns.
5. Click an on-diagonal green cell (e.g. `RJ45 ↔ RJ45`).
6. The page reloads, the cell stays green, and a warning flash appears.

---

## Do not

- Refactor the rest of `connectors.html` or `hw_logic.py` — out of scope.
- Change the inline `style="background:..."` to a CSS class — keeps the
  diff minimal; harmless cosmetic, can be a separate PR.
- Touch the `set_compat` helper in `hw_logic.py` — the guard goes in the
  route, not the data layer, so direct callers (tests, scripts) can still
  manage the matrix freely.
- Add new connectors or change the default compat matrix.

---

## Commit message

```
fix(hw): connector compat matrix cell can now be toggled off

The toggleCompat JS read element.style.background and checked for the
substring 'd1e7dd' to decide current state. Browsers normalise inline
colour values to rgb(...) when read via .style.background, so the check
always returned false and clicks could only ever enable compatibility,
never disable it.

Switch to a data-compat attribute as the source of truth. Add a guard
preventing disable of self-compatibility (a connector must always mate
with itself). Extend the e2e test to verify a true round-trip toggle
instead of just confirming the form posts.
```
