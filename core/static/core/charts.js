/* Chart.js styled from the design-system tokens (D104). Load after Chart.js; pages call gtCharts.*.
   Bars are fat pills with varied fills (gtCharts.vary) and one value tag (plugins.gtTag); multi-series run
   green-600, green-800, green-400; red only for losses; targets / pending values are hatched, never solid.
   Share-of-a-whole is the server-rendered Gauge ({% gauge %}), never a doughnut. */
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

  /* Diagonal hatch fill; `stroke` / `bg` override the grey-on-white default (e.g. red stripes for a loss). */
  function hatch(stroke, bg) {
    const c = document.createElement("canvas"); c.width = c.height = 8;
    const g = c.getContext("2d");
    g.fillStyle = bg || t("--surface-raised"); g.fillRect(0, 0, 8, 8);
    g.strokeStyle = stroke || t("--hatch"); g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(-2, 10); g.lineTo(10, -2); g.moveTo(-2, 2); g.lineTo(2, -2); g.moveTo(6, 10); g.lineTo(10, 6); g.stroke();
    return g.createPattern(c, "repeat");
  }

  const num = (v, d) => Number(v || 0).toLocaleString("id-ID", { maximumFractionDigits: d == null ? 1 : d });

  /* Value tag (design-system gt-bar__tip): a white pill with a green-400 border above one bar (beside it on
     horizontal charts), joined by a ringed dot. Set options.plugins.gtTag = { index, text }. */
  Chart.register({
    id: "gtTag",
    afterDatasetsDraw(chart, _args, o) {
      if (!o || o.index == null || o.index < 0) return;
      const el = chart.getDatasetMeta(o.dataset || 0).data[o.index];
      if (!el) return;
      const g = chart.ctx, horiz = chart.options.indexAxis === "y";
      g.save();
      g.font = "500 11px " + t("--font-sans");
      const w = g.measureText(o.text).width + 16, h = 20;
      const x = horiz ? el.x + 10 : el.x - w / 2, y = horiz ? el.y - h / 2 : el.y - h - 12;
      g.beginPath(); g.roundRect(x, y, w, h, h / 2);
      g.fillStyle = t("--surface-raised"); g.fill();
      g.lineWidth = 1; g.strokeStyle = t("--green-400"); g.stroke();
      g.fillStyle = t("--ink"); g.textAlign = "center"; g.textBaseline = "middle";
      g.fillText(o.text, x + w / 2, y + h / 2 + 0.5);
      if (!horiz) {
        g.beginPath(); g.arc(el.x, el.y - 4, 4, 0, Math.PI * 2);
        g.fillStyle = t("--surface-raised"); g.fill(); g.lineWidth = 2; g.strokeStyle = t("--green-700"); g.stroke();
      }
      g.restore();
    },
  });

  /* Per-bar fills by meaning (design-system ChartBar): the peak green-800, the current/latest bar green-400,
     small amounts (< 25% of the peak) green-400, everything else green-600; null values are hatched (not yet). */
  function vary(values, opts) {
    opts = opts || {};
    const nums = values.map((v) => (v == null ? 0 : Number(v)));
    const peak = Math.max(0, ...nums), at = nums.indexOf(peak);
    const C = { peak: t("--green-800"), base: t("--green-600"), light: t("--green-400") }, H = hatch();
    return values.map((v, i) => {
      if (v == null) return H;
      if (i === opts.current) return C.light;
      if (peak > 0 && i === at) return C.peak;
      if (peak > 0 && nums[i] < peak * 0.25) return C.light;
      return C.base;
    });
  }
  const peakIndex = (values) => { const n = values.map((v) => Number(v || 0)), p = Math.max(...n); return p > 0 ? n.indexOf(p) : -1; };

  window.gtCharts = {
    token: t,
    vary,
    peakIndex,
    /* Fat pill bars (gap ≈ bar width), like the design-system ChartBar. */
    BAR: { borderRadius: 999, borderSkipped: false, maxBarThickness: 44, categoryPercentage: 0.72, barPercentage: 0.92 },
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
