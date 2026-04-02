"""
pytestmark = pytest.mark.unit

Unit tests for ipam.py helper functions.
All Redis I/O is intercepted by the fake_redis fixture in conftest.py.
"""
import pytest
from db import parse_labels, new_id
from ipam import (
    carve_next_subnet,
    pool_by_label_set,
    resolve_template_rules,
    _validate_rules,
    project_pool_summary,
    global_pool_summary,
    get_project, save_project,
    get_network, save_network,
    get_ip, save_ip,
    add_labels_to_network, get_network_labels,
    remove_labels_from_network,
    label_scope,
    add_global_label, remove_global_label,
    add_project_label, remove_project_label,
    available_labels_for_project,
    get_template, save_template, delete_template,
    global_templates, project_templates,
    available_templates_for_project, template_scope,
    set_pending_slots, confirm_slot, confirm_all_slots,
    dismiss_slot, dismiss_all_slots,
    net_stats, project_networks, used_subnets_in_project,
    project_nets_key,
)
from db import r as _r
