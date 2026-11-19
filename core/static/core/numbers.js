/* Indonesian number inputs (D81). <input data-num="money|money2|kg"> is formatted while typing:
   dots group thousands, a comma marks decimals, and the "." key types a comma where decimals are
   allowed. On submit every such input is rewritten to a plain machine number ("1500.25"). */
(function () {
  var PLACES = { money: 0, money2: 2, kg: 3 };

  // Parses what an input shows. In the browser "." never types a decimal point (it types a comma),
  // so dots are always thousands separators here; the server's parser also accepts machine numbers.
  function parse(v) {
    if (typeof v === 'number') return v;
    var t = String(v == null ? '' : v).replace(/\s|Rp/gi, '').replace(/\./g, '').replace(',', '.');
    var n = parseFloat(t);
    return isNaN(n) ? NaN : n;
  }

  function group(digits) {
    return digits.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  function format(n, kind) {
    if (n === '' || n == null || isNaN(Number(n))) return '';
    var places = PLACES[kind || 'kg'];
    var parts = Math.abs(Number(n)).toFixed(places).split('.');
    var f = (parts[1] || '').replace(/0+$/, '');
    return (Number(n) < 0 ? '-' : '') + group(parts[0]) + (f ? ',' + f : '');
  }

  // Reformat a partially typed value, keeping a trailing comma and decimals being typed.
  function live(text, kind) {
    var places = PLACES[kind];
    var t = String(text).replace(/[^\d,]/g, '');
    var bits = t.split(',');
    var w = bits.shift();
    if (!places) return group(w || '');
    var f = bits.join('').slice(0, places);
    return group(w || (t.charAt(0) === ',' ? '0' : '')) + (t.indexOf(',') >= 0 ? ',' + f : '');
  }

  function reformat(el) {
    var kind = el.dataset.num, before = el.value;
    var caret = el.selectionStart == null ? before.length : el.selectionStart;
    var sig = before.slice(0, caret).replace(/[^\d,]/g, '').length;
    var after = live(before, kind);
    if (after === before) return;
    el.value = after;
    var pos = 0, seen = 0;
    while (pos < after.length && seen < sig) { if (/[\d,]/.test(after.charAt(pos))) seen++; pos++; }
    try { el.setSelectionRange(pos, pos); } catch (e) { /* not focused */ }
  }

  function isNum(el) { return el && el.matches && el.matches('input[data-num]'); }

  document.addEventListener('keydown', function (e) {
    var el = e.target;
    if (!isNum(el) || e.ctrlKey || e.metaKey) return;
    if (e.key === '.' || e.key === ',') {
      e.preventDefault();
      if (!PLACES[el.dataset.num] || el.value.indexOf(',') >= 0) return;
      var s = el.selectionStart, end = el.selectionEnd;
      el.value = el.value.slice(0, s) + ',' + el.value.slice(end);
      el.setSelectionRange(s + 1, s + 1);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  document.addEventListener('input', function (e) { if (isNum(e.target)) reformat(e.target); });
  document.addEventListener('submit', function (e) {
    e.target.querySelectorAll('input[data-num]').forEach(function (el) {
      if (el.value.trim() === '') return;
      var n = parse(el.value);
      if (!isNaN(n)) el.value = String(n);
    });
  }); // bubble phase: runs only for a submit the confirm dialog (capture phase) lets through

  // Server-rendered values are already Indonesian; normalise anything else (e.g. "25000.00").
  function init(root) {
    (root || document).querySelectorAll('input[data-num]').forEach(function (el) {
      if (el.value) { var n = parse(el.value); if (!isNaN(n)) el.value = format(n, el.dataset.num); }
    });
  }
  document.addEventListener('DOMContentLoaded', function () { init(); });
  window.idNum = { parse: parse, format: format, init: init };
})();

/* D105: money is written "Rp15.000" (no space) everywhere in JS. */
window.gtRupiah = (v) => "Rp" + Math.round(Number(v || 0)).toLocaleString("id-ID");
