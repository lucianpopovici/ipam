"""
Unit tests for services_logic helper functions.
"""
import pytest
import services_logic as svc


@pytest.mark.unit
class TestGetSaveService:
    def test_roundtrip(self):
        service = {
            'id': 'svc-001',
            'name': 'oob',
            'description': 'Out-of-band',
            'customer_id': 'cust-acme',
            'schema': [],
        }
        svc.save_service(service)
        loaded = svc.get_service('svc-001')
        assert loaded is not None
        assert loaded['name'] == 'oob'

    def test_get_missing(self):
        assert svc.get_service('nonexistent') is None


@pytest.mark.unit
class TestCustomerServices:
    def test_customer_services_sorted(self):
        for name in ('zzz', 'aaa', 'mmm'):
            svc.save_service({'id': f'svc-{name}', 'name': name,
                              'description': '', 'customer_id': 'cust-1', 'schema': []})
        result = svc.customer_services('cust-1')
        assert [s['name'] for s in result] == ['aaa', 'mmm', 'zzz']

    def test_delete_service(self):
        svc.save_service({'id': 'svc-del', 'name': 'del', 'description': '',
                          'customer_id': 'cust-1', 'schema': []})
        svc.delete_service('svc-del')
        assert svc.get_service('svc-del') is None
        assert 'svc-del' not in {s['id'] for s in svc.customer_services('cust-1')}


@pytest.mark.unit
class TestIFaceServiceLinks:
    def setup_method(self):
        svc.save_service({'id': 'svc-oob', 'name': 'oob', 'description': '',
                          'customer_id': 'cust-1', 'schema': []})

    def test_link_and_query(self):
        svc.link_service_to_iface('svc-oob', 'nt-server', 'if-idrac')
        linked = svc.services_for_iface('nt-server', 'if-idrac')
        assert len(linked) == 1
        assert linked[0]['id'] == 'svc-oob'

    def test_unlink(self):
        svc.link_service_to_iface('svc-oob', 'nt-server', 'if-idrac')
        svc.unlink_service_from_iface('svc-oob', 'nt-server', 'if-idrac')
        assert svc.services_for_iface('nt-server', 'if-idrac') == []

    def test_ifaces_for_service(self):
        svc.link_service_to_iface('svc-oob', 'nt-server', 'if-idrac')
        svc.link_service_to_iface('svc-oob', 'nt-router', 'if-mgmt')
        pairs = svc.ifaces_for_service('svc-oob')
        assert ('nt-server', 'if-idrac') in pairs
        assert ('nt-router', 'if-mgmt') in pairs

    def test_clear_iface_links(self):
        svc.link_service_to_iface('svc-oob', 'nt-server', 'if-idrac')
        svc.clear_iface_service_links('nt-server', 'if-idrac')
        assert svc.services_for_iface('nt-server', 'if-idrac') == []

    def test_delete_service_unlinks(self):
        svc.link_service_to_iface('svc-oob', 'nt-server', 'if-idrac')
        svc.delete_service('svc-oob')
        assert svc.services_for_iface('nt-server', 'if-idrac') == []


@pytest.mark.unit
class TestEffectiveScope:
    def test_inherit_from_iface(self):
        iface = {'sharing': 'pod'}
        field = {'scope_override': None}
        assert svc.effective_scope(iface, field) == 'pod'

    def test_override_wins(self):
        iface = {'sharing': 'interface'}
        field = {'scope_override': 'project'}
        assert svc.effective_scope(iface, field) == 'project'

    def test_default_interface(self):
        iface = {}
        field = {}
        assert svc.effective_scope(iface, field) == 'interface'


@pytest.mark.unit
class TestServiceValues:
    def test_read_none(self):
        binding = {}
        assert svc.read_service_values(binding, 'svc-001', 'f-host') is None

    def test_write_and_read(self):
        binding = {}
        payload = {'mode': 'text', 'value': 'oob.example.com'}
        svc.write_service_values(binding, 'svc-001', 'f-host', payload)
        result = svc.read_service_values(binding, 'svc-001', 'f-host')
        assert result == payload

    def test_write_preserves_other(self):
        binding = {'service_values': {'svc-001': {'f-a': {'mode': 'text', 'value': 'x'}}}}
        svc.write_service_values(binding, 'svc-001', 'f-b', {'mode': 'text', 'value': 'y'})
        assert binding['service_values']['svc-001']['f-a']['value'] == 'x'
        assert binding['service_values']['svc-001']['f-b']['value'] == 'y'


@pytest.mark.unit
class TestResolvedCount:
    # _resolved_count is internal but testable via compute_service_rows indirectly
    def test_text_resolved(self):
        from services_logic import _resolved_count
        sv = {'mode': 'text', 'value': 'foo'}
        assert _resolved_count(sv, 3) == 3

    def test_text_empty_unresolved(self):
        from services_logic import _resolved_count
        sv = {'mode': 'text', 'value': ''}
        assert _resolved_count(sv, 3) == 0

    def test_dynamic_resolved_when_mode_set(self):
        from services_logic import _resolved_count
        sv = {'mode': 'dynamic', 'template': 'x', 'custom': {}}
        assert _resolved_count(sv, 5) == 5

    def test_list_counts_nonempty(self):
        from services_logic import _resolved_count
        sv = {'mode': 'list', 'values': {'a': 'foo', 'b': '', 'c': 'bar'}}
        assert _resolved_count(sv, 3) == 2

    def test_none_mode_unresolved(self):
        from services_logic import _resolved_count
        assert _resolved_count(None, 5) == 0
        assert _resolved_count({'mode': None}, 5) == 0


@pytest.mark.unit
class TestEvaluateDynamic:
    def test_simple_template(self):
        result = svc.evaluate_dynamic(
            '{{ site.name | lower }}-{{ ne_instance.name | lower }}',
            {},
            {'site': {'name': 'LON'}, 'ne_instance': {'name': 'SRV-01'}},
        )
        assert result == 'lon-srv-01'

    def test_custom_slots(self):
        result = svc.evaluate_dynamic(
            '{{ custom_prefix }}-{{ ne_instance.name }}',
            {'custom_prefix': 'prod', 'custom_suffix': '', 'custom_env': '', 'custom_extra': ''},
            {'ne_instance': {'name': 'srv'}},
        )
        assert result == 'prod-srv'

    def test_error_returns_empty(self):
        result = svc.evaluate_dynamic('{{ undefined_fn() }}', {}, {})
        assert result == ''

    def test_pad_filter(self):
        result = svc.evaluate_dynamic('{{ index | pad(2) }}', {}, {'index': 3})
        assert result == '03'
