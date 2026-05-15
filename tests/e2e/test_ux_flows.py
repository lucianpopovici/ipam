"""
End-to-end tests for UX improvements and new navigation features.

Covers:
  - Navigation: breadcrumbs, project context strip, Admin dropdown, active states
  - Interface bulk builder: range patterns, {N} placeholder, plain+count, preview
  - Form preservation: inline errors on validation failure, values preserved
  - Empty states: teaching content shown when lists are empty
  - Delete confirmation modal: Bootstrap modal replaces browser confirm()
  - Client-side CIDR validation: is-invalid / is-valid toggled via API
  - Bulk list operations: checkboxes, bulk-action-bar, bulk delete
  - Mobile viewport: smoke flows at 390 px wide
"""
import re
import time
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')


def _create_project(page: Page, base: str,
                    name: str = 'UX E2E', supernet: str = '10.0.0.0/8') -> str:
    goto(page, base, '/projects/add')
    page.fill('input[name="name"]', name)
    page.fill('input[name="supernet"]', supernet)
    page.click('button[type="submit"]')
    return page.url.rstrip('/').split('/')[-1]


def _create_site(page: Page, base: str, pid: str, name: str = 'SITE-1') -> str:
    goto(page, base, f'/projects/{pid}/sites/add')
    page.fill('input[name="name"]', name)
    page.click('button[type="submit"]')
    # Extract sid from redirect URL
    return page.url.rstrip('/').split('/')[-1]


# ══════════════════════════════════════════════════════════════════════════════
# Navigation — breadcrumbs, project context strip, admin dropdown
# ══════════════════════════════════════════════════════════════════════════════

class TestNavigation:

    def test_breadcrumbs_appear_on_sites_list(self, page_base):
        """Sites list shows a breadcrumb trail including the project name."""
        page, base = page_base
        pid = _create_project(page, base, name='Breadcrumb Proj')
        goto(page, base, f'/projects/{pid}/sites')
        crumbs = page.locator('nav[aria-label="breadcrumb"] ol.breadcrumb')
        expect(crumbs).to_be_visible()
        expect(crumbs).to_contain_text('Projects')
        expect(crumbs).to_contain_text('Breadcrumb Proj')
        expect(crumbs).to_contain_text('Sites')

    def test_breadcrumbs_show_deep_hierarchy(self, page_base):
        """Site detail page shows 4-level breadcrumb: Projects > Proj > Sites > Site."""
        page, base = page_base
        pid = _create_project(page, base, name='Deep Crumb')
        _create_site(page, base, pid, name='SITE-DEEP')
        # Navigate to the newly created site's detail page
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('a', has_text='SITE-DEEP').first.click()
        crumbs = page.locator('nav[aria-label="breadcrumb"] ol.breadcrumb')
        expect(crumbs).to_contain_text('Deep Crumb')
        expect(crumbs).to_contain_text('Sites')
        expect(crumbs).to_contain_text('SITE-DEEP')

    def test_project_nav_strip_visible_inside_project(self, page_base):
        """Project context strip renders when inside a project URL."""
        page, base = page_base
        pid = _create_project(page, base, name='Nav Strip')
        goto(page, base, f'/projects/{pid}/sites')
        # The project nav is inside .project-nav
        expect(page.locator('.project-nav')).to_be_visible()

    def test_project_nav_strip_not_visible_on_homepage(self, page_base):
        """Project context strip does not render on the homepage."""
        page, base = page_base
        goto(page, base, '/')
        expect(page.locator('.project-nav')).to_have_count(0)

    def test_project_nav_shows_correct_project_name(self, page_base):
        """Project picker in the nav strip shows the current project name."""
        page, base = page_base
        pid = _create_project(page, base, name='My Project Nav')
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('.project-nav')).to_contain_text('My Project Nav')

    def test_project_nav_active_tab_highlighted(self, page_base):
        """Sites tab is highlighted when on the sites list page."""
        page, base = page_base
        pid = _create_project(page, base, name='Active Tab')
        goto(page, base, f'/projects/{pid}/sites')
        active = page.locator('.project-nav .pnav-active')
        expect(active).to_be_visible()
        expect(active).to_contain_text('Sites')

    def test_project_nav_inventory_tab_active_on_inventory(self, page_base):
        """Inventory tab is highlighted when on the inventory page."""
        page, base = page_base
        pid = _create_project(page, base, name='Inv Active')
        goto(page, base, f'/projects/{pid}/hw/inventory')
        active = page.locator('.project-nav .pnav-active')
        expect(active).to_contain_text('Inventory')

    def test_admin_dropdown_contains_global_links(self, page_base):
        """⚙ Admin dropdown exposes global config links."""
        page, base = page_base
        goto(page, base, '/')
        page.locator('.navbar .dropdown-toggle:has-text("Admin")').click()
        menu = page.locator('.navbar .dropdown-menu')
        expect(menu).to_be_visible()
        expect(menu).to_contain_text('Global Labels')
        expect(menu).to_contain_text('Subnet Templates')
        expect(menu).to_contain_text('HW Templates')
        expect(menu).to_contain_text('VMware')

    def test_projects_link_is_active_on_homepage(self, page_base):
        """Projects nav link gets active class on the homepage."""
        page, base = page_base
        goto(page, base, '/')
        projects_link = page.locator('.navbar a.nav-link:has-text("Projects")')
        expect(projects_link).to_have_class(re.compile(r'\bactive\b'))


