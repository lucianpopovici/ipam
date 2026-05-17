"""
End-to-end tests for hardware management flows using Playwright.

Covers:
  - Connector management UI (add, delete, compat matrix toggle)
  - Hardware template creation with port builder
  - Bill of Materials (add lines, save, generate instances)
  - Inventory (add manually, edit, delete, category filter)
  - Rack layout (visual placement, drag-and-drop API, remove device)
  - Bulk rack table placement
  - Cable plant (add cable, dynamic port dropdown, edit, delete)
  - Validation page (clean project, mismatch detection)
"""
import re
import time
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

# ── Shared helpers ─────────────────────────────────────────────────────────────

def goto(page: Page, base: str, path: str):
    """Navigate to a relative path."""
    page.goto(f'{base}{path}')


def _create_project(page: Page, base: str,
                    name: str = 'HW E2E Project',
                    supernet: str = '10.0.0.0/8') -> str:
    """Create a project via UI and return its ID."""
    goto(page, base, '/projects/add')
    page.fill('input[name="name"]', name)
    page.fill('input[name="supernet"]', supernet)
    page.click('button[type="submit"]')
    return page.url.rstrip('/').split('/')[-1]


def _seed_connectors(base: str):
    """Ensure default connectors are seeded (idempotent)."""
    from hw import seed_connectors
    seed_connectors()


