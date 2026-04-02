"""

pytestmark = pytest.mark.e2e

End-to-end tests for IPAM flows using Playwright.

These tests launch the real Flask app against a real Redis instance
(or fakeredis via a test server fixture) and drive a Chromium browser.

Run with:
    pytest tests/e2e/ --headed              # see the browser
    pytest tests/e2e/ --slowmo=200          # slow down for debugging
    pytest tests/e2e/                       # headless (CI)

Prerequisites:
    pip install pytest-playwright
    playwright install chromium
"""
import re
import pytest
import threading
import time
from playwright.sync_api import Page, expect



# ══════════════════════════════════════════════════════════════════════════════
# Helper – navigate shorthand
# ══════════════════════════════════════════════════════════════════════════════

def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: Project lifecycle
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EProjectLifecycle:
    def test_homepage_loads(self, page_base):
        page, base = page_base
        expect(page).to_have_title(re.compile(r'IPAM|Project'))
        expect(page.locator('h4, h3, h2')).to_be_visible()

    def test_create_project(self, page_base):
        page, base = page_base
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'E2E Project')
        page.fill('input[name="supernet"]', '172.16.0.0/12')
        page.fill('textarea[name="description"]', 'Created by E2E test')
        page.click('button[type="submit"]')
        # Should redirect to project detail
        expect(page).to_have_url(re.compile(r'/projects/'))
        expect(page.locator('body')).to_contain_text('E2E Project')

    def test_project_appears_on_homepage(self, page_base):
        page, base = page_base
        # Create project
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Homepage Project')
        page.fill('input[name="supernet"]', '10.10.0.0/16')
        page.click('button[type="submit"]')
        # Check homepage
        goto(page, base, '/')
        expect(page.locator('body')).to_contain_text('Homepage Project')

    def test_delete_project(self, page_base):
        page, base = page_base
        # Create
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Delete Me')
        page.fill('input[name="supernet"]', '10.20.0.0/16')
        page.click('button[type="submit"]')
        # Get project URL
        url = page.url
        pid = url.rstrip('/').split('/')[-1]
        # Delete
        page.on('dialog', lambda dialog: dialog.accept())
        page.click('form[action*="/delete"] button')
        # Wait for navigation and ensure we are not on the project page anymore
        page.wait_for_url(lambda u: f'/projects/{pid}' not in u)

    def test_invalid_supernet_shows_error(self, page_base):
        page, base = page_base
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Bad Project')
        page.fill('input[name="supernet"]', 'not-a-cidr')
        page.click('button[type="submit"]')
        expect(page.locator('.alert')).to_contain_text('Invalid')


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: Subnet management
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ESubnets:
    def _create_project(self, page: Page, base: str, name='Subnet Test', supernet='10.0.0.0/16'):
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', name)
        page.fill('input[name="supernet"]', supernet)
        page.click('button[type="submit"]')
        return page.url.rstrip('/').split('/')[-1]

    def test_add_subnet_manual(self, page_base):
        page, base = page_base
        pid = self._create_project(page, base, name='Subnet Manual')
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.0.0.0/24')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(f'/projects/{pid}'))
        expect(page.locator('body')).to_contain_text('10.0.0.0/24')

    def test_add_subnet_auto(self, page_base):
        page, base = page_base
        pid = self._create_project(page, base, name='Subnet Auto')
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'auto')
        page.fill('input[name="prefix_len"]', '24')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(f'/projects/{pid}'))

    def test_subnet_detail_visible(self, page_base):
        page, base = page_base
        pid = self._create_project(page, base, name='Subnet Detail')
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.0.1.0/24')
        page.click('button[type="submit"]')
        # Click into the subnet
        page.click('a:has-text("10.0.1.0/24")')
        expect(page).to_have_url(re.compile(r'/networks/'))
        expect(page.locator('body')).to_contain_text('10.0.1.0/24')

    def test_overlapping_subnet_rejected(self, page_base):
        page, base = page_base
        pid = self._create_project(page, base, name='Overlap Test')
        # Add first subnet
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.0.0.0/24')
        page.click('button[type="submit"]')
        # Try to add overlapping
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.0.0.0/25')
        page.click('button[type="submit"]')
        expect(page.locator('.alert')).to_contain_text('overlap')


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: IP allocation
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EIPAllocation:
    def _setup_subnet(self, page: Page, base: str):
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'IP Test')
        page.fill('input[name="supernet"]', '10.0.0.0/16')
        page.click('button[type="submit"]')
        pid = page.url.rstrip('/').split('/')[-1]
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.0.0.0/24')
        page.click('button[type="submit"]')
        # Get network id from the project page link
        page.click('a:has-text("10.0.0.0/24")')
        nid = page.url.rstrip('/').split('/')[-1]
        return pid, nid

    def test_allocate_ip(self, page_base):
        page, base = page_base
        pid, nid = self._setup_subnet(page, base)
        goto(page, base, f'/networks/{nid}/ip/add')
        page.fill('input[name="ip"]', '10.0.0.5')
        page.fill('input[name="hostname"]', 'web-01')
        page.click('button[type="submit"]')
        expect(page).to_have_url(re.compile(f'/networks/{nid}'))
        expect(page.locator('body')).to_contain_text('10.0.0.5')
        expect(page.locator('body')).to_contain_text('web-01')

    def test_next_available_button(self, page_base):
        page, base = page_base
        pid, nid = self._setup_subnet(page, base)
        goto(page, base, f'/networks/{nid}/ip/add')
        # Click "Next Available" button if present
        btn = page.locator('button:has-text("Next Available"), a:has-text("Next Available")')
        if btn.count() > 0:
            btn.first.click()
            ip_field = page.locator('input[name="ip"]')
            expect(ip_field).to_have_value(lambda v: v.startswith('10.0.0.'))

    def test_duplicate_ip_shows_warning(self, page_base):
        page, base = page_base
        pid, nid = self._setup_subnet(page, base)
        for _ in range(2):
            goto(page, base, f'/networks/{nid}/ip/add')
            page.fill('input[name="ip"]', '10.0.0.7')
            page.click('button[type="submit"]')
        expect(page.locator('.alert')).to_contain_text('already allocated')

    def test_edit_ip(self, page_base):
        page, base = page_base
        pid, nid = self._setup_subnet(page, base)
        goto(page, base, f'/networks/{nid}/ip/add')
        page.fill('input[name="ip"]', '10.0.0.8')
        page.fill('input[name="hostname"]', 'original')
        page.click('button[type="submit"]')
        # Click edit
        page.click('a[href*="/ip/10.0.0.8/edit"]')
        page.fill('input[name="hostname"]', 'updated-host')
        page.click('button[type="submit"]')
        expect(page.locator('body')).to_contain_text('updated-host')

    def test_delete_ip(self, page_base):
        page, base = page_base
        pid, nid = self._setup_subnet(page, base)
        goto(page, base, f'/networks/{nid}/ip/add')
        page.fill('input[name="ip"]', '10.0.0.9')
        page.click('button[type="submit"]')
        page.on('dialog', lambda d: d.accept())
        page.click('form[action*="/ip/10.0.0.9/delete"] button')
        expect(page.locator('body')).not_to_contain_text('10.0.0.9')


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: Labels
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ELabels:
    def test_add_global_label(self, page_base):
        page, base = page_base
        goto(page, base, '/labels')
        page.fill('input[name="label"]', 'E2E-GLOBAL')
        page.click('button[type="submit"]')
        expect(page.locator('body')).to_contain_text('E2E-GLOBAL')

    def test_label_appears_in_subnet_form(self, page_base):
        page, base = page_base
        # Add label
        goto(page, base, '/labels')
        page.fill('input[name="label"]', 'PROD-E2E')
        page.click('button[type="submit"]')
        # Create project and open subnet form
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Label Test')
        page.fill('input[name="supernet"]', '10.30.0.0/16')
        page.click('button[type="submit"]')
        pid = page.url.rstrip('/').split('/')[-1]
        goto(page, base, f'/projects/{pid}/subnet/add')
        expect(page.locator('body')).to_contain_text('PROD-E2E')

    def test_empty_label_rejected(self, page_base):
        page, base = page_base
        goto(page, base, '/labels')
        # Try to submit empty label
        page.fill('input[name="label"]', '')
        page.click('button[type="submit"]')
        expect(page.locator('.alert')).to_be_visible()


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: Subnet templates and pending slots
# ══════════════════════════════════════════════════════════════════════════════

