"""

pytestmark = pytest.mark.unit

Unit tests for ne.py helper functions.
"""
import pytest
from ne import (
    expand_site_pattern,
    get_schema, save_schema,
    get_ne_type, save_ne_type, delete_ne_type,
    global_ne_types, project_ne_types, available_ne_types,
    get_site, save_site, delete_site, project_sites, site_pods,
    get_pod, save_pod, delete_pod, project_pods, pod_sites,
    get_pod_slots, save_pod_slots,
    assign_pod_to_site, unassign_pod_from_site,
    compute_requirements, save_requirements, load_requirements,
    _sharing_count, collect_params,
)
from db import new_id, parse_labels
from ipam import save_project


# ══════════════════════════════════════════════════════════════════════════════
# expand_site_pattern
# ══════════════════════════════════════════════════════════════════════════════

class TestExpandSitePattern:
    def test_basic_pattern(self):
        names, err = expand_site_pattern('ran{0001..0005}')
        assert err is None
        assert names == ['ran0001', 'ran0002', 'ran0003', 'ran0004', 'ran0005']

    def test_zero_padding_preserved(self):
        names, err = expand_site_pattern('site-{01..03}-prod')
        assert err is None
        assert names == ['site-01-prod', 'site-02-prod', 'site-03-prod']

    def test_single_item_range(self):
        names, err = expand_site_pattern('ran{001..001}')
        assert err is None
        assert names == ['ran001']

    def test_no_braces_returns_error(self):
        names, err = expand_site_pattern('ran0001')
        assert err is not None
        assert names is None

    def test_end_less_than_start_error(self):
        _, err = expand_site_pattern('ran{005..001}')
        assert err is not None

    def test_large_range_error(self):
        _, err = expand_site_pattern('ran{00001..10001}')
        assert err is not None

    def test_prefix_and_suffix(self):
        names, err = expand_site_pattern('LON-{001..003}-dc')
        assert err is None
        assert names[0] == 'LON-001-dc'
        assert names[-1] == 'LON-003-dc'

    def test_count_is_correct(self):
        names, err = expand_site_pattern('s{001..100}')
        assert err is None
        assert len(names) == 100

    def test_padding_width_from_start(self):
        # start is '1' (no padding) → no zero-pad
        names, err = expand_site_pattern('s{1..3}')
        assert err is None
        assert names == ['s1', 's2', 's3']


# ══════════════════════════════════════════════════════════════════════════════
# Schema helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSchemaHelpers:
    def _field(self, label='Region', ftype='text'):
        return {
            'id':         new_id(),
            'name':       label.lower(),
            'label':      label,
            'field_type': ftype,
            'required':   False,
            'options':    [],
            'default':    '',
        }

    def test_save_and_get_global(self):
        fields = [self._field('Region'), self._field('Country')]
        save_schema('site', fields)
        loaded = get_schema('site')
        assert len(loaded) == 2
        assert loaded[0]['label'] == 'Region'

    def test_project_overrides_global(self):
        pid = new_id()
        save_schema('site', [self._field('Global-Field')])
        save_schema('site', [self._field('Project-Field')], pid=pid)
        assert get_schema('site', pid)[0]['label'] == 'Project-Field'

    def test_project_falls_back_to_global(self):
        pid = new_id()
        save_schema('site', [self._field('Global-Field')])
        # No project override
        assert get_schema('site', pid)[0]['label'] == 'Global-Field'

    def test_empty_schema_returns_list(self):
        assert get_schema('pod') == []

    def test_all_entity_types(self):
        for entity in ('site', 'pod', 'ne', 'interface'):
            save_schema(entity, [self._field(f'{entity}-field')])
            assert len(get_schema(entity)) == 1

    def test_overwrite_schema(self):
        save_schema('ne', [self._field('F1')])
        save_schema('ne', [self._field('F2'), self._field('F3')])
        assert len(get_schema('ne')) == 2


