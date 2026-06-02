"""Tests for projects without supernets (new allocation model)."""
import json
import pytest


def _create_customer(client):
    from customer import all_customers
    client.post('/customers/add', data={'name': 'Acme', 'slug': 'acme'})
    return all_customers()[0]['id']


@pytest.mark.api
class TestProjectNoSupernet:
    def test_create_project_without_supernet(self, client):
        cid = _create_customer(client)
        rv = client.post('/projects/add', data={
            'name': 'No-Supernet Project',
            'customer_id': cid,
            'description': 'VRF-based project',
        }, follow_redirects=True)
        assert rv.status_code == 200

        from ipam import all_projects
        projects = all_projects()
        proj = next((p for p in projects if p['name'] == 'No-Supernet Project'), None)
        assert proj is not None
        assert 'supernet' not in proj
        assert 'legacy_supernet' not in proj
        assert proj['customer_id'] == cid

    def test_create_project_with_supernet_stores_as_legacy(self, client):
        cid = _create_customer(client)
        rv = client.post('/projects/add', data={
            'name': 'Legacy Project',
            'customer_id': cid,
            'supernet': '10.0.0.0/8',
        }, follow_redirects=True)
        assert rv.status_code == 200

        from ipam import all_projects
        proj = next((p for p in all_projects() if p['name'] == 'Legacy Project'), None)
        assert proj is not None
        assert proj.get('legacy_supernet') == '10.0.0.0/8'
        assert 'supernet' not in proj

    def test_create_project_without_customer_shows_warning(self, client):
        """Project can be created without a customer (warning shown, not error)."""
        rv = client.post('/projects/add', data={
            'name': 'No Customer',
            'customer_id': '',
        }, follow_redirects=True)
        assert rv.status_code == 200
        from ipam import all_projects
        # Project is created with a warning flash
        proj = next((p for p in all_projects() if p['name'] == 'No Customer'), None)
        assert proj is not None
        assert not proj.get('customer_id')

    def test_add_subnet_manual_no_supernet(self, client):
        cid = _create_customer(client)
        client.post('/projects/add', data={
            'name': 'VRF Project', 'customer_id': cid,
        }, follow_redirects=True)
        from ipam import all_projects
        pid = next(p['id'] for p in all_projects() if p['name'] == 'VRF Project')

        rv = client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'manual',
            'cidr': '10.10.10.0/24',
            'name': 'test-subnet',
        }, follow_redirects=True)
        assert rv.status_code == 200

        from ipam import project_networks, get_network
        nets = project_networks(pid)
        assert any(n['cidr'] == '10.10.10.0/24' for n in nets)

        net = next(n for n in nets if n['cidr'] == '10.10.10.0/24')
        assert net.get('family') == 4

    def test_subnet_stores_vrf_id(self, client):
        cid = _create_customer(client)
        # Create a VRF
        client.post(f'/customers/{cid}/vrfs/add', data={'name': 'mgmt', 'rd': ''})
        from vrf import customer_vrfs
        vid = customer_vrfs(cid)[0]['id']

        # Create project
        client.post('/projects/add', data={'name': 'VRF Test', 'customer_id': cid})
        from ipam import all_projects
        pid = next(p['id'] for p in all_projects() if p['name'] == 'VRF Test')

        # Add subnet with VRF
        client.post(f'/projects/{pid}/subnet/add', data={
            'mode': 'manual',
            'cidr': '10.20.0.0/24',
            'vrf_id': vid,
        }, follow_redirects=True)

        from ipam import project_networks
        net = next((n for n in project_networks(pid) if n['cidr'] == '10.20.0.0/24'), None)
        assert net is not None
        assert net.get('vrf_id') == vid

    def test_pool_summary_no_supernet(self, client):
        cid = _create_customer(client)
        client.post('/projects/add', data={'name': 'PS Test', 'customer_id': cid})
        from ipam import all_projects, project_pool_summary
        pid = next(p['id'] for p in all_projects() if p['name'] == 'PS Test')

        summary = project_pool_summary(pid)
        assert isinstance(summary, dict)
        # No supernet, no subnets → total=0
        assert summary['total_ips'] == 0
        assert summary['allocated_ips'] == 0