def _make_server_template():
    """Create a server template directly and return it."""
    from db import new_id
    from hw import save_hw_template
    tmpl = {
        'id':          new_id(),
        'name':        'E2E-Server-1U',
        'vendor':      'Dell',
        'model':       'R650',
        'category':    'server',
        'form_factor': '19"',
        'u_size':      1,
        'cable_type':  '',
        'description': '',
        'ports': [
            {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
             'connector': 'RJ45', 'speed_gbps': 1, 'count': 4,
             'breakout_fan_out': 1, 'notes': ''},
            {'id': 'sfp0', 'name': 'sfp0', 'port_type': 'data',
             'connector': 'SFP28', 'speed_gbps': 25, 'count': 2,
             'breakout_fan_out': 1, 'notes': ''},
            {'id': 'psu0', 'name': 'psu0', 'port_type': 'power',
             'connector': 'IEC-C14', 'speed_gbps': 0, 'count': 2,
             'breakout_fan_out': 1, 'notes': ''},
        ],
        'scope':      'global',
        'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl


def _make_rack_template():
    """Create a rack template directly and return it."""
    from db import new_id
    from hw import save_hw_template
    tmpl = {
        'id': new_id(), 'name': 'E2E-Rack-42U', 'vendor': 'APC', 'model': 'AR3100',
        'category': 'rack', 'form_factor': '19"', 'u_size': 42,
        'cable_type': '', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl


def _make_cable_template():
    """Create a cable template directly and return it."""
    from db import new_id
    from hw import save_hw_template
    tmpl = {
        'id': new_id(), 'name': 'E2E-DAC-25G', 'vendor': 'Mellanox', 'model': 'MC2609130',
        'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
        'cable_type': 'DAC', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl


def _make_instance(pid: str, tmpl: dict) -> dict:
    """Create a hardware instance directly."""
    from db import new_id
    from hw import save_hw_instance
    inst = {
        'id':           new_id(),
        'template_id':  tmpl['id'],
        'project_id':   pid,
        'asset_tag':    f'{tmpl["name"][:6]}-{new_id()[:4]}',
        'serial':       '',
        'status':       'in-stock',
        'location':     {},
        'port_overrides': {},
    }
    save_hw_instance(inst)
    return inst


# ══════════════════════════════════════════════════════════════════════════════
# Connector management UI
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EConnectors:
    """E2E tests for connector management."""

    def test_connectors_page_loads(self, page_base):
        """Verify connectors page loads."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        expect(page).to_have_url(re.compile(r'/admin/hw/connectors'))
        expect(page.locator('body')).to_contain_text('RJ45')
        expect(page.locator('body')).to_contain_text('SFP28')

    def test_default_connectors_visible(self, page_base):
        """Verify default connectors are shown."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        for conn in ('RJ45', 'SFP+', 'SFP28', 'QSFP28', 'QSFP-DD'):
            expect(page.locator('body')).to_contain_text(conn)

    def test_add_custom_connector(self, page_base):
        """Verify adding a custom connector."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        page.fill('input[name="name"][type="text"]', 'CUSTOM-E2E')
        page.locator('form button:has-text("Add")').click()
        expect(page.locator('body')).to_contain_text('CUSTOM-E2E')

    def test_delete_connector(self, page_base):
        """Verify deleting a connector."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        # Add one to delete
        page.fill('input[name="name"][type="text"]', 'DEL-CONN-E2E')
        page.locator('form button:has-text("Add")').click()
        expect(page.locator('body')).to_contain_text('DEL-CONN-E2E')
        # Delete it
        page.on('dialog', lambda d: d.accept())
        # Target the specific '✕' button for this connector
        page.locator('li', has_text='DEL-CONN-E2E').get_by_role('button', name='✕').click()
        # Wait for it to be removed from the list
        expect(page.locator('li', has_text='DEL-CONN-E2E')).to_have_count(0)
        # Wait for removal from matrix table
        expect(page.locator('table')).not_to_contain_text('DEL-CONN-E2E')

    def test_compat_matrix_rendered(self, page_base):
        """Verify compatibility matrix is rendered."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        # Matrix table should exist
        matrix = page.locator('table tbody tr')
        assert matrix.count() > 0

    def test_toggle_compat_cell(self, page_base):
        """Click a cell to toggle compatibility off, then on — verify round-trip."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')

        # Pick a non-self cell that is currently compatible.
        # SFP+ ↔ SFP28 are compatible in the default matrix.
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


# ══════════════════════════════════════════════════════════════════════════════
# Hardware template creation UI
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EHWTemplates:
    """E2E tests for hardware template management."""

    def test_hw_templates_list_loads(self, page_base):
        """Verify hardware templates list loads."""
        page, base = page_base
        goto(page, base, '/hw/templates')
        expect(page).to_have_url(re.compile(r'/hw/templates'))

    def test_templates_list_shows_seeded_templates(self, page_base):
        """Verify seeded templates are visible."""
        page, base = page_base
        _make_server_template()
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('E2E-Server-1U')

    def test_add_template_form_loads(self, page_base):
        """Verify add template form loads."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        expect(page).to_have_url(re.compile(r'/hw/templates/add'))
        expect(page.locator('input[name="name"]')).to_be_visible()

    def test_create_minimal_template(self, page_base):
        """Verify creating a minimal template."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        page.fill('input[name="name"]', 'Minimal-Switch')
        page.select_option('select[name="category"]', 'switch')
        page.fill('input[name="u_size"]', '1')
        # ports_json hidden field — fill empty array
        page.evaluate("document.getElementById('portsJson') && (document.getElementById('portsJson').value = '[]')")
        page.click('button[type="submit"]')
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('Minimal-Switch')

    def test_port_builder_add_port(self, page_base):
        """Verify port builder '+ Add Port' button."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        page.click('button:has-text("Add Port")')
        # A new row should appear in the port list
        port_rows = page.locator('#portList .border-bottom')
        assert port_rows.count() >= 1

    def test_port_builder_remove_port(self, page_base):
        """Verify port builder remove button."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        page.click('button:has-text("Add Port")')
        page.click('button:has-text("Add Port")')
        before = page.locator('#portList .border-bottom').count()
        page.locator('#portList .btn-outline-danger').first.click()
        after = page.locator('#portList .border-bottom').count()
        assert after == before - 1

    def test_create_template_with_ports(self, page_base):
        """Verify creating a template with ports."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        page.fill('input[name="name"]', 'Ported-Switch')
        page.select_option('select[name="category"]', 'switch')
        page.fill('input[name="u_size"]', '1')
        page.click('button:has-text("Add Port")')
        # Fill the first port row
        page.locator('#portList input').nth(0).fill('eth0')  # name
        # Submit with the JS prepareSubmit
        page.evaluate("""
            () => {
                if(typeof ports !== 'undefined') {
                    ports[0] = {id:'p1', name:'eth0', port_type:'data',
                                connector:'SFP28', speed_gbps:25,
                                count:48, breakout_fan_out:1, notes:''};
                    document.getElementById('portsJson').value = JSON.stringify(ports);
                }
            }
        """)
        page.click('button[type="submit"]')
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('Ported-Switch')

    def test_edit_template(self, page_base):
        """Verify editing a hardware template."""
        page, base = page_base
        _seed_connectors(base)
        tmpl = _make_server_template()
        goto(page, base, f'/hw/templates/{tmpl["id"]}/edit')
        page.fill('input[name="name"]', 'Renamed-Server')
        page.evaluate(
            "document.getElementById('portsJson') && "
            "(document.getElementById('portsJson').value = JSON.stringify([]))"
        )
        page.click('button[type="submit"]')
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('Renamed-Server')

    def test_delete_template(self, page_base):
        """Verify deleting a hardware template."""
        page, base = page_base
        from db import new_id
        from hw import save_hw_template
        tmpl = {
            'id': new_id(), 'name': 'DELETE-ME-TMPL', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('DELETE-ME-TMPL')
        page.on('dialog', lambda d: d.accept())
        page.locator(f'form[action*="{tmpl["id"]}/delete"] button').first.click()
        # Check table instead of body to avoid flash message matching
        if page.locator('table').count() > 0:
            expect(page.locator('table').first).not_to_contain_text('DELETE-ME-TMPL')
        else:
            expect(page.locator('body')).to_contain_text('No hardware templates yet')

    def test_project_templates_page_loads(self, page_base):
        """Verify project hardware templates page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='ProjTmpl E2E')
        goto(page, base, f'/projects/{pid}/hw/templates')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/hw/templates'))


# ══════════════════════════════════════════════════════════════════════════════
# Bill of Materials
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EBOM:
    """E2E tests for Bill of Materials flows."""

    def test_bom_page_loads(self, page_base):
        """Verify BOM page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='BoM E2E')
        goto(page, base, f'/projects/{pid}/bom')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/bom'))

    def test_empty_bom_shows_no_lines(self, page_base):
        """Verify empty BOM message."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty BoM')
        goto(page, base, f'/projects/{pid}/bom')
        expect(page.locator('#bomList')).to_contain_text('No BoM lines')

    def test_add_bom_line_via_js(self, page_base):
        """Verify adding a BOM line."""
        page, base = page_base
        pid  = _create_project(page, base, name='BoM Add Line')
        _make_server_template()
        goto(page, base, f'/projects/{pid}/bom')
        # Use onclick attribute selector to avoid matching the project-picker dropdown
        # (whose button text contains the project name "BoM Add Line")
        page.click('button[onclick="addLine()"]')
        rows = page.locator('#bomList .border-bottom')
        assert rows.count() >= 1

    def test_save_bom(self, page_base):
        """Verify saving a BOM."""
        page, base = page_base
        pid  = _create_project(page, base, name='BoM Save')
        tmpl = _make_server_template()
        goto(page, base, f'/projects/{pid}/bom')
        # Inject a BoM line via JS
        page.evaluate(f"""
            () => {{
                bomData = [{{
                    id: 'test-bom-1',
                    template_id: '{tmpl["id"]}',
                    qty: 5,
                    tag_prefix: 'srv',
                    tag_start: 1,
                    tag_pad: 3,
                    description: 'E2E test'
                }}];
                render();
            }}
        """)
        page.click('button:has-text("Save BoM")')
        # Page should reload showing the saved line
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/bom'))
        # Saved lines should appear in the generate table
        expect(page.locator('body')).to_contain_text('E2E-Server-1U')

    def test_generate_instances_from_line(self, page_base):
        """Verify generating instances from BOM line."""
        page, base = page_base
        from db import new_id
        from hw import save_bom
        pid  = _create_project(page, base, name='BoM Generate')
        tmpl = _make_server_template()
        item_id = new_id()
        save_bom(pid, [{
            'id': item_id, 'template_id': tmpl['id'], 'qty': 3,
            'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': '',
        }])
        goto(page, base, f'/projects/{pid}/bom')
        page.click('button:has-text("Generate 3")')
        # Should redirect to inventory
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/hw/inventory'))
        expect(page.locator('body')).to_contain_text('srv-001')
        expect(page.locator('body')).to_contain_text('srv-003')

    def test_generate_all_instances(self, page_base):
        """Verify generating all instances from BOM."""
        page, base = page_base
        from db import new_id
        from hw import save_bom
        pid   = _create_project(page, base, name='BoM Gen All')
        srv_t = _make_server_template()
        rck_t = _make_rack_template()
        save_bom(pid, [
            {'id': new_id(), 'template_id': srv_t['id'], 'qty': 2,
             'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
            {'id': new_id(), 'template_id': rck_t['id'], 'qty': 1,
             'tag_prefix': 'rack', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
        ])
        goto(page, base, f'/projects/{pid}/bom')
        page.on('dialog', lambda d: d.accept())
        page.click('button:has-text("Generate All")')
        expect(page).to_have_url(re.compile(r'inventory'))
        # 3 total instances
        rows = page.locator('table tbody tr')
        assert rows.count() >= 3

    def test_bom_template_dropdown_populated(self, page_base):
        """Verify template dropdown is populated in BOM editor."""
        page, base = page_base
        pid  = _create_project(page, base, name='BoM Dropdown')
        _make_server_template()
        goto(page, base, f'/projects/{pid}/bom')
        page.click('button:has-text("Add Line")')
        # The template select should contain our template
        options = page.locator('#bomList select option')
        opt_texts = [options.nth(i).inner_text() for i in range(options.count())]
        assert any('E2E-Server-1U' in t for t in opt_texts)


# ══════════════════════════════════════════════════════════════════════════════
# Inventory
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EInventory:
    """E2E tests for inventory management."""

    def test_inventory_page_loads(self, page_base):
        """Verify inventory page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Inventory E2E')
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page).to_have_url(re.compile(r'inventory'))

    def test_inventory_shows_instances(self, page_base):
        """Verify instances are shown in inventory."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Show')
        tmpl = _make_server_template()
        inst = _make_instance(pid, tmpl)
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text(inst['asset_tag'])

    def test_inventory_category_filter(self, page_base):
        """Verify inventory category filter."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Filter')
        srv  = _make_server_template()
        rck  = _make_rack_template()
        _make_instance(pid, srv)
        _make_instance(pid, rck)
        goto(page, base, f'/projects/{pid}/hw/inventory?category=rack')
        expect(page.locator('body')).to_contain_text('E2E-Rack-42U')
        expect(page.locator('body')).not_to_contain_text('E2E-Server-1U')

    def test_add_instance_manually(self, page_base):
        """Verify adding an instance manually via UI."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Add')
        tmpl = _make_server_template()
        goto(page, base, f'/projects/{pid}/hw/instances/add')
        page.select_option('select[name="template_id"]', tmpl['id'])
        page.fill('input[name="asset_tag"]', 'MANUAL-E2E-001')
        page.fill('input[name="serial"]', 'SN-E2E-001')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'inventory'))
        expect(page.locator('body')).to_contain_text('MANUAL-E2E-001')

    def test_edit_instance(self, page_base):
        """Verify editing an instance via UI."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Edit')
        tmpl = _make_server_template()
        inst = _make_instance(pid, tmpl)
        goto(page, base, f'/projects/{pid}/hw/instances/{inst["id"]}/edit')
        page.fill('input[name="asset_tag"]', 'EDITED-E2E-TAG')
        page.select_option('select[name="status"]', 'deployed')
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text('EDITED-E2E-TAG')

    def test_delete_instance(self, page_base):
        """Verify deleting an instance via UI."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Delete')
        tmpl = _make_server_template()
        inst = _make_instance(pid, tmpl)
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text(inst['asset_tag'])
        page.on('dialog', lambda d: d.accept())
        page.locator(f'form[action*="{inst["id"]}/delete"] button').first.click()
        # Check table instead of body to avoid flash message matching
        if page.locator('table').count() > 0:
            expect(page.locator('table').first).not_to_contain_text(inst['asset_tag'])
        else:
            expect(page.locator('body')).to_contain_text('No hardware instances yet')

    def test_instance_status_badges(self, page_base):
        """Verify status badges in inventory list."""
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Status')
        tmpl = _make_server_template()
        from db import new_id
        from hw import save_hw_instance
        for status in ('in-stock', 'deployed', 'spare'):
            save_hw_instance({
                'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
                'asset_tag': f'{status}-e2e', 'serial': '',
                'status': status, 'location': {}, 'port_overrides': {},
            })
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text('in-stock')
        expect(page.locator('body')).to_contain_text('deployed')
        expect(page.locator('body')).to_contain_text('spare')


# ══════════════════════════════════════════════════════════════════════════════
# Rack layout — visual view
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ERackVisual:
    """E2E tests for visual rack layout."""

    def _setup_rack(self, page: Page, base: str):
        """Helper to set up a rack and device."""
        pid   = _create_project(page, base, name=f'Rack Visual {time.time():.0f}')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        dev   = _make_instance(pid, srv_t)
        return pid, rack, dev

    def test_rack_list_loads(self, page_base):
        """Verify rack list page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Rack List E2E')
        goto(page, base, f'/projects/{pid}/hw/racks')
        expect(page).to_have_url(re.compile(r'racks'))

    def test_rack_detail_loads(self, page_base):
        """Verify rack detail page loads."""
        page, base = page_base
        pid, rack, _ = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        expect(page).to_have_url(re.compile(rack['id']))
        expect(page.locator('body')).to_contain_text(rack['asset_tag'])

    def test_rack_shows_u_slots(self, page_base):
        """Verify U slots are shown in rack diagram."""
        page, base = page_base
        pid, rack, _ = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        # Should show numbered U slots in the visual table
        u_labels = page.locator('#rackTable td:first-child')
        assert u_labels.count() > 0

    def test_place_device_via_form(self, page_base):
        """Verify placing a device via the form."""
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        # Use the Place Device form
        page.select_option('select[name="instance_id"]', dev['id'])
        page.fill('input[name="u_pos"]', '10')
        page.click('button:has-text("Place")')
        expect(page).to_have_url(re.compile(rack['id']))
        expect(page.locator('body')).to_contain_text(dev['asset_tag'])

    def test_placed_device_shows_in_placed_table(self, page_base):
        """Verify placed device appears in the placements table."""
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        from hw import place_in_rack
        place_in_rack(rack['id'], dev['id'], u_pos=5)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        expect(page.locator('body')).to_contain_text(dev['asset_tag'])
        # Check that it's in the 'Placed Devices' table with U5 or just 5
        placed_table = page.locator(
            'div.card', has=page.locator('div.card-header:has-text("Placed Devices")')
        ).locator('table')
        expect(placed_table).to_contain_text('5')

    def test_remove_device_from_rack(self, page_base):
        """Verify removing a device from the rack diagram."""
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        from hw import place_in_rack
        place_in_rack(rack['id'], dev['id'], u_pos=3)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        # Click remove button in the rack diagram
        remove_btn = page.locator('form[action*="/remove"] button')
        if remove_btn.count() > 0:
            remove_btn.first.click()
            expect(page).to_have_url(re.compile(rack['id']))
            # Device should no longer be in the placed table
            placed_table = page.locator('table').last
            expect(placed_table).not_to_contain_text(dev['asset_tag'])

    def test_drag_and_drop_api_call(self, page_base):
        """Verify the drag-and-drop placement API."""
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        # Call the drag-and-drop API directly from JS
        result = page.evaluate(f"""
            async () => {{
                const resp = await fetch(
                    '/api/projects/{pid}/hw/racks/{rack["id"]}/place',
                    {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{instance_id: '{dev["id"]}', u_pos: 15}})
                    }}
                );
                return await resp.json();
            }}
        """)
        assert result['ok'] is True

    def test_drag_overlap_returns_error(self, page_base):
        """Verify API returns error on overlapping placement."""
        page, base = page_base
        pid, rack, dev1 = self._setup_rack(page, base)
        srv_t = _make_server_template()
        dev2  = _make_instance(pid, srv_t)
        from hw import place_in_rack
        place_in_rack(rack['id'], dev1['id'], u_pos=1)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        result = page.evaluate(f"""
            async () => {{
                const resp = await fetch(
                    '/api/projects/{pid}/hw/racks/{rack["id"]}/place',
                    {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{instance_id: '{dev2["id"]}', u_pos: 1}})
                    }}
                );
                return await resp.json();
            }}
        """)
        assert result['ok'] is False
        codes = [i['code'] for i in result['issues']]
        assert 'U_OCCUPIED' in codes

    def test_rack_utilization_shown(self, page_base):
        """Verify rack utilization is shown in rack list."""
        page, base = page_base
        pid = _create_project(page, base, name='Rack Util E2E')
        rck_t = _make_rack_template()
        rack  = _make_instance(pid, rck_t)
        goto(page, base, f'/projects/{pid}/hw/racks')
        expect(page.locator('body')).to_contain_text(rack['asset_tag'])
        # utilization % should be present
        expect(page.locator('body')).to_contain_text('%')


# ══════════════════════════════════════════════════════════════════════════════
# Rack layout — bulk table view
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ERackTable:
    """E2E tests for bulk rack table placement."""

    def test_rack_table_loads(self, page_base):
        """Verify rack table page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Rack Table E2E')
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        expect(page).to_have_url(re.compile(r'rack-table'))

    def test_add_placement_row(self, page_base):
        """Verify adding a row to the rack table."""
        page, base = page_base
        pid = _create_project(page, base, name='Rack Tbl Add')
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        page.click('button:has-text("Add Row")')
        rows = page.locator('#placementBody tr')
        # Should have at least one non-empty row
        assert rows.count() >= 1

    def test_bulk_place_via_js(self, page_base):
        """Verify bulk placement via API."""
        page, base = page_base
        pid   = _create_project(page, base, name='Rack Tbl Place')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        dev   = _make_instance(pid, srv_t)
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        # Inject a placement row and submit
        result = page.evaluate(f"""
            async () => {{
                const resp = await fetch(window.location.pathname, {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify([{{
                        rack_id: '{rack["id"]}',
                        instance_id: '{dev["id"]}',
                        u_pos: 7
                    }}])
                }});
                return await resp.json();
            }}
        """)
        assert isinstance(result, list)
        assert result[0]['ok'] is True

    def test_bulk_place_error_shown_in_ui(self, page_base):
        """Verify bulk placement errors are shown."""
        page, base = page_base
        pid   = _create_project(page, base, name='Rack Tbl Err')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        dev   = _make_instance(pid, srv_t)
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        # Inject a row and submit via the submitAll function
        page.evaluate(f"""
            () => {{
                rows = [{{rack_id: '{rack["id"]}', instance_id: '{dev["id"]}', u_pos: 99}}];
                render();
            }}
        """)
        page.click('button:has-text("Apply All")')
        time.sleep(0.5)
        # Error badge should appear
        error_badges = page.locator('.badge.bg-danger')
        assert error_badges.count() >= 1

    def test_rack_and_device_dropdowns_populated(self, page_base):
        """Verify dropdowns are populated in rack table."""
        page, base = page_base
        pid   = _create_project(page, base, name='Rack Tbl Dropdown')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        _make_instance(pid, srv_t)
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        page.click('button:has-text("Add Row")')
        # Rack dropdown should contain our rack
        rack_options = page.locator('#placementBody select').nth(0).locator('option')
        opt_values = [rack_options.nth(i).get_attribute('value')
                      for i in range(rack_options.count())]
        assert rack['id'] in opt_values


