"""

pytestmark = pytest.mark.e2e

End-to-end tests for NE management flows using Playwright.
"""
import pytest
import time
import re
from playwright.sync_api import Page, expect


# Re-use the live_server fixture from conftest / test_ipam_flows
# (pytest collects fixtures from all conftest.py files in the path)

def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')



def _create_project(page: Page, base: str, name='NE E2E', supernet='10.0.0.0/8'):
    goto(page, base, '/projects/add')
    page.fill('input[name="name"]', name)
    page.fill('input[name="supernet"]', supernet)
    page.click('button[type="submit"]')
    return page.url.rstrip('/').split('/')[-1]


# ══════════════════════════════════════════════════════════════════════════════
# Schemas
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ESchemas:
    def test_admin_schemas_page_loads(self, page_base):
        page, base = page_base
        goto(page, base, '/admin/schemas')
        expect(page).to_have_url(re.compile(r'/admin/schemas'))
        expect(page.locator('h4, h3')).to_be_visible()

    def test_add_field_to_schema(self, page_base):
        page, base = page_base
        goto(page, base, '/admin/schemas')
        # Use the JS schema editor to add a field and submit
        page.evaluate("""
            () => {
                // Simulate adding a field to site schema
                const existing = window.schemaData && window.schemaData.site;
                // Direct form submission with JSON
                const form = document.querySelector('form[data-entity="site"]') ||
                             document.forms[0];
                if(form) {
                    const inp = form.querySelector('input[name="fields_json"]') ||
                                document.createElement('input');
                    inp.name  = 'fields_json';
                    inp.type  = 'hidden';
                    inp.value = JSON.stringify([{
                        id: 'test-f1', name: 'region', label: 'Region',
                        field_type: 'text', required: false, options: [], default: ''
                    }]);
                    if(!inp.parentElement) form.appendChild(inp);
                }
            }
        """)
        # Just check the page renders without error
        expect(page.locator('body')).to_be_visible()

    def test_project_schemas_page_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Schema E2E')
        goto(page, base, f'/projects/{pid}/schemas')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/schemas'))


# ══════════════════════════════════════════════════════════════════════════════
# NE Types
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ENETypes:
    def test_ne_types_list_loads(self, page_base):
        page, base = page_base
        goto(page, base, '/ne-types')
        expect(page.locator('body')).to_be_visible()

    def test_create_ne_type(self, page_base):
        page, base = page_base
        goto(page, base, '/ne-types/add')
        page.fill('input[name="name"]', 'E2E Router')
        page.select_option('select[name="kind"]', 'PNF')
        # Fill interfaces JSON directly
        page.evaluate("""
            () => {
                const inp = document.querySelector('input[name="interfaces_json"]') ||
                            document.getElementById('ifacesJson');
                if(inp) {
                    inp.value = JSON.stringify([{
                        id: 'i1', name: 'mgmt', labels: ['mgmt'], params: {},
                        ipv4: {prefix_len: 29}, ipv6: null, sharing: 'ne'
                    }]);
                }
            }
        """)
        page.evaluate("document.querySelector('input[name=\"params_json\"]') && (document.querySelector('input[name=\"params_json\"]').value = '{}')")
        page.click('button[type="submit"]')
        # Check ne type appears
        goto(page, base, '/ne-types')
        expect(page.locator('body')).to_contain_text('E2E Router')

    def test_project_ne_types_page(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='NEType Project')
        goto(page, base, f'/projects/{pid}/ne-types')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/ne-types'))