# ══════════════════════════════════════════════════════════════════════════════
# NE Type helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestNETypeHelpers:
    def _ne(self, name='Firewall', scope='global', pid=''):
        return {
            'id':          new_id(),
            'name':        name,
            'kind':        'VNF',
            'description': '',
            'labels':      [],
            'params':      {},
            'interfaces':  [],
            'scope':       scope,
            'project_id':  pid,
        }

    def test_save_and_get(self):
        ne = self._ne()
        save_ne_type(ne)
        assert get_ne_type(ne['id'])['name'] == 'Firewall'

    def test_global_ne_type_in_global_list(self):
        ne = self._ne()
        save_ne_type(ne)
        assert any(x['id'] == ne['id'] for x in global_ne_types())

    def test_project_ne_type_in_project_list(self):
        pid = new_id()
        ne  = self._ne(scope='project', pid=pid)
        save_ne_type(ne)
        assert any(x['id'] == ne['id'] for x in project_ne_types(pid))

    def test_global_not_in_project_list(self):
        pid = new_id()
        ne  = self._ne()
        save_ne_type(ne)
        assert not any(x['id'] == ne['id'] for x in project_ne_types(pid))

    def test_delete(self):
        ne = self._ne()
        save_ne_type(ne)
        delete_ne_type(ne['id'])
        assert get_ne_type(ne['id']) is None

    def test_available_ne_types(self):
        pid = new_id()
        g   = self._ne(name='Global')
        p   = self._ne(name='Project', scope='project', pid=pid)
        save_ne_type(g); save_ne_type(p)
        av  = available_ne_types(pid)
        assert any(x['id'] == g['id'] for x in av['global'])
        assert any(x['id'] == p['id'] for x in av['project'])

    def test_ne_type_with_interfaces(self):
        ne = self._ne()
        ne['interfaces'] = [
            {'id': 'i1', 'name': 'mgmt', 'labels': [], 'params': {},
             'ipv4': {'prefix_len': 29}, 'ipv6': None, 'sharing': 'pod'},
        ]
        save_ne_type(ne)
        loaded = get_ne_type(ne['id'])
        assert loaded['interfaces'][0]['name'] == 'mgmt'
        assert loaded['interfaces'][0]['ipv4']['prefix_len'] == 29

    def test_sorted_by_name(self):
        for name in ('Zebra', 'Alpha', 'Middle'):
            save_ne_type(self._ne(name=name))
        names = [t['name'] for t in global_ne_types()]
        assert names == sorted(names)


# ══════════════════════════════════════════════════════════════════════════════
# Site helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestSiteHelpers:
    def _proj(self):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        return pid

    def _site(self, pid, name='LON-DC1'):
        return {
            'id':          new_id(),
            'name':        name,
            'project_id':  pid,
            'description': '',
            'labels':      ['London'],
            'params':      {},
        }

    def test_save_and_get(self):
        pid  = self._proj()
        site = self._site(pid)
        save_site(site)
        assert get_site(site['id'])['name'] == 'LON-DC1'

    def test_project_sites_list(self):
        pid  = self._proj()
        s1   = self._site(pid, 'LON-DC1')
        s2   = self._site(pid, 'AMS-DC1')
        save_site(s1); save_site(s2)
        names = [s['name'] for s in project_sites(pid)]
        assert 'LON-DC1' in names
        assert 'AMS-DC1' in names

    def test_delete_site(self):
        pid  = self._proj()
        site = self._site(pid)
        save_site(site)
        delete_site(site['id'])
        assert get_site(site['id']) is None
        assert not any(s['id'] == site['id'] for s in project_sites(pid))

    def test_sorted_by_name(self):
        pid = self._proj()
        for name in ('ZZZ', 'AAA', 'MMM'):
            save_site(self._site(pid, name))
        names = [s['name'] for s in project_sites(pid)]
        assert names == sorted(names)

    def test_labels_stored(self):
        pid  = self._proj()
        site = self._site(pid)
        site['labels'] = ['PROD', 'Europe']
        save_site(site)
        loaded = get_site(site['id'])
        assert 'PROD' in loaded['labels']

    def test_sites_isolated_between_projects(self):
        p1 = self._proj()
        p2 = self._proj()
        save_site(self._site(p1, 'P1-site'))
        save_site(self._site(p2, 'P2-site'))
        assert all(s['name'] != 'P2-site' for s in project_sites(p1))


