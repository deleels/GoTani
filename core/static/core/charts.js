/* Chart.js styled from the design-system tokens (D104). Load after Chart.js; pages call gtCharts.*.
   Bars are pill-shaped; series run green-600, green-800, green-400, hatch; red only for losses;
   targets / pending values use a diagonal hatch pattern, never a solid colour. */
(function () {
  if (!window.Chart) return;
  const t = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  Chart.defaults.font.family = t("--font-sans");
  Chart.defaults.font.size = 12;
  Chart.defaults.color = t("--ink-muted");
  Chart.defaults.borderColor = t("--divider");
  Object.assign(Chart.defaults.plugins.tooltip, {
    backgroundColor: t("--surface-raised"), titleColor: t("--ink"), bodyColor: t("--ink"),
    borderColor: t("--divider"), borderWidth: 1, cornerRadius: 12, padding: 12, displayColors: false,
    titleFont: { weight: "500" },
  });

  function hatch() {
    const c = document.createElement("canvas"); c.width = c.height = 8;
    const g = c.getContext("2d");
    g.fillStyle = t("--surface-raised"); g.fillRect(0, 0, 8, 8);
    g.strokeStyle = t("--hatch"); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(-2, 10); g.lineTo(10, -2); g.moveTo(-2, 2); g.lineTo(2, -2); g.moveTo(6, 10); g.lineTo(10, 6); g.stroke();
    return g.createPattern(c, "repeat");
  }

  const num = (v, d) => Number(v || 0).toLocaleString("id-ID", { maximumFractionDigits: d == null ? 1 : d });

  window.gtCharts = {
    token: t,
    BAR: { borderRadius: 999, borderSkipped: false, maxBarThickness: 36 },
    LINE: { borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, fill: false, tension: 0.3 },
    series: () => [t("--green-600"), t("--green-800"), t("--green-400"), hatch()],
    hatch,
    num,
    /* Short money for axes (Rp277,5 jt · Rp593rb); exact money for tooltips via gtRupiah. */
    rpShort(v) {
      const a = Math.abs(v), s = v < 0 ? "-" : "";
      if (a >= 1e9) return s + "Rp" + num(a / 1e9, 1) + " M";
      if (a >= 1e6) return s + "Rp" + num(a / 1e6, 1) + " jt";
      if (a >= 1e3) return s + "Rp" + num(a / 1e3, 0) + "rb";
      return s + "Rp" + num(a, 0);
    },
    grid: () => ({ color: t("--divider"), drawTicks: false }),
    axis: (extra) => Object.assign({ border: { display: false }, grid: { color: t("--divider"), drawTicks: false }, ticks: { padding: 8 } }, extra || {}),
  };
})();
