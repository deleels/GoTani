/* Phase 7: gtT(msgid, params) translates script text via window.GT_I18N; %(name)s placeholders. */
window.gtT = function (s, params) {
  var t = (window.GT_I18N && window.GT_I18N[s]) || s;
  if (params) t = t.replace(/%\((\w+)\)s/g, function (m, k) { return params[k] != null ? params[k] : m; });
  return t.replace(/%%/g, '%');
};
window.GT_EN = (window.GT_LANG || document.documentElement.lang || 'id').slice(0, 2) === 'en';
window.GT_LOCALE = window.GT_EN ? 'en-US' : 'id-ID';
/* Number inputs (D81, Phase 7). <input data-num="money|money2|kg"> is formatted while typing in the active
   language: Indonesian groups thousands with "." and marks decimals with ","; English the other way round.
   Pressing "." or "," types the decimal mark where decimals are allowed. On submit every such input is
   rewritten to a plain machine number ("1500.25"). */
(function () {
  var PLACES = { money: 0, money2: 2, kg: 3 };
  var TH = window.GT_EN ? ',' : '.', DEC = window.GT_EN ? '.' : ',';
  // Everything except digits and this language's decimal mark (character class, so "." needs no escape).
  var NOT_KEEP = new RegExp('[^0-9' + DEC + ']', 'g');

  // Parses what an input shows (thousands marks dropped, the decimal mark becomes "."); "Rp" and spaces ignored.
  function parse(v) {
    if (typeof v === 'number') return v;
    var t = String(v == null ? '' : v).replace(/\s|Rp/gi, '').split(TH).join('').replace(DEC, '.');
    var n = parseFloat(t);
    return isNaN(n) ? NaN : n;
  }

  function group(digits) {
    return digits.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, TH);
  }

  function format(n, kind) {
    if (n === '' || n == null || isNaN(Number(n))) return '';
    var places = PLACES[kind || 'kg'];
    var parts = Math.abs(Number(n)).toFixed(places).split('.');
    var f = (parts[1] || '').replace(/0+$/, '');
    return (Number(n) < 0 ? '-' : '') + group(parts[0]) + (f ? DEC + f : '');
  }

  // Reformat a partially typed value, keeping a trailing decimal mark and decimals being typed.
  function live(text, kind) {
    var places = PLACES[kind];
    var t = String(text).replace(/^\s*Rp/i, '').replace(NOT_KEEP, '');
    var bits = t.split(DEC);
    var w = bits.shift();
    if (!places) return group(w || '');
    var f = bits.join('').slice(0, places);
    return group(w || (t.charAt(0) === DEC ? '0' : '')) + (t.indexOf(DEC) >= 0 ? DEC + f : '');
  }

  function reformat(el) {
    var kind = el.dataset.num, before = el.value;
    var caret = el.selectionStart == null ? before.length : el.selectionStart;
    var sig = before.slice(0, caret).replace(NOT_KEEP, '').length;
    var after = live(before, kind);
    if (after === before) return;
    el.value = after;
    var pos = 0, seen = 0;
    while (pos < after.length && seen < sig) { if (/\d/.test(after.charAt(pos)) || after.charAt(pos) === DEC) seen++; pos++; }
    try { el.setSelectionRange(pos, pos); } catch (e) { /* not focused */ }
  }

  function isNum(el) { return el && el.matches && el.matches('input[data-num]'); }

  document.addEventListener('keydown', function (e) {
    var el = e.target;
    if (!isNum(el) || e.ctrlKey || e.metaKey) return;
    if (e.key === '.' || e.key === ',') {
      e.preventDefault();
      if (!PLACES[el.dataset.num] || el.value.indexOf(DEC) >= 0) return;
      var s = el.selectionStart, end = el.selectionEnd;
      el.value = el.value.slice(0, s) + DEC + el.value.slice(end);
      el.setSelectionRange(s + 1, s + 1);
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  // Pasted display text ("Rp 3.000") is cleaned the same way as typing.
  document.addEventListener('paste', function (e) {
    var el = e.target;
    if (!isNum(el) || !e.clipboardData) return;
    var txt = e.clipboardData.getData('text');
    var n = parse(txt.replace(/^\s*Rp\s*/i, ''));
    if (isNaN(n)) return;
    e.preventDefault();
    el.value = format(n, el.dataset.num);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  document.addEventListener('input', function (e) { if (isNum(e.target)) reformat(e.target); });
  document.addEventListener('submit', function (e) {
    // form.elements also holds inputs outside the <form> that join it with form="…" (Harga Produk rows).
    Array.prototype.forEach.call(e.target.elements || [], function (el) {
      if (el.tagName !== 'INPUT' || !el.hasAttribute('data-num') || el.value.trim() === '') return;
      var n = parse(el.value);
      if (!isNaN(n)) el.value = String(n);
    });
  }); // bubble phase: runs only for a submit the confirm dialog (capture phase) lets through

  // Server-rendered values are already in the display format; normalise anything else (e.g. "25000.00").
  function init(root) {
    (root || document).querySelectorAll('input[data-num]').forEach(function (el) {
      if (el.value) { var n = parse(el.value); if (!isNaN(n)) el.value = format(n, el.dataset.num); }
    });
  }
  document.addEventListener('DOMContentLoaded', function () { init(); });
  window.idNum = { parse: parse, format: format, init: init, live: live };
})();

/* D105: money is written "Rp15.000" (Indonesian) / "Rp15,000" (English), no space. */
window.gtRupiah = (v) => (Number(v) < 0 ? "-" : "") + "Rp" + Math.abs(Math.round(Number(v || 0))).toLocaleString(window.GT_LOCALE);
