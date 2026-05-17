"""
Default check template library.

These are the 8 templates described in CLAUDE-CHECKS.md.
Call seed_default_check_templates() once on first run; it is idempotent.
"""
from checks_logic import get_check_template, save_check_template

DEFAULT_TEMPLATES = [
    {
        'id':                 'default:iface_admin_down',
        'name':               'Interface admin-down before reconfiguration',
        'description':        'Confirm the target interface is administratively shut down before making changes.',
        'phase':              'pre',
        'attached_to':        'ne_iface',
        'attachment_filter':  {},
        'action_description': 'Verify {{ iface.name }} on {{ ne.name }} is administratively down before reconfiguration.',
        'expected_result':    'Interface {{ iface.name }} is in admin-down state.',
        'vendor_hints': {
            'cisco_ios':  'show interface {{ iface.name }} | include admin',
            'junos':      'show interfaces {{ iface.name }} terse',
            'arista_eos': 'show interface {{ iface.name }} status',
        },
        'severity': 'critical',
        'tags':     ['interface', 'pre-change'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:no_existing_routing',
        'name':               'No live routing protocols on target devices',
        'description':        'Confirm no live OSPF/BGP/EIGRP processes exist on devices before deployment.',
        'phase':              'pre',
        'attached_to':        'project',
        'attachment_filter':  {},
        'action_description': 'Confirm no live OSPF/BGP/EIGRP processes are running on target devices.',
        'expected_result':    'No active routing protocol sessions found.',
        'vendor_hints': {
            'cisco_ios':  'show ip ospf neighbor\nshow ip bgp summary',
            'junos':      'show ospf neighbor\nshow bgp summary',
            'arista_eos': 'show ip ospf neighbor\nshow bgp summary',
        },
        'severity': 'standard',
        'tags':     ['routing', 'pre-change'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:cabling_matches_design',
        'name':               'Cabling matches design',
        'description':        'Physically verify that each cable matches the design document.',
        'phase':              'pre',
        'attached_to':        'cable',
        'attachment_filter':  {},
        'action_description': 'Verify cable {{ cable.label }} is physically present between {{ cable.end_a }} and {{ cable.end_b }}.',
        'expected_result':    'Cable {{ cable.label }} is correctly installed and labelled.',
        'vendor_hints':       {},
        'severity': 'standard',
        'tags':     ['cabling', 'physical'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:software_version',
        'name':               'Software version check',
        'description':        'Verify each device is running the expected software version.',
        'phase':              'pre',
        'attached_to':        'hw_template',
        'attachment_filter':  {},
        'action_description': 'Verify device {{ hw.asset_tag }} is running expected software version.',
        'expected_result':    'Device {{ hw.asset_tag }} reports expected OS version.',
        'vendor_hints': {
            'cisco_ios':  'show version | include Version',
            'junos':      'show version',
            'arista_eos': 'show version',
            'linux':      'uname -r',
        },
        'severity': 'advisory',
        'tags':     ['software', 'version'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:iface_up',
        'name':               'Interface up/up after deployment',
        'description':        'Verify the configured interface is up at L1 and L2 after deployment.',
        'phase':              'post',
        'attached_to':        'ne_iface',
        'attachment_filter':  {},
        'action_description': 'Verify {{ iface.name }} on {{ ne.name }} is up/up.',
        'expected_result':    'Interface {{ iface.name }} status: up/up. No errors in the last 60 seconds.',
        'vendor_hints': {
            'cisco_ios':  'show interface {{ iface.name }} | include line protocol',
            'junos':      'show interfaces {{ iface.name }} terse',
            'arista_eos': 'show interface {{ iface.name }} status',
            'linux':      'ip -br link show {{ iface.name }}',
        },
        'severity': 'critical',
        'tags':     ['interface', 'l1', 'l2'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:bgp_session_up',
        'name':               'BGP session Established',
        'description':        'Verify BGP session is in Established state after deployment.',
        'phase':              'post',
        'attached_to':        'ne_iface',
        'attachment_filter':  {'iface_labels_any': ['bgp', 'uplink']},
        'action_description': 'Verify BGP session on {{ iface.name }} ({{ ne.name }}) is Established.',
        'expected_result':    'BGP session on {{ iface.name }} is in Established state.',
        'vendor_hints': {
            'cisco_ios':  'show bgp neighbors {{ iface.name }} | include BGP state',
            'junos':      'show bgp neighbor | match Established',
            'arista_eos': 'show bgp neighbors {{ iface.name }}',
        },
        'severity': 'critical',
        'tags':     ['bgp', 'routing'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:ping_reachability',
        'name':               'ICMP reachability from test host',
        'description':        'Verify the management IP responds to ICMP from a designated test host.',
        'phase':              'post',
        'attached_to':        'ne_iface',
        'attachment_filter':  {'iface_labels_any': ['mgmt']},
        'action_description': 'Verify {{ iface.name }} on {{ ne.name }} responds to ICMP from a designated test host.',
        'expected_result':    '{{ iface.name }} on {{ ne.name }} responds to ping within 5 ms.',
        'vendor_hints':       {'linux': 'ping -c 4 <ip>'},
        'severity': 'standard',
        'tags':     ['icmp', 'reachability', 'mgmt'],
        'scope':    'global',
        'project_id': '',
    },
    {
        'id':                 'default:route_present',
        'name':               'Expected route present in routing table',
        'description':        'Verify the expected route is visible in the routing table after deployment.',
        'phase':              'post',
        'attached_to':        'ne_iface',
        'attachment_filter':  {'iface_labels_any': ['uplink', 'data']},
        'action_description': 'Verify the expected route is present in the routing table on {{ ne.name }}.',
        'expected_result':    'Expected route is visible in the RIB on {{ ne.name }}.',
        'vendor_hints': {
            'cisco_ios':  'show ip route <prefix>',
            'junos':      'show route <prefix>',
            'arista_eos': 'show ip route <prefix>',
        },
        'severity': 'standard',
        'tags':     ['routing', 'rib'],
        'scope':    'global',
        'project_id': '',
    },
]


def seed_default_check_templates():
    """Create default check templates if they don't already exist. Idempotent."""
    created = 0
    for tmpl in DEFAULT_TEMPLATES:
        if not get_check_template(tmpl['id']):
            save_check_template(tmpl)
            created += 1
    return created
