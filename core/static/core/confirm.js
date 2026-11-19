/* Shared confirmation dialog (D83). Replaces every window.confirm().

   Declarative, on a <form> or on a submit <button>:
     data-confirm="Body text"            required; "\n" starts a new paragraph
     data-confirm-title="Batalkan order?" heading (default "Konfirmasi")
     data-confirm-action="Batalkan"      label of the action button (names the action, never "OK")
     data-confirm-level="2"              level 2 adds a "Saya sudah memeriksa data ini" checkbox
     data-confirm-danger                 red action button
     data-confirm-template="#id"         extra HTML from a <template> (summaries, from → to, warnings)
     data-confirm-reason="Alasan"        required reason textarea; its text is posted as `reason`
     data-confirm-reason-optional        makes the reason optional
     data-confirm-choices='[["V","Label"],…]'  a required picker (D94), posted as data-confirm-choice-name;
     data-confirm-other="OTHER"          picking this value makes the reason note required
   Script: gtConfirm({title, body, html, action, level, danger, reason}) → Promise<false | {reason}>. */
(function () {
  var dlg, resolver;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  var ICON_ALERT = '<svg class="gt-ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/></svg>';
  var ICON_CHECK = '<svg class="gt-ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/></svg>';

  // Design-system confirm dialog (Phase 3): gt-dialog--confirm, gt-radio choices, gt-check for level 2.
  function build() {
    dlg = el('dialog', 'gt-dialog gt-dialog--confirm app-confirm');
    dlg.setAttribute('aria-labelledby', 'gt-confirm-title');
    dlg.innerHTML =
      '<form method="dialog" class="app-confirm__form">' +
      '<span data-icon class="gt-dialog__icon"></span>' +
      '<h2 id="gt-confirm-title" class="gt-dialog__title"></h2>' +
      '<div data-body class="app-confirm__body"></div>' +
      '<div data-extra class="app-confirm__extra"></div>' +
      '<fieldset data-choice-wrap class="app-confirm__field"><legend class="app-label">Alasan</legend><div data-choice class="app-confirm__choices"></div></fieldset>' +
      '<div data-reason-wrap class="app-confirm__field"><label class="app-label" for="gt-confirm-reason" data-reason-label></label>' +
      '<textarea id="gt-confirm-reason" data-reason rows="2" class="app-field"></textarea></div>' +
      '<label data-check-wrap class="gt-check app-confirm__check"><input type="checkbox" data-check>' +
      '<span class="gt-check__title">Saya sudah memeriksa data ini</span></label>' +
      '<div class="app-confirm__actions">' +
      '<button value="cancel" data-cancel class="gt-btn gt-btn--outline gt-btn--md">Batal</button>' +
      '<button value="ok" data-ok class="gt-btn gt-btn--md"></button>' +
      '</div></form>';
    document.body.appendChild(dlg);
    var ok = dlg.querySelector('[data-ok]'), check = dlg.querySelector('[data-check]'), reason = dlg.querySelector('[data-reason]');
    var choice = dlg.querySelector('[data-choice]');
    choice.value = '';
    function sync() {
      var picked = choice.querySelector('input:checked');
      choice.value = picked ? picked.value : '';
      Array.prototype.forEach.call(choice.querySelectorAll('.gt-radio'), function (r) {
        r.classList.toggle('gt-radio--selected', !!r.querySelector('input:checked'));
      });
      var needCheck = dlg.querySelector('[data-check-wrap]').style.display !== 'none';
      var hasChoice = dlg.querySelector('[data-choice-wrap]').style.display !== 'none';
      var needReason = dlg.querySelector('[data-reason-wrap]').style.display !== 'none' &&
        (dlg.dataset.reasonRequired === '1' || (hasChoice && dlg.dataset.other && choice.value === dlg.dataset.other));
      dlg.querySelector('[data-reason-label]').textContent = dlg.dataset.reasonLabel + (needReason ? '' : ' (opsional)');
      ok.disabled = (needCheck && !check.checked) || (needReason && !reason.value.trim()) || (hasChoice && !choice.value);
    }
    choice.addEventListener('change', sync);
    check.addEventListener('change', sync);
    reason.addEventListener('input', sync);
    dlg.addEventListener('click', function (e) { if (e.target === dlg) dlg.close('cancel'); });  // backdrop click
    dlg.addEventListener('close', function () {
      var r = resolver; resolver = null;
      if (r) r(dlg.returnValue === 'ok' ? { reason: reason.value.trim(), choice: choice.value } : false);
    });
    dlg._sync = sync;
  }

  function gtConfirm(opts) {
    if (!dlg) build();
    if (resolver) resolver(false);
    dlg.querySelector('#gt-confirm-title').textContent = opts.title || 'Konfirmasi';
    var icon = dlg.querySelector('[data-icon]');
    icon.innerHTML = opts.danger ? ICON_ALERT : ICON_CHECK;
    icon.className = 'gt-dialog__icon' + (opts.danger ? ' app-dialog__icon--danger' : '');
    var body = dlg.querySelector('[data-body]');
    body.innerHTML = '';
    String(opts.body || '').split('\n').filter(Boolean).forEach(function (p) { body.appendChild(el('p', '', p)); });
    var extra = dlg.querySelector('[data-extra]');
    extra.innerHTML = '';
    extra.style.display = opts.html ? '' : 'none';
    if (opts.html) extra.appendChild(opts.html);
    var wrap = dlg.querySelector('[data-reason-wrap]');
    wrap.style.display = opts.reason ? '' : 'none';  // not `hidden`: display classes would override it
    dlg.dataset.reasonRequired = opts.reason && !opts.reasonOptional ? '1' : '';
    dlg.dataset.reasonLabel = opts.reason || '';
    var cw = dlg.querySelector('[data-choice-wrap]'), box = dlg.querySelector('[data-choice]');
    box.innerHTML = '';
    box.value = '';
    cw.style.display = opts.choices ? '' : 'none';
    dlg.dataset.other = opts.other || '';
    if (opts.choices) {
      opts.choices.forEach(function (c) {
        var lab = el('label', 'gt-radio app-radio--compact'), inp = el('input');
        inp.type = 'radio'; inp.name = 'gt-confirm-choice'; inp.value = c[0];
        var b = el('span', 'gt-radio__body');
        b.appendChild(el('span', 'gt-radio__title', c[1]));
        lab.appendChild(inp); lab.appendChild(b); box.appendChild(lab);
      });
    }
    dlg.querySelector('[data-reason]').value = '';
    dlg.querySelector('[data-check-wrap]').style.display = Number(opts.level || 1) < 2 ? 'none' : '';
    dlg.querySelector('[data-check]').checked = false;
    var ok = dlg.querySelector('[data-ok]');
    ok.textContent = opts.action || 'Lanjutkan';
    ok.className = 'gt-btn gt-btn--md ' + (opts.danger ? 'gt-btn--danger' : 'gt-btn--solid');
    dlg.returnValue = '';
    dlg._sync();
    dlg.showModal();
    var first = box.querySelector('input');
    (opts.choices && first ? first : opts.reason ? dlg.querySelector('[data-reason]') : dlg.querySelector('[data-cancel]')).focus();
    return new Promise(function (res) { resolver = res; });
  }

  function optsFrom(node) {
    var d = node.dataset, html = null;
    if (d.confirmTemplate) {
      var t = document.querySelector(d.confirmTemplate);
      if (t) html = t.content.cloneNode(true);
    }
    return {
      title: d.confirmTitle, body: d.confirm, html: html, action: d.confirmAction, level: d.confirmLevel,
      danger: 'confirmDanger' in d, reason: d.confirmReason, reasonOptional: 'confirmReasonOptional' in d,
      choices: d.confirmChoices ? JSON.parse(d.confirmChoices) : null, other: d.confirmOther,
    };
  }

  // A submit button's own data-confirm wins over its form's.
  document.addEventListener('submit', function (e) {
    var form = e.target, btn = e.submitter;
    if (form.dataset.confirmed === '1') { delete form.dataset.confirmed; return; }
    var src = btn && btn.dataset && btn.dataset.confirm != null ? btn : (form.dataset.confirm != null ? form : null);
    if (!src) return;
    if (src.dataset.confirmIf && !new Function('form', 'return (' + src.dataset.confirmIf + ')')(form)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    gtConfirm(optsFrom(src)).then(function (res) {
      if (!res) return;
      var field = form.querySelector('input[name=reason][data-confirm-reason-field]');
      if (src.dataset.confirmReason != null) {
        if (!field) { field = el('input'); field.type = 'hidden'; field.name = 'reason'; field.dataset.confirmReasonField = '1'; form.appendChild(field); }
        field.value = res.reason;
      }
      if (src.dataset.confirmChoices) {
        var cname = src.dataset.confirmChoiceName || 'choice';
        var cf = form.querySelector('input[name="' + cname + '"][data-confirm-choice-field]');
        if (!cf) { cf = el('input'); cf.type = 'hidden'; cf.name = cname; cf.dataset.confirmChoiceField = '1'; form.appendChild(cf); }
        cf.value = res.choice;
      }
      // The level-2 tick travels with the form so the server can require it.
      if (Number(src.dataset.confirmLevel || 1) >= 2 && !form.querySelector('input[name=confirmed_checked]')) {
        var c = el('input'); c.type = 'hidden'; c.name = 'confirmed_checked'; c.value = '1'; form.appendChild(c);
      }
      form.dataset.confirmed = '1';
      if (form.requestSubmit) form.requestSubmit(btn || undefined); else form.submit();
    });
  }, true);

  window.gtConfirm = gtConfirm;

  // Keeps Tab inside a sheet or dialog (used by components/sheet.html).
  window.gtTrapFocus = function (e, root) {
    var f = Array.prototype.filter.call(
      root.querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select:not([disabled]),textarea:not([disabled])'),
      function (n) { return n.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
})();

/* Info tips ({% info_tip %}): one floating bubble, shown on hover, keyboard focus or tap; Esc, scroll or a tap elsewhere hides it.
   Fixed-positioned in <body> so cards with overflow:hidden never clip it. */
(function () {
  var bubble, current = null;
  function ensure() {
    if (bubble) return bubble;
    bubble = document.createElement('div');
    bubble.id = 'gt-tip'; bubble.className = 'gt-tip__bubble'; bubble.setAttribute('role', 'tooltip'); bubble.hidden = true;
    document.body.appendChild(bubble);
    return bubble;
  }
  function show(btn) {
    var b = ensure();
    current = btn;
    b.textContent = btn.getAttribute('data-tip');
    b.hidden = false;
    var r = btn.getBoundingClientRect(), w = b.offsetWidth, h = b.offsetHeight;
    var left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    var top = r.top - h - 8;
    if (top < 8) top = r.bottom + 8;
    b.style.left = left + 'px'; b.style.top = top + 'px';
  }
  function hide() { if (bubble) bubble.hidden = true; current = null; }
  function tipOf(e) { return e.target && e.target.closest ? e.target.closest('.gt-tip') : null; }
  document.addEventListener('mouseover', function (e) { var t = tipOf(e); if (t) show(t); });
  document.addEventListener('mouseout', function (e) { var t = tipOf(e); if (t && t === current && document.activeElement !== t) hide(); });
  document.addEventListener('focusin', function (e) { var t = tipOf(e); if (t) show(t); });
  document.addEventListener('focusout', function (e) { if (tipOf(e)) hide(); });
  document.addEventListener('click', function (e) {
    var t = tipOf(e);
    if (t) { e.preventDefault(); if (current === t && bubble && !bubble.hidden) hide(); else show(t); }
    else hide();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') hide(); });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
})();
