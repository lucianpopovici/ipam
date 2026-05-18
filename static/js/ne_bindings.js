// NID and PID are injected as globals by the template before this file loads.
/* globals NID, PID */

let _allPorts = [];
let _selectedPorts = new Set();

function openBindModal(ifaceId, ifaceName, currentMode) {
  document.getElementById('bindModalIfaceName').textContent = ifaceName;
  document.getElementById('bindForm').action =
    '/ne-instances/' + NID + '/bindings/' + ifaceId;
  // Reset
  _selectedPorts = new Set();
  _allPorts = [];
  document.getElementById('pickHwInstance').value = '';
  document.getElementById('portList').innerHTML =
    '<span class="text-muted small">Select a hardware instance above.</span>';
  document.getElementById('portsJsonInput').value = '[]';
  document.getElementById('ruleJsonInput').value = '{}';

  const modeToSet = currentMode || 'single';
  const radio = document.querySelector(`input[name="bind_mode"][value="${modeToSet}"]`);
  if (radio) radio.checked = true;
  onModeChange(modeToSet);

  bootstrap.Modal.getOrCreateInstance(document.getElementById('bindModal')).show();
}

function onModeChange(mode) {
  const explSec = document.getElementById('sectionExplicit');
  const ruleSec = document.getElementById('sectionAutoRule');
  if (mode === 'auto-rule') {
    explSec.classList.add('d-none');
    ruleSec.classList.remove('d-none');
    previewRule();
  } else {
    explSec.classList.remove('d-none');
    ruleSec.classList.add('d-none');
  }
}

function loadPortsFor(hwid) {
  if (!hwid) {
    document.getElementById('portList').innerHTML =
      '<span class="text-muted small">Select a hardware instance above.</span>';
    return;
  }
  const typeFilter = document.getElementById('portTypeFilter').value;
  const url = `/api/projects/${PID}/hw/${hwid}/free-ports` +
              (typeFilter ? `?type=${typeFilter}` : '');
  fetch(url)
    .then(r => r.json())
    .then(data => {
      _allPorts = data.ports || [];
      renderPortList();
    })
    .catch(() => {
      document.getElementById('portList').innerHTML =
        '<span class="text-danger small">Error loading ports.</span>';
    });
}

function filterPorts() {
  const hwid = document.getElementById('pickHwInstance').value;
  if (hwid) loadPortsFor(hwid);
}

function renderPortList() {
  const freeOnly = document.getElementById('portFreeFilter').value === 'free';
  const mode = document.querySelector('input[name="bind_mode"]:checked')?.value || 'single';
  const singleMode = mode === 'single';

  const ports = freeOnly ? _allPorts.filter(p => !p.is_bound && !p.is_cabled) : _allPorts;
  if (!ports.length) {
    document.getElementById('portList').innerHTML =
      '<span class="text-muted small">No ports match the filter.</span>';
    return;
  }

  const hwid = document.getElementById('pickHwInstance').value;
  document.getElementById('portList').innerHTML = ports.map(p => {
    const checked = _selectedPorts.has(p.port_id);
    const disabled = !checked && singleMode && _selectedPorts.size > 0;
    let badge = '';
    if (p.is_bound) badge = `<span class="badge bg-warning text-dark ms-1 small">in use</span>`;
    else if (p.is_cabled) badge = `<span class="badge bg-light text-muted border ms-1 small">cabled</span>`;
    return `<div class="form-check">
      <input class="form-check-input" type="checkbox"
             id="port_${p.port_id}"
             value="${p.port_id}"
             data-hwid="${hwid}"
             ${checked ? 'checked' : ''}
             ${disabled || p.is_bound ? 'disabled' : ''}
             onchange="togglePort('${p.port_id}','${hwid}',this.checked)">
      <label class="form-check-label small" for="port_${p.port_id}">
        <code>${p.name}</code>
        <span class="text-muted">${p.port_type}</span>
        ${badge}
      </label>
    </div>`;
  }).join('');
}

function togglePort(portId, hwid, checked) {
  if (checked) {
    _selectedPorts.add(portId);
  } else {
    _selectedPorts.delete(portId);
  }
  const mode = document.querySelector('input[name="bind_mode"]:checked')?.value || 'single';
  if (mode === 'single' && _selectedPorts.size > 1) {
    _selectedPorts = new Set([portId]);
  }
  renderPortList();
}