# ══════════════════════════════════════════════════════════════════════════════
# Interface bulk builder (ne_type_form.html)
# ══════════════════════════════════════════════════════════════════════════════

class TestInterfaceBulkBuilder:

    def _open_add_bar(self, page: Page, base: str):
        """Navigate to the global NE type form and open the add-interface bar."""
        goto(page, base, '/ne-types/add')
        page.click('button:has-text("+ Add Interface")')
        page.locator('#addBar').wait_for(state='visible')

    def test_add_bar_appears_on_click(self, page_base):
        """Clicking '+ Add Interface' shows the inline add form."""
        page, base = page_base
        goto(page, base, '/ne-types/add')
        expect(page.locator('#addBar')).to_have_class(re.compile('d-none'))
        page.click('button:has-text("+ Add Interface")')
        page.locator('#addBar').wait_for(state='visible')
        expect(page.locator('#addBar')).not_to_have_class(re.compile('d-none'))

    def test_range_pattern_preview(self, page_base):
        """eth{0..3} pattern shows '4 interfaces' in the preview."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'eth{0..3}')
        page.dispatch_event('#addName', 'input')
        time.sleep(0.2)
        expect(page.locator('#addPreview')).to_contain_text('4 interfaces')
        expect(page.locator('#addPreview')).to_contain_text('eth0')
        expect(page.locator('#addPreview')).to_contain_text('eth3')

    def test_range_pattern_disables_count(self, page_base):
        """{start..end} present → count field is disabled."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'ge-{0..7}')
        page.dispatch_event('#addName', 'input')
        time.sleep(0.1)
        expect(page.locator('#addCount')).to_be_disabled()

    def test_range_pattern_adds_interfaces(self, page_base):
        """eth{0..2} → 3 interface rows added to the interface list."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'eth{0..2}')
        page.dispatch_event('#addName', 'input')
        page.click('#addBar button:has-text("Add")')
        # Bar should close and ifaceList should have 3 entries
        expect(page.locator('#addBar')).to_have_class(re.compile('d-none'))
        iface_rows = page.locator('#ifaceList .border-bottom')
        expect(iface_rows).to_have_count(3)
        # Names should be eth0, eth1, eth2
        expect(iface_rows.nth(0).locator('input').first).to_have_value('eth0')
        expect(iface_rows.nth(2).locator('input').first).to_have_value('eth2')

    def test_n_placeholder_with_count(self, page_base):
        """gi-{N}/0 with count=3 → gi-0/0, gi-1/0, gi-2/0."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'gi-{N}/0')
        page.fill('#addCount', '3')
        page.dispatch_event('#addName', 'input')
        time.sleep(0.1)
        expect(page.locator('#addPreview')).to_contain_text('3 interfaces')
        page.click('#addBar button:has-text("Add")')
        iface_rows = page.locator('#ifaceList .border-bottom')
        expect(iface_rows).to_have_count(3)
        expect(iface_rows.nth(0).locator('input').first).to_have_value('gi-0/0')
        expect(iface_rows.nth(2).locator('input').first).to_have_value('gi-2/0')

    def test_plain_name_with_count(self, page_base):
        """Plain name 'eth' with count=4 → eth0, eth1, eth2, eth3."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'eth')
        page.fill('#addCount', '4')
        page.dispatch_event('#addName', 'input')
        page.click('#addBar button:has-text("Add")')
        iface_rows = page.locator('#ifaceList .border-bottom')
        expect(iface_rows).to_have_count(4)
        expect(iface_rows.nth(3).locator('input').first).to_have_value('eth3')

    def test_cancel_closes_bar_without_adding(self, page_base):
        """Cancel button closes the add bar without adding any interface."""
        page, base = page_base
        self._open_add_bar(page, base)
        page.fill('#addName', 'eth{0..5}')
        page.click('#addBar button:has-text("Cancel")')
        expect(page.locator('#addBar')).to_have_class(re.compile('d-none'))
        expect(page.locator('#ifaceList .border-bottom')).to_have_count(0)


# ══════════════════════════════════════════════════════════════════════════════
# Form preservation — inline errors, values retained on failure
# ══════════════════════════════════════════════════════════════════════════════

class TestFormPreservation:

    def _bypass_required(self, page: Page, field_name: str):
        """Remove the browser-enforced `required` so server-side validation runs."""
        page.evaluate(
            f"document.querySelector('input[name=\"{field_name}\"]').removeAttribute('required')"
        )

    def test_add_site_empty_name_shows_inline_error(self, page_base):
        """Server re-renders add-site with is-invalid on name when it's blank."""
        page, base = page_base
        pid = _create_project(page, base, name='Form Preservation')
        goto(page, base, f'/projects/{pid}/sites/add')
        page.fill('input[name="description"]', 'kept description')
        self._bypass_required(page, 'name')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/sites/add'))
        expect(page.locator('input[name="name"]')).to_have_class(re.compile('is-invalid'))
        expect(page.locator('.invalid-feedback')).to_contain_text('required')
        expect(page.locator('input[name="description"]')).to_have_value('kept description')

    def test_add_pod_empty_name_shows_inline_error(self, page_base):
        """Server re-renders add-pod with is-invalid on name when it's blank."""
        page, base = page_base
        pid = _create_project(page, base, name='Pod Form Preservation')
        goto(page, base, f'/projects/{pid}/pods/add')
        page.fill('input[name="description"]', 'my pod desc')
        self._bypass_required(page, 'name')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/pods/add'))
        expect(page.locator('input[name="name"]')).to_have_class(re.compile('is-invalid'))
        expect(page.locator('input[name="description"]')).to_have_value('my pod desc')

    def test_add_project_empty_name_shows_inline_error(self, page_base):
        """Server re-renders add-project with is-invalid on name when blank."""
        page, base = page_base
        goto(page, base, '/projects/add')
        page.fill('input[name="supernet"]', '10.0.0.0/8')
        self._bypass_required(page, 'name')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/projects/add'))
        expect(page.locator('input[name="name"]')).to_have_class(re.compile('is-invalid'))
        expect(page.locator('input[name="supernet"]')).to_have_value('10.0.0.0/8')

    def test_add_project_invalid_supernet_preserved(self, page_base):
        """Invalid supernet stays in the field so the user can correct it."""
        page, base = page_base
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'My Project')
        page.fill('input[name="supernet"]', 'bad-value')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/projects/add'))
        expect(page.locator('input[name="supernet"]')).to_have_value('bad-value')
        expect(page.locator('input[name="supernet"]')).to_have_class(re.compile('is-invalid'))

    def test_add_ne_type_empty_name_shows_inline_error(self, page_base):
        """Server re-renders add-ne-type with is-invalid on name when blank."""
        page, base = page_base
        goto(page, base, '/ne-types/add')
        page.fill('input[name="description"]', 'some desc')
        self._bypass_required(page, 'name')
        # prepareSubmit populates interfaces_json; the button onclick does this
        # but we click submit directly after setting it manually
        page.evaluate("document.getElementById('ifacesJson').value = '[]'")
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/ne-types/add'))
        expect(page.locator('input[name="name"]')).to_have_class(re.compile('is-invalid'))