class TestE2ESubnetTemplates:
    def _setup(self, page: Page, base: str):
        goto(page, base, '/projects/add')
        page.fill('input[name="name"]', 'Template E2E')
        page.fill('input[name="supernet"]', '10.40.0.0/16')
        page.click('button[type="submit"]')
        pid = page.url.rstrip('/').split('/')[-1]
        goto(page, base, f'/projects/{pid}/subnet/add')
        page.select_option('select[name="mode"]', 'manual')
        page.fill('input[name="cidr"]', '10.40.0.0/24')
        page.click('button[type="submit"]')
        page.click('a:has-text("10.40.0.0/24")')
        nid = page.url.rstrip('/').split('/')[-1]
        return pid, nid

    def test_create_subnet_template(self, page_base):
        page, base = page_base
        goto(page, base, '/templates/add')
        page.fill('input[name="name"]', 'E2E Template')
        # Use the rule builder JS or just fill rules_json hidden field
        page.evaluate("""
            document.getElementById('rulesJson') &&
            (document.getElementById('rulesJson').value = JSON.stringify([
                {type:'from_start', offset:1, role:'gateway', status:'reserved'}
            ]))
        """)
        page.click('button[type="submit"]')
        goto(page, base, '/templates')
        expect(page.locator('body')).to_contain_text('E2E Template')

    def test_apply_template_creates_pending_slots(self, page_base):
        page, base = page_base
        # Create template directly via API
        from db import new_id
        from ipam import save_template
        tmpl = {
            'id': new_id(), 'name': 'E2E-Slots', 'description': '',
            'rules': [
                {'type': 'from_start', 'offset': 1, 'role': 'gateway', 'status': 'reserved'},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_template(tmpl)

        pid, nid = self._setup(page, base)
        goto(page, base, f'/networks/{nid}/template')
        page.select_option('select[name="template_id"]', tmpl['id'])
        page.click('button[type="submit"]')
        # Should redirect to network detail showing pending slot
        expect(page).to_have_url(re.compile(f'/networks/{nid}'))
        expect(page.locator('body')).to_contain_text('pending')

    def test_confirm_pending_slot(self, page_base):
        page, base = page_base
        from db import new_id
        from ipam import save_template, set_pending_slots, save_project
        from db import new_id as _nid, r
        from ipam import save_network, project_nets_key

        # Create project + subnet directly
        pid = new_id()
        save_project({'id': pid, 'name': 'Slot Confirm', 'supernet': '10.50.0.0/16', 'description': ''})
        nid = new_id()
        net = {'id': nid, 'name': 'n', 'cidr': '10.50.0.0/24',
               'description': '', 'vlan': '', 'project_id': pid}
        save_network(net)
        r.sadd(project_nets_key(pid), nid)

        tmpl = {
            'id': new_id(), 'name': 'Confirm-Tmpl', 'description': '',
            'rules': [{'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'}],
            'scope': 'global', 'project_id': '',
        }
        save_template(tmpl)
        set_pending_slots(nid, tmpl['id'])

        goto(page, base, f'/networks/{nid}')
        # Click confirm on the phantom row
        confirm_btn = page.locator('form[action*="slots/confirm"] button')
        if confirm_btn.count() > 0:
            confirm_btn.first.click()
            expect(page.locator('body')).to_contain_text('10.50.0.1')


# ══════════════════════════════════════════════════════════════════════════════
# IPAM E2E: Pool query UI
# ══════════════════════════════════════════════════════════════════════════════

class TestE2EPoolQuery:
    def test_pool_page_loads(self, page_base):
        page, base = page_base
        goto(page, base, '/pool')
        expect(page).to_have_url(re.compile(r'/pool'))

    def test_pool_query_with_label(self, page_base):
        page, base = page_base
        from ipam import (save_project, save_network, add_labels_to_network,
                          add_global_label, project_nets_key)
        from db import r, new_id

        pid = new_id()
        save_project({'id': pid, 'name': 'Pool Query', 'supernet': '10.60.0.0/16', 'description': ''})
        nid = new_id()
        net = {'id': nid, 'name': 'n', 'cidr': '10.60.0.0/24',
               'description': '', 'vlan': '', 'project_id': pid}
        save_network(net)
        r.sadd(project_nets_key(pid), nid)
        add_global_label('E2E-POOL')
        add_labels_to_network(nid, ['E2E-POOL'])

        goto(page, base, '/pool?labels=E2E-POOL')
        expect(page.locator('body')).to_contain_text('10.60.0.0/24')

    def test_search_finds_ip(self, page_base):
        page, base = page_base
        from ipam import (save_project, save_network, save_ip, project_nets_key)
        from db import new_id
        from db import r

        pid = new_id()
        save_project({'id': pid, 'name': 'Search Test', 'supernet': '10.70.0.0/16', 'description': ''})
        nid = new_id()
        save_network({'id': nid, 'name': 'n', 'cidr': '10.70.0.0/24',
                      'description': '', 'vlan': '', 'project_id': pid})
        r.sadd(project_nets_key(pid), nid)
        save_ip({'ip': '10.70.0.42', 'hostname': 'search-target', 'description': '',
                 'status': 'allocated', 'network_id': nid})

        goto(page, base, '/search?q=search-target')
        expect(page.locator('body')).to_contain_text('10.70.0.42')
        expect(page.locator('body')).to_contain_text('search-target')
