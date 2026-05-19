"""
Unit tests for service requirements computation.
"""
import json
import pytest
import services_logic as svc
from services_logic import compute_service_rows


def _make_iface(iface_id='if-01', name='mgmt', sharing='interface'):
    return {'id': iface_id, 'name': name, 'sharing': sharing}


def _make_ne_type(tid='nt-01', name='Server'):
    return {'id': tid, 'name': name, 'kind': 'PNF', 'interfaces': []}


def _make_service(sid='svc-01', name='oob', cust='cust-1', fields=None):
    if fields is None:
        fields = [{'id': 'f-host', 'name': 'hostname', 'label': 'Hostname',
                   'field_type': 'text', 'required': True, 'scope_override': None}]
    svc.save_service({'id': sid, 'name': name, 'description': '',
                      'customer_id': cust, 'schema': fields})
    return sid


def _site(sid='site-01', name='London'):
    return {'id': sid, 'name': name}


def _pod(pid='pod-01', name='LON-POD-1'):
    return {'id': pid, 'name': pid}


@pytest.mark.unit
class TestComputeServiceRows:
    def test_no_services_no_rows(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    1, 'cust-1', [], {})
        assert rows == []

    def test_linked_service_emits_row(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        sid = _make_service()
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    1, 'cust-1', [], {})
        assert len(rows) == 1
        row = rows[0]
        assert row['kind'] == 'service'
        assert row['service_id'] == sid
        assert row['field_id'] == 'f-host'
        assert row['expected_count'] == 1
        assert row['resolved_count'] == 0
        assert row['missing'] == 1
        assert row['input_mode'] is None

    def test_wrong_customer_excluded(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        sid = _make_service(cust='cust-other')
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    1, 'cust-1', [], {})
        assert rows == []

    def test_empty_cust_id_includes_all(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        sid = _make_service(cust='cust-anything')
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    1, '', [], {})
        assert len(rows) == 1

    def test_dedup_by_key(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        sid = _make_service(fields=[
            {'id': 'f-dom', 'name': 'domain', 'label': 'Domain',
             'field_type': 'text', 'required': True, 'scope_override': 'project'},
        ])
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        shared = {}
        site = _site()
        pod  = _pod()
        # Emit once
        rows1 = compute_service_rows('proj-1', site, pod, ne_type, iface,
                                     1, '', [], shared)
        # Emit again (same scope key) — should be deduped
        rows2 = compute_service_rows('proj-1', site, pod, ne_type, iface,
                                     1, '', [], shared)
        assert len(rows1) == 1
        assert rows2 == []

    def test_project_scope_expected_1(self):
        ne_type = _make_ne_type()
        iface   = _make_iface(sharing='interface')
        sid = _make_service(fields=[
            {'id': 'f-dom', 'name': 'domain', 'label': 'Domain',
             'field_type': 'text', 'required': False, 'scope_override': 'project'},
        ])
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    10, '', [], {})
        assert rows[0]['expected_count'] == 1
        assert rows[0]['effective_scope'] == 'project'

    def test_interface_scope_expected_ne_count(self):
        ne_type = _make_ne_type()
        iface   = _make_iface(sharing='interface')
        sid = _make_service()
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    5, '', [], {})
        assert rows[0]['expected_count'] == 5
        assert rows[0]['effective_scope'] == 'interface'

    def test_resolved_count_from_bindings(self):
        ne_type = _make_ne_type()
        iface   = _make_iface()
        sid = _make_service()
        svc.link_service_to_iface(sid, ne_type['id'], iface['id'])

        inst = {
            'id': 'ni-01', 'ne_type_id': ne_type['id'], 'project_id': 'proj-1',
            'iface_bindings': {
                iface['id']: {
                    'service_values': {
                        sid: {'f-host': {'mode': 'text', 'value': 'srv-01'}}
                    }
                }
            }
        }
        rows = compute_service_rows('proj-1', _site(), _pod(), ne_type, iface,
                                    1, '', [inst], {})
        assert rows[0]['resolved_count'] == 1
        assert rows[0]['missing'] == 0
        assert rows[0]['input_mode'] == 'text'