# ══════════════════════════════════════════════════════════════════════════════
# Empty states — teaching content for first-time users
# ══════════════════════════════════════════════════════════════════════════════

class TestEmptyStates:

    def test_sites_list_shows_empty_state(self, page_base):
        """Empty sites list shows icon, descriptive title, and add button."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty Sites')
        goto(page, base, f'/projects/{pid}/sites')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No sites yet')
        expect(empty.locator('a:has-text("Add Site"), a:has-text("+ Add")')).to_be_visible()

    def test_pods_list_shows_empty_state(self, page_base):
        """Empty PODs list shows descriptive empty state."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty Pods')
        goto(page, base, f'/projects/{pid}/pods')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No PODs yet')

    def test_ne_types_list_shows_empty_state(self, page_base):
        """Empty global NE types list shows descriptive empty state."""
        page, base = page_base
        goto(page, base, '/ne-types')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No global NE types yet')
        expect(empty.locator('a')).to_be_visible()

    def test_inventory_shows_empty_state(self, page_base):
        """Empty inventory page shows empty state with BoM link."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty Inv')
        goto(page, base, f'/projects/{pid}/hw/inventory')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No hardware instances yet')

    def test_rack_list_shows_empty_state(self, page_base):
        """Empty rack list page shows empty state."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty Racks')
        goto(page, base, f'/projects/{pid}/hw/racks')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No racks yet')

    def test_cable_list_shows_empty_state(self, page_base):
        """Empty cable list shows empty state with add link."""
        page, base = page_base
        pid = _create_project(page, base, name='Empty Cables')
        goto(page, base, f'/projects/{pid}/hw/cables')
        empty = page.locator('.text-center.py-5')
        expect(empty).to_be_visible()
        expect(empty).to_contain_text('No cables yet')

    def test_empty_state_has_primary_action(self, page_base):
        """Primary action button in empty state links to the add page."""
        page, base = page_base
        pid = _create_project(page, base, name='Action Test')
        goto(page, base, f'/projects/{pid}/sites')
        add_btn = page.locator('.text-center.py-5 .btn-primary')
        expect(add_btn).to_be_visible()
        href = add_btn.get_attribute('href')
        assert '/sites/add' in href


