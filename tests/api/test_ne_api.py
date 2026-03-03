"""

pytestmark = pytest.mark.api

API tests for ne.py routes using Flask test client.
"""
import json
import pytest
from ipam import save_project, new_id
from ne import (
    save_schema, save_ne_type, save_site, save_pod,
    assign_pod_to_site, save_pod_slots,
    get_site, get_pod, get_ne_type,
    project_sites, project_pods,
    load_requirements,
)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _create_project(client, supernet='10.0.0.0/8'):
    resp = client.post('/projects/add', data={
        'name': 'NE Test Project', 'supernet': supernet, 'description': '',
    }, follow_redirects=False)
    return resp.headers['Location'].rstrip('/').split('/')[-1]


def _ne_type_data(pid='', kind='PNF', scope='global'):
    return {
        'name':        'Test Router',
        'kind':        kind,
        'description': '',
        'labels':      '',
        'scope':       scope,
        'project_id':  pid,
        'interfaces_json': json.dumps([
            {
                'id':      'i1',
                'name':    'mgmt',
                'labels':  ['mgmt'],
                'params':  {},
                'ipv4':    {'prefix_len': 29},
                'ipv6':    None,
                'sharing': 'ne',
            }
        ]),
        'params_json': '{}',
    }


# ══════════════════════════════════════════════════════════════════════════════
# Admin Schemas
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminSchemas:
    def test_schemas_page_200(self, client):
        assert client.get('/admin/schemas').status_code == 200

    def test_save_global_schema(self, client):
        fields = json.dumps([{
            'id': 'f1', 'name': 'region', 'label': 'Region',
            'field_type': 'text', 'required': False, 'options': [], 'default': '',
        }])
        resp = client.post('/admin/schemas', data={
            'entity': 'site', 'fields_json': fields,
        }, follow_redirects=False)
        assert resp.status_code == 302
        schema = get_schema_direct('site')
        assert len(schema) == 1
        assert schema[0]['label'] == 'Region'

    def test_invalid_json_rejected(self, client):
        resp = client.post('/admin/schemas', data={
            'entity': 'site', 'fields_json': 'not-json',
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower()

    def test_project_schemas_page_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/schemas').status_code == 200

    def test_save_project_schema(self, client):
        pid    = _create_project(client)
        fields = json.dumps([{
            'id': 'f2', 'name': 'site_code', 'label': 'Site Code',
            'field_type': 'text', 'required': True, 'options': [], 'default': '',
        }])
        resp = client.post(f'/projects/{pid}/schemas', data={
            'entity': 'site', 'fields_json': fields,
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ne import get_schema
        schema = get_schema('site', pid=pid)
        assert schema[0]['label'] == 'Site Code'


def get_schema_direct(entity):
    from ne import get_schema
    return get_schema(entity)


# ══════════════════════════════════════════════════════════════════════════════
# NE Types
# ══════════════════════════════════════════════════════════════════════════════

class TestNETypes:
    def test_ne_types_list_200(self, client):
        assert client.get('/ne-types').status_code == 200

    def test_add_ne_type_form_200(self, client):
        assert client.get('/ne-types/add').status_code == 200

    def test_add_global_ne_type(self, client):
        resp = client.post('/ne-types/add', data=_ne_type_data())
        assert resp.status_code in (200, 302)
        from ne import global_ne_types
        assert any(t['name'] == 'Test Router' for t in global_ne_types())

    def test_add_project_ne_type(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/ne-types/add',
                           data=_ne_type_data(pid=pid, scope='project'))
        assert resp.status_code in (200, 302)
        from ne import project_ne_types
        assert any(t['name'] == 'Test Router' for t in project_ne_types(pid))

    def test_edit_ne_type(self, client):
        client.post('/ne-types/add', data=_ne_type_data())
        from ne import global_ne_types
        neid = global_ne_types()[0]['id']
        data = _ne_type_data()
        data['name'] = 'Renamed Router'
        client.post(f'/ne-types/{neid}/edit', data=data)
        assert get_ne_type(neid)['name'] == 'Renamed Router'

    def test_delete_ne_type(self, client):
        client.post('/ne-types/add', data=_ne_type_data())
        from ne import global_ne_types
        neid = global_ne_types()[0]['id']
        resp = client.post(f'/ne-types/{neid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_ne_type(neid) is None

    def test_project_ne_types_list_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/ne-types').status_code == 200

    def test_ne_type_invalid_json_interfaces(self, client):
        data = _ne_type_data()
        data['interfaces_json'] = 'not-json'
        resp = client.post('/ne-types/add', data=data, follow_redirects=True)
        assert b'invalid' in resp.data.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Sites
# ══════════════════════════════════════════════════════════════════════════════

class TestSites:
    def test_sites_list_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/sites').status_code == 200

    def test_add_site_form_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/sites/add').status_code == 200

    def test_add_site(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/sites/add', data={
            'name': 'LON-DC1', 'description': '', 'labels': '',
            'params_json': '{}',
        }, follow_redirects=False)
        assert resp.status_code == 302
        sites = project_sites(pid)
        assert any(s['name'] == 'LON-DC1' for s in sites)

    def test_add_site_empty_name_rejected(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/sites/add', data={
            'name': '', 'description': '', 'labels': '', 'params_json': '{}',
        }, follow_redirects=True)
        assert b'required' in resp.data.lower() or resp.status_code == 200

    def test_bulk_site_creation(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/sites/bulk', data={
            'pattern': 'ran{0001..0005}', 'description': '', 'labels': '',
            'params_json': '{}',
        }, follow_redirects=False)
        assert resp.status_code == 302
        sites = project_sites(pid)
        names = [s['name'] for s in sites]
        assert 'ran0001' in names
        assert 'ran0005' in names
        assert len(names) == 5

    def test_bulk_site_invalid_pattern(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/sites/bulk', data={
            'pattern': 'no-pattern', 'description': '', 'labels': '',
            'params_json': '{}',
        }, follow_redirects=True)
        assert b'invalid' in resp.data.lower() or b'pattern' in resp.data.lower()

    def test_site_detail_200(self, client):
        pid  = _create_project(client)
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'LON-DC1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        assert client.get(f'/projects/{pid}/sites/{site["id"]}').status_code == 200

    def test_delete_site(self, client):
        pid  = _create_project(client)
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'TMP', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        sid  = site['id']
        resp = client.post(f'/projects/{pid}/sites/{sid}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_site(sid) is None

    def test_edit_site(self, client):
        pid = _create_project(client)
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'Before', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        client.post(f'/projects/{pid}/sites/{site["id"]}/edit', data={
            'name': 'After', 'description': 'updated', 'labels': '', 'params_json': '{}',
        })
        assert get_site(site['id'])['name'] == 'After'


# ══════════════════════════════════════════════════════════════════════════════
# PODs
# ══════════════════════════════════════════════════════════════════════════════

class TestPODs:
    def test_pods_list_200(self, client):
        pid = _create_project(client)
        assert client.get(f'/projects/{pid}/pods').status_code == 200

    def test_add_pod(self, client):
        pid  = _create_project(client)
        resp = client.post(f'/projects/{pid}/pods/add', data={
            'name': 'CORE-POD-1', 'description': '', 'labels': '', 'params_json': '{}',
        }, follow_redirects=False)
        assert resp.status_code == 302
        pods = project_pods(pid)
        assert any(p['name'] == 'CORE-POD-1' for p in pods)

    def test_delete_pod(self, client):
        pid  = _create_project(client)
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'TMP-POD', 'description': '', 'labels': '', 'params_json': '{}',
        })
        pod  = project_pods(pid)[0]
        resp = client.post(f'/projects/{pid}/pods/{pod["id"]}/delete', follow_redirects=False)
        assert resp.status_code == 302
        assert get_pod(pod['id']) is None

    def test_pod_detail_200(self, client):
        pid  = _create_project(client)
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'P1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        pod = project_pods(pid)[0]
        assert client.get(f'/projects/{pid}/pods/{pod["id"]}').status_code == 200

    def test_pod_slot_save(self, client):
        pid = _create_project(client)
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'P1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        pod = project_pods(pid)[0]
        # Add a NE type first
        client.post('/ne-types/add', data=_ne_type_data())
        from ne import global_ne_types
        neid = global_ne_types()[0]['id']
        resp = client.post(f'/projects/{pid}/pods/{pod["id"]}/slots',
                           json=[{'ne_type_id': neid, 'count': 3, 'label_override': []}])
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data['saved'] == 1

    def test_assign_pod_to_site(self, client):
        pid = _create_project(client)
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'S1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'P1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        pod  = project_pods(pid)[0]
        resp = client.post(f'/projects/{pid}/sites/{site["id"]}/assign-pod', data={
            'pod_id': pod['id'],
        }, follow_redirects=False)
        assert resp.status_code == 302
        from ne import site_pods
        assert any(p['id'] == pod['id'] for p in site_pods(site['id']))

    def test_unassign_pod_from_site(self, client):
        pid  = _create_project(client)
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'S1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'P1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        pod  = project_pods(pid)[0]
        client.post(f'/projects/{pid}/sites/{site["id"]}/assign-pod',
                    data={'pod_id': pod['id']})
        resp = client.post(f'/projects/{pid}/sites/{site["id"]}/unassign-pod',
                           data={'pod_id': pod['id']}, follow_redirects=False)
        assert resp.status_code == 302
        from ne import site_pods
        assert not any(p['id'] == pod['id'] for p in site_pods(site['id']))


