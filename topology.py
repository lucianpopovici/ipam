"""Mermaid-based topology rendering blueprint.

Generates L3 logical (subnets + IPs) and L1 physical (racks + HW + cables)
topology diagrams as Mermaid text. DOT export is included for large graphs.

Routes live under /topology; the existing Cytoscape-based view under
/projects/<pid>/topology (in ne.py) is unchanged.
"""
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, Response, abort,
)

import db
from ipam import (
    get_project, all_projects, project_networks, get_network,
    network_addresses,
)
from hw_logic import project_instances, project_cables

topology_bp = Blueprint('topology', __name__, url_prefix='')
r = db.r

DEFAULT_MAX_NODES = 80

# ── Mermaid/DOT ID helpers ──────────────────────────────────────────────────────

def _mid(prefix: str, eid: str) -> str:
    """Mermaid-safe node ID: prefix + sanitised entity id."""
    return f'{prefix}_{re.sub(r"[^a-zA-Z0-9]", "_", str(eid))}'


def _dot_id(prefix: str, eid: str) -> str:
    """DOT-safe quoted ID."""
    safe = re.sub(r'"', '_', str(eid))
    return f'"{prefix}_{safe}"'


def _mermaid_label(text: str, max_len: int = 30) -> str:
    """Escape a label for use inside Mermaid quotes."""
    s = str(text or '').replace('"', "'")
    if len(s) > max_len:
        s = s[: max_len - 1] + '…'
    return s


# ── L3 logical renderer ─────────────────────────────────────────────────────────

def render_l3_mermaid(pid: str, label_filter: list = None,
                      max_nodes: int = DEFAULT_MAX_NODES) -> tuple:
    """Return (mermaid_text, truncated_bool) for the L3 logical view.

    Subnets are hub nodes (stadium shape); IPs with hostnames are spoke nodes.
    Edges: subnet → IP.  label_filter narrows subnets to those carrying all labels.
    """
    nets = project_networks(pid)
    if label_filter:
        from ipam import get_network_labels
        nets = [n for n in nets
                if all(l in get_network_labels(n['id']) for l in label_filter)]

    lines = ['graph LR']
    node_count = 0
    truncated  = False

    for net in sorted(nets, key=lambda n: n['cidr']):
        if node_count >= max_nodes:
            truncated = True
            break
        net_id  = _mid('net', net['id'])
        net_lbl = _mermaid_label(net['cidr'])
        lines.append(f'  {net_id}(["{net_lbl}"])')
        node_count += 1

        addrs = network_addresses(net['id'])
        for addr in addrs:
            if not addr.get('hostname'):
                continue
            if node_count >= max_nodes:
                truncated = True
                break
            ip_id  = _mid('ip', addr['ip'])
            ip_lbl = _mermaid_label(f"{addr['ip']}\\n{addr['hostname']}")
            lines.append(f'  {ip_id}["{ip_lbl}"]')
            lines.append(f'  {net_id} --> {ip_id}')
            node_count += 1

    if not node_count:
        lines.append('  empty["No data"]')

    return '\n'.join(lines), truncated


# ── L1 physical renderer ────────────────────────────────────────────────────────

