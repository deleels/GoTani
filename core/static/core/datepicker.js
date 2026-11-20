/* DatePicker (design system): every <input type="date"> / "datetime-local" opens the themed flatpickr popover
   instead of the native calendar. The real input keeps its name and ISO value (Y-m-d / Y-m-dTH:i), so forms and
   views are unchanged; a friendly alt input ("20 Nov 2026") is what the user sees. Inputs added later (sheets,
   Alpine templates) are picked up by a MutationObserver. Add data-native-date to opt out. */
(function () {
  if (!window.flatpickr) return;
  if (!window.GT_EN && flatpickr.l10ns && flatpickr.l10ns.id) flatpickr.localize(flatpickr.l10ns.id);
  var SEL = 'input[type="date"]:not([data-native-date]), input[type="datetime-local"]:not([data-native-date])';

  function init(el) {
    if (el._flatpickr) return;
    var dt = el.type === "datetime-local";
    var fp = flatpickr(el, {
      dateFormat: dt ? "Y-m-d\\TH:i" : "Y-m-d",
      altInput: true,
      altFormat: dt ? "j M Y, H:i" : "j M Y",
      enableTime: dt,
      time_24hr: true,
      disableMobile: true,
      minDate: el.min || null,
      maxDate: el.max || null,
      monthSelectorType: "static",
      onChange: function () { el.dispatchEvent(new Event("input", { bubbles: true })); },
    });
    var alt = fp.altInput;
    if (!alt) return;
    // The visible field takes over the label, help text and required state.
    if (el.id) { alt.id = el.id; el.id = el.id + "_iso"; }
    ["aria-describedby", "aria-invalid", "aria-label"].forEach(function (a) { if (el.hasAttribute(a)) alt.setAttribute(a, el.getAttribute(a)); });
    alt.required = el.required;
    alt.readOnly = true;
    alt.setAttribute("data-datepicker", "");
  }

  function scan(root) {
    if (root.matches && root.matches(SEL)) init(root);
    if (root.querySelectorAll) root.querySelectorAll(SEL).forEach(init);
  }

  function start() {
    scan(document);
    new MutationObserver(function (muts) {
      muts.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) scan(n); }); });
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
