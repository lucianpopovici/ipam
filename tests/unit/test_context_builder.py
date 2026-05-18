"""Unit tests for document_generation.context — snapshot builder."""
import json
import os
import sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


@pytest.mark.unit
def test_build_context_returns_required_keys(client, seeded_project):
    """Snapshot contains every documented top-level key."""
    import db
    # create a minimal customer
    cust = {
        'id': 'cust-test', 'name': 'Test Corp', 'slug': 'test',
        'primary_contact': {'name': '', 'email': '', 'phone': ''},
        'billing_ref': '', 'template_set_id': None,
        'branding': {'logo_path': '', 'primary_color': '#000', 'footer_line': ''},
        'default_locale': 'en-GB', 'notes': '', 'created_at': '',
    }
    db.r.set('customer:cust-test', json.dumps(cust))
    db.r.sadd('customers:index', 'cust-test')

    # link customer to project
    pid = seeded_project['id']
    import ipam as ipam_mod
    proj = ipam_mod.get_project(pid)
    proj['customer_id'] = 'cust-test'
    ipam_mod.save_project(proj)
    db.r.sadd('customer:cust-test:projects', pid)

    from document_generation.context import build_context, CONTEXT_SCHEMA_VERSION
    ctx = build_context(pid, 'u-test', 'Test User', 'test@example.com')

    required_keys = [
        'context_schema_version', 'generated_at', 'generated_by', 'approvals',
        'project', 'customer', 'sites', 'pods', 'racks',
        'vrfs', 'networks', 'ip_allocations',
        'ne_types', 'ne_instances', 'hw_templates', 'hw_instances', 'cables',
        'bindings_flat', 'validation', 'missing',
    ]
    for key in required_keys:
        assert key in ctx, f'Missing key: {key!r}'

    assert ctx['context_schema_version'] == CONTEXT_SCHEMA_VERSION
    assert ctx['project']['id'] == pid
    assert ctx['customer']['id'] == 'cust-test'


@pytest.mark.unit
def test_build_context_missing_summary(client, seeded_project):
    """missing.summary is populated and missing.fields is a list."""
    from document_generation.context import build_context
    ctx = build_context(seeded_project['id'])
    assert isinstance(ctx['missing']['fields'], list)
    assert isinstance(ctx['missing']['summary'], str)


@pytest.mark.unit
def test_build_context_project_not_found(client):
    """ValueError raised for unknown project ID."""
    from document_generation.context import build_context
    with pytest.raises(ValueError, match='not found'):
        build_context('nonexistent-pid')


@pytest.mark.unit
def test_bindings_flat_structure(client, seeded_project):
    """bindings_flat is a list of dicts with expected keys."""
    from document_generation.context import build_context
    ctx = build_context(seeded_project['id'])
    for row in ctx['bindings_flat']:
        for key in ('ne_instance_id', 'ne_instance_name', 'iface_name',
                    'hw_instance_id', 'hw_instance_name', 'port_name'):
            assert key in row
