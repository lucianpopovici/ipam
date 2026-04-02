"""Unit tests for hardware helper functions in hw_logic.py."""
import pytest

pytestmark = pytest.mark.unit

from hw_logic import (
    seed_connectors, all_connectors, add_connector, remove_connector,
    set_compat, connectors_compatible, full_compat_matrix,
    get_hw_template, save_hw_template, delete_hw_template,
    global_hw_templates, project_hw_templates,
    all_hw_templates_for_project,
    get_bom, save_bom, bom_with_templates,
    get_hw_instance, save_hw_instance,
    project_instances, generate_instances_from_bom_line,
    get_rack_slots, save_rack_slots, place_in_rack, rack_layout_view,
    _remove_from_rack,
    get_cable, save_cable, delete_cable, project_cables,
    _used_ports,
    validate_project,
)
from db import new_id
from ipam import save_project


# ══════════════════════════════════════════════════════════════════════════════
# Connector helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestConnectorHelpers:
    """Unit tests for connector helpers."""

    def test_seed_connectors_populates(self):
        """Test seeding connectors populates default values."""
        seed_connectors()
        conns = all_connectors()
        assert 'RJ45'    in conns
        assert 'SFP28'   in conns
        assert 'QSFP28'  in conns
        assert 'IEC-C13' in conns

    def test_seed_idempotent(self):
        """Test seeding is idempotent."""
        seed_connectors()
        seed_connectors()
        assert all_connectors().count('RJ45') == 1

    def test_add_connector(self):
        """Test adding a connector."""
        add_connector('CUSTOM-X')
        assert 'CUSTOM-X' in all_connectors()

    def test_new_connector_self_compatible(self):
        """Test new connector is self-compatible."""
        add_connector('CUSTOM-Y')
        assert connectors_compatible('CUSTOM-Y', 'CUSTOM-Y')

    def test_remove_connector(self):
        """Test removing a connector."""
        seed_connectors()
        add_connector('TEMP')
        remove_connector('TEMP')
        assert 'TEMP' not in all_connectors()

    def test_remove_cleans_compat(self):
        """Test removing a connector cleans compatibility matrix."""
        seed_connectors()
        add_connector('AA')
        add_connector('BB')
        set_compat('AA', 'BB', True)
        remove_connector('BB')
        assert not connectors_compatible('AA', 'BB')

    def test_set_compat_symmetric(self):
        """Test setting compatibility is symmetric."""
        seed_connectors()
        add_connector('C1'); add_connector('C2')
        set_compat('C1', 'C2', True)
        assert connectors_compatible('C1', 'C2')
        assert connectors_compatible('C2', 'C1')

    def test_unset_compat_symmetric(self):
        """Test unsetting compatibility is symmetric."""
        seed_connectors()
        add_connector('D1'); add_connector('D2')
        set_compat('D1', 'D2', True)
        set_compat('D1', 'D2', False)
        assert not connectors_compatible('D1', 'D2')
        assert not connectors_compatible('D2', 'D1')


class TestDefaultCompatMatrix:
    """Unit tests for default compatibility matrix."""

    def setup_method(self):
        """Setup for compatibility matrix tests."""
        seed_connectors()

    def test_rj45_only_compatible_with_rj45(self):
        """Verify RJ45 only compatible with RJ45."""
        assert connectors_compatible('RJ45', 'RJ45')
        assert not connectors_compatible('RJ45', 'SFP')
        assert not connectors_compatible('RJ45', 'SFP28')

    def test_sfp28_accepts_sfp_plus(self):
        """Verify SFP28 accepts SFP+."""
        assert connectors_compatible('SFP28', 'SFP+')

    def test_sfp28_accepts_sfp(self):
        """Verify SFP28 accepts SFP."""
        assert connectors_compatible('SFP28', 'SFP')

    def test_sfp_plus_accepts_sfp(self):
        """Verify SFP+ accepts SFP."""
        assert connectors_compatible('SFP+', 'SFP')

    def test_qsfp28_accepts_qsfp_dd(self):
        """Verify QSFP28 accepts QSFP-DD."""
        assert connectors_compatible('QSFP28', 'QSFP-DD')

    def test_qsfp_dd_accepts_qsfp28(self):
        """Verify QSFP-DD accepts QSFP28."""
        assert connectors_compatible('QSFP-DD', 'QSFP28')

    def test_power_connectors_isolated(self):
        """Verify power connectors are isolated from data connectors."""
        assert not connectors_compatible('IEC-C13', 'RJ45')
        assert not connectors_compatible('IEC-C13', 'SFP28')

    def test_iec_c13_c14_compatible(self):
        """Verify IEC-C13 and IEC-C14 are compatible."""
        assert connectors_compatible('IEC-C13', 'IEC-C14')

    def test_full_matrix_returns_all(self):
        """Verify full matrix returns all connectors."""
        matrix = full_compat_matrix()
        assert 'RJ45' in matrix
        assert 'SFP28' in matrix
        assert isinstance(matrix['RJ45'], list)


