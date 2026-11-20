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
