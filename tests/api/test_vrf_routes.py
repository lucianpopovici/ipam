"""API-level tests for VRF CRUD routes."""
import json
import pytest


@pytest.mark.api
class TestVrfCrud:
    def _create_customer(self, client):
        rv = client.post('/customers/add', data={
            'name': 'Test Corp', 'slug': 'test-corp',
        })
        assert rv.status_code in (200, 302)
        # find the new customer
        rv2 = client.get('/customers')
        assert rv2.status_code == 200
        # extract cid from redirect or listing
        from customer import all_customers
        custs = all_customers()
        assert custs
        return custs[0]['id']

    def test_add_and_list_vrf(self, client):
        cid = self._create_customer(client)

        rv = client.post(f'/customers/{cid}/vrfs/add', data={
            'name': 'mgmt',
            'description': 'Management VRF',
            'rd': '65000:10',
            'rt_import': '65000:10',
            'rt_export': '65000:10',
        }, follow_redirects=True)
        assert rv.status_code == 200

        rv2 = client.get(f'/api/customers/{cid}/vrfs')
        assert rv2.status_code == 200
        data = json.loads(rv2.data)
        assert any(v['name'] == 'mgmt' for v in data)

    def test_edit_vrf(self, client):
        cid = self._create_customer(client)
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'internet', 'rd': ''})

        from vrf import customer_vrfs
        vrfs = customer_vrfs(cid)
        assert vrfs
        vid = vrfs[0]['id']

        rv = client.post(f'/customers/{cid}/vrfs/{vid}/edit', data={
            'name': 'internet-v2', 'rd': '65000:200', 'rt_import': '', 'rt_export': '',
        }, follow_redirects=True)
        assert rv.status_code == 200

        from vrf import get_vrf
        updated = get_vrf(vid)
        assert updated['name'] == 'internet-v2'
        assert updated['rd'] == '65000:200'

    def test_delete_vrf_no_subnets(self, client):
        cid = self._create_customer(client)
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'to-delete', 'rd': ''})

        from vrf import customer_vrfs, get_vrf
        vid = customer_vrfs(cid)[0]['id']

        rv = client.post(f'/customers/{cid}/vrfs/{vid}/delete', follow_redirects=True)
        assert rv.status_code == 200
        assert get_vrf(vid) is None

    def test_api_impact_empty(self, client):
        cid = self._create_customer(client)
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'x', 'rd': ''})
        from vrf import customer_vrfs
        vid = customer_vrfs(cid)[0]['id']

        rv = client.get(f'/api/vrfs/{vid}/impact')
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data['cascades'][0]['count'] == 0

    def test_api_customer_vrfs(self, client):
        cid = self._create_customer(client)
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'a', 'rd': ''})
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'b', 'rd': ''})

        rv = client.get(f'/api/customers/{cid}/vrfs')
        data = json.loads(rv.data)
        names = {v['name'] for v in data}
        assert {'a', 'b'} == names
