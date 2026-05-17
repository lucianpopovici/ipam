"""
Tests for Standardized REST API (v1).
"""
import json
import pytest

@pytest.mark.api
def test_v1_subnets_list(client, seeded_subnet):
    """Verify listing subnets via v1 API."""
    resp = client.get('/api/v1/subnets')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert len(data) >= 1
    assert any(s['cidr'] == seeded_subnet['cidr'] for s in data)

@pytest.mark.api
def test_v1_request_ip_success(client, seeded_subnet):
    """Verify requesting an IP via v1 API."""
    cidr = seeded_subnet['cidr'] # e.g. 10.0.1.0/24
    prefix_path = cidr.replace("/", "_")

    resp = client.post(f'/api/v1/subnets/{prefix_path}/request-ip', json={
        'hostname': 'api-host',
        'description': 'Requested via API'
    })
    assert resp.status_code == 201
    data = json.loads(resp.data)
    assert data['ip'] == '10.0.1.1'
    assert data['hostname'] == 'api-host'

@pytest.mark.api
def test_v1_request_ip_not_found(client):
    """Verify 404 for unknown subnet."""
    resp = client.post('/api/v1/subnets/1.2.3.0_24/request-ip', json={})
    assert resp.status_code == 404

@pytest.mark.api
def test_v1_get_ip(client, seeded_subnet):
    """Verify getting IP details via v1 API."""
    # First allocate one
    prefix_path = seeded_subnet['cidr'].replace("/", "_")
    client.post(f'/api/v1/subnets/{prefix_path}/request-ip', json={'hostname': 'test-ip'})

    resp = client.get('/api/v1/ips/10.0.1.1')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['ip'] == '10.0.1.1'
    assert data['hostname'] == 'test-ip'

@pytest.mark.api
def test_v1_request_ip_with_ttl(client, seeded_subnet, fake_redis):
    """Verify requesting an IP with TTL via v1 API."""
    cidr = seeded_subnet['cidr']
    prefix_path = cidr.replace("/", "_")
    resp = client.post(f'/api/v1/subnets/{prefix_path}/request-ip', json={
        'ttl': 60
    })
    assert resp.status_code == 201
    ip = json.loads(resp.data)['ip']
    # Check TTL in Redis
    ttl = fake_redis.ttl(f'ip:{ip}')
    assert 0 < ttl <= 60

@pytest.mark.api
def test_real_time_publish(client, seeded_subnet, fake_redis):
    """Verify that IP allocation publishes a message via Redis Pub/Sub."""
    pubsub = fake_redis.pubsub()
    pubsub.subscribe('ipam:updates')
    # Consume subscription message
    pubsub.get_message()

    cidr = seeded_subnet['cidr']
    prefix_path = cidr.replace("/", "_")
    client.post(f'/api/v1/subnets/{prefix_path}/request-ip', json={'hostname': 'rt-test'})

    msg = pubsub.get_message()
    assert msg is not None
    assert msg['type'] == 'message'
    data = json.loads(msg['data'])
    assert data['action'] == 'allocate'
    assert data['data']['hostname'] == 'rt-test'

@pytest.mark.api
def test_openapi_spec(client):
    """Verify that the OpenAPI spec is generated and accessible."""
    resp = client.get('/openapi.json')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['info']['title'] == "Redis IPAM API"
    assert '/api/v1/subnets' in data['paths']
