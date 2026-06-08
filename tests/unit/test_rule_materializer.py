"""
Unit tests for the rules.py materializer module.
Covers: bucket_key, synthetic labels, port filtering, count expansion,
regex matching, category/port_type filtering, preview, exclusion.
"""
import pytest
import fakeredis
import db


@pytest.fixture(autouse=True)
def _fake_r(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    import hw_logic
    monkeypatch.setattr(db, 'r', fake)
    monkeypatch.setattr(hw_logic, 'r', fake)
    return fake


# ── Helpers to build fake hw_logic state ─────────────────────────────────────

def _make_template(fk, tid, category='server', ports=None):
    import json
    if ports is None:
        ports = [{'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                  'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                  'breakout_fan_out': 1, 'notes': ''}]
    tmpl = {'id': tid, 'name': f'Tmpl-{tid}', 'category': category,
            'u_size': 1, 'form_factor': '19"', 'vendor': '', 'model': '',
            'cable_type': '', 'description': '', 'scope': 'global',
            'project_id': '', 'ports': ports}
    fk.set(f'hw:template:{tid}', json.dumps(tmpl))
    fk.sadd('hw:templates:index', tid)
    return tmpl


def _make_instance(fk, iid, pid, tid, asset_tag=None, rack_id=None, location=None):
    import json
    loc = location or ({'rack_id': rack_id} if rack_id else {})
    inst = {'id': iid, 'template_id': tid, 'project_id': pid,
            'asset_tag': asset_tag or iid,
            'serial': '', 'status': 'deployed',
            'location': loc, 'port_overrides': {}, 'labels': []}
    fk.set(f'hw:instance:{iid}', json.dumps(inst))
    fk.sadd(f'project:{pid}:hw:instances', iid)
    return inst


# ── rules helpers ──────────────────────────────────────────────────────────────

import rules as R  # pylint: disable=wrong-import-position


@pytest.mark.unit
def test_bucket_synthetic_labels_empty():
    assert not R.bucket_synthetic_labels(())


@pytest.mark.unit
def test_bucket_synthetic_labels_single():
    result = R.bucket_synthetic_labels((('rack', 'R-01'),))
    assert result == ['rack:R-01']


@pytest.mark.unit
def test_bucket_synthetic_labels_multiple_sorted():
    key = (('category', 'server'), ('rack', 'R-01'))
    labels = R.bucket_synthetic_labels(key)
    assert labels == ['category:server', 'rack:R-01']


@pytest.mark.unit
def test_materialize_empty_project(_fake_r):
    ports = R.materialize_binding({'port_types': ['mgmt']}, 'pid-empty')
    assert not ports


@pytest.mark.unit
def test_materialize_basic(_fake_r):
    _make_template(_fake_r, 't1', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i1', 'pid1', 't1', asset_tag='srv-001')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'categories': ['server'],
         'name_regex': '.*', 'group_by': []},
        'pid1',
    )
    assert len(ports) == 1
    assert ports[0]['port_id'] == 'ilo'
    assert ports[0]['hw_instance_id'] == 'i1'
    assert ports[0]['role'] == 'primary'