function previewRule() {
  const rule = buildRule();
  document.getElementById('ruleJsonInput').value = JSON.stringify(rule);
  const el = document.getElementById('rulePreview');
  el.innerHTML = '<span class="text-muted">Loading…</span>';
  fetch(`/api/projects/${PID}/rules/preview`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rule, exclude_nid: NID}),
  })
  .then(r => r.json())
  .then(data => {
    if (!data.total_ports) {
      el.innerHTML = '<span class="text-warning">⚠ No ports matched.</span>';
      return;
    }
    el.innerHTML = `<strong>${data.total_ports} port(s) across ${data.buckets.length} bucket(s):</strong><ul class="mb-0 mt-1">` +
      data.buckets.map(b =>
        `<li>${b.label} → ${b.count} port(s)${b.warn ? ' <span class="text-warning">⚠ unracked</span>' : ''}</li>`
      ).join('') + '</ul>';
  })
  .catch(() => { el.innerHTML = '<span class="text-danger">Preview unavailable.</span>'; });
}

function buildRule() {
  return {
    port_types: [...document.querySelectorAll('.rule-port-type:checked')].map(c => c.value),
    categories: [...document.querySelectorAll('.rule-category:checked')].map(c => c.value),
    name_regex: document.getElementById('ruleNameRegex').value || '.*',
    group_by:   [...document.querySelectorAll('.rule-group:checked')].map(c => c.value),
  };
}

function prepareSubmit() {
  const mode = document.querySelector('input[name="bind_mode"]:checked')?.value || 'single';
  if (mode !== 'auto-rule') {
    const hwid = document.getElementById('pickHwInstance').value;
    const ports = [..._selectedPorts].map(portId => ({
      hw_instance_id: hwid,
      port_id: portId,
      role: 'primary',
    }));
    document.getElementById('portsJsonInput').value = JSON.stringify(ports);
  } else {
    document.getElementById('ruleJsonInput').value = JSON.stringify(buildRule());
  }
}

// ── Workflow B — Bulk bind ────────────────────────────────────────────────────
let _wbAllPorts = [];

function wbToggleAll(cb) {
  document.querySelectorAll('.wb-iface-check').forEach(c => { c.checked = cb.checked; });
  wbUpdatePreview();
}

function openBulkModal() {
  document.getElementById('wbHwInstance').value = '';
  _wbAllPorts = [];
  document.getElementById('wbPortStart').value = '0';
  document.getElementById('wbPortEnd').value   = '';
  document.getElementById('wbPortStep').value  = '1';
  document.getElementById('wbPreview').innerHTML =
    '<span class="text-muted small">Select interfaces and a hardware instance above.</span>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('bulkBindModal')).show();
}

function wbLoadPorts(hwid) {
  _wbAllPorts = [];
  if (!hwid) { wbUpdatePreview(); return; }
  fetch(`/api/projects/${PID}/hw/${hwid}/free-ports`)
    .then(r => r.json())
    .then(data => {
      _wbAllPorts = data.ports || [];
      const end = document.getElementById('wbPortEnd');
      if (!end.value) end.value = Math.max(0, _wbAllPorts.length - 1);
      wbUpdatePreview();
    });
}

