"""

pytestmark = pytest.mark.unit

Unit tests for hw.py helper functions.
"""
import pytest
from hw import (
    seed_connectors, all_connectors, add_connector, remove_connector,
    get_compat, set_compat, connectors_compatible, full_compat_matrix,
    get_hw_template, save_hw_template, delete_hw_template,
    global_hw_templates, project_hw_templates, available_hw_templates,
    all_hw_templates_for_project,
    get_bom, save_bom, bom_with_templates,
    get_hw_instance, save_hw_instance, delete_hw_instance,
    project_instances, generate_instances_from_bom_line,
    get_rack_slots, save_rack_slots, place_in_rack, rack_layout_view,
    _check_form_factor, _remove_from_rack,
    get_cable, save_cable, delete_cable, project_cables,
    _get_port, _used_ports,
    validate_project, _issue,
    _new_id, DEFAULT_CONNECTORS, DEFAULT_COMPAT,
)
from ipam import save_project, new_id as ipam_new_id


# ══════════════════════════════════════════════════════════════════════════════
# Connector helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectorHelpers:
    def test_seed_connectors_populates(self):
        seed_connectors()
        conns = all_connectors()
        assert 'RJ45'    in conns
        assert 'SFP28'   in conns
        assert 'QSFP28'  in conns
        assert 'IEC-C13' in conns

    def test_seed_idempotent(self):
        seed_connectors()
        seed_connectors()
        assert all_connectors().count('RJ45') == 1

    def test_add_connector(self):
        add_connector('CUSTOM-X')
        assert 'CUSTOM-X' in all_connectors()

    def test_new_connector_self_compatible(self):
        add_connector('CUSTOM-Y')
        assert connectors_compatible('CUSTOM-Y', 'CUSTOM-Y')

    def test_remove_connector(self):
        seed_connectors()
        add_connector('TEMP')
        remove_connector('TEMP')
        assert 'TEMP' not in all_connectors()

    def test_remove_cleans_compat(self):
        seed_connectors()
        add_connector('AA')
        add_connector('BB')
        set_compat('AA', 'BB', True)
        remove_connector('BB')
        assert not connectors_compatible('AA', 'BB')

    def test_set_compat_symmetric(self):
        seed_connectors()
        add_connector('C1'); add_connector('C2')
        set_compat('C1', 'C2', True)
        assert connectors_compatible('C1', 'C2')
        assert connectors_compatible('C2', 'C1')

    def test_unset_compat_symmetric(self):
        seed_connectors()
        add_connector('D1'); add_connector('D2')
        set_compat('D1', 'D2', True)
        set_compat('D1', 'D2', False)
        assert not connectors_compatible('D1', 'D2')
        assert not connectors_compatible('D2', 'D1')


class TestDefaultCompatMatrix:
    def setup_method(self):
        seed_connectors()

    def test_rj45_only_compatible_with_rj45(self):
        assert connectors_compatible('RJ45', 'RJ45')
        assert not connectors_compatible('RJ45', 'SFP')
        assert not connectors_compatible('RJ45', 'SFP28')

    def test_sfp28_accepts_sfp_plus(self):
        assert connectors_compatible('SFP28', 'SFP+')

    def test_sfp28_accepts_sfp(self):
        assert connectors_compatible('SFP28', 'SFP')

    def test_sfp_plus_accepts_sfp(self):
        assert connectors_compatible('SFP+', 'SFP')

    def test_qsfp28_accepts_qsfp_dd(self):
        assert connectors_compatible('QSFP28', 'QSFP-DD')

    def test_qsfp_dd_accepts_qsfp28(self):
        assert connectors_compatible('QSFP-DD', 'QSFP28')

    def test_power_connectors_isolated(self):
        assert not connectors_compatible('IEC-C13', 'RJ45')
        assert not connectors_compatible('IEC-C13', 'SFP28')

    def test_iec_c13_c14_compatible(self):
        assert connectors_compatible('IEC-C13', 'IEC-C14')

    def test_full_matrix_returns_all(self):
        matrix = full_compat_matrix()
        assert 'RJ45' in matrix
        assert 'SFP28' in matrix
        assert isinstance(matrix['RJ45'], list)


