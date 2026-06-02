"""Unit tests for per_hw/per_ne template rules and structured offsets."""
import pytest
from ipam import resolve_template_rules, _validate_rules, _validate_offset


@pytest.mark.unit
class TestValidateRules:
    def test_per_hw_valid(self):
        rules = [{'type': 'per_hw', 'offset': {'kind': 'fixed', 'value': 0},
                  'max_count': 10, 'role': 'server', 'status': 'reserved'}]
        _validate_rules(rules)  # must not raise

    def test_per_ne_valid(self):
        rules = [{'type': 'per_ne', 'offset': {'kind': 'fixed', 'value': 0},
                  'max_count': 5, 'per_ne_count': 1, 'role': 'ne', 'status': 'reserved'}]
        _validate_rules(rules)  # must not raise

    def test_after_previous_first_rule_raises(self):
        rules = [{'type': 'per_hw', 'offset': {'kind': 'after_previous', 'gap': 0},
                  'max_count': 5, 'role': 'x', 'status': 'reserved'}]
        with pytest.raises(ValueError, match='TMPL_FIRST_RULE_RELATIVE'):
            _validate_rules(rules)

    def test_after_previous_second_rule_ok(self):
        rules = [
            {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
            {'type': 'per_hw', 'offset': {'kind': 'after_previous', 'gap': 2},
             'max_count': 5, 'role': 'server', 'status': 'reserved'},
        ]
        _validate_rules(rules)  # must not raise

    def test_unknown_type_raises(self):
        rules = [{'type': 'magic', 'role': 'x', 'status': 'reserved'}]
        with pytest.raises(ValueError, match='unknown type'):
            _validate_rules(rules)


@pytest.mark.unit
class TestResolveOffsets:
    """per_hw rules with fixed / after_previous offsets."""

    def test_fixed_offset(self):
        # /24: per_hw with max_count=3 at offset=1; no pid → falls back to max_count
        rules = [{'type': 'per_hw', 'offset': {'kind': 'fixed', 'value': 1},
                  'max_count': 3, 'role': 'server', 'status': 'reserved'}]
        slots = resolve_template_rules('10.0.0.0/24', rules)
        # No pid → uses max_count=3 for preview
        assert len(slots) == 3
        assert all('phantom_source' in s for s in slots)

    def test_after_previous_chains(self):
        # from_start at offset=1 (index 0), then per_hw after_previous gap=1
        # No pid → per_hw uses max_count=5; total = 1 gateway + 5 servers
        rules = [
            {'type': 'from_start', 'offset': 1, 'role': 'gateway', 'status': 'reserved'},
            {'type': 'per_hw', 'offset': {'kind': 'after_previous', 'gap': 1},
             'max_count': 5, 'role': 'server', 'status': 'reserved'},
        ]
        slots = resolve_template_rules('10.0.0.0/24', rules)
        # 1 gateway at .1; servers start at index 0+1+1=2 → .2,.3,.4,.5,.6
        assert len(slots) == 6
        assert slots[0]['role'] == 'gateway'
        assert all('server' in s['role'] for s in slots[1:])

    def test_legacy_integer_offset(self):
        """Old-style integer offset still works."""
        rules = [{'type': 'from_start', 'offset': 2, 'role': 'vrrp', 'status': 'reserved'}]
        slots = resolve_template_rules('10.0.0.0/24', rules)
        assert len(slots) == 1
        assert slots[0]['ip'] == '10.0.0.2'

    def test_from_end_and_range_still_work(self):
        rules = [
            {'type': 'from_start', 'offset': 1, 'role': 'gw', 'status': 'reserved'},
            {'type': 'from_end',   'count': 2,  'role': 'mgmt', 'status': 'reserved'},
        ]
        slots = resolve_template_rules('10.0.0.0/29', rules)
        ips = [s['ip'] for s in slots]
        assert '10.0.0.1' in ips  # from_start offset=1
        assert '10.0.0.6' in ips  # from_end


@pytest.mark.unit
class TestMinPrefixInRequirements:
    """Smoke test: min_prefix_v4/v6 give sensible sizes."""

    def test_prefix_v4_p2p(self):
        from ipam import min_prefix_v4
        assert min_prefix_v4(2) == 31

    def test_prefix_v4_small_subnet(self):
        from ipam import min_prefix_v4
        # 6 hosts needs /29
        assert min_prefix_v4(6) == 29

    def test_prefix_v6_pair(self):
        from ipam import min_prefix_v6
        assert min_prefix_v6(2) == 127
