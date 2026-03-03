"""
E2E-specific fixtures.

The `live_server` session-scoped fixture starts the Flask app once per test
session in a background daemon thread, with fakeredis injected so no real
Redis is needed.  All three E2E test modules import it automatically because
pytest collects conftest.py files up the directory tree.
"""
import sys
import os
import socket
import threading
import time

import pytest
import fakeredis

# Make sure the app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ── Shared fake-Redis server (one server, one client, shared across session) ──

@pytest.fixture(scope='session')
def _fake_redis_server():
    """A single FakeServer instance reused for the whole E2E session."""
    return fakeredis.FakeServer()


@pytest.fixture(scope='session')
def _fake_redis_client(_fake_redis_server):
    return fakeredis.FakeRedis(server=_fake_redis_server, decode_responses=True)


# ── Patch all blueprint modules before app import ────────────────────────────

@pytest.fixture(scope='session', autouse=True)
def _patch_redis(_fake_redis_client):
    """
    Monkeypatch the module-level `r` in every blueprint before the session
    starts.  Session-scoped so it runs once; the same fake client is reused.
    """
    import db, ipam, ne, hw
    for mod in (db, ipam, ne, hw):
        mod.r = _fake_redis_client
    yield
    # Nothing to tear down — the fake server is discarded at process exit.


# ── Live Flask server ─────────────────────────────────────────────────────────

@pytest.fixture(scope='session')
def live_server(_patch_redis):
    """
    Start the Flask development server on a random free port.
    Returns the base URL string, e.g. ``'http://127.0.0.1:54321'``.

    The server runs in a daemon thread so it is killed automatically when the
    test session ends.
    """
    from app import app

    app.config['TESTING']    = True
    app.config['SECRET_KEY'] = 'e2e-test-secret-key'
    # Disable reloader and debugger — they don't work in threads
    app.config['DEBUG']      = False

    # Find a free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    def _run():
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)   # suppress request noise in test output
        app.run(host='127.0.0.1', port=port, use_reloader=False, threaded=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Poll until the server is accepting connections
    base_url = f'http://127.0.0.1:{port}'
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            import urllib.request
            urllib.request.urlopen(base_url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError(f'Live server did not start within 10 s on port {port}')

    yield base_url
    # Daemon thread dies with the process — no explicit shutdown needed.


# ── Per-test Redis flush ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _flush_redis_between_tests(_fake_redis_client):
    """
    Flush the fake Redis between every E2E test so tests are independent.
    Re-seed default connectors after flush so HW tests don't need to worry.
    """
    _fake_redis_client.flushall()
    # Re-seed connectors after every flush
    from hw import seed_connectors
    seed_connectors()
    yield
    # Nothing to do after the test.


# ── Convenience goto helper available to all E2E tests ───────────────────────

def goto(page, base: str, path: str):
    page.goto(f'{base}{path}')


# ── Per-test page fixture ─────────────────────────────────────────────────────

@pytest.fixture
def page_base(page, live_server):
    """Playwright page + base URL tuple used by all E2E test classes."""
    return page, live_server

