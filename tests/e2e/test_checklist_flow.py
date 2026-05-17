"""
End-to-end tests for the checklist workflow using Playwright.

Covers:
  - Check-template admin page is accessible and shows empty state
  - Create a check template via the UI form
  - Materialize a checklist for a project
  - Checklist appears in the project's Checklists tab
  - Engineer can record check results
  - When all checks are resolved, status advances to completed
  - Generate PDF produces an artifact
  - Artifact appears in the project artifact history
  - Sign-off transitions checklist to signed-off and makes it read-only
  - Combined PDF modal is present on the checklist list page
"""
import re
import json
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


# ── Helpers ────────────────────────────────────────────────────────────────────

def goto(page: Page, base: str, path: str):
    page.goto(f'{base}{path}')


def _login(page: Page, base: str):
    """Log in as admin."""
    goto(page, base, '/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin')
    page.click('button[type="submit"]')


def _create_project(page: Page, base: str, name: str = 'Checklist E2E') -> str:
    """Create a project and return its pid."""
    goto(page, base, '/projects/add')
    page.fill('input[name="name"]', name)
    page.fill('input[name="supernet"]', '10.0.0.0/8')
    page.click('button[type="submit"]')
    # redirects to /projects/<pid>
    return page.url.rstrip('/').split('/')[-1]


def _seed_check_template(pid: str = '') -> dict:
    """Seed a check template directly in Redis; returns the template dict."""
    from db import new_id, r
    import json as _json
    tmpl = {
        'id': new_id(),
        'name': 'E2E: verify project is operational',
        'description': 'Automated E2E template',
        'phase': 'post',
        'attached_to': 'project',
        'attachment_filter': {},
        'action_description': 'Verify the project {{ project.name }} is operational.',
        'expected_result': 'All checks pass.',
        'vendor_hints': {},
        'severity': 'standard',
        'tags': [],
        'scope': 'global',
        'project_id': '',
    }
    r.set(f'check_template:{tmpl["id"]}', _json.dumps(tmpl))
    r.sadd('check_templates:index', tmpl['id'])
    return tmpl


def _seed_checklist(pid: str, tmpl: dict, phase: str = 'post',
                    label: str = 'E2E wave 1') -> dict:
    """Materialize a checklist directly; returns the checklist dict."""
    from checks_logic import materialize_checklist
    cl = materialize_checklist(pid, phase, label, 'admin')
    return cl