# ══════════════════════════════════════════════════════════════════════════════
# Hardware template helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestHWTemplateHelpers:
    """Unit tests for hardware template helpers."""

    def _tmpl(self, name='Server-1U', cat='server', scope='global', pid=''):
        """Create a template dictionary for testing."""
        return {
            'id':          new_id(),
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
        """Test saving and retrieving a template."""
        t = self._tmpl()
        save_hw_template(t)
        assert get_hw_template(t['id'])['name'] == 'Server-1U'

    def test_delete(self):
        """Test deleting a template."""
        t = self._tmpl()
        save_hw_template(t)
        delete_hw_template(t['id'])
        assert get_hw_template(t['id']) is None

    def test_global_template_in_list(self):
        """Verify global template appears in global list."""
        t = self._tmpl()
        save_hw_template(t)
        assert any(x['id'] == t['id'] for x in global_hw_templates())

    def test_project_template_in_project_list(self):
        """Verify project template appears in project list."""
        pid = new_id()
        t   = self._tmpl(scope='project', pid=pid)
        save_hw_template(t)
        assert any(x['id'] == t['id'] for x in project_hw_templates(pid))

    def test_category_filter(self):
        """Test filtering templates by category."""
        save_hw_template(self._tmpl(name='S1', cat='server'))
        save_hw_template(self._tmpl(name='R1', cat='rack'))
        servers = global_hw_templates(category='server')
        assert all(t['category'] == 'server' for t in servers)

    def test_available_templates_flat_list(self):
        """Verify flat list includes both global and project templates."""
        pid = new_id()
        g   = self._tmpl(name='Global')
        p   = self._tmpl(name='Proj', scope='project', pid=pid)
        save_hw_template(g); save_hw_template(p)
        flat = all_hw_templates_for_project(pid)
        ids  = {t['id'] for t in flat}
        assert g['id'] in ids
        assert p['id'] in ids

    def test_sorted_by_category_then_name(self):
        """Verify templates are sorted by category then name."""
        for name, cat in [('Z-server', 'server'), ('A-server', 'server'), ('A-rack', 'rack')]:
            save_hw_template(self._tmpl(name=name, cat=cat))
        tmpls = global_hw_templates()
        cats  = [t['category'] for t in tmpls]
        # rack < server alphabetically
        assert cats.index('rack') < cats.index('server')

    def test_ports_stored(self):
        """Verify template ports are correctly stored and retrieved."""
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
    """Unit tests for BoM (Bill of Materials) helpers."""

    def _setup(self):
        """Set up a test project and template."""
        pid  = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        tmpl = {
            'id': new_id(), 'name': 'Server-1U', 'vendor': 'ACME', 'model': 'X1',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        return pid, tmpl

    def test_empty_bom(self):
        """Test retrieving BoM for a project with no items."""
        pid, _ = self._setup()
        assert get_bom(pid) == []

    def test_save_and_get_bom(self):
        """Test saving and retrieving BoM items."""
        pid, tmpl = self._setup()
        bom = [{'id': new_id(), 'template_id': tmpl['id'], 'qty': 5,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        loaded = get_bom(pid)
        assert len(loaded) == 1
        assert loaded[0]['qty'] == 5

    def test_bom_with_templates_enriches(self):
        """Verify bom_with_templates correctly enriches BoM items with template data."""
        pid, tmpl = self._setup()
        bom = [{'id': new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        enriched = bom_with_templates(pid)
        assert enriched[0]['template']['name'] == 'Server-1U'

    def test_bom_missing_template_still_returned(self):
        """Verify BoM items with missing templates are still returned with None template."""
        pid, _ = self._setup()
        bom = [{'id': new_id(), 'template_id': 'nonexistent', 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 3, 'description': ''}]
        save_bom(pid, bom)
        enriched = bom_with_templates(pid)
        assert enriched[0]['template'] is None

    def test_bom_overwritten(self):
        """Test that saving a BoM overwrites the existing one."""
        pid, tmpl = self._setup()
        save_bom(pid, [{'id': new_id(), 'template_id': tmpl['id'], 'qty': 1,
                        'tag_prefix': 'a', 'tag_start': 1, 'tag_pad': 3, 'description': ''}])
        save_bom(pid, [])
        assert get_bom(pid) == []


# ══════════════════════════════════════════════════════════════════════════════
# Instance generation
# ══════════════════════════════════════════════════════════════════════════════

class TestInstanceGeneration:
    """Unit tests for hardware instance generation from BoM."""

    def _setup(self):
        """Set up a test project and template."""
        pid  = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        tmpl = {
            'id': new_id(), 'name': 'Server-1U', 'vendor': 'ACME', 'model': 'X1',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        return pid, tmpl

    def test_generates_correct_count(self):
        """Test that the correct number of instances are generated."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 5,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 3, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert len(created) == 5

    def test_asset_tags_sequential(self):
        """Verify generated asset tags are sequential."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 3,
                'tag_prefix': 'srv', 'tag_start': 10, 'tag_pad': 3, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        tags = [i['asset_tag'] for i in created]
        assert tags == ['srv-010', 'srv-011', 'srv-012']

    def test_zero_padding(self):
        """Verify asset tag sequential numbers are correctly padded."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'rack', 'tag_start': 1, 'tag_pad': 4, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert created[0]['asset_tag'] == 'rack-0001'
        assert created[1]['asset_tag'] == 'rack-0002'

    def test_instances_saved_to_redis(self):
        """Verify generated instances are persisted."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'sw', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        for inst in created:
            assert get_hw_instance(inst['id']) is not None

    def test_instances_appear_in_project_list(self):
        """Verify generated instances appear in project instance list."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 2,
                'tag_prefix': 'srv', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        instances = project_instances(pid)
        ids = {i['id'] for i in instances}
        for inst in created:
            assert inst['id'] in ids

    def test_missing_template_raises(self):
        """Test that generation fails if template is missing."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        item = {'id': new_id(), 'template_id': 'no-such-tmpl', 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        with pytest.raises(ValueError, match='not found'):
            generate_instances_from_bom_line(pid, item)

    def test_instance_status_defaults_to_in_stock(self):
        """Verify default status for new instances."""
        pid, tmpl = self._setup()
        item = {'id': new_id(), 'template_id': tmpl['id'], 'qty': 1,
                'tag_prefix': 'x', 'tag_start': 1, 'tag_pad': 2, 'description': ''}
        created = generate_instances_from_bom_line(pid, item)
        assert created[0]['status'] == 'in-stock'


# ══════════════════════════════════════════════════════════════════════════════
# Rack placement helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestRackPlacement:
    """Unit tests for rack placement and layout helpers."""

    def _make_rack_instance(self, pid, ff='19"', u_size=42):
        """Create a rack instance for testing."""
        tmpl = {
            'id': new_id(), 'name': 'Rack', 'vendor': 'APC', 'model': 'AR3000',
            'category': 'rack', 'form_factor': ff, 'u_size': u_size,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = {'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
                'asset_tag': 'rack-001', 'serial': '', 'status': 'deployed',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(inst)
        return inst, tmpl

    def _make_device_instance(self, pid, ff='19"', u_size=1, cat='server'):
        """Create a device instance for testing rack placement."""
        tmpl = {
            'id': new_id(), 'name': 'Server', 'vendor': 'Dell', 'model': 'R650',
            'category': cat, 'form_factor': ff, 'u_size': u_size,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(tmpl)
        inst = {'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
                'asset_tag': f'dev-{new_id()}', 'serial': '', 'status': 'in-stock',
                'location': {}, 'port_overrides': {}}
        save_hw_instance(inst)
        return inst, tmpl

    def test_place_success(self):
        """Test successful placement of a device in a rack."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=10)
        errors = [i for i in issues if i['severity'] == 'error']
        assert errors == []
        slots = get_rack_slots(rack_inst['id'])
        assert any(s['instance_id'] == dev_inst['id'] for s in slots)

    def test_place_updates_instance_location(self):
        """Verify that placement updates the device's location field."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=5)
        updated = get_hw_instance(dev_inst['id'])
        assert updated['location']['rack_id'] == rack_inst['id']
        assert updated['location']['u_pos']   == 5

    def test_u_overflow_error(self):
        """Verify error when device exceeds rack height."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, u_size=10)
        dev_inst,  _ = self._make_device_instance(pid, u_size=2)
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=10)
        assert any(i['code'] == 'U_OVERFLOW' for i in issues)

    def test_u_overlap_error(self):
        """Verify error when placing a device in occupied slots."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev1, _      = self._make_device_instance(pid)
        dev2, _      = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev1['id'], u_pos=5)
        issues = place_in_rack(rack_inst['id'], dev2['id'], u_pos=5)
        assert any(i['code'] == 'U_OCCUPIED' for i in issues)

    def test_form_factor_mismatch_ocp_in_19inch(self):
        """Verify error when placing OCP device in 19" rack."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='19"')
        dev_inst,  _ = self._make_device_instance(pid, ff='OCP')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        assert any(i['code'] == 'FORM_FACTOR_MISMATCH' for i in issues)

    def test_form_factor_mismatch_19inch_in_ocp(self):
        """Verify error when placing 19" device in OCP rack."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='OCP')
        dev_inst,  _ = self._make_device_instance(pid, ff='19"')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        assert any(i['code'] == 'FORM_FACTOR_MISMATCH' for i in issues)

    def test_21inch_rack_accepts_19inch(self):
        """Verify that 21" racks can accept 19" devices."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid, ff='21"')
        dev_inst,  _ = self._make_device_instance(pid, ff='19"')
        issues = place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        ff_errs = [i for i in issues if i['code'] == 'FORM_FACTOR_MISMATCH']
        assert ff_errs == []

    def test_replace_existing_placement(self):
        """Verify that re-placing a device moves it from its old position."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        rack_inst, _ = self._make_rack_instance(pid)
        dev_inst,  _ = self._make_device_instance(pid)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=1)
        place_in_rack(rack_inst['id'], dev_inst['id'], u_pos=3)
        slots = get_rack_slots(rack_inst['id'])
        positions = [s['u_pos'] for s in slots if s['instance_id'] == dev_inst['id']]
        assert positions == [3]

    def test_rack_layout_view_structure(self):
        """Verify the structure of the rack layout view data."""
        pid = new_id()
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
        """Test removing a device from a rack."""
        pid = new_id()
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
    """Unit tests for cable-related helpers."""

    def _make_cable(self, pid):
        """Create a cable dictionary for testing."""
        return {
            'id':            new_id(),
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
        """Test saving and retrieving a cable."""
        pid = new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        assert get_cable(c['id'])['asset_tag'] == 'CAB-001'

    def test_delete(self):
        """Test deleting a cable."""
        pid = new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        delete_cable(c['id'])
        assert get_cable(c['id']) is None

    def test_project_cables_list(self):
        """Verify cable appears in project cable list."""
        pid = new_id()
        c   = self._make_cable(pid)
        save_cable(c)
        cables = project_cables(pid)
        assert any(x['id'] == c['id'] for x in cables)

    def test_cables_isolated_between_projects(self):
        """Verify cables are isolated between projects."""
        p1 = new_id(); p2 = new_id()
        c1 = self._make_cable(p1); c2 = self._make_cable(p2)
        save_cable(c1); save_cable(c2)
        assert not any(x['id'] == c2['id'] for x in project_cables(p1))

    def test_used_ports_detected(self):
        """Verify that _used_ports correctly identifies ports with connected cables."""
        pid  = new_id()
        iid  = new_id()
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
    """Unit tests for the hardware validation engine."""

    def _make_project(self):
        """Create a project for validation tests."""
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        return pid

    def _server_tmpl(self, sfp_connector='SFP28'):
        """Create a server template for validation tests."""
        t = {
            'id': new_id(), 'name': 'Server', 'vendor': 'Dell', 'model': 'R650',
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
        """Create a switch template for validation tests."""
        t = {
            'id': new_id(), 'name': 'Switch', 'vendor': 'Cisco', 'model': 'N9K',
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
        """Create a hardware instance for validation tests."""
        inst = {
            'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
            'asset_tag': f'{tmpl["name"][:3]}-{new_id()}',
            'serial': '', 'status': 'deployed',
            'location': {}, 'port_overrides': {},
        }
        save_hw_instance(inst)
        return inst

    def _dac_cable_tmpl(self, pid=''):
        """Create a DAC cable template for validation tests."""
        t = {
            'id': new_id(), 'name': 'DAC25G', 'vendor': 'ACME', 'model': 'D25',
            'category': 'cable', 'form_factor': 'N/A', 'u_size': 0,
            'cable_type': 'DAC', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': pid,
        }
        save_hw_template(t)
        return t

    def _cable(self, pid, tmpl_id, inst_a_id, port_a, inst_b_id, port_b, tag='CAB-001'):
        """Create a cable for validation tests."""
        c = {
            'id':            new_id(),
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
        """Verify that a valid project configuration returns no issues."""
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
        """Verify detection of mismatched connectors between cable and port."""
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
        """Verify detection of cable port type mismatch."""
        seed_connectors()
        pid   = self._make_project()
        srv_t = self._server_tmpl()
        sw_t  = self._switch_tmpl()
        srv   = self._instance(pid, srv_t)
        sw    = self._instance(pid, sw_t)
        # Make a power cable template
        pwr_t = {
            'id': new_id(), 'name': 'Power', 'vendor': '', 'model': '',
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
        """Verify warning for speed mismatch on DAC cables."""
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
        """Verify detection of ports connected to multiple cables."""
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
        """Verify warning for cables with unconnected ends."""
        seed_connectors()
        pid = self._make_project()
        c = {
            'id': new_id(), 'template_id': None, 'project_id': pid,
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
        """Verify detection of overlapping devices in rack slots."""
        seed_connectors()
        pid      = self._make_project()
        rack_t   = {
            'id': new_id(), 'name': 'Rack', 'vendor': 'APC', 'model': 'R42',
            'category': 'rack', 'form_factor': '19"', 'u_size': 42,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(rack_t)
        rack_inst = {
            'id': new_id(), 'template_id': rack_t['id'], 'project_id': pid,
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
        """Verify detection of form factor mismatch between device and rack."""
        seed_connectors()
        pid    = self._make_project()
        rack_t = {
            'id': new_id(), 'name': 'OCP-Rack', 'vendor': 'Meta', 'model': 'OCP42',
            'category': 'rack', 'form_factor': 'OCP', 'u_size': 42,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': '',
        }
        save_hw_template(rack_t)
        rack_inst = {
            'id': new_id(), 'template_id': rack_t['id'], 'project_id': pid,
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
        """Verify that validation issues are correctly cached."""
        seed_connectors()
        pid    = self._make_project()
        issues = validate_project(pid)
        from hw import load_validation
        cached = load_validation(pid)
        assert cached == issues

    def test_empty_project_no_issues(self):
        """Verify that an empty project has no validation issues."""
        pid    = self._make_project()
        issues = validate_project(pid)
        assert issues == []