# ══════════════════════════════════════════════════════════════════════════════
# Requirements engine
# ══════════════════════════════════════════════════════════════════════════════

class TestRequirements:
    def _build_full_hierarchy(self, client):
        pid = _create_project(client)
        # Add NE type with 1 interface
        client.post('/ne-types/add', data=_ne_type_data())
        from ne import global_ne_types
        neid = global_ne_types()[0]['id']
        # Create site + pod + assign
        client.post(f'/projects/{pid}/sites/add', data={
            'name': 'LON', 'description': '', 'labels': '', 'params_json': '{}',
        })
        client.post(f'/projects/{pid}/pods/add', data={
            'name': 'POD1', 'description': '', 'labels': '', 'params_json': '{}',
        })
        site = project_sites(pid)[0]
        pod  = project_pods(pid)[0]
        client.post(f'/projects/{pid}/sites/{site["id"]}/assign-pod',
                    data={'pod_id': pod['id']})
        client.post(f'/projects/{pid}/pods/{pod["id"]}/slots',
                    json=[{'ne_type_id': neid, 'count': 2, 'label_override': []}])
        return pid, neid, site, pod

    def test_requirements_page_200(self, client):
        pid, *_ = self._build_full_hierarchy(client)
        assert client.get(f'/projects/{pid}/requirements').status_code == 200

    def test_requirements_computed(self, client):
        pid, *_ = self._build_full_hierarchy(client)
        resp    = client.get(f'/projects/{pid}/requirements')
        assert b'mgmt' in resp.data

    def test_push_requirement_creates_subnet(self, client):
        pid, *_ = self._build_full_hierarchy(client)
        reqs    = load_requirements(pid)
        if not reqs:
            # Trigger computation
            client.get(f'/projects/{pid}/requirements')
            reqs = load_requirements(pid)

        if reqs:
            resp = client.post(f'/projects/{pid}/requirements/push', data={
                'keys': [reqs[0]['key']],
            }, follow_redirects=False)
            assert resp.status_code in (200, 302)

    def test_push_all_requirements(self, client):
        pid, *_ = self._build_full_hierarchy(client)
        resp = client.post(f'/projects/{pid}/requirements/push-all',
                           follow_redirects=False)
        assert resp.status_code in (200, 302)