# ══════════════════════════════════════════════════════════════════════════════
# Hardware template helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestHWTemplateHelpers:
    def _tmpl(self, name='Server-1U', cat='server', scope='global', pid=''):
        return {
            'id':          _new_id(),
            'name':        name,
            'vendor':      'ACME',
            'model':       'X1',
            'category':    cat,
            'form_factor': '19"',
            'u_size':      1,
            'cable_type':  '',
            'description': '',
            'ports':       [],
            'scope':       scope,
            'project_id':  pid,
        }

    def test_save_and_get(self):
        t = self._tmpl()
        save_hw_template(t)
        assert get_hw_template(t['id'])['name'] == 'Server-1U'

    def test_delete(self):
        t = self._tmpl()
        save_hw_template(t)
        delete_hw_template(t['id'])
        assert get_hw_template(t['id']) is None

    def test_global_template_in_list(self):
        t = self._tmpl()
        save_hw_template(t)
        assert any(x['id'] == t['id'] for x in global_hw_templates())

    def test_project_template_in_project_list(self):
        pid = _new_id()
        t   = self._tmpl(scope='project', pid=pid)
        save_hw_template(t)
        assert any(x['id'] == t['id'] for x in project_hw_templates(pid))

    def test_category_filter(self):
        save_hw_template(self._tmpl(name='S1', cat='server'))
        save_hw_template(self._tmpl(name='R1', cat='rack'))
        servers = global_hw_templates(category='server')
        assert all(t['category'] == 'server' for t in servers)

    def test_available_templates_flat_list(self):
        pid = _new_id()
        g   = self._tmpl(name='Global')
        p   = self._tmpl(name='Proj', scope='project', pid=pid)
        save_hw_template(g); save_hw_template(p)
        flat = all_hw_templates_for_project(pid)
        ids  = {t['id'] for t in flat}
        assert g['id'] in ids
        assert p['id'] in ids

    def test_sorted_by_category_then_name(self):
        for name, cat in [('Z-server', 'server'), ('A-server', 'server'), ('A-rack', 'rack')]:
            save_hw_template(self._tmpl(name=name, cat=cat))
        tmpls = global_hw_templates()
        cats  = [t['category'] for t in tmpls]
        # rack < server alphabetically
        assert cats.index('rack') < cats.index('server')

    def test_ports_stored(self):
        t = self._tmpl()
        t['ports'] = [
            {'id': 'p1', 'name': 'eth0', 'port_type': 'data',
             'connector': 'RJ45', 'speed_gbps': 1, 'count': 4,
             'breakout_fan_out': 1, 'notes': ''},
        ]
        save_hw_template(t)
        loaded = get_hw_template(t['id'])
        assert len(loaded['ports']) == 1
        assert loaded['ports'][0]['connector'] == 'RJ45'