# ══════════════════════════════════════════════════════════════════════════════
# Delete confirmation modal
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteModal:

    def test_del_button_opens_modal_not_dialog(self, page_base):
        """Clicking Del on a site row opens #deleteModal (no browser dialog)."""
        page, base = page_base
        pid = _create_project(page, base, name='Modal Test')
        _create_site(page, base, pid, 'MODAL-SITE')
        goto(page, base, f'/projects/{pid}/sites')
        # Intercept any native dialog — should never fire
        dialog_fired = []
        page.on('dialog', lambda d: dialog_fired.append(True) or d.dismiss())
        page.locator('tr:has-text("MODAL-SITE") [data-delete-url]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        assert not dialog_fired, 'Browser confirm() fired — should use Bootstrap modal'

    def test_modal_shows_entity_name(self, page_base):
        """Delete modal displays the entity name passed via data-delete-label."""
        page, base = page_base
        pid = _create_project(page, base, name='Label Modal')
        _create_site(page, base, pid, 'MY-NAMED-SITE')
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('tr:has-text("MY-NAMED-SITE") [data-delete-url]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        expect(page.locator('#deleteModalLabel')).to_contain_text('MY-NAMED-SITE')

    def test_modal_cancel_does_not_delete(self, page_base):
        """Clicking Cancel in the modal leaves the entity intact."""
        page, base = page_base
        pid = _create_project(page, base, name='Cancel Modal')
        _create_site(page, base, pid, 'KEEP-ME')
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('tr:has-text("KEEP-ME") [data-delete-url]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        page.locator('#deleteModal button:has-text("Cancel")').click()
        # Modal should close
        page.wait_for_timeout(400)
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('body')).to_contain_text('KEEP-ME')

    def test_modal_confirm_deletes_site(self, page_base):
        """Confirming in the delete modal removes the site from the list."""
        page, base = page_base
        pid = _create_project(page, base, name='Confirm Modal')
        _create_site(page, base, pid, 'DELETE-ME')
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('tr:has-text("DELETE-ME") [data-delete-url]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        page.locator('#deleteModalForm button[type="submit"]').click()
        page.wait_for_load_state('networkidle')
        # Reload to get a clean DOM (avoid matching the hidden modal label)
        page.reload()
        page.wait_for_load_state('networkidle')
        # The site table or empty state should not contain the deleted name
        expect(page.locator('table tbody, .text-center.py-5')).not_to_contain_text('DELETE-ME')

    def test_project_delete_modal_shows_impact(self, page_base):
        """Project delete modal fetches and shows subnet cascade info."""
        page, base = page_base
        pid = _create_project(page, base, name='Impact Modal')
        # Add a subnet so the impact shows non-zero
        from ipam import save_network
        from db import new_id
        save_network({
            'id': new_id(), 'name': 'mgmt', 'cidr': '10.0.1.0/24',
            'description': '', 'vlan': '', 'project_id': pid,
            'template_id': '', 'pending_slots': [],
        })
        goto(page, base, '/')
        page.locator(f'[data-delete-url*="{pid}/delete"]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        # Impact div should load (might say "Loading…" briefly, then content)
        page.wait_for_timeout(800)
        impact = page.locator('#deleteModalImpact')
        # Should contain either cascade info or "No cascading effects"
        expect(impact).not_to_be_empty()

    def test_pod_delete_modal(self, page_base):
        """POD delete modal opens correctly and confirms deletion."""
        page, base = page_base
        pid = _create_project(page, base, name='POD Modal')
        goto(page, base, f'/projects/{pid}/pods/add')
        page.fill('input[name="name"]', 'DEL-POD')
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/pods')
        page.locator('tr:has-text("DEL-POD") [data-delete-url]').click()
        page.locator('#deleteModal').wait_for(state='visible')
        expect(page.locator('#deleteModalLabel')).to_contain_text('DEL-POD')
        page.locator('#deleteModalForm button[type="submit"]').click()
        page.wait_for_load_state('networkidle')
        page.reload()
        expect(page.locator('table tbody, .text-center.py-5')).not_to_contain_text('DEL-POD')


# ══════════════════════════════════════════════════════════════════════════════
# Client-side CIDR validation
# ══════════════════════════════════════════════════════════════════════════════

class TestCIDRValidation:

    def test_invalid_supernet_gets_is_invalid_class(self, page_base):
        """Typing an invalid value into data-validate="ip-interface" adds is-invalid."""
        page, base = page_base
        goto(page, base, '/projects/add')
        inp = page.locator('input[data-validate="ip-interface"]')
        inp.fill('not-a-cidr')
        inp.blur()
        # Wait for debounce + fetch
        page.wait_for_timeout(700)
        expect(inp).to_have_class(re.compile('is-invalid'))

    def test_valid_supernet_gets_is_valid_class(self, page_base):
        """Typing a valid CIDR into data-validate="ip-interface" adds is-valid."""
        page, base = page_base
        goto(page, base, '/projects/add')
        inp = page.locator('input[data-validate="ip-interface"]')
        inp.fill('10.0.0.0/8')
        inp.blur()
        page.wait_for_timeout(700)
        expect(inp).to_have_class(re.compile('is-valid'))

    def test_validate_api_cidr_endpoint(self, page_base):
        """The /api/validate/cidr endpoint returns ok:true for a valid CIDR."""
        page, base = page_base
        resp = page.request.get(f'{base}/api/validate/cidr?v=192.168.1.0/24')
        assert resp.status == 200
        data = resp.json()
        assert data['ok'] is True

    def test_validate_api_cidr_rejects_invalid(self, page_base):
        """The /api/validate/cidr endpoint returns ok:false for invalid input."""
        page, base = page_base
        resp = page.request.get(f'{base}/api/validate/cidr?v=300.400.500.600/99')
        assert resp.status == 200
        data = resp.json()
        assert data['ok'] is False
        assert 'error' in data

    def test_subnet_cidr_validates_against_supernet(self, page_base):
        """CIDR outside the project supernet shows is-invalid on the subnet form."""
        page, base = page_base
        pid = _create_project(page, base, name='CIDR Validate', supernet='10.0.0.0/16')
        goto(page, base, f'/projects/{pid}/subnet/add')
        # Switch to Manual CIDR mode so the input becomes visible
        page.locator('input[value="manual"]').click()
        cidr_input = page.locator('input[data-validate]')
        expect(cidr_input).to_be_visible()
        cidr_input.fill('192.168.1.0/24')  # outside 10.0.0.0/16
        cidr_input.blur()
        page.wait_for_timeout(700)
        expect(cidr_input).to_have_class(re.compile('is-invalid'))


# ══════════════════════════════════════════════════════════════════════════════
# Bulk list operations
# ══════════════════════════════════════════════════════════════════════════════

class TestBulkOperations:

    def _seed_sites(self, page: Page, base: str, pid: str, count: int = 3) -> list:
        """Create `count` sites and return their names."""
        names = [f'BULK-{i}' for i in range(count)]
        for name in names:
            _create_site(page, base, pid, name)
        return names

    def test_row_checkboxes_present_on_sites_list(self, page_base):
        """Sites list has per-row checkboxes when sites exist."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Check')
        self._seed_sites(page, base, pid, count=2)
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('.row-select')).not_to_have_count(0)

    def test_select_all_checkbox_present(self, page_base):
        """Header select-all checkbox is present when sites exist."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk SelectAll')
        self._seed_sites(page, base, pid, count=2)
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('#selectAll')).to_be_visible()

    def test_bulk_bar_hidden_initially(self, page_base):
        """Bulk action bar is hidden when no rows are selected."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Hidden')
        self._seed_sites(page, base, pid, count=2)
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('.bulk-action-bar')).to_have_class(re.compile('d-none'))

    def test_bulk_bar_appears_on_selection(self, page_base):
        """Checking a row reveals the bulk action bar."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Visible')
        self._seed_sites(page, base, pid, count=2)
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('.row-select').first.check()
        expect(page.locator('.bulk-action-bar')).not_to_have_class(re.compile('d-none'))
        expect(page.locator('.selected-count')).to_contain_text('1')

    def test_select_all_checks_all_rows(self, page_base):
        """Checking the select-all box selects every row."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk All')
        self._seed_sites(page, base, pid, count=3)
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('#selectAll').check()
        checked = page.locator('.row-select:checked')
        all_rows = page.locator('.row-select')
        expect(checked).to_have_count(all_rows.count())

    def test_bulk_delete_sites(self, page_base):
        """Selecting all sites and bulk-deleting removes them from the list."""
        page, base = page_base
        pid = _create_project(page, base, name='Bulk Delete')
        self._seed_sites(page, base, pid, count=2)
        goto(page, base, f'/projects/{pid}/sites')
        page.locator('#selectAll').check()
        # Click the bulk delete button (triggers a fetch POST + reload)
        page.locator('[data-bulk-action="delete"]').click()
        # Confirm the browser confirm inside bulk-actions.js
        page.on('dialog', lambda d: d.accept())
        page.locator('[data-bulk-action="delete"]').click()
        page.wait_for_load_state('networkidle')
        # Either empty state or no BULK- rows
        expect(page.locator('body')).not_to_contain_text('BULK-0')

    def test_bulk_delete_api_endpoint(self, page_base):
        """POST to /projects/<pid>/sites/bulk-delete with JSON ids deletes them."""
        page, base = page_base
        pid = _create_project(page, base, name='API Bulk Delete')
        from ne import save_site
        from db import new_id
        sid = new_id()
        save_site({'id': sid, 'name': 'API-DEL', 'project_id': pid,
                   'description': '', 'labels': [], 'params': {}})
        # Call the endpoint directly
        resp = page.request.post(
            f'{base}/projects/{pid}/sites/bulk-delete',
            data='{"ids": ["' + sid + '"]}',
            headers={'Content-Type': 'application/json'},
        )
        assert resp.status == 200
        data = resp.json()
        assert data['deleted'] >= 1

    def test_pods_list_has_bulk_checkboxes(self, page_base):
        """POD list also has .row-select checkboxes when pods exist."""
        page, base = page_base
        pid = _create_project(page, base, name='POD Bulk')
        goto(page, base, f'/projects/{pid}/pods/add')
        page.fill('input[name="name"]', 'P1')
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/pods')
        expect(page.locator('.row-select')).not_to_have_count(0)


# ══════════════════════════════════════════════════════════════════════════════
# Mobile viewport smoke tests (390 × 844 — iPhone-ish)
# ══════════════════════════════════════════════════════════════════════════════

class TestMobileViewport:

    def test_homepage_loads_at_390px(self, page_base_mobile):
        """Homepage renders without horizontal overflow at 390 px wide."""
        page, base = page_base_mobile
        goto(page, base, '/')
        expect(page).to_have_title(re.compile(r'IPAM'))
        # No horizontal scrollbar — scrollWidth should equal clientWidth
        overflow = page.evaluate(
            'document.documentElement.scrollWidth > document.documentElement.clientWidth'
        )
        assert not overflow, 'Horizontal overflow at 390 px'

    def test_navbar_toggler_visible_on_mobile(self, page_base_mobile):
        """Hamburger navbar-toggler is visible at mobile width."""
        page, base = page_base_mobile
        goto(page, base, '/')
        toggler = page.locator('.navbar-toggler')
        expect(toggler).to_be_visible()

    def test_navbar_collapses_and_expands(self, page_base_mobile):
        """Clicking the toggler opens the collapsed nav menu."""
        page, base = page_base_mobile
        goto(page, base, '/')
        # Menu should start collapsed
        expect(page.locator('#mainNav')).not_to_be_visible()
        page.locator('.navbar-toggler').click()
        expect(page.locator('#mainNav')).to_be_visible()
        # Links should now be visible
        expect(page.locator('#mainNav a:has-text("Projects")')).to_be_visible()

    def test_project_form_usable_on_mobile(self, page_base_mobile):
        """Project creation form is functional on a 390 px viewport."""
        page, base = page_base_mobile
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Mobile Project')
        page.fill('input[name="supernet"]', '10.0.0.0/16')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(r'/projects/'))
        expect(page.locator('body')).to_contain_text('Mobile Project')

    def test_project_nav_strip_scrollable_on_mobile(self, page_base_mobile):
        """Project context nav strip is present and doesn't break layout on mobile."""
        page, base = page_base_mobile
        # Create project at mobile viewport
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Mobile Nav')
        page.fill('input[name="supernet"]', '10.1.0.0/16')
        page.click('button[type="submit"]')
        pid = page.url.rstrip('/').split('/')[-1]
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('.project-nav')).to_be_visible()
        # No horizontal overflow
        overflow = page.evaluate(
            'document.documentElement.scrollWidth > document.documentElement.clientWidth'
        )
        assert not overflow, 'Horizontal overflow with project nav at 390 px'

    def test_sites_list_table_responsive_on_mobile(self, page_base_mobile):
        """Sites list table is wrapped in table-responsive on mobile."""
        page, base = page_base_mobile
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Mobile Sites')
        page.fill('input[name="supernet"]', '10.2.0.0/16')
        page.click('button[type="submit"]')
        pid = page.url.rstrip('/').split('/')[-1]
        goto(page, base, f'/projects/{pid}/sites/add')
        page.fill('input[name="name"]', 'MOBILE-SITE')
        page.click('button[type="submit"]')
        goto(page, base, f'/projects/{pid}/sites')
        expect(page.locator('.table-responsive')).to_be_visible()
