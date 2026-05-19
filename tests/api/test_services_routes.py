"""
API tests for services blueprint routes.
"""
import json
import pytest
import services_logic as svc_logic


def _make_customer(client, name='ACME'):
    resp = client.post('/customers/add', data={
        'name': name, 'slug': name.lower(),
        'contact_name': '', 'contact_email': '', 'contact_phone': '',
        'billing_ref': '', 'primary_color': '#000000', 'footer_line': '',
        'default_locale': 'en-GB', 'notes': '',
    }, follow_redirects=False)
    assert resp.status_code in (200, 302)
    # Extract cid from redirect or parse
    from customer import all_customers
    custs = all_customers()
    return next(c for c in custs if c['name'] == name)


def _make_service(cid, name='oob'):
    sid = f'svc-{name}'
    svc_logic.save_service({
        'id': sid, 'name': name, 'description': 'test',
        'customer_id': cid,
        'schema': [
            {'id': 'f-host', 'name': 'hostname', 'label': 'Hostname',
             'field_type': 'text', 'required': True, 'scope_override': None},
        ],
    })
    return sid


@pytest.mark.api
class TestServicesCRUD:
    def test_services_list_empty(self, client, fake_redis):
        cust = _make_customer(client)
        resp = client.get(f'/customers/{cust["id"]}/services')
        assert resp.status_code == 200
        assert b'No services yet' in resp.data

    def test_add_service_get(self, client, fake_redis):
        cust = _make_customer(client)
        resp = client.get(f'/customers/{cust["id"]}/services/add')
        assert resp.status_code == 200
        assert b'New Service' in resp.data

    def test_add_service_post(self, client, fake_redis):
        cust = _make_customer(client)
        resp = client.post(f'/customers/{cust["id"]}/services/add', data={
            'name': 'oob',
            'description': 'Out of band',
            'fields_json': json.dumps([{
                'id': 'f-host', 'name': 'hostname', 'label': 'Hostname',
                'field_type': 'text', 'required': True, 'options': [],
                'default': '', 'scope_override': None,
            }]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'oob' in resp.data
        services = svc_logic.customer_services(cust['id'])
        assert len(services) == 1
        assert services[0]['name'] == 'oob'

    def test_add_service_missing_name(self, client, fake_redis):
        cust = _make_customer(client)
        resp = client.post(f'/customers/{cust["id"]}/services/add', data={
            'name': '', 'description': '', 'fields_json': '[]',
        })
        assert resp.status_code == 200
        assert b'required' in resp.data.lower()

    def test_edit_service(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        resp = client.post(f'/customers/{cust["id"]}/services/{sid}/edit', data={
            'name': 'oob-renamed',
            'description': 'updated',
            'fields_json': json.dumps([]),
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert svc_logic.get_service(sid)['name'] == 'oob-renamed'

    def test_delete_service_no_links(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        resp = client.post(f'/customers/{cust["id"]}/services/{sid}/delete',
                           follow_redirects=True)
        assert resp.status_code == 200
        assert svc_logic.get_service(sid) is None

    def test_delete_service_with_links_blocked(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        svc_logic.link_service_to_iface(sid, 'nt-01', 'if-01')
        resp = client.post(f'/customers/{cust["id"]}/services/{sid}/delete',
                           follow_redirects=True)
        assert resp.status_code == 200
        # Service should still exist
        assert svc_logic.get_service(sid) is not None

    def test_force_delete_with_links(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        svc_logic.link_service_to_iface(sid, 'nt-01', 'if-01')
        resp = client.post(
            f'/customers/{cust["id"]}/services/{sid}/delete-force',
            follow_redirects=True)
        assert resp.status_code == 200
        assert svc_logic.get_service(sid) is None
        assert svc_logic.services_for_iface('nt-01', 'if-01') == []


@pytest.mark.api
class TestServiceImpact:
    def test_impact_no_links(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        resp = client.get(f'/customers/{cust["id"]}/services/{sid}/impact')
        assert resp.status_code == 200
        assert b'safe to delete' in resp.data.lower()

    def test_impact_json(self, client, fake_redis):
        cust = _make_customer(client)
        sid = _make_service(cust['id'])
        svc_logic.link_service_to_iface(sid, 'nt-01', 'if-01')
        resp = client.get(
            f'/customers/{cust["id"]}/services/{sid}/impact',
            headers={'Accept': 'application/json'},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'label' in data
        assert 'cascades' in data


@pytest.mark.api
class TestAPICustomerServices:
    def test_api_list(self, client, fake_redis):
        cust = _make_customer(client)
        _make_service(cust['id'], 'oob')
        _make_service(cust['id'], 'oam')
        resp = client.get(f'/api/customers/{cust["id"]}/services')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        names = {s['name'] for s in data}
        assert names == {'oob', 'oam'}

    def test_api_404_unknown_customer(self, client, fake_redis):
        resp = client.get('/api/customers/no-such/services')
        assert resp.status_code == 404


@pytest.mark.api
class TestSaveServiceRow:
    def test_save_text_mode(self, client, fake_redis, seeded_project):
        pid = seeded_project['id']
        # No requirements yet — should 404
        resp = client.post(
            '/projects/{}/requirements/service/{}'.format(pid, 'svc:x:y:proj'),
            data={'mode': 'text', 'value': 'example.com'},
        )
        assert resp.status_code == 404

    def test_invalid_mode_returns_400(self, client, fake_redis, seeded_project):
        pid = seeded_project['id']
        resp = client.post(
            f'/projects/{pid}/requirements/service/some-key',
            data={'mode': 'invalid'},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 400