def render_l1_mermaid(pid: str, max_nodes: int = DEFAULT_MAX_NODES) -> tuple:
    """Return (mermaid_text, truncated_bool) for the L1 physical view.

    Racks are subgraphs; HW instances are nodes inside or outside racks;
    cables are edges annotated with asset_tag.
    """
    instances = project_instances(pid)
    cables    = project_cables(pid)

    lines = ['graph TB']
    node_count = 0
    truncated  = False

    # Group instances by rack
    racked: dict   = {}   # rack_iid → [inst, ...]
    unracked: list = []
    rack_tags: dict = {}  # rack_iid → asset_tag

    for inst in instances:
        loc = inst.get('location') or {}
        rack_iid = loc.get('rack_id') or loc.get('rack_iid')
        if rack_iid:
            racked.setdefault(rack_iid, []).append(inst)
            # Try to find rack asset tag
            rack_inst = next((i for i in instances if i['id'] == rack_iid), None)
            rack_tags[rack_iid] = rack_inst.get('asset_tag', rack_iid) if rack_inst else rack_iid
        else:
            unracked.append(inst)

    # Emit rack subgraphs
    for rack_iid, rack_insts in sorted(racked.items()):
        if node_count >= max_nodes:
            truncated = True
            break
        rack_gid = _mid('rack', rack_iid)
        rack_lbl = _mermaid_label(rack_tags.get(rack_iid, rack_iid))
        lines.append(f'  subgraph {rack_gid} ["{rack_lbl}"]')
        for inst in rack_insts:
            if node_count >= max_nodes:
                truncated = True
                break
            iid   = _mid('hw', inst['id'])
            tmpl  = inst.get('template') or {}
            label = _mermaid_label(
                f"{inst.get('asset_tag', inst['id'])}\\n{tmpl.get('name', '')}"
            )
            lines.append(f'    {iid}["{label}"]')
            node_count += 1
        lines.append('  end')

    # Emit unracked instances
    for inst in unracked:
        if node_count >= max_nodes:
            truncated = True
            break
        iid   = _mid('hw', inst['id'])
        tmpl  = inst.get('template') or {}
        label = _mermaid_label(
            f"{inst.get('asset_tag', inst['id'])}\\n{tmpl.get('name', '')}"
        )
        lines.append(f'  {iid}["{label}"]')
        node_count += 1

    # Emit cable edges
    all_inst_ids = {inst['id'] for inst in instances}
    for cable in cables:
        end_a = cable.get('end_a') or {}
        end_b = cable.get('end_b') or {}
        iid_a = end_a.get('instance_id') or ''
        iid_b = end_b.get('instance_id') or ''
        if not iid_a or not iid_b:
            continue
        if iid_a not in all_inst_ids or iid_b not in all_inst_ids:
            continue
        a_id  = _mid('hw', iid_a)
        b_id  = _mid('hw', iid_b)
        label = _mermaid_label(cable.get('asset_tag') or cable.get('label') or '')
        if label:
            lines.append(f'  {a_id} -- "{label}" --- {b_id}')
        else:
            lines.append(f'  {a_id} --- {b_id}')

    if node_count == 0:
        lines.append('  empty["No HW instances"]')

    return '\n'.join(lines), truncated


# ── DOT export (L3) ─────────────────────────────────────────────────────────────

def render_l3_dot(pid: str, label_filter: list = None) -> str:
    """Return DOT-format L3 graph (no node cap; suitable for large fleets)."""
    nets = project_networks(pid)
    if label_filter:
        from ipam import get_network_labels
        nets = [n for n in nets
                if all(l in get_network_labels(n['id']) for l in label_filter)]

    lines = [
        'digraph L3 {',
        '  rankdir=LR;',
        '  node [fontname="Helvetica"];',
        '',
    ]
    for net in sorted(nets, key=lambda n: n['cidr']):
        nid = _dot_id('net', net['id'])
        lines.append(f'  {nid} [shape=ellipse label="{net["cidr"]}"];')
        for addr in network_addresses(net['id']):
            if not addr.get('hostname'):
                continue
            aid = _dot_id('ip', addr['ip'])
            lbl = f"{addr['ip']}\\n{addr['hostname']}"
            lines.append(f'  {aid} [shape=box label="{lbl}"];')
            lines.append(f'  {nid} -> {aid};')
    lines.append('}')
    return '\n'.join(lines)


def render_l1_dot(pid: str) -> str:
    """Return DOT-format L1 graph."""
    instances = project_instances(pid)
    cables    = project_cables(pid)

    lines = [
        'graph L1 {',
        '  rankdir=TB;',
        '  node [fontname="Helvetica"];',
        '',
    ]
    racked: dict = {}
    for inst in instances:
        loc = inst.get('location') or {}
        rack_iid = loc.get('rack_id') or loc.get('rack_iid')
        if rack_iid:
            racked.setdefault(rack_iid, []).append(inst)
        else:
            iid = _dot_id('hw', inst['id'])
            lbl = inst.get('asset_tag', inst['id'])
            lines.append(f'  {iid} [shape=box label="{lbl}"];')

    for rack_iid, rack_insts in racked.items():
        lines.append(f'  subgraph "cluster_{rack_iid}" {{')
        lines.append(f'    label="{rack_iid}";')
        for inst in rack_insts:
            iid = _dot_id('hw', inst['id'])
            lbl = inst.get('asset_tag', inst['id'])
            lines.append(f'    {iid} [shape=box label="{lbl}"];')
        lines.append('  }')

    all_inst_ids = {inst['id'] for inst in instances}
    for cable in cables:
        end_a = cable.get('end_a') or {}
        end_b = cable.get('end_b') or {}
        iid_a = end_a.get('instance_id') or ''
        iid_b = end_b.get('instance_id') or ''
        if not iid_a or not iid_b:
            continue
        if iid_a not in all_inst_ids or iid_b not in all_inst_ids:
            continue
        a = _dot_id('hw', iid_a)
        b = _dot_id('hw', iid_b)
        lbl = cable.get('asset_tag') or cable.get('label') or ''
        lines.append(f'  {a} -- {b} [label="{lbl}"];')

    lines.append('}')
    return '\n'.join(lines)


