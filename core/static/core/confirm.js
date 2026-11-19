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

  function build() {
    dlg = el('dialog', 'gt-confirm w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-stone-200 bg-white p-0 text-stone-800 shadow-xl backdrop:bg-stone-900/50');
    dlg.setAttribute('aria-labelledby', 'gt-confirm-title');
    dlg.innerHTML =
      '<form method="dialog" class="p-5">' +
      '<h2 id="gt-confirm-title" class="text-lg font-semibold"></h2>' +
      '<div data-body class="mt-2 space-y-2 text-sm text-stone-600"></div>' +
      '<div data-extra class="mt-3 text-sm"></div>' +
      '<label data-choice-wrap class="mt-3 block text-sm"><span class="text-stone-600">Alasan</span>' +
      '<select data-choice class="mt-1 w-full rounded-lg border border-stone-300 px-2 py-1.5 text-sm"></select></label>' +
      '<label data-reason-wrap class="mt-3 block text-sm"><span data-reason-label class="text-stone-600"></span>' +
      '<textarea data-reason rows="2" class="mt-1 w-full rounded-lg border border-stone-300 px-2 py-1.5 text-sm"></textarea></label>' +
      '<label data-check-wrap class="mt-3 flex items-start gap-2 rounded-lg border border-amber-400 p-2 text-sm font-medium text-stone-700">' +
      '<input type="checkbox" data-check class="mt-0.5"> <span>Saya sudah memeriksa data ini</span></label>' +
      '<div class="mt-5 flex justify-end gap-2">' +
      '<button value="cancel" data-cancel class="rounded-lg border border-stone-300 px-4 py-2 text-sm hover:bg-stone-100">Batal</button>' +
      '<button value="ok" data-ok class="rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40"></button>' +
      '</div></form>';
    document.body.appendChild(dlg);
    var ok = dlg.querySelector('[data-ok]'), check = dlg.querySelector('[data-check]'), reason = dlg.querySelector('[data-reason]');
    var choice = dlg.querySelector('[data-choice]');
    function sync() {
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
    var body = dlg.querySelector('[data-body]');
    body.innerHTML = '';
    String(opts.body || '').split('\n').filter(Boolean).forEach(function (p) { body.appendChild(el('p', '', p)); });
    var extra = dlg.querySelector('[data-extra]');
    extra.innerHTML = '';
    if (opts.html) extra.appendChild(opts.html);
    var wrap = dlg.querySelector('[data-reason-wrap]');
    wrap.style.display = opts.reason ? '' : 'none';  // not `hidden`: Tailwind's display classes override it
    dlg.dataset.reasonRequired = opts.reason && !opts.reasonOptional ? '1' : '';
    dlg.dataset.reasonLabel = opts.reason || '';
    var cw = dlg.querySelector('[data-choice-wrap]'), sel = dlg.querySelector('[data-choice]');
    sel.innerHTML = '';
    cw.style.display = opts.choices ? '' : 'none';
    dlg.dataset.other = opts.other || '';
    if (opts.choices) {
      sel.appendChild(el('option', '', '— pilih alasan —')).value = '';
      opts.choices.forEach(function (c) { var o = el('option', '', c[1]); o.value = c[0]; sel.appendChild(o); });
    }
    dlg.querySelector('[data-reason]').value = '';
    dlg.querySelector('[data-check-wrap]').style.display = Number(opts.level || 1) < 2 ? 'none' : '';
    dlg.querySelector('[data-check]').checked = false;
    var ok = dlg.querySelector('[data-ok]');
    ok.textContent = opts.action || 'Lanjutkan';
    ok.className = 'rounded-lg px-4 py-2 text-sm font-medium text-white disabled:opacity-40 ' +
      (opts.danger ? 'bg-red-600 hover:bg-red-700' : 'bg-emerald-600 hover:bg-emerald-700');
    dlg.returnValue = '';
    dlg._sync();
    dlg.showModal();
    (opts.choices ? sel : opts.reason ? dlg.querySelector('[data-reason]') : dlg.querySelector('[data-cancel]')).focus();
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
})();
