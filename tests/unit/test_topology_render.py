"""Unit tests for topology Mermaid / DOT renderers."""
import pytest
from topology import (
    render_l3_mermaid, render_l1_mermaid, render_l3_dot, render_l1_dot,
    _mid, _mermaid_label,
)
from ipam import new_id, save_network, save_ip, add_labels_to_network
import ipam as _ipam_mod
from hw_logic import save_hw_template, save_hw_instance, save_cable


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _net(cidr, labels=None):
    pid = new_id()
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr, 'description': '',
           'vlan': '', 'project_id': pid, 'template_id': None, 'pending_slots': []}
    save_network(net)
    # Register with project index (save_network only writes to networks:index)
    _ipam_mod.r.sadd(f'project:{pid}:networks', net['id'])
    if labels:
        add_labels_to_network(net['id'], labels)
    return pid, net


def _net_in_project(pid, cidr, labels=None):
    net = {'id': new_id(), 'name': cidr, 'cidr': cidr, 'description': '',
           'vlan': '', 'project_id': pid, 'template_id': None, 'pending_slots': []}
    save_network(net)
    _ipam_mod.r.sadd(f'project:{pid}:networks', net['id'])
    if labels:
        add_labels_to_network(net['id'], labels)
    return net


def _ip(net_id, ip_str, hostname=''):
    save_ip({'ip': ip_str, 'hostname': hostname, 'description': '',
             'status': 'allocated', 'network_id': net_id})


def _hw_inst(pid, asset_tag='srv-01', rack_id=None):
    tmpl = {'id': new_id(), 'name': 'Server', 'vendor': 'X', 'model': 'Y',
            'category': 'server', 'form_factor': '19"', 'u_size': 1,
            'cable_type': '', 'description': '', 'ports': [],
            'scope': 'global', 'project_id': ''}
    save_hw_template(tmpl)
    loc = {'rack_id': rack_id} if rack_id else {}
    inst = {'id': new_id(), 'template_id': tmpl['id'], 'project_id': pid,
            'asset_tag': asset_tag, 'serial': '', 'status': 'deployed',
            'location': loc, 'port_overrides': {}}
    save_hw_instance(inst)
    return inst


def _cable(pid, inst_a, inst_b, tag='CAB-01'):
    c = {'id': new_id(), 'template_id': None, 'project_id': pid,
         'asset_tag': tag, 'label': '', 'length_m': '',
         'end_a': {'instance_id': inst_a['id'], 'port_id': 'p0'},
         'end_b': {'instance_id': inst_b['id'], 'port_id': 'p0'},
         'breakout': False, 'breakout_fan_out': 1}
    save_cable(c)
    return c


# ── _mid helper ───────────────────────────────────────────────────────────────────

class TestMid:
    def test_basic(self):
        assert _mid('net', 'abc123') == 'net_abc123'

    def test_sanitises_hyphens_and_dots(self):
        mid = _mid('ip', '10.0.0.1')
        assert '.' not in mid
        assert mid.startswith('ip_')

    def test_sanitises_slash(self):
        mid = _mid('net', '10.0.0.0/24')
        assert '/' not in mid


class TestMermaidLabel:
    def test_passthrough(self):
        assert _mermaid_label('hello') == 'hello'

    def test_truncates_long(self):
        label = _mermaid_label('a' * 50)
        assert len(label) <= 31  # max_len + 1 for ellipsis char

    def test_escapes_double_quotes(self):
        assert '"' not in _mermaid_label('say "hi"')


# ── L3 Mermaid renderer ───────────────────────────────────────────────────────────

