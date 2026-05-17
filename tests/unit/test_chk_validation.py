"""
Unit tests for CHK_ validation codes emitted by _check_checklists()
and for the default_bind_rule pre-population in add_ne_instance.
"""
import json
import pytest
import fakeredis
import hw_logic


@pytest.fixture(autouse=True)
def fake_r(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(hw_logic, 'r', fr)
    return fr


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ct(fr, ctid, *, name='T', attached_to='project', phase='post',
        action='Hello {{ undefined_var }}', expected='OK',
        vendor_hints=None, scope='global'):
    tmpl = {
        'id': ctid, 'name': name, 'phase': phase,
        'attached_to': attached_to, 'attachment_filter': {},
        'action_description': action, 'expected_result': expected,
        'vendor_hints': vendor_hints or {},
        'severity': 'standard', 'scope': scope,
    }
    fr.set(f'check_template:{ctid}', json.dumps(tmpl))
    fr.sadd('check_templates:index', ctid)
    return tmpl


def _cl(fr, cid, pid, *, status='in-progress', phase='post', label='w1',
        checks=None):
    cl = {
        'id': cid, 'project_id': pid, 'phase': phase,
        'deployment_label': label, 'status': status,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'generated_by': 'admin',
        'checks': checks or [],
        'signed_off_at': None, 'signed_off_by': None,
        'artifact_id': None, 'supersedes_checklist_id': None,
    }
    fr.set(f'checklist:{cid}', json.dumps(cl))
    fr.lpush(f'project:{pid}:checklists', cid)
    return cl


def _run(fr, pid='p1'):
    issues = []
    hw_logic._check_checklists(pid, issues)
    return issues


# ── CHK_TEMPLATE_BAD_JINJA ────────────────────────────────────────────────────

@pytest.mark.unit
def test_bad_jinja_action_description(fake_r):
    _ct(fake_r, 'ct1', action='{% for %}broken', expected='OK')
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_TEMPLATE_BAD_JINJA' for i in issues)


@pytest.mark.unit
def test_bad_jinja_expected_result(fake_r):
    _ct(fake_r, 'ct1', action='Good {{ x }}', expected='{% if %}bad')
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_TEMPLATE_BAD_JINJA' for i in issues)


@pytest.mark.unit
def test_valid_jinja_no_bad_jinja_code(fake_r):
    _ct(fake_r, 'ct1', action='Hello {{ ne.name }}', expected='It works.')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_TEMPLATE_BAD_JINJA' for i in issues)


@pytest.mark.unit
def test_empty_template_body_no_bad_jinja(fake_r):
    _ct(fake_r, 'ct1', action='', expected='')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_TEMPLATE_BAD_JINJA' for i in issues)


# ── CHK_TEMPLATE_NO_SUBJECT ───────────────────────────────────────────────────

@pytest.mark.unit
def test_no_subject_ne_type_no_instances(fake_r):
    _ct(fake_r, 'ct1', attached_to='ne_type', action='x', expected='y')
    # project has no NE instances (scard = 0)
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_TEMPLATE_NO_SUBJECT' for i in issues)


@pytest.mark.unit
def test_no_subject_cable_no_cables(fake_r):
    _ct(fake_r, 'ct1', attached_to='cable', action='x', expected='y')
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_TEMPLATE_NO_SUBJECT' for i in issues)


@pytest.mark.unit
def test_no_subject_hw_template_no_instances(fake_r):
    _ct(fake_r, 'ct1', attached_to='hw_template', action='x', expected='y')
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_TEMPLATE_NO_SUBJECT' for i in issues)


@pytest.mark.unit
def test_no_subject_project_always_has_subject(fake_r):
    _ct(fake_r, 'ct1', attached_to='project', action='x', expected='y')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_TEMPLATE_NO_SUBJECT' for i in issues)


@pytest.mark.unit
def test_no_subject_suppressed_when_instances_exist(fake_r):
    _ct(fake_r, 'ct1', attached_to='ne_type', action='x', expected='y')
    fake_r.sadd('project:p1:ne_instances', 'n1')  # at least one NE instance
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_TEMPLATE_NO_SUBJECT' for i in issues)


# ── CHK_LIST_INCOMPLETE ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_list_incomplete_emitted_for_inprogress_with_pending(fake_r):
    checks = [{'id': 'c1', 'status': 'pending', 'severity': 'standard'}]
    _cl(fake_r, 'cl1', 'p1', status='in-progress', checks=checks)
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_LIST_INCOMPLETE' for i in issues)
    inc = next(i for i in issues if i['code'] == 'CHK_LIST_INCOMPLETE')
    assert inc['context']['pending'] == 1


@pytest.mark.unit
def test_list_incomplete_not_emitted_for_all_pass(fake_r):
    checks = [{'id': 'c1', 'status': 'pass', 'severity': 'standard'}]
    _cl(fake_r, 'cl1', 'p1', status='in-progress', checks=checks)
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_LIST_INCOMPLETE' for i in issues)


@pytest.mark.unit
def test_list_incomplete_not_emitted_for_draft(fake_r):
    checks = [{'id': 'c1', 'status': 'pending', 'severity': 'standard'}]
    _cl(fake_r, 'cl1', 'p1', status='draft', checks=checks)
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_LIST_INCOMPLETE' for i in issues)


# ── CHK_LIST_FAILED_CRITICAL ──────────────────────────────────────────────────

@pytest.mark.unit
def test_failed_critical_emitted_on_signed_off_checklist(fake_r):
    checks = [{'id': 'c1', 'status': 'fail', 'severity': 'critical'}]
    _cl(fake_r, 'cl1', 'p1', status='signed-off', checks=checks)
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_LIST_FAILED_CRITICAL' for i in issues)
    fc = next(i for i in issues if i['code'] == 'CHK_LIST_FAILED_CRITICAL')
    assert fc['severity'] == 'error'
    assert fc['context']['failed_critical'] == 1


@pytest.mark.unit
def test_failed_critical_not_emitted_when_only_standard_fails(fake_r):
    checks = [{'id': 'c1', 'status': 'fail', 'severity': 'standard'}]
    _cl(fake_r, 'cl1', 'p1', status='signed-off', checks=checks)
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_LIST_FAILED_CRITICAL' for i in issues)


@pytest.mark.unit
def test_failed_critical_not_emitted_for_inprogress(fake_r):
    checks = [{'id': 'c1', 'status': 'fail', 'severity': 'critical'}]
    _cl(fake_r, 'cl1', 'p1', status='in-progress', checks=checks)
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_LIST_FAILED_CRITICAL' for i in issues)


# ── CHK_VENDOR_HINT_MISSING ───────────────────────────────────────────────────

def _hw_instance(fr, iid, tid, pid='p1'):
    inst = {'id': iid, 'template_id': tid, 'project_id': pid,
            'asset_tag': iid, 'port_overrides': {}, 'labels': []}
    fr.set(f'hw:instance:{iid}', json.dumps(inst))
    fr.sadd(f'project:{pid}:hw:instances', iid)
    return inst


def _hw_template(fr, tid, vendor='cisco'):
    tmpl = {'id': tid, 'name': 'Router', 'vendor': vendor,
            'ports': [], 'category': 'router'}
    fr.set(f'hw:template:{tid}', json.dumps(tmpl))
    return tmpl


@pytest.mark.unit
def test_vendor_hint_missing_when_vendor_not_in_hints(fake_r):
    _ct(fake_r, 'ct1', vendor_hints={'junos': 'show interfaces'})
    _hw_template(fake_r, 't1', vendor='cisco')
    _hw_instance(fake_r, 'i1', 't1')
    issues = _run(fake_r)
    assert any(i['code'] == 'CHK_VENDOR_HINT_MISSING' for i in issues)
    hint_issue = next(i for i in issues if i['code'] == 'CHK_VENDOR_HINT_MISSING')
    assert hint_issue['context']['vendor'] == 'cisco'


@pytest.mark.unit
def test_vendor_hint_not_missing_when_covered(fake_r):
    _ct(fake_r, 'ct1', vendor_hints={'cisco': 'show ip int brief'})
    _hw_template(fake_r, 't1', vendor='cisco')
    _hw_instance(fake_r, 'i1', 't1')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_VENDOR_HINT_MISSING' for i in issues)


@pytest.mark.unit
def test_vendor_hint_case_insensitive(fake_r):
    _ct(fake_r, 'ct1', vendor_hints={'Cisco': 'show ip int brief'})
    _hw_template(fake_r, 't1', vendor='CISCO')
    _hw_instance(fake_r, 'i1', 't1')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_VENDOR_HINT_MISSING' for i in issues)


@pytest.mark.unit
def test_no_vendor_hints_no_chk_vendor_issue(fake_r):
    _ct(fake_r, 'ct1', vendor_hints={})  # no hints → skip check
    _hw_template(fake_r, 't1', vendor='cisco')
    _hw_instance(fake_r, 'i1', 't1')
    issues = _run(fake_r)
    assert not any(i['code'] == 'CHK_VENDOR_HINT_MISSING' for i in issues)


# ── No templates / no checklists — no crash ───────────────────────────────────

@pytest.mark.unit
def test_empty_project_no_issues(fake_r):
    issues = _run(fake_r)
    assert issues == []
