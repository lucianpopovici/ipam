"""Unit tests for the unassigned-instance health check."""
import pytest
from db import new_id
from ipam import save_project
from hw_logic import save_hw_template, save_hw_instance
from health_logic import _check_unassigned_instances

pytestmark = pytest.mark.unit


def _setup():
    pid = new_id()
    save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
    tmpl = {
        'id': new_id(), 'name': 'Server-1U', 'vendor': 'ACME', 'model': 'X1',
        'category': 'server', 'form_factor': '19"', 'u_size': 1,
        'cable_type': '', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    return pid, tmpl


def _inst(pid, tmpl, tag, site_id=''):
    inst = {
        'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
        'asset_tag': tag, 'serial': '', 'status': 'in-stock',
        'site_id': site_id, 'location': {}, 'port_overrides': {},
    }
    save_hw_instance(inst)
    return inst


def test_flags_instance_without_site():
    pid, tmpl = _setup()
    _inst(pid, tmpl, 'NO-SITE')
    issues = _check_unassigned_instances(pid)
    assert len(issues) == 1
    assert issues[0]['type'] == 'unassigned_instance'
    assert issues[0]['severity'] == 'warning'
    assert 'NO-SITE' in issues[0]['message']
    assert issues[0]['context']['asset_tag'] == 'NO-SITE'


def test_assigned_instance_not_flagged():
    pid, tmpl = _setup()
    _inst(pid, tmpl, 'HAS-SITE', site_id='site-123')
    assert _check_unassigned_instances(pid) == []


def test_only_unassigned_flagged_among_mixed():
    pid, tmpl = _setup()
    _inst(pid, tmpl, 'A', site_id='site-1')
    _inst(pid, tmpl, 'B')
    _inst(pid, tmpl, 'C')
    tags = {i['context']['asset_tag'] for i in _check_unassigned_instances(pid)}
    assert tags == {'B', 'C'}
