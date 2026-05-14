/**
 * List-view bulk selection — Phase 6 of UX improvements.
 *
 * Convention:
 *   - Each selectable row: <input type="checkbox" class="row-select" value="{{ id }}">
 *   - Select-all:          <input type="checkbox" id="selectAll">
 *   - Action bar:          <div class="bulk-action-bar d-none" data-entity="sites">
 *   - Count label:         <span class="selected-count">0</span>
 *   - Action buttons:      <button data-bulk-action="delete">Delete selected</button>
 *
 * Action handlers post a JSON body {ids: [...]} to the URL in the button's
 * data-action-url attribute (or fall back to data-entity-based defaults).
 */
(function () {
  'use strict';

  function init() {
    const bar = document.querySelector('.bulk-action-bar');
    if (!bar) return;

    const checkboxes  = () => document.querySelectorAll('.row-select');
    const checked     = () => [...checkboxes()].filter(c => c.checked);
    const countLabel  = bar.querySelector('.selected-count');
    const selectAll   = document.getElementById('selectAll');

    function refresh() {
      const n = checked().length;
      bar.classList.toggle('d-none', n === 0);
      if (countLabel) countLabel.textContent = n;
      if (selectAll) {
        const all = checkboxes();
        selectAll.checked = all.length > 0 && n === all.length;
        selectAll.indeterminate = n > 0 && n < all.length;
      }
    }

    document.addEventListener('change', function (e) {
      if (e.target.classList.contains('row-select') || e.target.id === 'selectAll') {
        if (e.target.id === 'selectAll') {
          checkboxes().forEach(c => { c.checked = e.target.checked; });
        }
        refresh();
      }
    });

    bar.addEventListener('click', function (e) {
      const btn = e.target.closest('[data-bulk-action]');
      if (!btn) return;
      const action = btn.dataset.bulkAction;
      const ids    = checked().map(c => c.value);
      if (!ids.length) return;

      if (action === 'delete') {
        const url = btn.dataset.actionUrl;
        if (!url) return;
        if (!confirm(`Delete ${ids.length} item(s)? This cannot be undone.`)) return;
        fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ids}),
        }).then(r => {
          if (r.ok) window.location.reload();
          else r.json().then(d => alert(d.error || 'Delete failed'));
        });
      }

      if (action === 'export') {
        const url = btn.dataset.actionUrl;
        if (!url) return;
        const form = document.createElement('form');
        form.method = 'POST';
        form.action = url;
        ids.forEach(id => {
          const inp = document.createElement('input');
          inp.type = 'hidden'; inp.name = 'ids'; inp.value = id;
          form.append(inp);
        });
        document.body.append(form);
        form.submit();
        form.remove();
      }
    });

    refresh();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
