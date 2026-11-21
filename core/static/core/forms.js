/* Phase 7 step 2: forms, toasts and page navigation.
   - Forms never show the browser's "Please fill out this field" bubble: every form is novalidate and this
     script checks the same rules (required, min/max, length, email, number) on submit, writes an app message
     under each bad field, scrolls to the first one and shows a toast. It runs in the capture phase before
     confirm.js, so a dialog never opens for an invalid form.
   - Toasts: window.gtToast(text, tone): bottom right, every toast fades out on its own (D122).
   - Navigation: a thin progress bar while the next page loads; repeat clicks on a link that is already
     loading are ignored. Loaded synchronously (not deferred) right after numbers.js. */
(function () {
  var T = window.gtT || function (s) { return s; };

  /* ---------- toasts ---------- */
  var ICON = {
    success: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    error: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'
  };
  ICON.warning = ICON.error;
  function stack() {
    var s = document.getElementById('gt-toasts');
    if (!s) {
      s = document.createElement('div');
      s.id = 'gt-toasts'; s.className = 'app-toasts no-print';
      s.setAttribute('role', 'status'); s.setAttribute('aria-live', 'polite');
      document.body.appendChild(s);
    }
    return s;
  }
  window.gtToast = function (text, tone) {
    tone = tone || 'info';
    var t = document.createElement('div');
    t.className = 'app-toast app-toast--' + tone;
    t.innerHTML = '<span class="app-toast__icon"><svg class="gt-ico" viewBox="0 0 24 24" aria-hidden="true">' + (ICON[tone] || ICON.info) +
      '</svg></span><span class="app-toast__text"></span><button type="button" class="app-toast__close" aria-label="' + T('Tutup') + '">×</button>';
    t.querySelector('.app-toast__text').textContent = text;
    stack().appendChild(t);
    wire(t, tone);
    return t;
  };
  // Every toast leaves on its own (D122): 5 s for success/info, 7 s for warnings and errors so there is time to read
  // them. The countdown pauses while the pointer or keyboard focus is on the toast.
  function wire(t, tone) {
    var gone = false, timer = null, left = (tone === 'success' || tone === 'info') ? 5000 : 7000, started = 0;
    var close = function () {
      if (gone) return; gone = true; clearTimeout(timer);
      t.classList.add('is-leaving'); setTimeout(function () { t.remove(); }, 200);
    };
    var run = function () { started = Date.now(); timer = setTimeout(close, left); };
    var pause = function () { if (timer) { clearTimeout(timer); timer = null; left = Math.max(1500, left - (Date.now() - started)); } };
    var resume = function () { if (!timer && !gone && !t.matches(':hover') && !t.contains(document.activeElement)) run(); };
    var b = t.querySelector('.app-toast__close');
    if (b) b.addEventListener('click', close);
    t.addEventListener('mouseenter', pause); t.addEventListener('mouseleave', resume);
    t.addEventListener('focusin', pause); t.addEventListener('focusout', function () { setTimeout(resume); });
    run();
  }

  /* ---------- validation ---------- */
  function fmt(v) { return window.idNum && v !== '' && !isNaN(Number(v)) ? idNum.format(Number(v), 'kg') : v; }
  function message(el) {
    var v = el.validity;
    if (v.valueMissing) return T('Wajib diisi.');
    if (v.typeMismatch && el.type === 'email') return T('Email tidak valid.');
    if (v.badInput || v.stepMismatch) return T('Angka tidak valid.');
    if (v.rangeUnderflow) return T('Minimal %(min)s.', { min: fmt(el.min) });
    if (v.rangeOverflow) return T('Maksimal %(max)s.', { max: fmt(el.max) });
    if (v.tooShort) return T('Minimal %(n)s karakter.', { n: el.minLength });
    if (v.tooLong) return T('Maksimal %(n)s karakter.', { n: el.maxLength });
    if (v.patternMismatch || v.typeMismatch) return T('Format tidak sesuai.');
    return el.validationMessage;
  }
  function rowOf(el) { return el.closest('[data-errors-at-end], .app-formrow, label, .app-stackform > *') || el.parentElement; }
  function clear(el) {
    var row = rowOf(el);
    el.removeAttribute('aria-invalid');
    if (row) {
      row.classList.remove('app-formrow--error');
      row.querySelectorAll('[data-gt-err]').forEach(function (n) { n.remove(); });
    }
  }
  function mark(el) {
    var row = rowOf(el), id = (el.id || el.name || 'f') + '-gt-err';
    el.setAttribute('aria-invalid', 'true');
    if (!row) return;
    row.classList.add('app-formrow--error');
    row.querySelectorAll('[data-gt-err]').forEach(function (n) { n.remove(); });
    var p = document.createElement('p');
    p.className = 'app-error'; p.setAttribute('data-gt-err', ''); p.id = id;
    p.innerHTML = '<svg class="gt-ico" width="14" height="14" viewBox="0 0 24 24" aria-hidden="true">' + ICON.error + '</svg><span></span>';
    p.lastChild.textContent = message(el);
    // flatpickr: the visible field is the alt input right after the hidden real one.
    // The message goes under the whole field: the "Rp"/unit wrapper, a number + unit pair, or the custom select.
    var ctl = el._flatpickr && el._flatpickr.altInput ? el._flatpickr.altInput : el;
    var anchor = el.closest('.app-joined') || el.closest('.app-money, .app-unit, .app-select, .app-field-wrap') || ctl;
    var host = el.closest('[data-errors-at-end]');  // e.g. an input + button row: the message goes under both
    if (host) host.appendChild(p);
    else if (el.type === 'radio' || el.type === 'checkbox') row.appendChild(p);
    else anchor.insertAdjacentElement('afterend', p);
    var described = (ctl.getAttribute('aria-describedby') || '').split(' ').filter(function (x) { return x && x !== id; });
    described.push(id);
    ctl.setAttribute('aria-describedby', described.join(' '));
  }
  function visible(el) { return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) || (el._flatpickr && el._flatpickr.altInput); }

  function prep(root) {
    (root || document).querySelectorAll('form').forEach(function (f) { f.noValidate = true; });
  }
  document.addEventListener('DOMContentLoaded', function () {
    // Server messages arrive as toast markup (components/messages.html); the stack lives on <body> so no
    // transformed ancestor turns position: fixed into position: absolute.
    var st = document.getElementById('gt-toasts');
    if (st && st.parentElement !== document.body) document.body.appendChild(st);
    document.querySelectorAll('#gt-toasts .app-toast').forEach(function (t) {
      wire(t, (t.className.match(/app-toast--(\w+)/) || [])[1] || 'info');
    });
    prep();
    new MutationObserver(function (ms) {
      ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (n.tagName === 'FORM') n.noValidate = true; else prep(n); } }); });
    }).observe(document.body, { childList: true, subtree: true });
    // Server-side errors: bring the first one into view and say so once.
    var first = document.querySelector('.app-formrow--error, form .gt-callout--danger[role=alert]');
    if (first) {
      first.scrollIntoView({ block: 'center', behavior: 'auto' });
      var f = first.querySelector('input:not([type=hidden]), select, textarea');
      if (f) try { f.focus({ preventScroll: true }); } catch (e) { /* ignore */ }
      gtToast(T('Periksa kolom yang ditandai.'), 'error');
    }
  });

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    var submitter = e.submitter;
    if (submitter && submitter.formNoValidate) return;
    var bad = [];
    Array.prototype.forEach.call(form.elements, function (el) {
      if (!el.willValidate || el.disabled || el.type === 'hidden') return;
      if (el.type !== 'radio' && !visible(el) && !el.required) return;
      if (el.checkValidity()) { clear(el); return; }
      if (el.type === 'radio' && bad.some(function (b) { return b.name === el.name; })) return;
      bad.push(el);
    });
    if (!bad.length) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    bad.forEach(mark);
    var target = bad[0]._flatpickr && bad[0]._flatpickr.altInput ? bad[0]._flatpickr.altInput : bad[0];
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    try { target.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
    gtToast(T('Periksa kolom yang ditandai.'), 'error');
  }, true);
  ['input', 'change'].forEach(function (ev) {
    document.addEventListener(ev, function (e) {
      var el = e.target;
      if (el && el.getAttribute && el.getAttribute('aria-invalid') === 'true' && el.checkValidity && el.checkValidity()) clear(el);
    });
  });

  /* ---------- password show/hide ({% pw_toggle %}, D121) ---------- */
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-pw-toggle]');
    if (!b) return;
    var input = b.parentElement.querySelector('input');
    if (!input) return;
    var on = input.type === 'password';
    input.type = on ? 'text' : 'password';
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    var label = b.getAttribute(on ? 'data-label-hide' : 'data-label-show');
    b.setAttribute('aria-label', label); b.title = label;
  });
  // A submitted password field goes back to dots (the browser keeps the page in the back/forward cache).
  document.addEventListener('submit', function (e) {
    e.target.querySelectorAll && e.target.querySelectorAll('[data-pw-toggle][aria-pressed="true"]').forEach(function (b) { b.click(); });
  });

  /* ---------- unit that follows a select: <span class="app-unit" data-unit-from="id_item" data-units='{"3":"kg"}'> ---------- */
  function applyUnits(root) {
    (root || document).querySelectorAll('[data-unit-from]').forEach(function (w) {
      var src = document.getElementById(w.getAttribute('data-unit-from')), map = {};
      try { map = JSON.parse(w.getAttribute('data-units') || '{}'); } catch (err) { /* ignore */ }
      w.setAttribute('data-unit', src && map[src.value] ? map[src.value] : '');
    });
  }
  document.addEventListener('DOMContentLoaded', function () { applyUnits(); });
  document.addEventListener('change', function (e) {
    if (e.target && e.target.id && document.querySelector('[data-unit-from="' + e.target.id + '"]')) applyUnits();
  });

  /* ---------- conditional rows: data-show-when="field=A|B" ---------- */
  function applyShowWhen(form) {
    form.querySelectorAll('[data-show-when]').forEach(function (row) {
      var m = row.getAttribute('data-show-when').split('='), ctl = form.elements[m[0]];
      if (!ctl) return;
      var val = ctl.type === 'checkbox' ? (ctl.checked ? 'on' : '') : ctl.value;
      if (ctl instanceof RadioNodeList) val = ctl.value;
      var on = m[1].split('|').indexOf(val) !== -1;
      row.hidden = !on;
      row.querySelectorAll('input, select, textarea').forEach(function (el) { el.disabled = !on; });
    });
  }
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (f) { if (f.querySelector('[data-show-when]')) applyShowWhen(f); });
  });
  document.addEventListener('change', function (e) {
    var f = e.target && e.target.form;
    if (f && f.querySelector('[data-show-when]')) applyShowWhen(f);
  });
  window.gtShowWhen = applyShowWhen;

  /* ---------- page navigation: progress bar + repeat-click guard ---------- */
  var bar, loading = null;
  function startProgress() {
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'app-progress no-print'; bar.setAttribute('aria-hidden', 'true');
      document.body.appendChild(bar);
    }
    bar.classList.remove('is-done');
    void bar.offsetWidth;
    bar.classList.add('is-on');
  }
  function stopProgress() { if (bar) { bar.classList.remove('is-on'); bar.classList.add('is-done'); } loading = null; }
  window.gtProgress = { start: startProgress, stop: stopProgress };

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (a.target && a.target !== '_self') return;
    if (a.hasAttribute('download') || a.getAttribute('href').charAt(0) === '#') return;
    var url;
    try { url = new URL(a.href, location.href); } catch (err) { return; }
    if (url.origin !== location.origin || url.protocol.indexOf('http') !== 0) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    if (/[?&]format=csv/.test(url.search)) return;  // downloads don't leave the page
    if (loading && loading === url.href) { e.preventDefault(); return; }  // already on its way
    loading = url.href;
    startProgress();
  });
  // A submit nobody cancelled (confirm.js and Alpine handlers cancel theirs) is a page load.
  document.addEventListener('submit', function (e) { if (!e.defaultPrevented && !(e.target.target && e.target.target !== '_self')) startProgress(); });
  window.addEventListener('pageshow', stopProgress);
  window.addEventListener('pagehide', function () { loading = null; });
})();