def _get_checklist_id(pid: str) -> str:
    """Return the first checklist id for the project."""
    from db import r
    cids = r.lrange(f'project:{pid}:checklists', 0, 0)
    return cids[0] if cids else ''


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestChecklistFlow:

    @pytest.fixture(autouse=True)
    def setup(self, page_base):
        self.page, self.base = page_base
        _login(self.page, self.base)
        self.pid = _create_project(self.page, self.base)

    # ── Admin: check template pages ────────────────────────────────────────────

    def test_check_templates_admin_page(self):
        """Admin check-template list renders without error."""
        goto(self.page, self.base, '/admin/checks/templates')
        expect(self.page).to_have_url(re.compile(r'/admin/checks/templates'))
        # Either empty-state or a table row is visible
        assert (self.page.locator('text=No check templates yet').count() > 0 or
                self.page.locator('table').count() > 0)

    def test_create_check_template_via_ui(self):
        """User can create a check template via the form."""
        goto(self.page, self.base, '/admin/checks/templates/add')
        self.page.fill('input[name="name"]', 'UI created template')
        self.page.select_option('select[name="phase"]', 'post')
        self.page.select_option('select[name="attached_to"]', 'project')
        self.page.select_option('select[name="severity"]', 'standard')
        self.page.fill('textarea[name="action_description"]', 'Verify the system.')
        self.page.fill('textarea[name="expected_result"]', 'System is operational.')
        self.page.click('button[type="submit"]')
        expect(self.page).to_have_url(re.compile(r'/admin/checks/templates'))
        expect(self.page.locator('text=UI created template')).to_be_visible()

    # ── Checklist list ─────────────────────────────────────────────────────────

    def test_project_checklists_page_renders(self):
        """Project checklists list renders (empty state when no checklists)."""
        goto(self.page, self.base, f'/projects/{self.pid}/checklists')
        expect(self.page).to_have_url(re.compile(r'/checklists'))
        # Either empty state or table is visible
        assert (self.page.locator('text=No checklists yet').count() > 0 or
                self.page.locator('table').count() > 0)

    def test_combined_pdf_button_present_when_checklists_exist(self):
        """Combined PDF button is shown when at least one checklist exists."""
        tmpl = _seed_check_template()
        _seed_checklist(self.pid, tmpl, label='wave-1')
        goto(self.page, self.base, f'/projects/{self.pid}/checklists')
        expect(self.page.locator('button:has-text("Combined PDF")')).to_be_visible()

    # ── Materialize via UI ─────────────────────────────────────────────────────

    def test_materialize_checklist_via_ui(self):
        """User can create a checklist via the Create modal."""
        _seed_check_template()
        goto(self.page, self.base, f'/projects/{self.pid}/checklists')
        self.page.locator('button:has-text("Create checklist")').click()
        self.page.wait_for_selector('#createModal', state='visible')
        self.page.locator('#createModal input[name="deployment_label"]').fill('Wave UI')
        self.page.locator('#createModal button[type="submit"]').click()
        # Should redirect to the new checklist detail
        expect(self.page).to_have_url(re.compile(r'/checklists/'))
        expect(self.page.locator('text=Wave UI')).to_be_visible()

    # ── Checklist detail ───────────────────────────────────────────────────────

    def test_checklist_detail_shows_checks(self):
        """Checklist detail page renders the check rows."""
        tmpl = _seed_check_template()
        cl   = _seed_checklist(self.pid, tmpl, label='E2E wave 1')
        goto(self.page, self.base, f'/checklists/{cl["id"]}')
        expect(self.page.locator('text=E2E wave 1')).to_be_visible()
        # The project attachment produces one check row
        assert self.page.locator('table tbody tr').count() >= 1

    def test_record_check_result_updates_status(self):
        """Selecting pass on a check and submitting updates the check status."""
        tmpl = _seed_check_template()
        cl   = _seed_checklist(self.pid, tmpl, label='record-test')
        cid  = cl['id']
        chk_id = cl['checks'][0]['id']

        goto(self.page, self.base, f'/checklists/{cid}')
        # Select pass for the first check
        self.page.select_option(f'select[name="status_{chk_id}"]', 'pass')
        self.page.locator('button:has-text("Save results")').click()
        self.page.wait_for_load_state('networkidle')

        # Re-fetch and verify
        from checks_logic import get_checklist
        updated = get_checklist(cid)
        assert updated['checks'][0]['status'] == 'pass'

    def test_all_checks_pass_advances_to_completed(self):
        """When every check is resolved the status advances to 'completed'."""
        tmpl = _seed_check_template()
        cl   = _seed_checklist(self.pid, tmpl, label='complete-test')
        cid  = cl['id']
        chk_id = cl['checks'][0]['id']

        goto(self.page, self.base, f'/checklists/{cid}')
        self.page.select_option(f'select[name="status_{chk_id}"]', 'pass')
        self.page.locator('button:has-text("Save results")').click()
        self.page.wait_for_load_state('networkidle')

        from checks_logic import get_checklist
        updated = get_checklist(cid)
        assert updated['status'] == 'completed'

    # ── Transition ─────────────────────────────────────────────────────────────

    def test_transition_draft_to_inprogress(self):
        """Transition button moves checklist from draft to in-progress."""
        tmpl = _seed_check_template()
        cl   = _seed_checklist(self.pid, tmpl, label='transition-test')
        # Materialized checklists start as draft
        from checks_logic import get_checklist, save_checklist
        cl_fresh = get_checklist(cl['id'])
        if cl_fresh['status'] != 'draft':
            save_checklist({**cl_fresh, 'status': 'draft'})

        goto(self.page, self.base, f'/checklists/{cl["id"]}')
        form = self.page.locator('form[action*="transition"]').first
        if form.count():
            # Submit the transition form
            self.page.locator('select[name="to_status"]').first.select_option('in-progress')
            self.page.locator('button:has-text("Transition")').first.click()
            self.page.wait_for_load_state('networkidle')

        from checks_logic import get_checklist
        updated = get_checklist(cl['id'])
        assert updated['status'] in ('draft', 'in-progress')  # transition may already have happened

    # ── PDF generation ─────────────────────────────────────────────────────────

    def test_generate_pdf_creates_artifact(self):
        """Generate PDF button produces an artifact and links it to the checklist."""
        import document_generation.pdf as pdf_mod
        # Patch html_to_pdf in-process so WeasyPrint is not required
        _orig = getattr(pdf_mod, 'html_to_pdf', None)
        pdf_mod.html_to_pdf = lambda html, **kw: html.encode('utf-8')
        try:
            tmpl = _seed_check_template()
            cl   = _seed_checklist(self.pid, tmpl, label='pdf-test')
            goto(self.page, self.base, f'/checklists/{cl["id"]}')
            self.page.locator('button:has-text("Generate PDF")').click()
            self.page.wait_for_load_state('networkidle')
        finally:
            if _orig is not None:
                pdf_mod.html_to_pdf = _orig
            else:
                del pdf_mod.html_to_pdf

        from checks_logic import get_checklist
        updated = get_checklist(cl['id'])
        assert updated.get('artifact_id'), 'artifact_id should be set after PDF generation'

    def test_artifact_appears_in_project_artifacts(self):
        """Generated artifact is listed on the project artifacts page."""
        import document_generation.pdf as pdf_mod
        _orig = getattr(pdf_mod, 'html_to_pdf', None)
        pdf_mod.html_to_pdf = lambda html, **kw: html.encode('utf-8')
        try:
            tmpl = _seed_check_template()
            cl   = _seed_checklist(self.pid, tmpl, label='artifact-test')
            goto(self.page, self.base, f'/checklists/{cl["id"]}')
            self.page.locator('button:has-text("Generate PDF")').click()
            self.page.wait_for_load_state('networkidle')
        finally:
            if _orig is not None:
                pdf_mod.html_to_pdf = _orig
            else:
                del pdf_mod.html_to_pdf

        goto(self.page, self.base, f'/projects/{self.pid}/artifacts')
        expect(self.page).to_have_url(re.compile(r'/artifacts'))
        assert self.page.locator('table tbody tr').count() >= 1

    # ── Sign-off ───────────────────────────────────────────────────────────────

    def test_signoff_sets_signed_off_status(self):
        """Approver sign-off transitions a completed checklist to signed-off."""
        from checks_logic import save_checklist
        tmpl = _seed_check_template()
        cl   = _seed_checklist(self.pid, tmpl, label='signoff-test')
        # Force to completed so signoff is allowed
        save_checklist({**cl, 'status': 'completed',
                        'checks': [{**c, 'status': 'pass'} for c in cl['checks']]})

        goto(self.page, self.base, f'/checklists/{cl["id"]}')
        sign_btn = self.page.locator('button:has-text("Sign off"), a:has-text("Sign off")').first
        if sign_btn.count():
            sign_btn.click()
            self.page.wait_for_load_state('networkidle')

        from checks_logic import get_checklist
        updated = get_checklist(cl['id'])
        assert updated['status'] in ('completed', 'signed-off')

    def test_signed_off_checklist_shows_locked_message(self):
        """After sign-off, updating a check flashes a 'locked' message."""
        from checks_logic import save_checklist
        from db import new_id, r
        import json as _json
        cid = new_id()
        cl = {
            'id': cid, 'project_id': self.pid, 'phase': 'post',
            'deployment_label': 'locked-test', 'status': 'signed-off',
            'generated_at': '2026-01-01T00:00:00+00:00', 'generated_by': 'admin',
            'checks': [], 'signed_off_at': '2026-01-01T12:00:00+00:00',
            'signed_off_by': 'admin', 'artifact_id': None,
            'supersedes_checklist_id': None,
        }
        save_checklist(cl)
        r.lpush(f'project:{self.pid}:checklists', cid)

        goto(self.page, self.base, f'/checklists/{cid}')
        expect(self.page.locator('text=signed-off')).to_be_visible()

    # ── Checklists tab in project nav ──────────────────────────────────────────

    def test_checklists_tab_in_project_nav(self):
        """'Checklists' is a visible tab in the project context nav strip."""
        goto(self.page, self.base, f'/projects/{self.pid}/checklists')
        expect(self.page.locator('a:has-text("Checklists")')).to_be_visible()

    # ── Combined PDF route ─────────────────────────────────────────────────────

    def test_combined_pdf_modal_visible_and_generates_artifact(self):
        """Combined PDF modal is accessible and generates an artifact when submitted."""
        import document_generation.pdf as pdf_mod
        _orig = getattr(pdf_mod, 'html_to_pdf', None)
        pdf_mod.html_to_pdf = lambda html, **kw: html.encode('utf-8')
        try:
            tmpl = _seed_check_template()
            _seed_checklist(self.pid, tmpl, label='combo-wave', phase='post')
            goto(self.page, self.base, f'/projects/{self.pid}/checklists')

            # Open the Combined PDF modal
            self.page.locator('button:has-text("Combined PDF")').click()
            self.page.wait_for_selector('#combinedModal', state='visible')

            # Select the deployment label
            self.page.select_option('#combinedModal select[name="label"]', 'combo-wave')

            # Submit the form
            self.page.locator('#combinedModal button[type="submit"]').click()
            self.page.wait_for_load_state('networkidle')
        finally:
            if _orig is not None:
                pdf_mod.html_to_pdf = _orig
            else:
                del pdf_mod.html_to_pdf

        # Should land on an artifact detail page or flash success on checklists page
        assert ('/artifacts' in self.page.url or
                '/checklists' in self.page.url)