@pytest.mark.unit
def test_materialize_port_type_filter(_fake_r):
    _make_template(_fake_r, 't2', category='server',
                   ports=[
                       {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                        'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                       {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                        'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                   ])
    _make_instance(_fake_r, 'i2', 'pid2', 't2')

    ports = R.materialize_binding({'port_types': ['mgmt']}, 'pid2')
    assert len(ports) == 1
    assert ports[0]['port_id'] == 'ilo'


@pytest.mark.unit
def test_materialize_category_filter(_fake_r):
    _make_template(_fake_r, 't3s', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_template(_fake_r, 't3p', category='pdu',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i3s', 'pid3', 't3s')
    _make_instance(_fake_r, 'i3p', 'pid3', 't3p')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'categories': ['server']}, 'pid3'
    )
    assert len(ports) == 1
    assert ports[0]['hw_instance_id'] == 'i3s'


@pytest.mark.unit
def test_materialize_name_regex(_fake_r):
    _make_template(_fake_r, 't4', category='server',
                   ports=[
                       {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                        'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                       {'id': 'mgmt0', 'name': 'mgmt0', 'port_type': 'mgmt',
                        'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                       {'id': 'data0', 'name': 'Eth1/0', 'port_type': 'data',
                        'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                   ])
    _make_instance(_fake_r, 'i4', 'pid4', 't4')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'name_regex': r'^(iLO|iDRAC|BMC)\d*$'}, 'pid4'
    )
    assert len(ports) == 1
    assert ports[0]['port_id'] == 'ilo'


@pytest.mark.unit
def test_materialize_regex_case_insensitive(_fake_r):
    _make_template(_fake_r, 't5', category='server',
                   ports=[{'id': 'ILO', 'name': 'ILO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i5', 'pid5', 't5')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'name_regex': '^ilo$'}, 'pid5'
    )
    assert len(ports) == 1


@pytest.mark.unit
def test_materialize_port_count_expansion(_fake_r):
    # count=4 → 4 synthetic ports: eth0-0, eth0-1, eth0-2, eth0-3
    _make_template(_fake_r, 't6', category='server',
                   ports=[{'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                            'connector': 'SFP28', 'speed_gbps': 25, 'count': 4,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i6', 'pid6', 't6')

    ports = R.materialize_binding({'port_types': ['data']}, 'pid6')
    # count=4 → all 4 sub-ports; but port_id is the same 'eth0' for all
    assert len(ports) == 4


@pytest.mark.unit
def test_materialize_excluded_ports(_fake_r):
    _make_template(_fake_r, 't7', category='server',
                   ports=[
                       {'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                        'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                       {'id': 'bmc', 'name': 'BMC', 'port_type': 'mgmt',
                        'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                        'breakout_fan_out': 1, 'notes': ''},
                   ])
    _make_instance(_fake_r, 'i7', 'pid7', 't7')

    excluded = {('i7', 'ilo')}
    ports = R.materialize_binding({'port_types': ['mgmt']}, 'pid7', excluded)
    assert len(ports) == 1
    assert ports[0]['port_id'] == 'bmc'


@pytest.mark.unit
def test_materialize_group_by_rack_unracked(_fake_r):
    _make_template(_fake_r, 't8', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i8', 'pid8', 't8')  # no rack

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'group_by': ['rack']}, 'pid8'
    )
    assert len(ports) == 1
    assert 'rack:unracked' in ports[0]['bucket']


@pytest.mark.unit
def test_materialize_group_by_rack_with_rack(_fake_r):
    import json
    # Create a rack instance with asset_tag 'R-01'
    rack = {'id': 'rack-01', 'template_id': 'tmpl-rack', 'project_id': 'pid9',
            'asset_tag': 'R-01', 'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {}, 'labels': []}
    _fake_r.set('hw:instance:rack-01', json.dumps(rack))

    _make_template(_fake_r, 't9', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i9', 'pid9', 't9', rack_id='rack-01')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'group_by': ['rack']}, 'pid9'
    )
    assert len(ports) == 1
    assert 'rack:R-01' in ports[0]['bucket']


@pytest.mark.unit
def test_materialize_group_by_hw_template(_fake_r):
    _make_template(_fake_r, 't10', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i10', 'pid10', 't10')

    ports = R.materialize_binding(
        {'port_types': ['mgmt'], 'group_by': ['hw_template']}, 'pid10'
    )
    assert len(ports) == 1
    assert any('hw-tmpl:' in lbl for lbl in ports[0]['bucket'])


@pytest.mark.unit
def test_materialize_port_label_filter(_fake_r):
    # Two data ports; only one carries the 'uplink' label.
    _make_template(_fake_r, 't_lbl', category='switch',
                   ports=[
                       {'id': 'eth1', 'name': 'Eth1', 'port_type': 'data',
                        'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                        'breakout_fan_out': 1, 'notes': '', 'labels': ['uplink']},
                       {'id': 'eth2', 'name': 'Eth2', 'port_type': 'data',
                        'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                        'breakout_fan_out': 1, 'notes': '', 'labels': ['access']},
                   ])
    _make_instance(_fake_r, 'i_lbl', 'pid_lbl', 't_lbl')

    ports = R.materialize_binding({'port_labels': ['uplink']}, 'pid_lbl')
    assert len(ports) == 1
    assert ports[0]['port_id'] == 'eth1'


@pytest.mark.unit
def test_materialize_port_label_any_of(_fake_r):
    # A rule listing multiple labels matches ports carrying ANY of them.
    _make_template(_fake_r, 't_lbl2', category='switch',
                   ports=[
                       {'id': 'eth1', 'name': 'Eth1', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['uplink']},
                       {'id': 'eth2', 'name': 'Eth2', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['peering']},
                       {'id': 'eth3', 'name': 'Eth3', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['access']},
                   ])
    _make_instance(_fake_r, 'i_lbl2', 'pid_lbl2', 't_lbl2')

    ports = R.materialize_binding({'port_labels': ['uplink', 'peering']}, 'pid_lbl2')
    assert {p['port_id'] for p in ports} == {'eth1', 'eth2'}


@pytest.mark.unit
def test_materialize_no_labels_matches_all(_fake_r):
    # Empty port_labels keeps prior behavior: no label filtering.
    _make_template(_fake_r, 't_lbl3', category='switch',
                   ports=[
                       {'id': 'eth1', 'name': 'Eth1', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['uplink']},
                       {'id': 'eth2', 'name': 'Eth2', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': []},
                   ])
    _make_instance(_fake_r, 'i_lbl3', 'pid_lbl3', 't_lbl3')

    ports = R.materialize_binding({'port_types': ['data']}, 'pid_lbl3')
    assert {p['port_id'] for p in ports} == {'eth1', 'eth2'}


@pytest.mark.unit
def test_preview_port_label_filter(_fake_r):
    _make_template(_fake_r, 't_lbl4', category='switch',
                   ports=[
                       {'id': 'eth1', 'name': 'Eth1', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['uplink']},
                       {'id': 'eth2', 'name': 'Eth2', 'port_type': 'data',
                        'connector': 'SFP28', 'count': 1, 'labels': ['access']},
                   ])
    _make_instance(_fake_r, 'i_lbl4', 'pid_lbl4', 't_lbl4')

    result = R.preview_binding({'port_labels': ['uplink']}, 'pid_lbl4')
    assert result['total_ports'] == 1


@pytest.mark.unit
def test_preview_binding_empty(_fake_r):
    result = R.preview_binding({}, 'pid-empty2')
    assert result == {'total_ports': 0, 'buckets': []}


@pytest.mark.unit
def test_preview_binding_with_ports(_fake_r):
    _make_template(_fake_r, 't11', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i11', 'pid11', 't11')

    result = R.preview_binding({'port_types': ['mgmt']}, 'pid11')
    assert result['total_ports'] == 1
    assert len(result['buckets']) == 1


@pytest.mark.unit
def test_preview_unracked_warn(_fake_r):
    _make_template(_fake_r, 't12', category='server',
                   ports=[{'id': 'ilo', 'name': 'iLO', 'port_type': 'mgmt',
                            'connector': 'RJ45', 'speed_gbps': 0, 'count': 1,
                            'breakout_fan_out': 1, 'notes': ''}])
    _make_instance(_fake_r, 'i12', 'pid12', 't12')  # unracked

    result = R.preview_binding(
        {'port_types': ['mgmt'], 'group_by': ['rack']}, 'pid12'
    )
    assert result['total_ports'] == 1
    assert result['buckets'][0]['warn'] is True