# ══════════════════════════════════════════════════════════════════════════════
# POD helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestPodHelpers:
    def _proj(self):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        return pid

    def _site(self, pid):
        s = {'id': new_id(), 'name': 'S1', 'project_id': pid,
             'description': '', 'labels': [], 'params': {}}
        save_site(s); return s

    def _pod(self, pid, name='CORE-POD-1'):
        return {'id': new_id(), 'name': name, 'project_id': pid,
                'description': '', 'labels': [], 'params': {}}

    def test_save_and_get(self):
        pid = self._proj()
        pod = self._pod(pid)
        save_pod(pod)
        assert get_pod(pod['id'])['name'] == 'CORE-POD-1'

    def test_project_pods_list(self):
        pid = self._proj()
        p1  = self._pod(pid, 'P1')
        p2  = self._pod(pid, 'P2')
        save_pod(p1); save_pod(p2)
        names = [p['name'] for p in project_pods(pid)]
        assert 'P1' in names and 'P2' in names

    def test_delete_pod(self):
        pid = self._proj()
        pod = self._pod(pid)
        save_pod(pod)
        delete_pod(pod['id'])
        assert get_pod(pod['id']) is None

    def test_assign_and_unassign_pod_to_site(self):
        pid  = self._proj()
        pod  = self._pod(pid)
        save_pod(pod)
        site = self._site(pid)
        assign_pod_to_site(pod['id'], site['id'])
        assert any(p['id'] == pod['id'] for p in site_pods(site['id']))
        assert any(s['id'] == site['id'] for s in pod_sites(pod['id']))
        unassign_pod_from_site(pod['id'], site['id'])
        assert not any(p['id'] == pod['id'] for p in site_pods(site['id']))

    def test_pod_slots_round_trip(self):
        pid = self._proj()
        pod = self._pod(pid)
        save_pod(pod)
        slots = [
            {'ne_type_id': 'ne1', 'count': 3, 'label_override': ['LB']},
            {'ne_type_id': 'ne2', 'count': 1, 'label_override': []},
        ]
        save_pod_slots(pod['id'], slots)
        loaded = get_pod_slots(pod['id'])
        assert len(loaded) == 2
        assert loaded[0]['ne_type_id'] == 'ne1'
        assert loaded[0]['count'] == 3

    def test_empty_slots_returns_empty_list(self):
        pid = self._proj()
        pod = self._pod(pid)
        save_pod(pod)
        assert get_pod_slots(pod['id']) == []


# ══════════════════════════════════════════════════════════════════════════════
# _sharing_count
# ══════════════════════════════════════════════════════════════════════════════

class TestSharingCount:
    def test_project_level(self):
        assert _sharing_count('project', 10) == 1

    def test_site_level(self):
        assert _sharing_count('site', 10) == 1

    def test_pod_level(self):
        assert _sharing_count('pod', 10) == 1

    def test_ne_level(self):
        assert _sharing_count('ne', 10) == 1

    def test_interface_level(self):
        assert _sharing_count('interface', 5) == 5

    def test_interface_single_instance(self):
        assert _sharing_count('interface', 1) == 1


