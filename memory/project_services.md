---
name: project-services
description: Customer-scoped service definitions feature — CLAUDE-SERVICES.md implemented
metadata:
  type: project
---

Services feature (CLAUDE-SERVICES.md) fully implemented.

**Why:** Captures non-IP deployment requirements (hostnames, DNS, realm strings) linked to NE-type interfaces, surfaced on the requirements page alongside subnet rows.

**How to apply:** New files: `services.py` (blueprint), `services_logic.py` (logic), `templates/services/` (4 templates). Key integrations: `ne.py::compute_requirements` emits `kind='service'` rows alongside `kind='ip'` rows; `health_logic.py` gains `service_field_missing` gap; `ne_type_form.html` shows per-iface service checkboxes.

**Data model:** `service:{sid}` JSON, `customer:{cid}:services` Set, `iface_of_service` relation (composite key `ne_type_id:iface_id`). Values stored on `iface_bindings[iface_id].service_values[svc_id][field_id]`.

**Limitation:** NE instances not associated to pods (ne_in_pod relation declared but unused), so service resolved_count looks at all project instances of a type, not pod-filtered.
