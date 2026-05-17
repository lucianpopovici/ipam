"""
Unit tests for checks_logic.materialize_checklist and related helpers.
"""
import json
import pytest
import fakeredis


@pytest.fixture()
def fake_r(monkeypatch):
    fr = fakeredis.FakeRedis(decode_responses=True)
    import db
    import checks_logic
    monkeypatch.setattr(db, 'r', fr)
    monkeypatch.setattr(checks_logic, 'r', fr)
    return fr


PID = 'proj-test'


def _seed_project(fr):
    proj = {'id': PID, 'name': 'TestProj', 'supernet': '10.0.0.0/8', 'description': ''}
    fr.set(f'project:{PID}', json.dumps(proj))
    fr.sadd('projects:index', PID)
    return proj


def _seed_ne_inst(fr, ne_type_id, iface_bindings=None):
    from db import new_id
    ne_type = {
        'id': ne_type_id, 'name': 'Router', 'kind': 'PNF',
        'interfaces': [
            {'id': 'iface-mgmt', 'name': 'mgmt', 'labels': ['mgmt'],
             'sharing': 'ne', 'ipv4': None, 'ipv6': None, 'params': {}},
            {'id': 'iface-uplink', 'name': 'uplink', 'labels': ['data'],
             'sharing': 'ne', 'ipv4': None, 'ipv6': None, 'params': {}},
        ],
        'labels': [], 'params': {}, 'scope': 'global', 'project_id': '',
        'description': '',
    }
    fr.set(f'ne_type:{ne_type_id}', json.dumps(ne_type))
    fr.sadd('ne_types:index', ne_type_id)

    nid = new_id()
    inst = {
        'id': nid, 'name': 'pe-01', 'ne_type_id': ne_type_id,
        'project_id': PID, 'description': '', 'labels': [], 'params': {},
        'iface_bindings': iface_bindings or {},
    }
    fr.set(f'ne_inst:{nid}', json.dumps(inst))
    fr.sadd('ne_instances:index', nid)
    fr.sadd(f'project:{PID}:ne_instances', nid)
    return inst, ne_type


def _seed_cable(fr):
    from db import new_id
    cid = new_id()
    cable = {
        'id': cid, 'asset_tag': 'cab-001',
        'end_a': {'instance_id': 'inst-a', 'port_id': 'p1'},
        'end_b': {'instance_id': 'inst-b', 'port_id': 'p2'},
        'template': None,
    }
    fr.set(f'hw:cable:{cid}', json.dumps(cable))
    fr.sadd('hw:cables:index', cid)
    fr.sadd(f'project:{PID}:hw:cables', cid)
    return cable


def _mk_tmpl(fr, attached_to, phase='post', attachment_filter=None,
             action='Check {{ ne.name }}', expected='OK', ctid=None):
    from db import new_id
    from checks_logic import save_check_template
    ctid = ctid or new_id()
    tmpl = {
        'id': ctid, 'name': f'tmpl-{ctid}',
        'description': '', 'phase': phase,
        'attached_to': attached_to,
        'attachment_filter': attachment_filter or {},
        'action_description': action,
        'expected_result': expected,
        'vendor_hints': {},
        'severity': 'standard', 'tags': [],
        'scope': 'global', 'project_id': '',
    }
    save_check_template(tmpl)
    return tmpl


# ── resolve_subjects ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_project_subject_is_singleton(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'project', 'attachment_filter': {}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 1
    assert subjects[0]['_type'] == 'project'