class TestRenderL3Mermaid:
    def test_empty_project(self, fake_redis):
        pid = new_id()
        graph, truncated = render_l3_mermaid(pid)
        assert 'graph LR' in graph
        assert not truncated

    def test_subnet_node_emitted(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        graph, _ = render_l3_mermaid(pid)
        assert '10.0.1.0/24' in graph

    def test_ip_node_with_hostname(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        _ip(net['id'], '10.0.1.10', 'srv01')
        graph, _ = render_l3_mermaid(pid)
        assert '10.0.1.10' in graph
        assert 'srv01' in graph

    def test_ip_without_hostname_not_emitted(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        _ip(net['id'], '10.0.1.99', '')
        graph, _ = render_l3_mermaid(pid)
        assert '10.0.1.99' not in graph

    def test_edge_subnet_to_ip(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        _ip(net['id'], '10.0.1.5', 'host5')
        graph, _ = render_l3_mermaid(pid)
        assert '-->' in graph

    def test_label_filter_includes(self, fake_redis):
        pid = new_id()
        net_in  = _net_in_project(pid, '10.0.1.0/24', labels=['prod'])
        net_out = _net_in_project(pid, '10.0.2.0/24', labels=['test'])
        _ip(net_in['id'],  '10.0.1.1', 'h1')
        _ip(net_out['id'], '10.0.2.1', 'h2')
        graph, _ = render_l3_mermaid(pid, label_filter=['prod'])
        assert '10.0.1.0/24' in graph
        assert '10.0.2.0/24' not in graph

    def test_truncation(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        for i in range(1, 20):
            _ip(net['id'], f'10.0.1.{i}', f'host{i}')
        graph, truncated = render_l3_mermaid(pid, max_nodes=5)
        assert truncated

    def test_no_truncation_when_under_cap(self, fake_redis):
        pid, net = _net('10.0.1.0/24')
        _ip(net['id'], '10.0.1.1', 'h1')
        _ip(net['id'], '10.0.1.2', 'h2')
        graph, truncated = render_l3_mermaid(pid, max_nodes=80)
        assert not truncated

    def test_starts_with_graph_lr(self, fake_redis):
        pid = new_id()
        graph, _ = render_l3_mermaid(pid)
        assert graph.startswith('graph LR')


# ── L1 Mermaid renderer ───────────────────────────────────────────────────────────

class TestRenderL1Mermaid:
    def test_empty_project(self, fake_redis):
        pid = new_id()
        graph, truncated = render_l1_mermaid(pid)
        assert 'graph TB' in graph
        assert not truncated

    def test_instance_node_emitted(self, fake_redis):
        pid = new_id()
        inst = _hw_inst(pid, asset_tag='spine-01')
        graph, _ = render_l1_mermaid(pid)
        assert 'spine-01' in graph

    def test_rack_subgraph(self, fake_redis):
        pid  = new_id()
        rack = _hw_inst(pid, asset_tag='rack-A01')
        dev  = _hw_inst(pid, asset_tag='sw-01', rack_id=rack['id'])
        graph, _ = render_l1_mermaid(pid)
        assert 'subgraph' in graph

    def test_cable_edge(self, fake_redis):
        pid  = new_id()
        srv  = _hw_inst(pid, asset_tag='srv-01')
        sw   = _hw_inst(pid, asset_tag='sw-01')
        _cable(pid, srv, sw, tag='DAC-001')
        graph, _ = render_l1_mermaid(pid)
        assert 'DAC-001' in graph
        assert '---' in graph

    def test_truncation(self, fake_redis):
        pid = new_id()
        for i in range(20):
            _hw_inst(pid, asset_tag=f'srv-{i:02d}')
        graph, truncated = render_l1_mermaid(pid, max_nodes=5)
        assert truncated

    def test_starts_with_graph_tb(self, fake_redis):
        pid = new_id()
        graph, _ = render_l1_mermaid(pid)
        assert graph.startswith('graph TB')


# ── DOT renderers ─────────────────────────────────────────────────────────────────

class TestRenderL3Dot:
    def test_digraph_header(self, fake_redis):
        pid = new_id()
        dot = render_l3_dot(pid)
        assert 'digraph L3' in dot

    def test_subnet_node_in_dot(self, fake_redis):
        pid, net = _net('10.0.5.0/24')
        dot = render_l3_dot(pid)
        assert '10.0.5.0/24' in dot

    def test_ip_with_hostname_in_dot(self, fake_redis):
        pid, net = _net('10.0.5.0/24')
        _ip(net['id'], '10.0.5.1', 'gw')
        dot = render_l3_dot(pid)
        assert '10.0.5.1' in dot
        assert 'gw' in dot

    def test_label_filter_in_dot(self, fake_redis):
        pid = new_id()
        net_in  = _net_in_project(pid, '10.0.1.0/24', labels=['prod'])
        net_out = _net_in_project(pid, '10.0.2.0/24', labels=['test'])
        dot = render_l3_dot(pid, label_filter=['prod'])
        assert '10.0.1.0/24' in dot
        assert '10.0.2.0/24' not in dot


class TestRenderL1Dot:
    def test_graph_header(self, fake_redis):
        pid = new_id()
        dot = render_l1_dot(pid)
        assert 'graph L1' in dot

    def test_instance_in_dot(self, fake_redis):
        pid  = new_id()
        inst = _hw_inst(pid, asset_tag='leaf-01')
        dot  = render_l1_dot(pid)
        assert 'leaf-01' in dot
