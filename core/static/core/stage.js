/* Majukan tahap form (D84), used by templates/farm/_stage_form.html. */
function stageForm(dataId) {
  var p = JSON.parse(document.getElementById(dataId).textContent);
  return {
    p: p,
    skip: false,
    stage: p.stages.length ? p.stages[0].value : '',
    label: function (v) { var s = p.stages.find(function (x) { return x.value === v; }); return s ? s.label : v; },
    confirmAdvance: function (form) {
      var target = this.stage, lines = [], level = 1, idx = p.orders[target];
      var skipped = p.stages.filter(function (s) { return p.orders[s.value] < idx; }).map(function (s) { return s.label; });
      lines.push(p.cycle + ': ' + p.current + ' → ' + this.label(target) + '.');
      if (skipped.length) { lines.push('Tahap yang dilewati: ' + skipped.join(', ') + '.'); level = 2; }
      if (idx >= p.harvesting_index && p.orders[p.stages[0].value] <= p.harvesting_index) {
        lines.push(p.check_pct === null ? 'Peringatan: belum ada cek pra-panen.'
          : 'Cek pra-panen terakhir: ' + String(p.check_pct).replace('.', ',') + '% lengkap' + (p.check_pct < 100 ? ' (belum lengkap).' : '.'));
      }
      if (target === 'CLEARING') {
        level = 2;
        lines.push('Pembersihan menutup siklus; tidak ada panen lagi setelah ini.');
        if (p.writeoff !== null) {
          lines.push(p.writeoff > 0 ? 'Biaya belum terserap ' + window.gtRupiah(p.writeoff) + ' dicatat sebagai biaya siklus tak terserap (write-off).'
            : 'Tidak ada biaya yang belum terserap.');
        }
      }
      lines.push('Pembatalan hanya bisa oleh OWNER/MANAGER di hari yang sama, selama belum ada catatan baru.');
      gtConfirm({ title: 'Majukan tahap?', body: lines.join('\n'), action: 'Majukan ke ' + this.label(target), level: level })
        .then(function (ok) { if (ok) form.submit(); });
    },
  };
}
