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
    var price = idNum.parse(form.querySelector('[name=selling_price_idr]').value);
    var old = Number(form.dataset.oldPrice || 0), cogs = Number(form.dataset.cogs || 0), lines = [];
    if (!(price > 0)) return false;
    if (old > 0 && Math.abs(price - old) / old > 0.3) {
      lines.push('Harga ' + (price > old ? 'naik ' : 'turun ') + pct(Math.abs(price - old) / old) + ': ' + rp(old) + ' → ' + rp(price) + ' per kg.');
    }
    if (cogs > 0 && price < cogs) {
      lines.push('Harga di bawah HPP rata-rata lot yang masih ada (' + rp(cogs) + '/kg); setiap kg terjual merugi.');
    }
    return lines.length ? show(form, 'Periksa harga baru', lines, 'Simpan harga') : false;
  };

  // Input purchase: unit cost more than 50% away from the item's current average cost.
  window.purchaseWarn = function (form) {
    var costs = data('input-costs'), itemEl = form.querySelector('[name=item]');
    var item = costs[itemEl ? itemEl.value : ''];
    var cost = idNum.parse(form.querySelector('[name=cost_per_unit_idr]').value);
    if (!item || !(Number(item.avg) > 0) || !(cost > 0)) return false;
    var diff = (cost - Number(item.avg)) / Number(item.avg);
    if (Math.abs(diff) <= 0.5) return false;
    return show(form, 'Harga beli tidak biasa', [
      item.name + ': ' + rp(cost) + ' per ' + item.unit + ', ' + pct(Math.abs(diff)) + (diff > 0 ? ' lebih mahal' : ' lebih murah') +
        ' dari rata-rata ' + rp(Number(item.avg)) + '.',
      'Harga ini mengubah biaya rata-rata input dan biaya pemakaian berikutnya. Periksa satuan dan angkanya.'], 'Simpan pembelian');
  };
})();
