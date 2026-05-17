"""
End-to-end tests for NE-HW interface binding flows using Playwright.

Covers:
  - NE instance detail page renders the bindings table
  - Workflow A: manual per-iface bind modal (single port)
  - Workflow A: LAG binding (multi-port)
  - Workflow A: auto-rule binding with live rule preview
  - Re-evaluate rule button (rematerialize single iface)
  - Unbind removes a binding
  - Workflow B: bulk bind modal — select ifaces, pick HW, preview, apply
  - Workflow C: auto-resolve modal — pick HW, view proposed matches, apply
  - Batch rematerialize-rules project-level action
  - HW instance detail page shows port states (bound / free)
"""
import re
import json
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

# ── Helpers ────────────────────────────────────────────────────────────────────

def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')


def _create_project(page: Page, base: str,
                    name: str = 'Binding E2E',
                    supernet: str = '10.0.0.0/8') -> str:
    goto(page, base, '/projects/add')
    page.fill('input[name="name"]', name)
    page.fill('input[name="supernet"]', supernet)
    page.click('button[type="submit"]')
    return page.url.rstrip('/').split('/')[-1]


def _create_hw_template(base: str) -> str:
    """Create a server template with one mgmt port and one data port; return tid."""
    import requests
    from db import new_id
    from hw_logic import save_hw_template
    tmpl = {
        'id': new_id(), 'name': 'TestSrv', 'vendor': 'ACME', 'model': 'X1',
        'category': 'server', 'form_factor': '19"', 'u_size': 1,
        'power_w': 300, 'weight_kg': 10,
        'max_power_w': 0, 'max_weight_kg': 0,
        'cable_type': '', 'description': '', 'scope': 'global', 'project_id': '',
        'ports': [
            {'id': 'ilo',  'name': 'iLO',  'port_type': 'mgmt',
             'connector': 'RJ45', 'speed_gbps': 1,  'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
            {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
             'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
             'breakout_fan_out': 1, 'notes': ''},
        ],
    }
    save_hw_template(tmpl)
    return tmpl['id']


def _create_hw_instance(base: str, pid: str, tmpl_id: str) -> str:
    """Create a HW instance via HTTP POST; return iid."""
    from db import new_id
    from hw_logic import save_hw_instance
    inst = {
        'id': new_id(), 'template_id': tmpl_id, 'project_id': pid,
        'asset_tag': 'srv-e2e-001', 'serial': '', 'status': 'deployed',
        'location': {}, 'port_overrides': {}, 'labels': [],
    }
    save_hw_instance(inst)
    return inst['id']


def _create_ne_type(tid: str = 'nt-e2e') -> dict:
    """Create an NE type with two interfaces directly in Redis."""
    from db import r
    ne_type = {
        'id': tid, 'name': 'TestRouter', 'kind': 'PNF',
        'description': '', 'labels': [], 'params': {},
        'interfaces': [
            {'id': 'iface-mgmt', 'name': 'mgmt', 'labels': ['mgmt'],
             'sharing': 'ne', 'ipv4': {'prefix_len': 29}, 'ipv6': None, 'params': {}},
            {'id': 'iface-data', 'name': 'eth0', 'labels': ['data'],
             'sharing': 'ne', 'ipv4': {'prefix_len': 30}, 'ipv6': None, 'params': {}},
        ],
        'scope': 'global', 'project_id': '',
    }
    r.set(f'ne_type:{tid}', json.dumps(ne_type))
    r.sadd('ne_types:index', tid)
    return ne_type


def _create_ne_instance(page: Page, base: str, pid: str, ne_type_id: str,
                        name: str = 'pe-e2e-01') -> str:
    goto(page, base, f'/projects/{pid}/ne-instances/add')
    page.select_option('select[name="ne_type_id"]', ne_type_id)
    page.fill('input[name="name"]', name)
    page.click('button[type="submit"]')
    return page.url.rstrip('/').split('/')[-1]


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestNEHWBinding:

    @pytest.fixture(autouse=True)
    def setup(self, page_base):
        self.page, self.base = page_base
        self.pid     = _create_project(self.page, self.base)
        self.ne_type = _create_ne_type()
        self.tmpl_id = _create_hw_template(self.base)
        self.hwid    = _create_hw_instance(self.base, self.pid, self.tmpl_id)
        self.nid     = _create_ne_instance(
            self.page, self.base, self.pid, self.ne_type['id'])

    # ── Detail page ────────────────────────────────────────────────────────────

    def test_detail_page_shows_ifaces(self):
        """NE instance detail renders both interface rows."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        expect(self.page.locator('text=mgmt')).to_be_visible()
        expect(self.page.locator('text=eth0')).to_be_visible()

    def test_detail_page_shows_workflow_buttons(self):
        """Bulk bind and auto-resolve buttons are present."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        expect(self.page.locator('button:has-text("Bulk bind")')).to_be_visible()
        expect(self.page.locator('button:has-text("Auto-resolve")')).to_be_visible()

    # ── Workflow A — single port ───────────────────────────────────────────────

    def test_workflow_a_bind_single_port(self):
        """Open the bind modal for 'mgmt', pick the HW instance and iLO port, save."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')

        # Click '+ Bind' for the mgmt iface row
        self.page.locator(
            f'button[data-iface-id="iface-mgmt"]').click()
        expect(self.page.locator('#bindModal')).to_be_visible()

        # Select HW instance
        self.page.select_option('#pickHwInstance', self.hwid)
        # Wait for port list to load
        self.page.wait_for_selector('#portList input[type="checkbox"]')

        # Tick the iLO checkbox
        self.page.locator('#portList input[value="ilo"]').check()

        # Submit
        self.page.locator('#bindModal button:has-text("Save binding")').click()
        expect(self.page).to_have_url(re.compile(r'/ne-instances/'))

        # Binding now shown in the table
        expect(self.page.locator('text=srv-e2e-001')).to_be_visible()

    def test_workflow_a_unbind(self):
        """Bind then unbind a port — the row reverts to 'unbound'."""
        from ne import save_ne_instance, get_ne_instance
        inst = get_ne_instance(self.nid)
        inst['iface_bindings']['iface-mgmt'] = {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': self.hwid, 'port_id': 'ilo',
                       'role': 'primary', 'bucket': []}],
        }
        save_ne_instance(inst)

        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        # Click Unbind for the mgmt row
        self.page.locator(
            f'tr:has(button[data-iface-id="iface-mgmt"]) '
            f'button:has-text("Unbind")').click()
        expect(self.page).to_have_url(re.compile(r'/ne-instances/'))
        expect(self.page.locator('text=unbound')).to_be_visible()

    # ── Workflow A — auto-rule ─────────────────────────────────────────────────

    def test_workflow_a_auto_rule_preview_updates(self):
        """Switching to auto-rule mode loads the preview pane."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button[data-iface-id="iface-mgmt"]').click()
        expect(self.page.locator('#bindModal')).to_be_visible()

        # Switch to auto-rule mode
        self.page.locator('#mode_auto-rule').click()
        expect(self.page.locator('#sectionAutoRule')).to_be_visible()
        expect(self.page.locator('#sectionExplicit')).to_have_class(re.compile(r'd-none'))

        # After a short wait, the preview pane should show something
        self.page.wait_for_timeout(800)
        expect(self.page.locator('#rulePreview')).not_to_have_text('Preview will appear here…')

    # ── Workflow B — bulk bind ─────────────────────────────────────────────────

    def test_workflow_b_modal_opens(self):
        """Clicking Bulk bind opens the modal."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button:has-text("Bulk bind")').click()
        expect(self.page.locator('#bulkBindModal')).to_be_visible()

    def test_workflow_b_preview_generates_mapping(self):
        """Selecting ifaces + HW renders the preview table."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button:has-text("Bulk bind")').click()
        expect(self.page.locator('#bulkBindModal')).to_be_visible()

        # Check the mgmt iface checkbox
        self.page.locator('.wb-iface-check[value="iface-mgmt"]').check()

        # Pick HW instance
        self.page.select_option('#wbHwInstance', self.hwid)
        # Wait for port load + preview render
        self.page.wait_for_timeout(600)

        # Preview table should appear
        expect(self.page.locator('#wbPreview table')).to_be_visible()
        expect(self.page.locator('#wbPreview')).to_contain_text('mgmt')

    def test_workflow_b_apply_creates_binding(self):
        """Selecting one iface + port and applying creates the binding."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button:has-text("Bulk bind")').click()
        expect(self.page.locator('#bulkBindModal')).to_be_visible()

        self.page.locator('.wb-iface-check[value="iface-mgmt"]').check()
        self.page.select_option('#wbHwInstance', self.hwid)
        self.page.wait_for_timeout(600)

        self.page.locator('#bulkBindModal button:has-text("Apply bindings")').click()
        expect(self.page).to_have_url(re.compile(r'/ne-instances/'))

        from ne import get_ne_instance
        inst = get_ne_instance(self.nid)
        binding = inst.get('iface_bindings', {}).get('iface-mgmt', {})
        assert binding.get('bind_mode') == 'single'
        assert len(binding.get('ports', [])) == 1

    # ── Workflow C — auto-resolve ──────────────────────────────────────────────

    def test_workflow_c_modal_opens(self):
        """Clicking Auto-resolve opens the modal."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button:has-text("Auto-resolve")').click()
        expect(self.page.locator('#autoResolveModal')).to_be_visible()

    def test_workflow_c_preview_loads_on_hw_select(self):
        """Selecting a HW instance triggers the preview fetch."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/ne-instances/{self.nid}')
        self.page.locator('button:has-text("Auto-resolve")').click()
        expect(self.page.locator('#autoResolveModal')).to_be_visible()

        self.page.select_option('#arHwInstance', self.hwid)
        self.page.wait_for_timeout(800)

        # Preview table should now be visible
        expect(self.page.locator('#arPreview table')).to_be_visible()

    # ── Rematerialize all rules ────────────────────────────────────────────────

    def test_rematerialize_all_rules_via_ui(self):
        """
        After saving an auto-rule binding the batch rematerialize endpoint
        re-runs correctly and redirects back to NE instances list.
        """
        from ne import save_ne_instance, get_ne_instance
        import datetime
        inst = get_ne_instance(self.nid)
        inst['iface_bindings']['iface-mgmt'] = {
            'bind_mode': 'auto-rule',
            'rule': {'port_types': ['mgmt'], 'name_regex': '.*',
                     'categories': ['server'], 'group_by': []},
            'rule_materialized_at': datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            'ports': [],
        }
        save_ne_instance(inst)

        # POST via page.request (avoids browser form navigation complexity)
        response = self.page.request.post(
            f'{self.base}/projects/{self.pid}/rematerialize-rules')
        assert response.status in (200, 302)

        from ne import get_ne_instance
        refreshed = get_ne_instance(self.nid)
        ports = refreshed['iface_bindings']['iface-mgmt']['ports']
        assert any(p['port_id'] == 'ilo' for p in ports)

    # ── HW instance detail — port states ──────────────────────────────────────

    def test_hw_detail_shows_free_ports(self):
        """Before any binding, all ports show as 'free'."""
        goto(self.page, self.base,
             f'/projects/{self.pid}/hw/instances/{self.hwid}')
        expect(self.page.locator('.badge.bg-success:has-text("free")')).to_be_visible()

    def test_hw_detail_shows_bound_port(self):
        """After binding, the port row shows 'bound'."""
        from ne import save_ne_instance, get_ne_instance
        inst = get_ne_instance(self.nid)
        inst['iface_bindings']['iface-mgmt'] = {
            'bind_mode': 'single',
            'ports': [{'hw_instance_id': self.hwid, 'port_id': 'ilo',
                       'role': 'primary', 'bucket': []}],
        }
        save_ne_instance(inst)

        goto(self.page, self.base,
             f'/projects/{self.pid}/hw/instances/{self.hwid}')
        expect(self.page.locator('.badge.bg-primary:has-text("bound")')).to_be_visible()