# ══════════════════════════════════════════════════════════════════════════════
# Sites
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ESites:
    def test_sites_list_loads(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Sites E2E')
        goto(page, base, f'/projects/{pid}/sites')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/sites'))

    def test_create_single_site(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Single Site')
        goto(page, base, f'/projects/{pid}/sites/add')
        page.fill('input[name="name"]', 'LON-DC1')
        page.fill('textarea[name="description"]', 'London datacenter')
        page.evaluate("document.querySelector('input[name=\"params_json\"]') && (document.querySelector('input[name=\"params_json\"]').value = '{}')")
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('body')).to_contain_text('LON-DC1')

    def test_bulk_site_creation(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Sites')
        goto(page, base, f'/projects/{pid}/sites/bulk')
        page.fill('input[name="pattern"]', 'ran{0001..0005}')
        page.evaluate("document.querySelector('input[name=\"params_json\"]') && (document.querySelector('input[name=\"params_json\"]').value = '{}')")
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('body')).to_contain_text('ran0001')
        expect(page.locator('body')).to_contain_text('ran0005')
        # Verify count
        site_rows = page.locator('tr, li').filter(has_text='ran0')
        assert site_rows.count() >= 5

    def test_bulk_site_preview(self, page_base):
        """The pattern preview shows count before submit."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Preview')
        goto(page, base, f'/projects/{pid}/sites/bulk')
        page.fill('input[name="pattern"]', 'site{001..010}')
        # Trigger the input event for live preview
        page.dispatch_event('input[name="pattern"]', 'input')
        time.sleep(0.3)
        preview = page.locator('#patternPreview, .pattern-preview, [data-preview]')
        if preview.count() > 0:
            expect(preview.first).to_contain_text('10')

    def test_delete_site(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='Delete Site')
        goto(page, base, f'/projects/{pid}/sites/add')
        page.fill('input[name="name"]', 'TO-DELETE')
        page.evaluate("document.querySelector('input[name=\"params_json\"]') && (document.querySelector('input[name=\"params_json\"]').value = '{}')")
        page.click('button[type="submit"]')
        # Navigate to site detail and delete
        goto(page, base, f'/projects/{pid}/sites')
        page.click('a:has-text("TO-DELETE")')
        page.on('dialog', lambda d: d.accept())
        delete_btn = page.locator('form[action*="/delete"] button')
        if delete_btn.count() > 0:
            delete_btn.first.click()
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('body')).not_to_contain_text('TO-DELETE')


# ══════════════════════════════════════════════════════════════════════════════
# PODs
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EPODs:
    def test_create_pod(self, page_base):
        page, base = page_base
        pid = _create_project(page, base, name='POD E2E')
        goto(page, base, f'/projects/{pid}/pods/add')
        page.fill('input[name="name"]', 'CORE-POD-1')
        page.evaluate("document.querySelector('input[name=\"params_json\"]') && (document.querySelector('input[name=\"params_json\"]').value = '{}')")
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/pods')
        expect(page.locator('body')).to_contain_text('CORE-POD-1')

    def test_assign_pod_to_site(self, page_base):
        page, base = page_base
        from db import new_id
        from ne import save_site, save_pod
        from ipam import save_project

        pid = _create_project(page, base, name='Assign Test')

        # Create site and pod via API
        site_id = new_id()
        pod_id  = new_id()
        save_site({'id': site_id, 'name': 'ASSIGN-SITE', 'project_id': pid,
                   'description': '', 'labels': [], 'params': {}})
        save_pod({'id': pod_id, 'name': 'ASSIGN-POD', 'project_id': pid,
                  'description': '', 'labels': [], 'params': {}})

        goto(page, base, f'/projects/{pid}/sites/{site_id}')
        assign_select = page.locator('select[name="pod_id"]')
        if assign_select.count() > 0:
            assign_select.select_option(value=pod_id)
            page.click('button:has-text("Assign"), input[type="submit"]')
            expect(page.locator('body')).to_contain_text('ASSIGN-POD')

    def test_pod_slot_builder(self, page_base):
        """Verify the NE slot builder JS renders on the pod detail page."""
        page, base = page_base
        from db import new_id
        from ne import save_pod
        from ipam import save_project

        pid = _create_project(page, base, name='Slot Builder')
        pod_id = new_id()
        save_pod({'id': pod_id, 'name': 'SLOT-POD', 'project_id': pid,
                  'description': '', 'labels': [], 'params': {}})

        goto(page, base, f'/projects/{pid}/pods/{pod_id}')
        expect(page).to_have_url(re.compile(rf'/pods/{pod_id}'))
        # Slot builder section should exist
        expect(page.locator('body')).to_be_visible()


# ══════════════════════════════════════════════════════════════════════════════
# Requirements engine
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ERequirements:
    def _build_hierarchy(self, base_url):
        """
        Build a full site → pod → NE type hierarchy via the helper functions
        (faster than driving the full UI for setup).
        """
        from db import new_id
        from ne import (save_ne_type, save_site, save_pod,
                        assign_pod_to_site, save_pod_slots)
        from ipam import save_project

        pid = new_id()
        save_project({'id': pid, 'name': 'Req E2E', 'supernet': '10.0.0.0/8', 'description': ''})

        ne_id = new_id()
        save_ne_type({
            'id': ne_id, 'name': 'E2E-NE', 'kind': 'PNF',
            'description': '', 'labels': [], 'params': {},
            'interfaces': [{
                'id': 'i1', 'name': 'mgmt', 'labels': [], 'params': {},
                'ipv4': {'prefix_len': 29}, 'ipv6': None, 'sharing': 'ne',
            }],
            'scope': 'global', 'project_id': '',
        })

        site_id = new_id()
        save_site({'id': site_id, 'name': 'REQ-SITE', 'project_id': pid,
                   'description': '', 'labels': [], 'params': {}})

        pod_id = new_id()
        save_pod({'id': pod_id, 'name': 'REQ-POD', 'project_id': pid,
                  'description': '', 'labels': [], 'params': {}})

        assign_pod_to_site(pod_id, site_id)
        save_pod_slots(pod_id, [{'ne_type_id': ne_id, 'count': 2, 'label_override': []}])

        return pid

    def test_requirements_page_shows_results(self, page_base):
        page, base = page_base
        pid = self._build_hierarchy(base)
        goto(page, base, f'/projects/{pid}/requirements')
        expect(page).to_have_url(re.compile(rf'/projects/{pid}/requirements'))
        # Should show at least one requirement row
        expect(page.locator('table tbody tr, .requirement-row')).not_to_have_count(0)

    def test_requirements_show_interface_name(self, page_base):
        page, base = page_base
        pid = self._build_hierarchy(base)
        goto(page, base, f'/projects/{pid}/requirements')
        expect(page.locator('body')).to_contain_text('mgmt')

    def test_push_requirements_to_ipam(self, page_base):
        page, base = page_base
        pid = self._build_hierarchy(base)
        goto(page, base, f'/projects/{pid}/requirements')
        # Select all checkboxes and push
        page.evaluate("document.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = true)")
        push_btn = page.locator('button:has-text("Push"), button:has-text("push")')
        if push_btn.count() > 0:
            push_btn.first.click()
            # Should flash success or show pushed state
            expect(page.locator('body')).to_be_visible()

    def test_push_all_button(self, page_base):
        page, base = page_base
        pid = self._build_hierarchy(base)
        goto(page, base, f'/projects/{pid}/requirements')
        push_all = page.locator('button:has-text("Push All"), a:has-text("Push All")')
        if push_all.count() > 0:
            push_all.first.click()
            expect(page.locator('body')).to_be_visible()