# ══════════════════════════════════════════════════════════════════════════════
# Cable plant
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ECablePlant:
    """E2E tests for cable plant management."""

    def _setup(self, page: Page, base: str, project_name: str = 'Cable E2E'):
        """Helper to set up devices and cable template."""
        pid   = _create_project(page, base, name=project_name)
        srv_t = _make_server_template()
        cab_t = _make_cable_template()
        dev1  = _make_instance(pid, srv_t)
        dev2  = _make_instance(pid, srv_t)
        return pid, dev1, dev2, cab_t

    def test_cable_list_loads(self, page_base):
        """Verify cable list page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Cable List E2E')
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page).to_have_url(re.compile(r'cables'))

    def test_cable_list_empty_state(self, page_base):
        """Verify empty cable list message."""
        page, base = page_base
        pid = _create_project(page, base, name='Cable Empty')
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page.locator('body')).to_contain_text('No cables')

    def test_add_cable_form_loads(self, page_base):
        """Verify add cable form loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Cable Form')
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        expect(page).to_have_url(re.compile(r'cables/add'))
        expect(page.locator('select[name="template_id"]')).to_be_visible()

    def test_add_cable(self, page_base):
        """Verify adding a cable via UI."""
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable Add E2E')
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        page.select_option('select[name="template_id"]', cab_t['id'])
        page.fill('input[name="asset_tag"]', 'E2E-CAB-001')
        page.fill('input[name="label"]',     'e2e test link')
        page.fill('input[name="length_m"]',  '1.5')
        # Select end A device — triggers dynamic port load
        page.select_option('select[name="end_a_instance"]', dev1['id'])
        # Wait for port options to be populated (more than just the default empty/select one)
        page.wait_for_selector('select[name="end_a_port"] option:nth-child(2)', state='attached')
        page.select_option('select[name="end_a_port"]', 'sfp0')
        # Select end B device
        page.select_option('select[name="end_b_instance"]', dev2['id'])
        page.wait_for_selector('select[name="end_b_port"] option:nth-child(2)', state='attached')
        page.select_option('select[name="end_b_port"]', 'sfp0')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'cables'))
        expect(page.locator('body')).to_contain_text('E2E-CAB-001')

    def test_dynamic_port_dropdown_loads(self, page_base):
        """Verify port dropdown populates after selecting a device."""
        page, base = page_base
        pid, dev1, _, _ = self._setup(page, base, 'Cable Dynamic')
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        page.select_option('select[name="end_a_instance"]', dev1['id'])
        time.sleep(0.5)
        port_select = page.locator('select[name="end_a_port"]')
        options = port_select.locator('option')
        # Should have at least eth0, sfp0, psu0
        assert options.count() >= 3
        opt_texts = [options.nth(i).inner_text() for i in range(options.count())]
        assert any('eth0' in t for t in opt_texts)
        assert any('sfp0' in t for t in opt_texts)

    def test_port_in_use_marked(self, page_base):
        """Verify 'in use' indicator in port dropdown."""
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable InUse')
        # Create a cable that uses dev1 sfp0
        from db import new_id
        from hw import save_cable
        save_cable({
            'id': new_id(), 'template_id': cab_t['id'], 'project_id': pid,
            'asset_tag': 'EXISTING-CAB', 'label': '', 'length_m': '',
            'end_a': {'instance_id': dev1['id'], 'port_id': 'sfp0'},
            'end_b': {'instance_id': dev2['id'], 'port_id': 'sfp0'},
            'breakout': False, 'breakout_fan_out': 1,
        })
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        page.select_option('select[name="end_a_instance"]', dev1['id'])
        time.sleep(0.5)
        port_options = page.locator('select[name="end_a_port"] option')
        texts = [port_options.nth(i).inner_text() for i in range(port_options.count())]
        assert any('in use' in t for t in texts)

    def test_edit_cable(self, page_base):
        """Verify editing a cable via UI."""
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable Edit E2E')
        from db import new_id
        from hw import save_cable
        cable_id = new_id()
        save_cable({
            'id': cable_id, 'template_id': cab_t['id'], 'project_id': pid,
            'asset_tag': 'EDIT-BEFORE', 'label': '', 'length_m': '1',
            'end_a': {'instance_id': dev1['id'], 'port_id': 'sfp0'},
            'end_b': {'instance_id': dev2['id'], 'port_id': 'sfp0'},
            'breakout': False, 'breakout_fan_out': 1,
        })
        goto(page, base, f'/projects/{pid}/hw/cables/{cable_id}/edit')
        # Wait for port options to be populated before we potentially submit too fast
        page.wait_for_selector('select[name="end_a_port"] option:nth-child(2)', state='attached')
        page.wait_for_selector('select[name="end_b_port"] option:nth-child(2)', state='attached')
        expect(page.locator('select[name="end_a_port"]')).not_to_contain_text('Loading...')
        expect(page.locator('select[name="end_b_port"]')).not_to_contain_text('Loading...')
        # Ensure correct ports are selected
        expect(page.locator('select[name="end_a_port"]')).to_have_value('sfp0')
        expect(page.locator('select[name="end_b_port"]')).to_have_value('sfp0')

        page.fill('input[name="asset_tag"]', 'EDIT-AFTER')
        page.fill('input[name="length_m"]',  '3.0')
        page.click('button:has-text("Update")')
        # Wait for the URL to NOT contain '/edit' anymore
        expect(page).to_have_url(re.compile(r'/hw/cables$'))
        expect(page.locator('body')).to_contain_text('EDIT-AFTER')
        # Check table instead of body to avoid flash message matching
        expect(page.locator('table')).not_to_contain_text('EDIT-BEFORE')

    def test_delete_cable(self, page_base):
        """Verify deleting a cable via UI."""
        page, base = page_base
        pid, *_ = self._setup(page, base, 'Cable Delete E2E')
        from db import new_id
        from hw import save_cable
        cable_id = new_id()
        save_cable({
            'id': cable_id, 'template_id': None, 'project_id': pid,
            'asset_tag': 'DELETE-CAB', 'label': '', 'length_m': '',
            'end_a': {}, 'end_b': {}, 'breakout': False, 'breakout_fan_out': 1,
        })
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page.locator('body')).to_contain_text('DELETE-CAB')
        page.on('dialog', lambda d: d.accept())
        page.locator(f'form[action*="{cable_id}/delete"] button').first.click()
        # Check table instead of body to avoid flash message matching
        if page.locator('table').count() > 0:
            expect(page.locator('table').first).not_to_contain_text('DELETE-CAB')
        else:
            expect(page.locator('body')).to_contain_text('No cables')

    def test_cable_list_shows_issue_badge(self, page_base):
        """Verify issue badge for incompatible cable in list."""
        page, base = page_base
        _seed_connectors(base)
        pid = _create_project(page, base, name='Cable Badge E2E')
        # Server has RJ45 eth0, switch has SFP28 swp0 — incompatible
        from db import new_id
        from hw import save_hw_template, save_hw_instance, save_cable
        srv_t = {
            'id': new_id(), 'name': 'SrvBadge', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                       'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        sw_t = {
            'id': new_id(), 'name': 'SwBadge', 'vendor': '', 'model': '',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'swp0', 'name': 'swp0', 'port_type': 'data',
                       'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(srv_t)
        save_hw_template(sw_t)
        srv  = {'id': new_id(), 'template_id': srv_t['id'], 'project_id': pid,
                'asset_tag': 'SRV-BADGE', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        sw   = {'id': new_id(), 'template_id': sw_t['id'],  'project_id': pid,
                'asset_tag': 'SW-BADGE',  'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(srv)
        save_hw_instance(sw)
        save_cable({
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'MISMATCH-CAB', 'label': '', 'length_m': '',
            'end_a': {'instance_id': srv['id'], 'port_id': 'eth0'},
            'end_b': {'instance_id': sw['id'],  'port_id': 'swp0'},
            'breakout': False, 'breakout_fan_out': 1,
        })
        # Run validation to cache issues
        from hw import validate_project
        validate_project(pid)
        goto(page, base, f'/projects/{pid}/hw/cables')
        # Red badge should appear on the mismatch cable row
        expect(page.locator('body')).to_contain_text('MISMATCH-CAB')
        expect(page.locator('.badge.bg-danger')).to_be_visible()


# ══════════════════════════════════════════════════════════════════════════════
# Validation page
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EValidation:
    """E2E tests for the hardware validation page."""

    def test_validation_page_loads(self, page_base):
        """Verify validation page loads."""
        page, base = page_base
        pid = _create_project(page, base, name='Validation E2E')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page).to_have_url(re.compile(r'validate'))

    def test_clean_project_shows_no_issues(self, page_base):
        """Verify clean project has no issues."""
        page, base = page_base
        pid = _create_project(page, base, name='Clean Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('No issues')

    def test_issue_count_summary_cards(self, page_base):
        """Verify summary cards on validation page."""
        page, base = page_base
        pid = _create_project(page, base, name='Issue Cards')
        goto(page, base, f'/projects/{pid}/hw/validate')
        # Summary cards for errors/warnings should exist
        cards = page.locator('.card')
        assert cards.count() >= 3

    def test_connector_mismatch_shown_on_page(self, page_base):
        """Verify connector mismatch appears on validation page."""
        page, base = page_base
        _seed_connectors(base)
        pid = _create_project(page, base, name='Mismatch Validate')
        from db import new_id
        from hw import save_hw_template, save_hw_instance, save_cable
        srv_t = {
            'id': new_id(), 'name': 'SrvV', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                       'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        sw_t = {
            'id': new_id(), 'name': 'SwV', 'vendor': '', 'model': '',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [{'id': 'swp0', 'name': 'swp0', 'port_type': 'data',
                       'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                       'breakout_fan_out': 1, 'notes': ''}],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(srv_t)
        save_hw_template(sw_t)
        srv = {'id': new_id(), 'template_id': srv_t['id'], 'project_id': pid,
               'asset_tag': 'SRV-V', 'serial': '', 'status': 'deployed',
               'location': {}, 'port_overrides': {}}
        sw  = {'id': new_id(), 'template_id': sw_t['id'],  'project_id': pid,
               'asset_tag': 'SW-V',  'serial': '', 'status': 'deployed',
               'location': {}, 'port_overrides': {}}
        save_hw_instance(srv)
        save_hw_instance(sw)
        save_cable({
            'id': new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'BAD-CAB-V', 'label': '', 'length_m': '',
            'end_a': {'instance_id': srv['id'], 'port_id': 'eth0'},
            'end_b': {'instance_id': sw['id'],  'port_id': 'swp0'},
            'breakout': False, 'breakout_fan_out': 1,
        })
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('CONNECTOR_MISMATCH')
        expect(page.locator('body')).to_contain_text('BAD-CAB-V')

    def test_form_factor_mismatch_shown(self, page_base):
        """Verify form factor mismatch appears on validation page."""
        page, base = page_base
        pid = _create_project(page, base, name='FF Mismatch Validate')
        from db import new_id
        from hw import save_hw_template, save_hw_instance
        from hw_logic import save_rack_slots
        ocp_rack_t = {
            'id': new_id(), 'name': 'OCP-Rack-V', 'vendor': '', 'model': '',
            'category': 'rack', 'form_factor': 'OCP', 'u_size': 42,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        srv_19_t = {
            'id': new_id(), 'name': 'Std-Srv-V', 'vendor': '', 'model': '',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(ocp_rack_t)
        save_hw_template(srv_19_t)
        rack = {'id': new_id(), 'template_id': ocp_rack_t['id'], 'project_id': pid,
                'asset_tag': 'OCP-RACK-V', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        srv  = {'id': new_id(), 'template_id': srv_19_t['id'],   'project_id': pid,
                'asset_tag': 'STD-SRV-V',  'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(rack)
        save_hw_instance(srv)
        save_rack_slots(rack['id'], [{'u_pos': 1, 'instance_id': srv['id']}])
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('FORM_FACTOR_MISMATCH')

    def test_re_run_validation_button(self, page_base):
        """Verify the re-run validation button."""
        page, base = page_base
        pid = _create_project(page, base, name='Rerun Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        page.click('a:has-text("Re-run")')
        expect(page).to_have_url(re.compile(r'validate'))

    def test_navigation_links_on_validation_page(self, page_base):
        """Verify navigation links on validation page."""
        page, base = page_base
        pid = _create_project(page, base, name='Nav Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('a:has-text("Racks")').first).to_be_visible()
        expect(page.locator('a:has-text("Cables")').first).to_be_visible()
        expect(page.locator('a:has-text("Inventory")').first).to_be_visible()

    def test_validate_link_from_project_detail(self, page_base):
        """Verify the validate link from project detail page."""
        page, base = page_base
        pid = _create_project(page, base, name='Validate Link')
        goto(page, base, f'/projects/{pid}')
        # Use the btn-outline-warning link in the HW card (nav-strip tab may be scrolled off)
        page.locator('a.btn-outline-warning:has-text("Validate")').click()
        expect(page).to_have_url(re.compile(r'validate'))


# ══════════════════════════════════════════════════════════════════════════════
# Full end-to-end workflow: BoM → instances → rack → cables → validate
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EFullWorkflow:
    """E2E tests for the complete hardware provisioning workflow."""

    def test_complete_hw_provisioning_flow(self, page_base):
        """Verify the complete BoM to validation workflow."""
        page, base = page_base
        _seed_connectors(base)
        from db import new_id
        from hw import save_bom, save_cable, place_in_rack

        pid   = _create_project(page, base, name='Full Workflow E2E')
        srv_t = _make_server_template()
        rck_t = _make_rack_template()
        cab_t = _make_cable_template()

        # Save BoM
        save_bom(pid, [
            {'id': new_id(), 'template_id': srv_t['id'], 'qty': 2,
             'tag_prefix': 'wf-srv', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
            {'id': new_id(), 'template_id': rck_t['id'], 'qty': 1,
             'tag_prefix': 'wf-rack', 'tag_start': 1, 'tag_pad': 2, 'description': ''},
        ])

        # Generate all instances
        goto(page, base, f'/projects/{pid}/bom')
        page.on('dialog', lambda d: d.accept())
        page.click('button:has-text("Generate All")')
        expect(page).to_have_url(re.compile(r'inventory'))

        # Retrieve instances
        from hw import project_instances
        servers = project_instances(pid, category='server')
        racks   = project_instances(pid, category='rack')
        assert len(servers) == 2
        assert len(racks)   == 1

        # Place servers in rack
        rack = racks[0]
        place_in_rack(rack['id'], servers[0]['id'], u_pos=1)
        place_in_rack(rack['id'], servers[1]['id'], u_pos=2)

        # Connect SFP28 cable between the two servers
        save_cable({
            'id':           new_id(),
            'template_id':  cab_t['id'],
            'project_id':   pid,
            'asset_tag':    'WF-CAB-001',
            'label':        'srv-01 sfp0 → srv-02 sfp0',
            'length_m':     '0.5',
            'end_a':        {'instance_id': servers[0]['id'], 'port_id': 'sfp0'},
            'end_b':        {'instance_id': servers[1]['id'], 'port_id': 'sfp0'},
            'breakout':     False,
            'breakout_fan_out': 1,
        })

        # Run validation — should be clean
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('No issues')

    def test_project_detail_hw_links(self, page_base):
        """Verify hardware navigation links on project detail page."""
        page, base = page_base
        pid = _create_project(page, base, name='Detail Links E2E')
        goto(page, base, f'/projects/{pid}')
        for label in ('BoM', 'Inventory', 'Racks', 'Cables', 'Validate'):
            expect(page.locator(f'a:has-text("{label}")').first).to_be_visible()
