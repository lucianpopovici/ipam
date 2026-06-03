"""Cross-project lint blueprint.

Detects fleet-wide hazards that per-project validators cannot see:
  Rule 1: CIDR overlaps across projects within the same VRF
  Rule 2: Exact-CIDR collisions (strict subset of Rule 1)
  Rule 5: Subnets falling inside operator-declared reserved ranges
  Rule L1: Orphan labels (defined but on no subnets)
  Rule L3: Probable label typos (Levenshtein ≤ 2 with ratio threshold)
  Rule L4: Label case/separator drift (same label, different casing or -/_)
"""
import hashlib
import ipaddress
import json
from collections import defaultdict
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify,
)

import db
from auth import editor_required
from ipam import (new_id, all_networks, get_project, all_projects,
                  global_labels, project_labels, get_network_labels)

lint_bp = Blueprint('lint', __name__, url_prefix='')
r = db.r

# ── Redis keys ──────────────────────────────────────────────────────────────────

CACHE_KEY      = 'lint:findings:cache'
CACHE_TTL      = 60          # seconds
RESERVED_INDEX = 'lint:reserved:index'
ACK_INDEX      = 'lint:acks:index'


def _reserved_key(rid: str) -> str:
    return f'lint:reserved:{rid}'


def _ack_key(fid: str) -> str:
    return f'lint:ack:{fid}'


# ── Finding ID ──────────────────────────────────────────────────────────────────