# ══════════════════════════════════════════════════════════════════════════════
# BoM helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestBomHelpers:
    def _setup(self):
        pid  = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        tmpl = {
            'id': _new_id(), 'name': 'Server-1U', 'vendor': 'ACME', 'model': 'X1',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        return pid, tmpl

    def test_empty_bom(self):
        pid, _ = self._setup()
        assert get_bom(pid) == []

    def test_save_and_get_bom(self):
        pid, tmpl = self._setup()
        bom = [{'id': _new_id(), 'template_id': tmpl['id'], 'qty': 5,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        loaded = get_bom(pid)
        assert len(loaded) == 1
        assert loaded[0]['qty'] == 5

    def test_bom_with_templates_enriches(self):
        pid, tmpl = self._setup()
        bom = [{'id': _new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        enriched = bom_with_templates(pid)
        assert enriched[0]['template']['name'] == 'Server-1U'

    def test_bom_missing_template_still_returned(self):
        pid, _ = self._setup()
        bom = [{'id': _new_id(), 'template_id': 'nonexistent', 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        enriched = bom_with_templates(pid)
        assert enriched[0]['template'] is None

    def test_bom_overwritten(self):
        pid, tmpl = self._setup()
        save_bom(pid, [{'id': _new_id(), 'template_id': tmpl['id'], 'qty': 1,
                        'tag_prefix': 'a', 'tag_start': 1, 'tag_pad': 3, 'description': ''}])
        save_bom(pid, [])
        assert get_bom(pid) == []


# ══════════════════════════════════════════════════════════════════════════════
# Instance generation
# ══════════════════════════════════════════════════════════════════════════════

class TestInstanceGeneration:
    def _setup(self):
        pid  = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        tmpl = {
            'id': _new_id(), 'name': 'Server-1U', 'vendor': 'ACME', 'model': 'X1',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        return pid, tmpl

    def test_generates_correct_count(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 5,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert len(created) == 5

    def test_asset_tags_sequential(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 3,
                'tag_prefix': 'srv', 'tag_start': 10, 'tag_pad': 3, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        tags = [i['asset_tag'] for i in created]
        assert tags == ['srv-010', 'srv-011', 'srv-012']

    def test_zero_padding(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'rack', 'tag_start': 1, 'tag_pad': 4, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert created[0]['asset_tag'] == 'rack-0001'
        assert created[1]['asset_tag'] == 'rack-0002'

    def test_instances_saved_to_redis(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'sw', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        for inst in created:
            assert get_hw_instance(inst['id']) is not None

    def test_instances_appear_in_project_list(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        instances = project_instances(pid)
        ids = {i['id'] for i in instances}
        for inst in created:
            assert inst['id'] in ids

    def test_missing_template_raises(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        item = {'id': _new_id(), 'template_id': 'no-such-tmpl', 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        with pytest.raises(ValueError, match='not found'):
            generate_instances_from_bom_line(pid, item)

    def test_instance_status_defaults_to_in_stock(self):
        pid, tmpl = self._setup()
        item = {'id': _new_id(), 'template_id': tmpl['id'], 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert created[0]['status'] == 'in-stock'


# ══════════════════════════════════════════════════════════════════════════════
# Rack placement helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestRackPlacement:
    def _make_rack_instance(self, pid, ff='19"', u_size=42):
        tmpl = {
            'id': _new_id(), 'name': 'Rack', 'vendor': 'APC', 'model': 'AR3000',
            'category': 'rack', 'form_factor': ff, 'u_size': u_size,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = {'id': _new_id(), 'template_id': tmpl['id'], 'project_id': pid,
                'asset_tag': 'rack-001', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(inst)
        return inst, tmpl

    def _make_device_instance(self, pid, ff='19"', u_size=1, cat='server'):
        tmpl = {
            'id': _new_id(), 'name': 'Server', 'vendor': 'Dell', 'model': 'R650',
            'category': cat, 'form_factor': ff, 'u_size': u_size,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = {'id': _new_id(), 'template_id': tmpl['id'], 'project_id': pid,
                'asset_tag': f'dev-{_new_id()}', 'serial': '', 'status': 'in-stock',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(inst)
        return inst, tmpl

    def test_place_success(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=10)
        errors = [i for i in issues if i['severity'] == 'error']
        assert errors == []
        slots = get_rack_slots(rack_inst['id'])
        assert any(s['instance_id'] == dev_inst['id'] for s in slots)

    def test_place_updates_instance_location(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=5)
        updated = get_hw_instance(dev_inst['id'])
        assert updated['location']['rack_id'] == rack_inst['id']
        assert updated['location']['u_pos']   == 5

    def test_u_overflow_error(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, u_size=10)
        dev_inst,  _ = self._make_device_instance(pid, u_size=2)
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=10)
        assert any(i['code'] == 'U_OVERFLOW' for i in issues)

    def test_u_overlap_error(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev1, _      = self._make_device_instance(pid)
        dev2, _      = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev1['id'], u_pos=5)
        issues = place_in_rack(rack_inst['id'], dev2['id'], u_pos=5)
        assert any(i['code'] == 'U_OCCUPIED' for i in issues)

    def test_form_factor_mismatch_ocp_in_19inch(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='19"')
        dev_inst,  _ = self._make_device_instance(pid, ff='OCP')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        assert any(i['code'] == 'FORM_FACTOR_MISMATCH' for i in issues)

    def test_form_factor_mismatch_19inch_in_ocp(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='OCP')
        dev_inst,  _ = self._make_device_instance(pid, ff='19"')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        assert any(i['code'] == 'FORM_FACTOR_MISMATCH' for i in issues)

    def test_21inch_rack_accepts_19inch(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='21"')
        dev_inst,  _ = self._make_device_instance(pid, ff='19"')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        ff_errs = [i for i in issues if i['code'] == 'FORM_FACTOR_MISMATCH']
        assert ff_errs == []

    def test_replace_existing_placement(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=3)
        slots = get_rack_slots(rack_inst['id'])
        positions = [s['u_pos'] for s in slots if s['instance_id'] == dev_inst['id']]
        assert positions == [3]

    def test_rack_layout_view_structure(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, u_size=5)
        dev_inst,  _ = self._make_device_instance(pid, u_size=2)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=4)
        view = rack_layout_view(rack_inst['id'])
        assert view['rack_u'] == 5
        types = [r['type'] for r in view['rows']]
        assert 'device' in types
        assert 'empty'  in types

    def test_remove_from_rack(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        _remove_from_rack(rack_inst['id'], dev_inst['id'])
        assert get_rack_slots(rack_inst['id']) == []


# ══════════════════════════════════════════════════════════════════════════════
# Cable helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestCableHelpers:
    def _make_cable(self, pid):
        return {
            'id':            _new_id(),
            'template_id':   None,
            'project_id':    pid,
            'asset_tag':     'CAB-001',
            'label':         'test cable',
            'length_m':      '1.0',
            'end_a':         {'instance_id': '', 'port_id': ''},
            'end_b':         {'instance_id': '', 'port_id': ''},
            'breakout':      False,
            'breakout_fan_out': 1,
        }

    def test_save_and_get(self):
        pid = _new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        assert get_cable(c['id'])['asset_tag'] == 'CAB-001'

    def test_delete(self):
        pid = _new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        delete_cable(c['id'])
        assert get_cable(c['id']) is None

    def test_project_cables_list(self):
        pid = _new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        cables = project_cables(pid)
        assert any(x['id'] == c['id'] for x in cables)

    def test_cables_isolated_between_projects(self):
        p1 = _new_id(); p2 = _new_id()
        c1 = self._make_cable(p1); c2 = self._make_cable(p2)
        save_cable(c1); save_cable(c2)
        assert not any(x['id'] == c2['id'] for x in project_cables(p1))

    def test_used_ports_detected(self):
        pid  = _new_id()
        iid  = _new_id()
        cable = {**self._make_cable(pid),
                 'end_a': {'instance_id': iid, 'port_id': 'p1'},
                 'end_b': {'instance_id': iid, 'port_id': 'p2'}}
        save_cable(cable)
        used = _used_ports(pid)
        assert (iid, 'p1') in used
        assert (iid, 'p2') in used


# ══════════════════════════════════════════════════════════════════════════════
# Validation engine
# ══════════════════════════════════════════════════════════════════════════════

class TestValidationEngine:
    def _make_project(self):
        pid = _new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        return pid

    def _server_tmpl(self, sfp_connector='SFP28'):
        t = {
            'id': _new_id(), 'name': 'Server', 'vendor': 'Dell', 'model': 'R650',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [
                {'id': 'eth0', 'name': 'eth0', 'port_type': 'data',
                 'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
                 'breakout_fan_out': 1, 'notes': ''},
                {'id': 'sfp0', 'name': 'sfp0', 'port_type': 'data',
                 'connector': sfp_connector, 'speed_gbps': 25, 'count': 1,
                 'breakout_fan_out': 1, 'notes': ''},
                {'id': 'psu0', 'name': 'psu0', 'port_type': 'power',
                 'connector': 'IEC-C14', 'speed_gbps': 0, 'count': 1,
                 'breakout_fan_out': 1, 'notes': ''},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(t)
        return t

    def _switch_tmpl(self):
        t = {
            'id': _new_id(), 'name': 'Switch', 'vendor': 'Cisco', 'model': 'N9K',
            'category': 'switch', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '',
            'ports': [
                {'id': 'swp0', 'name': 'swp0', 'port_type': 'data',
                 'connector': 'SFP28', 'speed_gbps': 25, 'count': 1,
                 'breakout_fan_out': 1, 'notes': ''},
                {'id': 'rj0', 'name': 'rj0', 'port_type': 'mgmt',
                 'connector': 'RJ45', 'speed_gbps': 1, 'count': 1,
                 'breakout_fan_out': 1, 'notes': ''},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(t)
        return t

    def _instance(self, pid, tmpl):
        inst = {
            'id': _new_id(), 'template_id': tmpl['id'], 'project_id': pid,
            'asset_tag': f'{tmpl["name"][:3]}-{_new_id()}',
            'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {},
        }
        save_hw_instance(inst)
        return inst

    def _dac_cable_tmpl(self, pid=''):
        t = {
            'id': _new_id(), 'name': 'DAC25G', 'vendor': 'ACME', 'model': 'D25',
            'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
            'cable_type': 'DAC', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': pid,
        }
        save_hw_template(t)
        return t

    def _cable(self, pid, tmpl_id, inst_a_id, port_a, inst_b_id, port_b, tag='CAB-001'):
        c = {
            'id':            _new_id(),
            'template_id':   tmpl_id,
            'project_id':    pid,
            'asset_tag':     tag,
            'label':         '',
            'length_m':      '1',
            'end_a':         {'instance_id': inst_a_id, 'port_id': port_a},
            'end_b':         {'instance_id': inst_b_id, 'port_id': port_b},
            'breakout':      False,
            'breakout_fan_out': 1,
        }
        save_cable(c)
        return c

    def test_clean_project_no_issues(self):
        seed_connectors()
        pid    = self._make_project()
        srv_t  = self._server_tmpl()
        sw_t   = self._switch_tmpl()
        srv    = self._instance(pid, srv_t)
        sw     = self._instance(pid, sw_t)
        dac_t  = self._dac_cable_tmpl()
        self._cable(pid, dac_t['id'], srv['id'], 'sfp0', sw['id'], 'swp0')
        issues = validate_project(pid)
        errors = [i for i in issues if i['severity'] == 'error']
        assert errors == []

    def test_connector_mismatch_detected(self):
        seed_connectors()
        pid   = self._make_project()
        # Server has RJ45 eth0, switch has SFP28 swp0 — incompatible
        srv_t = self._server_tmpl()
        sw_t  = self._switch_tmpl()
        srv   = self._instance(pid, srv_t)
        sw    = self._instance(pid, sw_t)
        dac_t = self._dac_cable_tmpl()
        self._cable(pid, dac_t['id'], srv['id'], 'eth0', sw['id'], 'swp0')
        issues = validate_project(pid)
        assert any(i['code'] == 'CONNECTOR_MISMATCH' for i in issues)

    def test_power_cable_on_data_port(self):
        seed_connectors()
        pid   = self._make_project()
        srv_t = self._server_tmpl()
        sw_t  = self._switch_tmpl()
        srv   = self._instance(pid, srv_t)
        sw    = self._instance(pid, sw_t)
        # Make a power cable template
        pwr_t = {
            'id': _new_id(), 'name': 'Power', 'vendor': '', 'model': '',
            'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
            'cable_type': 'power', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(pwr_t)
        # Connect power cable to data ports
        self._cable(pid, pwr_t['id'], srv['id'], 'sfp0', sw['id'], 'swp0')
        issues = validate_project(pid)
        assert any(i['code'] == 'CABLE_PORT_TYPE_MISMATCH' for i in issues)

    def test_speed_mismatch_warning_for_dac(self):
        seed_connectors()
        pid   = self._make_project()
        # Server SFP28 @25G, switch SFP28 @25G → OK
        # But let's make server have 10G SFP28
        srv_t_10g = self._server_tmpl(sfp_connector='SFP28')
        srv_t_10g['ports'][1]['speed_gbps'] = 10  # override to 10G
        save_hw_template(srv_t_10g)
        sw_t  = self._switch_tmpl()  # 25G SFP28
        srv   = self._instance(pid, srv_t_10g)
        sw    = self._instance(pid, sw_t)
        dac_t = self._dac_cable_tmpl()
        self._cable(pid, dac_t['id'], srv['id'], 'sfp0', sw['id'], 'swp0')
        issues = validate_project(pid)
        assert any(i['code'] == 'SPEED_MISMATCH' and i['severity'] == 'warning' for i in issues)

    def test_port_double_connected(self):
        seed_connectors()
        pid   = self._make_project()
        srv_t = self._server_tmpl()
        sw_t  = self._switch_tmpl()
        sw2_t = self._switch_tmpl()
        srv   = self._instance(pid, srv_t)
        sw1   = self._instance(pid, sw_t)
        sw2   = self._instance(pid, sw2_t)
        dac_t = self._dac_cable_tmpl()
        self._cable(pid, dac_t['id'], srv['id'], 'sfp0', sw1['id'], 'swp0', tag='CAB-001')
        self._cable(pid, dac_t['id'], srv['id'], 'sfp0', sw2['id'], 'swp0', tag='CAB-002')
        issues = validate_project(pid)
        assert any(i['code'] == 'PORT_DOUBLE_CONNECTED' for i in issues)

    def test_unconnected_cable_warning(self):
        seed_connectors()
        pid = self._make_project()
        c = {
            'id': _new_id(), 'template_id': None, 'project_id': pid,
            'asset_tag': 'LOOSE-CAB', 'label': '', 'length_m': '',
            'end_a': {'instance_id': '', 'port_id': ''},
            'end_b': {'instance_id': '', 'port_id': ''},
            'breakout': False, 'breakout_fan_out': 1,
        }
        save_cable(c)
        issues = validate_project(pid)
        codes  = {i['code'] for i in issues}
        assert 'CABLE_UNCONNECTED_A' in codes
        assert 'CABLE_UNCONNECTED_B' in codes

    def test_rack_u_overlap_detected(self):
        seed_connectors()
        pid      = self._make_project()
        rack_t   = {
            'id': _new_id(), 'name': 'Rack', 'vendor': 'APC', 'model': 'R42',
            'category': 'rack', 'form_factor': '19"', 'u_size': 42,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(rack_t)
        rack_inst = {
            'id': _new_id(), 'template_id': rack_t['id'], 'project_id': pid,
            'asset_tag': 'rack-001', 'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {},
        }
        save_hw_instance(rack_inst)

        srv_t = self._server_tmpl()
        d1    = self._instance(pid, srv_t)
        d2    = self._instance(pid, srv_t)

        # Force overlap by directly writing slots (bypass placement validation)
        save_rack_slots(rack_inst['id'], [
            {'u_pos': 5, 'instance_id': d1['id']},
            {'u_pos': 5, 'instance_id': d2['id']},
        ])
        issues = validate_project(pid)
        assert any(i['code'] == 'U_OVERLAP' for i in issues)

    def test_form_factor_mismatch_in_validation(self):
        seed_connectors()
        pid    = self._make_project()
        rack_t = {
            'id': _new_id(), 'name': 'OCP-Rack', 'vendor': 'Meta', 'model': 'OCP42',
            'category': 'rack', 'form_factor': 'OCP', 'u_size': 42,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(rack_t)
        rack_inst = {
            'id': _new_id(), 'template_id': rack_t['id'], 'project_id': pid,
            'asset_tag': 'ocp-rack-001', 'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {},
        }
        save_hw_instance(rack_inst)

        srv_t = self._server_tmpl()  # 19" server
        srv   = self._instance(pid, srv_t)
        # Force placement
        save_rack_slots(rack_inst['id'], [{'u_pos': 1, 'instance_id': srv['id']}])
        issues = validate_project(pid)
        assert any(i['code'] == 'FORM_FACTOR_MISMATCH' for i in issues)

    def test_no_issues_cached(self):
        seed_connectors()
        pid    = self._make_project()
        issues = validate_project(pid)
        from hw import load_validation
        cached = load_validation(pid)
        assert cached == issues

    def test_empty_project_no_issues(self):
        pid    = self._make_project()
        issues = validate_project(pid)
        assert issues == []
