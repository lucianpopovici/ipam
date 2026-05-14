/**
 * Client-side field validation — Phase 5 of UX improvements.
 *
 * Convention: add data-validate="<type>" to any input to opt in.
 * Supported types: "cidr", "ip", "prefix-len"
 *
 * On blur the input is checked via a debounced fetch to /api/validate/<type>.
 * Bootstrap is-invalid / is-valid classes are toggled accordingly.
 */
(function () {
  'use strict';

  const DEBOUNCE_MS = 300;
  const timers = new WeakMap();

  function setValidity(input, ok, message) {
    input.classList.toggle('is-invalid', !ok);
    input.classList.toggle('is-valid', ok && input.value.length > 0);
    let fb = input.nextElementSibling;
    if (!fb || !fb.classList.contains('invalid-feedback')) {
      fb = document.createElement('div');
      fb.className = 'invalid-feedback';
      input.after(fb);
    }
    fb.textContent = ok ? '' : (message || 'Invalid value');
  }

  function validate(input) {
    const type = input.dataset.validate;
    const val  = input.value.trim();
    if (!val) {
      input.classList.remove('is-invalid', 'is-valid');
      return;
    }
    const url = `/api/validate/${type}?v=${encodeURIComponent(val)}`;
    // also pass supernet if present (for prefix-in-supernet checks)
    const supernet = input.dataset.supernet || '';
    const fullUrl  = supernet ? `${url}&supernet=${encodeURIComponent(supernet)}` : url;

    fetch(fullUrl)
      .then(r => r.json())
      .then(data => setValidity(input, data.ok, data.error))
      .catch(() => input.classList.remove('is-invalid', 'is-valid'));
  }

  function debounced(input) {
    clearTimeout(timers.get(input));
    timers.set(input, setTimeout(() => validate(input), DEBOUNCE_MS));
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-validate]').forEach(function (input) {
      input.addEventListener('input', () => debounced(input));
      input.addEventListener('blur',  () => validate(input));
    });
  });
})();