# ── Routes ──────────────────────────────────────────────────────────────────────

@topology_bp.route('/topology')
def topology_overview():
    """Fleet-level topology landing: project picker with node counts."""
    projects = all_projects()
    stats = []
    for p in sorted(projects, key=lambda x: x['name']):
        nets     = project_networks(p['id'])
        insts    = project_instances(p['id'])
        cables   = project_cables(p['id'])
        stats.append({
            'id':       p['id'],
            'name':     p['name'],
            'subnets':  len(nets),
            'devices':  len(insts),
            'cables':   len(cables),
        })
    return render_template('topology_views/overview.html', projects=stats)


@topology_bp.route('/topology/projects/<pid>')
def project_topology_mermaid(pid):
    """Per-project Mermaid topology with L3 / L1 tabs."""
    proj = get_project(pid)
    if not proj:
        abort(404)

    view       = request.args.get('view', 'l3')
    max_nodes  = int(request.args.get('max_nodes', DEFAULT_MAX_NODES))
    label_filter = [l.strip() for l in request.args.get('labels', '').split(',') if l.strip()]

    if view == 'l1':
        graph, truncated = render_l1_mermaid(pid, max_nodes=max_nodes)
    else:
        graph, truncated = render_l3_mermaid(pid, label_filter=label_filter or None,
                                              max_nodes=max_nodes)

    return render_template('topology_views/project.html',
                           proj=proj, view=view, graph=graph,
                           truncated=truncated, max_nodes=max_nodes,
                           labels=','.join(label_filter))


@topology_bp.route('/topology/projects/<pid>/render.mermaid')
def download_mermaid(pid):
    """Download raw Mermaid text file."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    view  = request.args.get('view', 'l3')
    label_filter = [l.strip() for l in request.args.get('labels', '').split(',') if l.strip()]
    if view == 'l1':
        graph, _ = render_l1_mermaid(pid)
    else:
        graph, _ = render_l3_mermaid(pid, label_filter=label_filter or None)
    fname = f'{proj["name"].replace(" ", "_")}_{view}.mermaid'
    return Response(graph, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


@topology_bp.route('/topology/projects/<pid>/render.dot')
def download_dot(pid):
    """Download GraphViz DOT text file (no node cap)."""
    proj = get_project(pid)
    if not proj:
        abort(404)
    view  = request.args.get('view', 'l3')
    label_filter = [l.strip() for l in request.args.get('labels', '').split(',') if l.strip()]
    if view == 'l1':
        dot = render_l1_dot(pid)
    else:
        dot = render_l3_dot(pid, label_filter=label_filter or None)
    fname = f'{proj["name"].replace(" ", "_")}_{view}.dot'
    return Response(dot, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


# ── Embed API (for inline panels on other pages) ─────────────────────────────────

@topology_bp.route('/api/topology/projects/<pid>/l3.mermaid')
def api_l3_mermaid(pid):
    """JSON {graph, truncated} for inline embedding."""
    label_filter = [l.strip() for l in request.args.get('labels', '').split(',') if l.strip()]
    max_nodes    = int(request.args.get('max_nodes', 40))
    graph, truncated = render_l3_mermaid(pid, label_filter=label_filter or None,
                                          max_nodes=max_nodes)
    return jsonify({'graph': graph, 'truncated': truncated})


@topology_bp.route('/api/topology/projects/<pid>/l1.mermaid')
def api_l1_mermaid(pid):
    """JSON {graph, truncated} for inline embedding."""
    max_nodes = int(request.args.get('max_nodes', 40))
    graph, truncated = render_l1_mermaid(pid, max_nodes=max_nodes)
    return jsonify({'graph': graph, 'truncated': truncated})
