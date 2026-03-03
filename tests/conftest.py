"""
Shared fixtures for all test layers.

Install deps:
    pip install fakeredis pytest pytest-asyncio pytest-playwright
    playwright install chromium
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import fakeredis
import db                      # the shared db module
from app import app as flask_app


# ── Fake-Redis fixture ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """
    Replace the real Redis client with a fresh fakeredis server for every test.
    All modules that imported `r` from db will use the fake via monkeypatching.
    """
    server = fakeredis.FakeServer()
    fake_r = fakeredis.FakeRedis(server=server, decode_responses=True)

    # Patch the module-level `r` in every blueprint module
    import ipam, ne, hw
    for mod in (db, ipam, ne, hw):
        monkeypatch.setattr(mod, 'r', fake_r)

    yield fake_r


# ── Flask test client ─────────────────────────────────────────────────────────

@pytest.fixture
def app():
    flask_app.config.update({
        'TESTING':    True,
        'SECRET_KEY': 'test-secret',
    })
    return flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        with app.app_context():
            yield c


# ── Seed helpers (used by API + E2E tests) ────────────────────────────────────

@pytest.fixture
def seeded_project(client):
    """Create a project and return its dict."""
    resp = client.post('/projects/add', data={
        'name':        'Test Project',
        'supernet':    '10.0.0.0/8',
        'description': 'Fixture project',
    }, follow_redirects=False)
    # Extract pid from redirect Location header
    location = resp.headers.get('Location', '')
    pid = location.rstrip('/').split('/')[-1]
    return {'id': pid, 'name': 'Test Project', 'supernet': '10.0.0.0/8'}


@pytest.fixture
def seeded_subnet(client, seeded_project):
    """Add a /24 subnet to the fixture project."""
    pid = seeded_project['id']
    client.post(f'/projects/{pid}/subnet/add', data={
        'mode':        'manual',
        'cidr':        '10.0.1.0/24',
        'name':        'test-subnet',
        'description': '',
        'vlan':        '',
        'labels':      'PROD,London',
    }, follow_redirects=False)
    # Find the network id via the project page
    from ipam import project_networks
    nets = project_networks(pid)
    net  = next((n for n in nets if n['cidr'] == '10.0.1.0/24'), None)
    return net


@pytest.fixture
def seeded_hw_template(fake_redis):
    """Create a minimal server hardware template directly."""
    from hw import save_hw_template, _new_id
    tmpl = {
        'id':          _new_id(),
        'name':        'Test Server',
        'vendor':      'ACME',
        'model':       'TS-1U',
        'category':    'server',
        'form_factor': '19"',
        'u_size':      1,
        'cable_type':  '',
        'description': '',
        'ports': [
            {'id': 'p1', 'name': 'eth0',  'port_type': 'data',  'connector': 'RJ45',   'speed_gbps': 1,   'count': 4, 'breakout_fan_out': 1, 'notes': ''},
            {'id': 'p2', 'name': 'sfp0',  'port_type': 'data',  'connector': 'SFP28',  'speed_gbps': 25,  'count': 2, 'breakout_fan_out': 1, 'notes': ''},
            {'id': 'p3', 'name': 'psu0',  'port_type': 'power', 'connector': 'IEC-C14','speed_gbps': 0,   'count': 2, 'breakout_fan_out': 1, 'notes': ''},
        ],
        'scope':      'global',
        'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl


@pytest.fixture
def seeded_rack_template(fake_redis):
    from hw import save_hw_template, _new_id
    tmpl = {
        'id': _new_id(), 'name': 'Test Rack', 'vendor': 'ACME', 'model': 'R42',
        'category': 'rack', 'form_factor': '19"', 'u_size': 42,
        'cable_type': '', 'description': '', 'ports': [],
        'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl


@pytest.fixture
def seeded_cable_template(fake_redis):
    from hw import save_hw_template, _new_id
    tmpl = {
        'id': _new_id(), 'name': 'DAC 25G', 'vendor': 'ACME', 'model': 'DAC25',
        'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
        'cable_type': 'DAC', 'description': '',
        'ports': [], 'scope': 'global', 'project_id': '',
    }
    save_hw_template(tmpl)
    return tmpl
