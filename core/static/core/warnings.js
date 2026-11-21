/* Soft data-entry warnings (D87), wired through the shared dialog's data-confirm-if (confirm.js).
   Each function returns true when the dialog should open, after writing its text onto the form. */
(function () {
  var rp = window.gtRupiah;
  function pct(v) { return Math.round(v * 100) + '%'; }
  function data(id) { var el = document.getElementById(id); return el ? JSON.parse(el.textContent) : {}; }
  function show(form, title, lines, action) {
    form.dataset.confirmTitle = title;
    form.dataset.confirm = lines.join('\n');
    form.dataset.confirmAction = action;
    return true;
  }

  // Harga Produk: a change of more than 30%, or a price below the open lots' average cost per kg.
  window.priceWarn = function (form) {
    var price = idNum.parse(form.elements.selling_price_idr.value);
    var old = Number(form.dataset.oldPrice || 0), cogs = Number(form.dataset.cogs || 0), lines = [];
    if (!(price > 0)) return false;
    if (old > 0 && Math.abs(price - old) / old > 0.3) {
      lines.push(gtT(price > old ? 'Harga naik %(pct)s: %(old)s → %(new)s per kg.' : 'Harga turun %(pct)s: %(old)s → %(new)s per kg.',
        { pct: pct(Math.abs(price - old) / old), old: rp(old), 'new': rp(price) }));
    }
    if (cogs > 0 && price < cogs) {
      lines.push(gtT('Harga di bawah HPP rata-rata lot yang masih ada (%(cogs)s/kg); setiap kg terjual merugi.', { cogs: rp(cogs) }));
    }
    return lines.length ? show(form, gtT('Periksa harga baru'), lines, gtT('Simpan harga')) : false;
  };

  // Catat pembayaran (D114): money left after the allocations. With other open invoices still unpaid the dialog
  // asks where it goes (posted as `leftover`: apply | credit); otherwise it confirms it becomes credit.
  window.paymentWarn = function (form) {
    var stale = form.querySelector('input[name=leftover][data-confirm-choice-field]');
    if (stale) stale.remove();
    delete form.dataset.confirmChoices;
    var amount = idNum.parse(form.elements.amount_idr.value) || 0, applied = 0, room = [];
    form.querySelectorAll('[data-alloc]').forEach(function (el) {
      var v = idNum.parse(el.value) || 0;
      applied += v;
      if (Number(el.dataset.outstanding) - v > 0.004) room.push(el.dataset.invoice);
    });
    var left = amount - applied, buyer = form.dataset.buyer;
    if (!(amount > 0) || left <= 0.004) return false;
    if (room.length) {
      form.dataset.confirmChoices = JSON.stringify([
        ['apply', gtT('Pakai untuk %(invoices)s (jatuh tempo paling awal dulu)', { invoices: room.join(', ') })],
        ['credit', gtT('Simpan sebagai kredit %(buyer)s', { buyer: buyer })]]);
      return show(form, gtT('Sisa %(left)s belum dialokasikan', { left: rp(left) }), [
        gtT('Dari %(amount)s yang diterima, %(applied)s masuk ke invoice.', { amount: rp(amount), applied: rp(applied) })],
        gtT('Simpan pembayaran'));
    }
    return show(form, gtT('Simpan %(left)s sebagai kredit?', { left: rp(left) }), [
      gtT('%(amount)s melebihi semua tagihan terbuka %(buyer)s sebesar %(left)s.', { amount: rp(amount), buyer: buyer, left: rp(left) }),
      gtT('Sisanya disimpan sebagai kredit %(buyer)s dan bisa dipakai untuk invoice berikutnya; tidak bisa dicairkan.', { buyer: buyer })],
      gtT('Simpan dengan kredit'));
  };

  // Input purchase: unit cost more than 50% away from the item's current average cost.
  window.purchaseWarn = function (form) {
    var costs = data('input-costs'), itemEl = form.querySelector('[name=item]');
    var item = costs[itemEl ? itemEl.value : ''];
    var cost = idNum.parse(form.querySelector('[name=cost_per_unit_idr]').value);
    if (!item || !(Number(item.avg) > 0) || !(cost > 0)) return false;
    var diff = (cost - Number(item.avg)) / Number(item.avg);
    if (Math.abs(diff) <= 0.5) return false;
    return show(form, gtT('Harga beli tidak biasa'), [
      gtT(diff > 0 ? '%(item)s: %(cost)s per %(unit)s, %(pct)s lebih mahal dari rata-rata %(avg)s.'
                   : '%(item)s: %(cost)s per %(unit)s, %(pct)s lebih murah dari rata-rata %(avg)s.',
          { item: item.name, cost: rp(cost), unit: item.unit, pct: pct(Math.abs(diff)), avg: rp(Number(item.avg)) }),
      gtT('Harga ini mengubah biaya rata-rata input dan biaya pemakaian berikutnya. Periksa satuan dan angkanya.')], gtT('Simpan pembelian'));
  };
})();