def finding_id(*parts: str) -> str:
    """Stable 16-hex-char hash from an ordered sequence of strings."""
    payload = '|'.join(str(p) for p in parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── Rule 1 + 2: CIDR overlap / collision within VRF across projects ─────────────

def find_cidr_overlaps() -> list:
    """
    Rule 1: cross-project CIDR overlaps within the same VRF.
    Rule 2: exact-CIDR collisions (same CIDR, same VRF, different projects).

    Uses a sort-then-walk O(n log n) algorithm.
    """
    by_vrf: dict = defaultdict(list)
    for net in all_networks():
        vrf = net.get('vrf_id') or '_def'
        by_vrf[vrf].append(net)

    findings = []
    for vrf, nets in by_vrf.items():
        try:
            sorted_nets = sorted(
                nets,
                key=lambda n: int(ipaddress.ip_network(n['cidr'], strict=False).network_address),
            )
        except ValueError:
            continue

        for i, net_a in enumerate(sorted_nets):
            try:
                n_a = ipaddress.ip_network(net_a['cidr'], strict=False)
            except ValueError:
                continue

            for net_b in sorted_nets[i + 1:]:
                try:
                    n_b = ipaddress.ip_network(net_b['cidr'], strict=False)
                except ValueError:
                    continue

                if int(n_b.network_address) > int(n_a.broadcast_address):
                    break  # sorted — nothing further can overlap

                if net_a.get('project_id') == net_b.get('project_id'):
                    continue  # within-project overlaps handled by existing validator

                if not n_a.overlaps(n_b):
                    continue

                rule = (
                    'same_cidr_collision'
                    if net_a['cidr'] == net_b['cidr']
                    else 'cross_project_overlap'
                )
                fid = finding_id(rule, vrf,
                                 net_a['cidr'], net_a.get('project_id', ''),
                                 net_b['cidr'], net_b.get('project_id', ''))
                findings.append({
                    'id':       fid,
                    'rule':     rule,
                    'severity': 'error',
                    'vrf':      vrf,
                    'subnet_a': {
                        'id':         net_a['id'],
                        'cidr':       net_a['cidr'],
                        'name':       net_a.get('name', net_a['cidr']),
                        'project_id': net_a.get('project_id', ''),
                    },
                    'subnet_b': {
                        'id':         net_b['id'],
                        'cidr':       net_b['cidr'],
                        'name':       net_b.get('name', net_b['cidr']),
                        'project_id': net_b.get('project_id', ''),
                    },
                })
    return findings


# ── Rule 5: Reserved-range overlaps ────────────────────────────────────────────

def get_reserved(rid: str) -> dict | None:
    raw = r.get(_reserved_key(rid))
    return json.loads(raw) if raw else None


def save_reserved(res: dict) -> None:
    r.set(_reserved_key(res['id']), json.dumps(res))
    r.sadd(RESERVED_INDEX, res['id'])


def delete_reserved_record(rid: str) -> None:
    r.delete(_reserved_key(rid))
    r.srem(RESERVED_INDEX, rid)


def all_reserved() -> list:
    return sorted(
        [x for x in (get_reserved(rid) for rid in r.smembers(RESERVED_INDEX)) if x],
        key=lambda x: x['cidr'],
    )


def find_reserved_overlaps() -> list:
    """Rule 5: subnets that overlap operator-declared reserved ranges."""
    reservations = all_reserved()
    if not reservations:
        return []

    findings = []
    for net in all_networks():
        try:
            n = ipaddress.ip_network(net['cidr'], strict=False)
        except ValueError:
            continue
        for res in reservations:
            try:
                rn = ipaddress.ip_network(res['cidr'], strict=False)
            except ValueError:
                continue
            if n.overlaps(rn):
                fid = finding_id('reserved_range_overlap', net['cidr'], res['cidr'])
                findings.append({
                    'id':          fid,
                    'rule':        'reserved_range_overlap',
                    'severity':    res.get('severity', 'warning'),
                    'subnet':      {
                        'id':         net['id'],
                        'cidr':       net['cidr'],
                        'name':       net.get('name', net['cidr']),
                        'project_id': net.get('project_id', ''),
                    },
                    'reserved':    res,
                    'description': res.get('description', ''),
                })
    return findings


# ── Label lint helpers ───────────────────────────────────────────────────────────

def label_usage_counts() -> dict:
    """Return {label: count_of_subnets} for every known label across the fleet."""
    counts: dict = {}
    for lbl in global_labels():
        counts.setdefault(lbl, db.r.scard(f'label:{lbl}:nets'))
    for proj in all_projects():
        for lbl in project_labels(proj['id']):
            counts.setdefault(lbl, db.r.scard(f'label:{lbl}:nets'))
    for net in all_networks():
        for lbl in get_network_labels(net['id']):
            counts.setdefault(lbl, db.r.scard(f'label:{lbl}:nets'))
    return counts


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for ch_a in a:
        curr = [prev[0] + 1]
        for j, ch_b in enumerate(b):
            curr.append(min(curr[-1] + 1, prev[j + 1] + 1, prev[j] + (ch_a != ch_b)))
        prev = curr
    return prev[-1]


def find_label_orphans() -> list:
    """
    Rule L1: Global labels used on zero subnets (warning — pool queries will
    silently match nothing). Project labels used on no subnets in their
    project (info).
    """
    label_to_pids: dict = defaultdict(set)
    for net in all_networks():
        pid = net.get('project_id', '')
        for lbl in get_network_labels(net['id']):
            label_to_pids[lbl].add(pid)

    findings = []
    for lbl in global_labels():
        if db.r.scard(f'label:{lbl}:nets') == 0:
            findings.append({
                'id':       finding_id('orphan_global_label', lbl),
                'rule':     'orphan_global_label',
                'label':    lbl,
                'severity': 'warning',
            })

    for proj in all_projects():
        pid = proj['id']
        for lbl in project_labels(pid):
            if pid not in label_to_pids.get(lbl, set()):
                findings.append({
                    'id':           finding_id('orphan_project_label', pid, lbl),
                    'rule':         'orphan_project_label',
                    'label':        lbl,
                    'project_id':   pid,
                    'project_name': proj.get('name', pid),
                    'severity':     'info',
                })

    return findings


def find_label_typos(typo_ratio: int = 5) -> list:
    """
    Rule L3: Labels within Levenshtein distance ≤ 2 where one label is at
    least typo_ratio× more common than the other.
    """
    counts = label_usage_counts()
    active  = [lbl for lbl, c in counts.items() if c > 0]

    findings = []
    seen: set = set()

    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if abs(len(a) - len(b)) > 2:
                continue
            d = _levenshtein(a, b)
            if d < 1 or d > 2:
                continue
            ca, cb = counts[a], counts[b]
            if max(ca, cb) < typo_ratio * max(min(ca, cb), 1):
                continue
            rare, common = (a, b) if ca <= cb else (b, a)
            pair = (min(rare, common), max(rare, common))
            if pair in seen:
                continue
            seen.add(pair)
            findings.append({
                'id':            finding_id('probable_label_typo', rare, common),
                'rule':          'probable_label_typo',
                'rare_label':    rare,
                'rare_count':    counts[rare],
                'common_label':  common,
                'common_count':  counts[common],
                'edit_distance': d,
                'severity':      'warning',
            })

    return findings


def find_label_case_drift() -> list:
    """
    Rule L4: Labels that differ only in case or hyphen vs. underscore.
    Info-level because some teams use both deliberately.
    """
    counts = label_usage_counts()
    groups: dict = defaultdict(list)
    for lbl in counts:
        key = lbl.casefold().replace('-', '_')
        groups[key].append(lbl)

    findings = []
    for variants in groups.values():
        if len(variants) < 2:
            continue
        findings.append({
            'id':       finding_id('label_case_drift', *sorted(variants)),
            'rule':     'label_case_drift',
            'variants': sorted(variants, key=lambda v: counts.get(v, 0), reverse=True),
            'counts':   {v: counts.get(v, 0) for v in variants},
            'severity': 'info',
        })

    return findings


# ── Combined runner + cache ─────────────────────────────────────────────────────

def run_all_rules() -> list:
    """Run every enabled lint rule and return the combined findings list."""
    findings = []
    findings.extend(find_cidr_overlaps())
    findings.extend(find_reserved_overlaps())
    findings.extend(find_label_orphans())
    findings.extend(find_label_typos())
    findings.extend(find_label_case_drift())
    return findings


def get_findings(force: bool = False) -> list:
    """Return cached findings (60 s TTL). force=True recomputes immediately."""
    if not force:
        cached = r.get(CACHE_KEY)
        if cached:
            return json.loads(cached)
    findings = run_all_rules()
    r.setex(CACHE_KEY, CACHE_TTL, json.dumps(findings))
    return findings


def invalidate_cache() -> None:
    """Call after any subnet or reserved-range write."""
    r.delete(CACHE_KEY)


def project_finding_counts(findings: list) -> dict:
    """Return {pid: {'errors': N, 'warnings': N, 'total': N}} for all pids."""
    counts: dict = {}
    for f in findings:
        pids = set()
        for key in ('subnet_a', 'subnet_b', 'subnet'):
            if key in f:
                pids.add(f[key].get('project_id', ''))
        for pid in pids:
            if not pid:
                continue
            c = counts.setdefault(pid, {'errors': 0, 'warnings': 0, 'total': 0})
            c['total'] += 1
            if f['severity'] == 'error':
                c['errors'] += 1
            elif f['severity'] == 'warning':
                c['warnings'] += 1
    return counts


# ── Acknowledgements ────────────────────────────────────────────────────────────

def get_ack(fid: str) -> dict | None:
    raw = r.get(_ack_key(fid))
    return json.loads(raw) if raw else None


def save_ack(fid: str, reason: str = '') -> None:
    ack = {
        'finding_id': fid,
        'reason':     reason,
        'acked_at':   datetime.now(timezone.utc).isoformat(),
    }
    r.set(_ack_key(fid), json.dumps(ack))
    r.sadd(ACK_INDEX, fid)


def delete_ack(fid: str) -> None:
    r.delete(_ack_key(fid))
    r.srem(ACK_INDEX, fid)


# ── Routes ──────────────────────────────────────────────────────────────────────

@lint_bp.route('/lint')
def lint_fleet():
    force    = request.args.get('refresh') == '1'
    findings = get_findings(force=force)

    proj_cache: dict = {}
    def _proj_name(pid: str) -> str:
        if pid not in proj_cache:
            p = get_project(pid)
            proj_cache[pid] = p['name'] if p else pid
        return proj_cache[pid]

    enriched = []
    for f in findings:
        f = dict(f)
        f['acked']     = bool(get_ack(f['id']))
        f['ack_info']  = get_ack(f['id'])
        enriched.append(f)

    errors   = [f for f in enriched if f['severity'] == 'error'   and not f['acked']]
    warnings = [f for f in enriched if f['severity'] == 'warning'  and not f['acked']]
    infos    = [f for f in enriched if f['severity'] == 'info'     and not f['acked']]
    acked    = [f for f in enriched if f['acked']]

    return render_template('lint/fleet.html',
                           errors=errors, warnings=warnings,
                           infos=infos, acked=acked,
                           proj_name=_proj_name,
                           reservations=all_reserved())


@lint_bp.route('/lint/projects/<pid>')
def lint_project(pid):
    proj = get_project(pid)
    if not proj:
        flash('Project not found.', 'danger')
        return redirect(url_for('lint.lint_fleet'))

    findings = get_findings()
    related  = []
    for f in findings:
        pids = set()
        for key in ('subnet_a', 'subnet_b', 'subnet'):
            if key in f:
                pids.add(f[key].get('project_id', ''))
        if pid in pids:
            f = dict(f)
            f['acked']    = bool(get_ack(f['id']))
            f['ack_info'] = get_ack(f['id'])
            related.append(f)

    proj_cache: dict = {}
    def _proj_name(p: str) -> str:
        if p not in proj_cache:
            obj = get_project(p)
            proj_cache[p] = obj['name'] if obj else p
        return proj_cache[p]

    return render_template('lint/project.html', proj=proj,
                           findings=related, proj_name=_proj_name)


@lint_bp.route('/api/lint/findings.json')
def lint_json():
    findings = get_findings()
    return jsonify({
        'findings': findings,
        'total':    len(findings),
        'errors':   sum(1 for f in findings if f['severity'] == 'error'),
        'warnings': sum(1 for f in findings if f['severity'] == 'warning'),
    })


@lint_bp.route('/lint/findings/<fid>/ack', methods=['POST'])
@editor_required
def ack_finding(fid):
    reason = request.form.get('reason', '').strip()
    save_ack(fid, reason)
    flash('Finding acknowledged.', 'success')
    return redirect(request.referrer or url_for('lint.lint_fleet'))


@lint_bp.route('/lint/findings/<fid>/unack', methods=['POST'])
@editor_required
def unack_finding(fid):
    delete_ack(fid)
    flash('Acknowledgement removed.', 'info')
    return redirect(request.referrer or url_for('lint.lint_fleet'))


@lint_bp.route('/lint/reserved')
def list_reserved():
    return render_template('lint/reserved.html', reservations=all_reserved())


@lint_bp.route('/lint/reserved/add', methods=['POST'])
@editor_required
def add_reserved():
    cidr     = request.form.get('cidr', '').strip()
    name     = request.form.get('name', '').strip()
    severity = request.form.get('severity', 'warning')
    desc     = request.form.get('description', '').strip()
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        flash(f'Invalid CIDR: {cidr}', 'danger')
        return redirect(url_for('lint.list_reserved'))
    if not name:
        flash('Name is required.', 'danger')
        return redirect(url_for('lint.list_reserved'))
    res = {'id': new_id(), 'cidr': cidr, 'name': name,
           'severity': severity, 'description': desc}
    save_reserved(res)
    invalidate_cache()
    flash(f'Reserved range {cidr} added.', 'success')
    return redirect(url_for('lint.list_reserved'))


@lint_bp.route('/lint/reserved/<rid>/delete', methods=['POST'])
@editor_required
def delete_reserved_route(rid):
    res = get_reserved(rid)
    if res:
        delete_reserved_record(rid)
        invalidate_cache()
        flash(f'Reserved range {res["cidr"]} removed.', 'success')
    return redirect(url_for('lint.list_reserved'))
