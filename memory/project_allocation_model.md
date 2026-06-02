---
name: project-allocation-model
description: Allocation model overhaul (VRF entity, supernet→legacy_supernet, address_count, label categories, pool resolver) — implemented 2026-05-20
metadata:
  type: project
---

Allocation model (CLAUDE-ALLOCATION-MODEL.md) is now implemented.

**What changed:**
- VRF entity added (`vrf.py`, scoped per customer). Routes under `/customers/<cid>/vrfs`.
- `project.supernet` → stored as `legacy_supernet` on create; no longer required. Customer required on form but not hard-enforced server-side (warning if missing).
- `project_pool_summary` / `global_pool_summary` use `legacy_supernet or supernet`; gracefully handle no supernet (sum of subnet capacities).
- Subnets now store `family`, `vrf_id`, `site_id`, `pod_id` fields.
- NE-type iface IP config uses `address_count` + `min_prefix` instead of `prefix_len`. Old `prefix_len` format still read (backward compat via `_iface_address_count` helper).
- `compute_requirements` groups ifaces per `(family, vrf_id, labels, sharing_scope)` and emits `address_count` + computed `min_prefix`. `prefix_len` kept as alias for compat.
- Template rules support `per_hw` and `per_ne` types with structured `offset` (fixed / after_previous).
- `min_prefix_v4/v6` and `address_count_from_prefix` helpers in `ipam.py`.
- VRF-aware pool resolver `resolve_pool()` in `ipam.py`.
- Migration script at `migrations/alloc_model.py`.

**Why:** Customer/VRF scoping, correct subnet sizing from address counts, inventory-aware template rules.

**How to apply:** When creating new projects, customer assignment is encouraged but not enforced server-side. New subnets should use VRF picker. NE-type ifaces should use `address_count` not `prefix_len`. Run `migrations/alloc_model.py` against existing Redis data.
