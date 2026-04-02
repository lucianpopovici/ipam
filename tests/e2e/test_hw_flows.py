import pytest

pytestmark = pytest.mark.e2e

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
import time
import json
import re
from playwright.sync_api import Page, expect


# ── Shared helpers ─────────────────────────────────────────────────────────────

def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')



def _create_project(page: Page, base: str,
                    name: str = 'HW E2E Project',
                    supernet: str = '10.0.0.0/8') -> str:
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
    def test_connectors_page_loads(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        expect(page).to_have_url(re.compile(r'/admin/hw/connectors'))
        expect(page.locator('body')).to_contain_text('RJ45')
        expect(page.locator('body')).to_contain_text('SFP28')

    def test_default_connectors_visible(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        for conn in ('RJ45', 'SFP+', 'SFP28', 'QSFP28', 'QSFP-DD'):
            expect(page.locator('body')).to_contain_text(conn)

    def test_add_custom_connector(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        page.fill('input[name="name"]', 'CUSTOM-E2E')
        page.click('button[type="submit"]')
        expect(page.locator('body')).to_contain_text('CUSTOM-E2E')

    def test_delete_connector(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        # Add one to delete
        page.fill('input[name="name"]', 'DEL-CONN-E2E')
        page.click('button[type="submit"]')
        expect(page.locator('body')).to_contain_text('DEL-CONN-E2E')
        # Delete it
        page.locator('form').filter(has_text='DEL-CONN-E2E').locator('button').click()
        expect(page.locator('body')).not_to_contain_text('DEL-CONN-E2E')

    def test_compat_matrix_rendered(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/admin/hw/connectors')
        # Matrix table should exist
        matrix = page.locator('table tbody tr')
        assert matrix.count() > 0

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


# ══════════════════════════════════════════════════════════════════════════════
# Hardware template creation UI
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EHWTemplates:
    def test_hw_templates_list_loads(self, page_base):
        page, base = page_base
        goto(page, base, '/hw/templates')
        expect(page).to_have_url(re.compile(r'/hw/templates'))

    def test_templates_list_shows_seeded_templates(self, page_base):
        page, base = page_base
        _make_server_template()
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('E2E-Server-1U')

    def test_add_template_form_loads(self, page_base):
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        expect(page).to_have_url(re.compile(r'/hw/templates/add'))
        expect(page.locator('input[name="name"]')).to_be_visible()

    def test_create_minimal_template(self, page_base):
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
        """Verify clicking '+ Add Port' inserts a port row."""
        page, base = page_base
        _seed_connectors(base)
        goto(page, base, '/hw/templates/add')
        page.click('button:has-text("Add Port")')
        # A new row should appear in the port list
        port_rows = page.locator('#portList .border-bottom')
        assert port_rows.count() >= 1

    def test_port_builder_remove_port(self, page_base):
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
        page, base = page_base
        _seed_connectors(base)
        tmpl = _make_server_template()
        goto(page, base, f'/hw/templates/{tmpl["id"]}/edit')
        page.fill('input[name="name"]', 'Renamed-Server')
        page.evaluate("document.getElementById('portsJson') && (document.getElementById('portsJson').value = JSON.stringify([]))")
        page.click('button[type="submit"]')
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).to_contain_text('Renamed-Server')

    def test_delete_template(self, page_base):
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
        page.locator(f'form[action*="{tmpl["id"]}/delete"] button').click()
        goto(page, base, '/hw/templates')
        expect(page.locator('body')).not_to_contain_text('DELETE-ME-TMPL')

    def test_project_templates_page_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='ProjTmpl E2E')
        goto(page, base, f'/projects/{pid}/hw/templates')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/hw/templates'))


# ══════════════════════════════════════════════════════════════════════════════
# Bill of Materials
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EBOM:
    def test_bom_page_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='BoM E2E')
        goto(page, base, f'/projects/{pid}/bom')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/bom'))

    def test_empty_bom_shows_no_lines(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Empty BoM')
        goto(page, base, f'/projects/{pid}/bom')
        expect(page.locator('#bomList')).to_contain_text('No BoM lines')

    def test_add_bom_line_via_js(self, page_base):
        page, base = page_base
        pid  = _create_project(page, base, name='BoM Add Line')
        tmpl = _make_server_template()
        goto(page, base, f'/projects/{pid}/bom')
        page.click('button:has-text("Add Line")')
        # A row should appear
        rows = page.locator('#bomList .border-bottom')
        assert rows.count() >= 1

    def test_save_bom(self, page_base):
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
        page, base = page_base
        pid  = _create_project(page, base, name='BoM Dropdown')
        tmpl = _make_server_template()
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
    def test_inventory_page_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Inventory E2E')
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page).to_have_url(re.compile(r'inventory'))

    def test_inventory_shows_instances(self, page_base):
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Show')
        tmpl = _make_server_template()
        inst = _make_instance(pid, tmpl)
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text(inst['asset_tag'])

    def test_inventory_category_filter(self, page_base):
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
        page, base = page_base
        pid  = _create_project(page, base, name='Inv Delete')
        tmpl = _make_server_template()
        inst = _make_instance(pid, tmpl)
        goto(page, base, f'/projects/{pid}/hw/inventory')
        expect(page.locator('body')).to_contain_text(inst['asset_tag'])
        page.on('dialog', lambda d: d.accept())
        page.locator(f'form[action*="{inst["id"]}/delete"] button').click()
        expect(page.locator('body')).not_to_contain_text(inst['asset_tag'])

    def test_instance_status_badges(self, page_base):
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
    def _setup_rack(self, page: Page, base: str):
        pid   = _create_project(page, base, name=f'Rack Visual {time.time():.0f}')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        dev   = _make_instance(pid, srv_t)
        return pid, rack, dev

    def test_rack_list_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Rack List E2E')
        goto(page, base, f'/projects/{pid}/hw/racks')
        expect(page).to_have_url(re.compile(r'racks'))

    def test_rack_detail_loads(self, page_base):
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        expect(page).to_have_url(re.compile(rack['id']))
        expect(page.locator('body')).to_contain_text(rack['asset_tag'])

    def test_rack_shows_u_slots(self, page_base):
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        # Should show numbered U slots in the visual table
        u_labels = page.locator('#rackTable td:first-child')
        assert u_labels.count() > 0

    def test_place_device_via_form(self, page_base):
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
        page, base = page_base
        pid, rack, dev = self._setup_rack(page, base)
        from hw import place_in_rack
        place_in_rack(rack['id'], dev['id'], u_pos=5)
        goto(page, base, f'/projects/{pid}/hw/racks/{rack["id"]}')
        expect(page.locator('body')).to_contain_text(dev['asset_tag'])
        expect(page.locator('body')).to_contain_text('U5')

    def test_remove_device_from_rack(self, page_base):
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
        """Verify the drag API endpoint is reachable and responds correctly."""
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
    def test_rack_table_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Rack Table E2E')
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        expect(page).to_have_url(re.compile(r'rack-table'))

    def test_add_placement_row(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Rack Tbl Add')
        goto(page, base, f'/projects/{pid}/hw/rack-table')
        page.click('button:has-text("Add Row")')
        rows = page.locator('#placementBody tr')
        # Should have at least one non-empty row
        assert rows.count() >= 1

    def test_bulk_place_via_js(self, page_base):
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
        page, base = page_base
        pid   = _create_project(page, base, name='Rack Tbl Dropdown')
        rck_t = _make_rack_template()
        srv_t = _make_server_template()
        rack  = _make_instance(pid, rck_t)
        dev   = _make_instance(pid, srv_t)
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
    def _setup(self, page: Page, base: str, project_name: str = 'Cable E2E'):
        pid   = _create_project(page, base, name=project_name)
        srv_t = _make_server_template()
        cab_t = _make_cable_template()
        dev1  = _make_instance(pid, srv_t)
        dev2  = _make_instance(pid, srv_t)
        return pid, dev1, dev2, cab_t

    def test_cable_list_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Cable List E2E')
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page).to_have_url(re.compile(r'cables'))

    def test_cable_list_empty_state(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Cable Empty')
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page.locator('body')).to_contain_text('No cables')

    def test_add_cable_form_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Cable Form')
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        expect(page).to_have_url(re.compile(r'cables/add'))
        expect(page.locator('select[name="template_id"]')).to_be_visible()

    def test_add_cable(self, page_base):
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable Add E2E')
        goto(page, base, f'/projects/{pid}/hw/cables/add')
        page.select_option('select[name="template_id"]', cab_t['id'])
        page.fill('input[name="asset_tag"]', 'E2E-CAB-001')
        page.fill('input[name="label"]',     'e2e test link')
        page.fill('input[name="length_m"]',  '1.5')
        # Select end A device — triggers dynamic port load
        page.select_option('select[name="end_a_instance"]', dev1['id'])
        time.sleep(0.4)   # wait for fetch
        page.select_option('select[name="end_a_port"]', 'sfp0')
        # Select end B device
        page.select_option('select[name="end_b_instance"]', dev2['id'])
        time.sleep(0.4)
        page.select_option('select[name="end_b_port"]', 'sfp0')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'cables'))
        expect(page.locator('body')).to_contain_text('E2E-CAB-001')

    def test_dynamic_port_dropdown_loads(self, page_base):
        """Selecting a device triggers the JS fetch and populates the port dropdown."""
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable Dynamic')
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
        """A port already connected by another cable shows '⚠ in use'."""
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
        page.fill('input[name="asset_tag"]', 'EDIT-AFTER')
        page.fill('input[name="length_m"]',  '3.0')
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/hw/cables')
        expect(page.locator('body')).to_contain_text('EDIT-AFTER')
        expect(page.locator('body')).not_to_contain_text('EDIT-BEFORE')

    def test_delete_cable(self, page_base):
        page, base = page_base
        pid, dev1, dev2, cab_t = self._setup(page, base, 'Cable Delete E2E')
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
        page.locator(f'form[action*="{cable_id}/delete"] button').click()
        expect(page.locator('body')).not_to_contain_text('DELETE-CAB')

    def test_cable_list_shows_issue_badge(self, page_base):
        """A cable with a connector mismatch gets a red badge in the list."""
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
        save_hw_template(srv_t); save_hw_template(sw_t)
        srv  = {'id': new_id(), 'template_id': srv_t['id'], 'project_id': pid,
                'asset_tag': 'SRV-BADGE', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        sw   = {'id': new_id(), 'template_id': sw_t['id'],  'project_id': pid,
                'asset_tag': 'SW-BADGE',  'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(srv); save_hw_instance(sw)
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
    def test_validation_page_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Validation E2E')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page).to_have_url(re.compile(r'validate'))

    def test_clean_project_shows_no_issues(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Clean Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('No issues')

    def test_issue_count_summary_cards(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Issue Cards')
        goto(page, base, f'/projects/{pid}/hw/validate')
        # Summary cards for errors/warnings should exist
        cards = page.locator('.card')
        assert cards.count() >= 3

    def test_connector_mismatch_shown_on_page(self, page_base):
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
        save_hw_template(srv_t); save_hw_template(sw_t)
        srv = {'id': new_id(), 'template_id': srv_t['id'], 'project_id': pid,
               'asset_tag': 'SRV-V', 'serial': '', 'status': 'deployed',
               'location': {}, 'port_overrides': {}}
        sw  = {'id': new_id(), 'template_id': sw_t['id'],  'project_id': pid,
               'asset_tag': 'SW-V',  'serial': '', 'status': 'deployed',
               'location': {}, 'port_overrides': {}}
        save_hw_instance(srv); save_hw_instance(sw)
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
        save_hw_template(ocp_rack_t); save_hw_template(srv_19_t)
        rack = {'id': new_id(), 'template_id': ocp_rack_t['id'], 'project_id': pid,
                'asset_tag': 'OCP-RACK-V', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        srv  = {'id': new_id(), 'template_id': srv_19_t['id'],   'project_id': pid,
                'asset_tag': 'STD-SRV-V',  'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(rack); save_hw_instance(srv)
        save_rack_slots(rack['id'], [{'u_pos': 1, 'instance_id': srv['id']}])
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('body')).to_contain_text('FORM_FACTOR_MISMATCH')

    def test_re_run_validation_button(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Rerun Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        page.click('a:has-text("Re-run"), button:has-text("Re-run")')
        expect(page).to_have_url(re.compile(r'validate'))

    def test_navigation_links_on_validation_page(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Nav Validate')
        goto(page, base, f'/projects/{pid}/hw/validate')
        expect(page.locator('a:has-text("Racks")')).to_be_visible()
        expect(page.locator('a:has-text("Cables")')).to_be_visible()
        expect(page.locator('a:has-text("Inventory")')).to_be_visible()

    def test_validate_link_from_project_detail(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Validate Link')
        goto(page, base, f'/projects/{pid}')
        page.click('a:has-text("Validate")')
        expect(page).to_have_url(re.compile(r'validate'))


# ══════════════════════════════════════════════════════════════════════════════
# Full end-to-end workflow: BoM → instances → rack → cables → validate
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EFullWorkflow:
    def test_complete_hw_provisioning_flow(self, page_base):
        """
        Happy-path end-to-end:
        1. Create project
        2. Save BoM (2 servers + 1 rack)
        3. Generate all instances
        4. Place both servers in the rack
        5. Connect a cable between the servers
        6. Run validation — expect no errors
        """
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
        """Verify all HW navigation links are present on the project detail page."""
        page, base = page_base
        pid = _create_project(page, base, name='Detail Links E2E')
        goto(page, base, f'/projects/{pid}')
        for label in ('BoM', 'Inventory', 'Racks', 'Cables', 'Validate'):
            expect(page.locator(f'a:has-text("{label}")')).to_be_visible()