# ══════════════════════════════════════════════════════════════════════════════
# compute_requirements
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeRequirements:
    def _setup_full(self):
        """
        Build: 1 project → 1 site → 1 pod → 1 NE type (2 interfaces)
        """
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})

        ne = {
            'id': new_id(), 'name': 'Router', 'kind': 'PNF',
            'description': '', 'labels': ['backbone'], 'params': {},
            'interfaces': [
                {'id': 'i1', 'name': 'mgmt', 'labels': ['mgmt'], 'params': {},
                 'ipv4': {'prefix_len': 29}, 'ipv6': None, 'sharing': 'ne'},
                {'id': 'i2', 'name': 'data', 'labels': ['data'], 'params': {},
                 'ipv4': {'prefix_len': 30}, 'ipv6': {'prefix_len': 64},
                 'sharing': 'interface'},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_ne_type(ne)

        site = {'id': new_id(), 'name': 'LON', 'project_id': pid,
                'description': '', 'labels': ['London'], 'params': {}}
        save_site(site)

        pod = {'id': new_id(), 'name': 'POD1', 'project_id': pid,
               'description': '', 'labels': ['core'], 'params': {}}
        save_pod(pod)
        assign_pod_to_site(pod['id'], site['id'])

        # 3 instances of the NE
        save_pod_slots(pod['id'], [
            {'ne_type_id': ne['id'], 'count': 3, 'label_override': []}
        ])

        return pid, ne, site, pod

    def test_requirement_count(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        # mgmt / ne sharing → 1 subnet (shared across 3 instances)
        # data / interface / ipv4 → 3 subnets (one per instance)
        # data / interface / ipv6 → 3 subnets
        assert len(reqs) == 3   # mgmt-ipv4 + data-ipv4 + data-ipv6

    def test_ne_sharing_count_is_1(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        mgmt = next(r for r in reqs if r['iface_name'] == 'mgmt')
        assert mgmt['count'] == 1

    def test_interface_sharing_count_equals_ne_count(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        data_reqs = [r for r in reqs if r['iface_name'] == 'data']
        for req in data_reqs:
            assert req['count'] == 3

    def test_label_union(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        mgmt = next(r for r in reqs if r['iface_name'] == 'mgmt')
        # Should contain labels from site + pod + ne + interface
        assert 'London'   in mgmt['labels']
        assert 'core'     in mgmt['labels']
        assert 'backbone' in mgmt['labels']
        assert 'mgmt'     in mgmt['labels']

    def test_both_ip_versions(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        versions = {r['ip_version'] for r in reqs if r['iface_name'] == 'data'}
        assert 'ipv4' in versions
        assert 'ipv6' in versions

    def test_project_sharing_dedup(self):
        pid  = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})

        ne = {
            'id': new_id(), 'name': 'NTP', 'kind': 'VNF',
            'description': '', 'labels': [], 'params': {},
            'interfaces': [
                {'id': 'i1', 'name': 'mgmt', 'labels': [], 'params': {},
                 'ipv4': {'prefix_len': 28}, 'ipv6': None, 'sharing': 'project'},
            ],
            'scope': 'global', 'project_id': '',
        }
        save_ne_type(ne)

        # Two sites, each with a pod hosting this NE
        for site_name in ('SITE-A', 'SITE-B'):
            site = {'id': new_id(), 'name': site_name, 'project_id': pid,
                    'description': '', 'labels': [], 'params': {}}
            save_site(site)
            pod = {'id': new_id(), 'name': f'POD-{site_name}', 'project_id': pid,
                   'description': '', 'labels': [], 'params': {}}
            save_pod(pod)
            assign_pod_to_site(pod['id'], site['id'])
            save_pod_slots(pod['id'], [{'ne_type_id': ne['id'], 'count': 1, 'label_override': []}])

        reqs = compute_requirements(pid)
        # project-level sharing → only 1 requirement despite 2 sites
        assert len(reqs) == 1
        assert reqs[0]['count'] == 1

    def test_no_sites_returns_empty(self):
        pid = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        assert compute_requirements(pid) == []

    def test_site_with_no_pods_returns_empty(self):
        pid  = new_id()
        save_project({'id': pid, 'name': 'p', 'supernet': '10.0.0.0/8', 'description': ''})
        site = {'id': new_id(), 'name': 'S', 'project_id': pid,
                'description': '', 'labels': [], 'params': {}}
        save_site(site)
        assert compute_requirements(pid) == []

    def test_requirements_persisted(self):
        pid, _, _, _ = self._setup_full()
        reqs = compute_requirements(pid)
        save_requirements(pid, reqs)
        loaded = load_requirements(pid)
        assert len(loaded) == len(reqs)


# ══════════════════════════════════════════════════════════════════════════════
# collect_params
# ══════════════════════════════════════════════════════════════════════════════

class TestCollectParams:
    def test_text_field(self):
        schema = [{'id': 'f1', 'field_type': 'text', 'label': 'Region'}]
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request
        builder = EnvironBuilder(method='POST', data={'f1': 'EU-WEST'})
        env     = builder.get_environ()
        req     = Request(env)
        result  = collect_params(schema, req.form)
        assert result['f1'] == 'EU-WEST'

    def test_checkbox_true(self):
        schema = [{'id': 'cb', 'field_type': 'checkbox', 'label': 'Active'}]
        # Use ImmutableMultiDict directly for simpler testing
        from werkzeug.datastructures import ImmutableMultiDict
        form = ImmutableMultiDict([('cb', 'on')])
        assert collect_params(schema, form)['cb'] is True

    def test_checkbox_false(self):
        schema = [{'id': 'cb', 'field_type': 'checkbox', 'label': 'Active'}]
        from werkzeug.datastructures import ImmutableMultiDict
        form = ImmutableMultiDict([])
        assert collect_params(schema, form)['cb'] is False

    def test_multiselect(self):
        schema = [{'id': 'ms', 'field_type': 'multi-select', 'label': 'Tags'}]
        from werkzeug.datastructures import ImmutableMultiDict
        form = ImmutableMultiDict([('ms', 'A'), ('ms', 'B')])
        result = collect_params(schema, form)
        assert sorted(result['ms']) == ['A', 'B']