@pytest.mark.unit
def test_ne_type_subject_per_instance(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    _seed_ne_inst(fake_r, 'ne-type-A')
    _seed_ne_inst(fake_r, 'ne-type-A')
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'ne_type', 'attachment_filter': {}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 2
    assert all(s['_type'] == 'ne_type' for s in subjects)


@pytest.mark.unit
def test_ne_type_filter_by_type_id(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    _seed_ne_inst(fake_r, 'ne-type-A')
    _seed_ne_inst(fake_r, 'ne-type-B')
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'ne_type', 'attachment_filter': {'ne_type_id': 'ne-type-A'}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 1
    assert subjects[0]['ne_type']['id'] == 'ne-type-A'


@pytest.mark.unit
def test_ne_iface_subject_per_bound_iface(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    bindings = {
        'iface-mgmt': {'bind_mode': 'single',
                        'ports': [{'hw_instance_id': 'hw1', 'port_id': 'p1',
                                   'role': 'primary', 'bucket': []}]},
    }
    _seed_ne_inst(fake_r, 'ne-type-A', iface_bindings=bindings)
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'ne_iface', 'attachment_filter': {}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 1
    assert subjects[0]['iface']['id'] == 'iface-mgmt'


@pytest.mark.unit
def test_ne_iface_filter_by_label(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    bindings = {
        'iface-mgmt':   {'bind_mode': 'single',
                          'ports': [{'hw_instance_id': 'h', 'port_id': 'p',
                                     'role': 'primary', 'bucket': []}]},
        'iface-uplink': {'bind_mode': 'single',
                          'ports': [{'hw_instance_id': 'h', 'port_id': 'q',
                                     'role': 'primary', 'bucket': []}]},
    }
    _seed_ne_inst(fake_r, 'ne-type-A', iface_bindings=bindings)
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'ne_iface',
            'attachment_filter': {'iface_labels_any': ['mgmt']}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 1
    assert subjects[0]['iface']['id'] == 'iface-mgmt'


@pytest.mark.unit
def test_ne_iface_no_subjects_when_unbound(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    _seed_ne_inst(fake_r, 'ne-type-A', iface_bindings={})  # no bindings
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'ne_iface', 'attachment_filter': {}}
    subjects = resolve_subjects(tmpl, ctx)
    assert subjects == []


@pytest.mark.unit
def test_cable_subject_per_cable(fake_r):
    from checks_logic import resolve_subjects, build_context_snapshot
    _seed_project(fake_r)
    _seed_cable(fake_r)
    _seed_cable(fake_r)
    ctx = build_context_snapshot(PID)
    tmpl = {'attached_to': 'cable', 'attachment_filter': {}}
    subjects = resolve_subjects(tmpl, ctx)
    assert len(subjects) == 2
    assert all(s['_type'] == 'cable' for s in subjects)


# ── Jinja2 rendering ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_jinja_renders_ne_name(fake_r):
    from checks_logic import materialize_checklist
    _seed_project(fake_r)
    bindings = {'iface-mgmt': {'bind_mode': 'single',
                                'ports': [{'hw_instance_id': 'h', 'port_id': 'p',
                                           'role': 'primary', 'bucket': []}]}}
    _seed_ne_inst(fake_r, 'ne-type-A', iface_bindings=bindings)
    _mk_tmpl(fake_r, 'ne_iface', phase='post',
             action='Check {{ ne.name }} / {{ iface.name }}',
             expected='{{ ne.name }} up')

    cl = materialize_checklist(PID, 'post', 'wave1')
    assert len(cl['checks']) == 1
    check = cl['checks'][0]
    assert 'pe-01' in check['action_text']
    assert 'mgmt'  in check['action_text']
    assert 'pe-01' in check['expected_text']


@pytest.mark.unit
def test_jinja_bad_template_doesnt_crash(fake_r):
    """A Jinja2 syntax error in a template falls back to the raw string."""
    from checks_logic import materialize_checklist
    _seed_project(fake_r)
    _mk_tmpl(fake_r, 'project', phase='post', action='{% for %}bad{%endfor%}')
    cl = materialize_checklist(PID, 'post', 'wave1')
    assert len(cl['checks']) == 1
    # Should not raise; raw string preserved
    assert cl['checks'][0]['action_text']


# ── Full materialization ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_materialize_creates_checklist_record(fake_r):
    from checks_logic import materialize_checklist, get_checklist
    _seed_project(fake_r)
    _mk_tmpl(fake_r, 'project', phase='pre')

    cl = materialize_checklist(PID, 'pre', 'My Deployment')
    assert cl['project_id'] == PID
    assert cl['phase'] == 'pre'
    assert cl['status'] == 'draft'
    assert cl['deployment_label'] == 'My Deployment'
    assert len(cl['checks']) == 1

    # Must be persisted
    assert get_checklist(cl['id']) is not None


@pytest.mark.unit
def test_materialize_stored_newest_first(fake_r):
    from checks_logic import materialize_checklist, project_checklists
    _seed_project(fake_r)
    _mk_tmpl(fake_r, 'project', phase='post')

    cl1 = materialize_checklist(PID, 'post', 'wave1')
    cl2 = materialize_checklist(PID, 'post', 'wave2')

    cls = project_checklists(PID)
    assert cls[0]['id'] == cl2['id']   # newest first
    assert cls[1]['id'] == cl1['id']


@pytest.mark.unit
def test_only_matching_phase_included(fake_r):
    from checks_logic import materialize_checklist
    _seed_project(fake_r)
    _mk_tmpl(fake_r, 'project', phase='pre',  action='Pre check')
    _mk_tmpl(fake_r, 'project', phase='post', action='Post check')

    pre_cl  = materialize_checklist(PID, 'pre',  'pre-wave')
    post_cl = materialize_checklist(PID, 'post', 'post-wave')

    assert len(pre_cl['checks'])  == 1
    assert len(post_cl['checks']) == 1
    assert 'Pre'  in pre_cl['checks'][0]['action_text']
    assert 'Post' in post_cl['checks'][0]['action_text']


@pytest.mark.unit
def test_filter_iface_labels_any_narrows_subjects(fake_r):
    from checks_logic import materialize_checklist
    _seed_project(fake_r)
    bindings = {
        'iface-mgmt':   {'bind_mode': 'single',
                          'ports': [{'hw_instance_id': 'h', 'port_id': 'p1',
                                     'role': 'primary', 'bucket': []}]},
        'iface-uplink': {'bind_mode': 'single',
                          'ports': [{'hw_instance_id': 'h', 'port_id': 'p2',
                                     'role': 'primary', 'bucket': []}]},
    }
    _seed_ne_inst(fake_r, 'ne-type-A', iface_bindings=bindings)
    # Template only matches mgmt ifaces
    _mk_tmpl(fake_r, 'ne_iface', phase='post',
             attachment_filter={'iface_labels_any': ['mgmt']})

    cl = materialize_checklist(PID, 'post', 'wave1')
    assert len(cl['checks']) == 1
    assert cl['checks'][0]['subject']['iface_id'] == 'iface-mgmt'


@pytest.mark.unit
def test_vendor_hint_picked_correctly(fake_r):
    from db import new_id
    from checks_logic import save_check_template, materialize_checklist
    _seed_project(fake_r)
    ctid = new_id()
    tmpl = {
        'id': ctid, 'name': 'hint-test', 'description': '', 'phase': 'post',
        'attached_to': 'project', 'attachment_filter': {},
        'action_description': 'Check it', 'expected_result': 'OK',
        'vendor_hints': {'cisco_ios': 'show ver', 'junos': 'show ver junos'},
        'severity': 'standard', 'tags': [], 'scope': 'global', 'project_id': '',
    }
    save_check_template(tmpl)
    cl = materialize_checklist(PID, 'post', 'w')
    # Project attachment has no hw context → fallback to first hint
    assert cl['checks'][0]['vendor_hint_text'] in ('show ver', 'show ver junos')