function wbUpdatePreview() {
  const ifaces = [...document.querySelectorAll('.wb-iface-check:checked')]
    .map(c => ({id: c.value, name: c.dataset.name}));
  const startIdx = Math.max(0, parseInt(document.getElementById('wbPortStart').value) || 0);
  const endIdx   = parseInt(document.getElementById('wbPortEnd').value);
  const step     = Math.max(1, parseInt(document.getElementById('wbPortStep').value) || 1);
  const maxIdx   = Number.isNaN(endIdx) ? _wbAllPorts.length - 1 : endIdx;

  const portSubset = [];
  for (let i = startIdx; i <= Math.min(maxIdx, _wbAllPorts.length - 1); i += step) {
    portSubset.push(_wbAllPorts[i]);
  }

  const el = document.getElementById('wbPreview');
  if (!ifaces.length || !portSubset.length) {
    el.innerHTML = '<span class="text-muted small">Select interfaces and a hardware instance above.</span>';
    return;
  }

  let html = '<table class="table table-sm table-bordered mb-0"><thead class="table-light">' +
    '<tr><th>NE Interface</th><th>→</th><th>HW Port</th></tr></thead><tbody>';
  ifaces.forEach((iface, idx) => {
    const port = portSubset[idx] || null;
    const portBound = port && port.is_bound;
    html += `<tr class="${!port || portBound ? 'table-warning' : ''}">
      <td><code>${iface.name}</code></td>
      <td class="text-center text-muted">→</td>
      <td>${port
        ? `<code>${port.name}</code>${portBound ? ' <span class="badge bg-warning text-dark">in use</span>' : ''}`
        : '<span class="text-warning small">no port (index out of range)</span>'
      }</td>
    </tr>`;
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

function submitBulkBind() {
  const hwid = document.getElementById('wbHwInstance').value;
  if (!hwid) { alert('Select a hardware instance first.'); return; }

  const ifaces = [...document.querySelectorAll('.wb-iface-check:checked')]
    .map(c => ({id: c.value, name: c.dataset.name}));
  if (!ifaces.length) { alert('Select at least one interface.'); return; }

  const startIdx = Math.max(0, parseInt(document.getElementById('wbPortStart').value) || 0);
  const endIdx   = parseInt(document.getElementById('wbPortEnd').value);
  const step     = Math.max(1, parseInt(document.getElementById('wbPortStep').value) || 1);
  const maxIdx   = Number.isNaN(endIdx) ? _wbAllPorts.length - 1 : endIdx;

  const portSubset = [];
  for (let i = startIdx; i <= Math.min(maxIdx, _wbAllPorts.length - 1); i += step) {
    portSubset.push(_wbAllPorts[i]);
  }

  const pairs = ifaces
    .map((iface, idx) => portSubset[idx] ? {
      iface_id: iface.id, hw_instance_id: hwid,
      port_id: portSubset[idx].port_id, role: 'primary',
    } : null)
    .filter(Boolean);

  if (!pairs.length) { alert('No valid iface-port pairs to bind.'); return; }

  document.getElementById('wbPairsJson').value = JSON.stringify(pairs);
  document.getElementById('bulkBindForm').submit();
}

// ── Workflow C — Auto-resolve ─────────────────────────────────────────────────
let _arMatches = [];

function openAutoResolveModal() {
  document.getElementById('arHwInstance').value = '';
  _arMatches = [];
  document.getElementById('arPreview').innerHTML =
    '<span class="text-muted small">Select a hardware instance to compute matches.</span>';
  bootstrap.Modal.getOrCreateInstance(document.getElementById('autoResolveModal')).show();
}

function arLoadPreview(hwid) {
  _arMatches = [];
  const el = document.getElementById('arPreview');
  if (!hwid) {
    el.innerHTML = '<span class="text-muted small">Select a hardware instance to compute matches.</span>';
    return;
  }
  el.innerHTML = '<span class="text-muted small">Computing…</span>';
  fetch(`/api/ne-instances/${NID}/autoresolve-preview?hw_instance_id=${hwid}`)
    .then(r => r.json())
    .then(data => {
      _arMatches = data.matches || [];
      const unmatched = data.unmatched || [];

      if (!_arMatches.length && !unmatched.length) {
        el.innerHTML = '<span class="text-muted small">No unbound interfaces to resolve.</span>';
        return;
      }

      let html = '<table class="table table-sm table-bordered mb-0"><thead class="table-light">' +
        '<tr><th>NE Interface</th><th>→</th><th>HW Port</th><th>Match</th></tr></thead><tbody>';

      _arMatches.forEach(m => {
        const badge = m.match_type === 'exact'
          ? '<span class="badge bg-success">exact</span>'
          : '<span class="badge bg-info text-dark">substring</span>';
        html += `<tr>
          <td><code>${m.iface_name}</code></td>
          <td class="text-center text-muted">→</td>
          <td><code>${m.port_name}</code></td>
          <td>${badge}</td>
        </tr>`;
      });

      unmatched.forEach(u => {
        html += `<tr class="table-warning">
          <td><code>${u.iface_name}</code></td>
          <td class="text-center text-muted">→</td>
          <td colspan="2"><span class="text-muted small">no match — bind manually</span></td>
        </tr>`;
      });

      html += '</tbody></table>';
      if (_arMatches.length) {
        html += `<p class="small text-muted mt-2 mb-0">
          ${_arMatches.length} match(es) will be applied. Unmatched interfaces stay unbound.
        </p>`;
      }
      el.innerHTML = html;
    })
    .catch(() => { el.innerHTML = '<span class="text-danger small">Error computing preview.</span>'; });
}

function submitAutoResolve() {
  if (!_arMatches.length) { alert('No matches to apply. Select a hardware instance first.'); return; }
  document.getElementById('arPairsJson').value = JSON.stringify(_arMatches);
  document.getElementById('autoResolveForm').submit();
}
