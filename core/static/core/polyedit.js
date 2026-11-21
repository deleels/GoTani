/* GoTani polygon editor for Leaflet 1.9 (Phase 7, replaces Leaflet.draw).
   gtPolyEditor(map, { style, onChange }) draws and edits one polygon:
   - draw(): each map click adds a corner; a dashed guide follows the pointer; tapping the first corner (or finish())
     closes the shape, which then stays editable.
   - edit(latlngs): corners drag; a "+" on every edge adds a corner where it is tapped (or dragged to);
     right-click / long-press removes a corner (a shape keeps at least 3).
   - undo() steps back through every change; cancel() removes everything; latlngs() returns the corners.
   All Leaflet objects live in this closure (never in Alpine state: its proxies break Leaflet). onChange gets plain
   values: { mode, count, areaM2, crosses }. */
(function () {
  const R = 6378137, D2R = Math.PI / 180;

  // Geodesic area in m² (same formula as Leaflet.draw's GeometryUtil.geodesicArea).
  function area(pts) {
    let a = 0;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = pts[(i + 1) % pts.length];
      a += (q.lng - p.lng) * D2R * (2 + Math.sin(p.lat * D2R) + Math.sin(q.lat * D2R));
    }
    return Math.abs(a * R * R / 2);
  }

  function segCross(a, b, c, d) {
    const o = (p, q, r) => Math.sign((q.lng - p.lng) * (r.lat - p.lat) - (q.lat - p.lat) * (r.lng - p.lng));
    return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0;
  }

  // True when two non-neighbouring edges of the closed shape cross.
  function crosses(pts) {
    const n = pts.length;
    if (n < 4) return false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (j === i + 1 || (i === 0 && j === n - 1)) continue;
        if (segCross(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return true;
      }
    }
    return false;
  }

  window.gtPolyEditor = function (map, opts) {
    const style = opts.style, onChange = opts.onChange || function () {};
    const layer = L.layerGroup().addTo(map);
    let mode = null, pts = [], hist = [], shape = null, guide = null, mids = [], cursor = null;

    const icon = (cls) => L.divIcon({ className: "app-vx " + cls, html: "<span></span>", iconSize: [26, 26] });
    const copy = () => pts.map((p) => L.latLng(p.lat, p.lng));
    const snapshot = () => { hist.push(copy()); if (hist.length > 200) hist.shift(); };
    const emit = () => onChange({ mode, count: pts.length, areaM2: pts.length >= 3 ? area(pts) : 0,
                                  crosses: pts.length >= 4 && crosses(pts), canUndo: hist.length > 0 });

    // A tap (press and release within a few pixels) on a marker. Done by hand: Leaflet's drag handling on
    // draggable markers can swallow the plain click.
    function onTap(marker, fn) {
      const el = marker.getElement();
      if (!el) return;
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        const x = e.clientX, y = e.clientY;
        document.addEventListener("pointerup", (u) => { if (Math.hypot(u.clientX - x, u.clientY - y) < 4) fn(); },
          { once: true, capture: true });
      });
    }

    function drawShape() {
      if (shape && shape._gtMode === mode) shape.setLatLngs(pts);
      else {
        if (shape) layer.removeLayer(shape);
        shape = mode === "draw" ? L.polyline(pts, Object.assign({}, style, { interactive: false, fill: false }))
                                : L.polygon(pts, Object.assign({}, style, { interactive: false }));
        shape._gtMode = mode; shape.addTo(layer);
      }
      drawGuide();
    }

    function drawGuide() {
      if (guide) { layer.removeLayer(guide); guide = null; }
      if (mode !== "draw" || !pts.length || !cursor) return;
      const line = [pts[pts.length - 1], cursor];
      if (pts.length >= 2) line.push(pts[0]);
      guide = L.polyline(line, { color: style.color, weight: 2, dashArray: "6 6", opacity: 0.85, interactive: false }).addTo(layer);
    }

    // Halfway along an edge, in coordinates (at field scale the projection makes no visible difference).
    const midOf = (i) => { const a = pts[i], b = pts[(i + 1) % pts.length]; return L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2); };
    const placeMids = () => mids.forEach((m, i) => m.setLatLng(midOf(i)));

    function remove(i) {
      if (mode === "edit" && pts.length <= 3) return;
      snapshot(); pts.splice(i, 1); rebuild(); emit();
    }

    function addVertex(i) {
      const first = mode === "draw" && i === 0;
      const m = L.marker(pts[i], { icon: icon(first && pts.length >= 3 ? "app-vx--close" : ""), draggable: true, keyboard: false,
                                   autoPan: true, bubblingMouseEvents: false, zIndexOffset: 1000 }).addTo(layer);
      m.on("dragstart", snapshot);
      m.on("drag", (e) => { pts[i] = e.target.getLatLng(); drawShape(); placeMids(); });
      m.on("dragend", () => { placeMids(); emit(); });
      m.on("contextmenu", (e) => { L.DomEvent.preventDefault(e.originalEvent); remove(i); });
      if (first) onTap(m, () => { if (mode === "draw" && pts.length >= 3) finish(); });
    }

    function addMid(i) {
      const m = L.marker(midOf(i), { icon: icon("app-vx--mid"), draggable: true, keyboard: false,
                                     bubblingMouseEvents: false, zIndexOffset: 900 }).addTo(layer);
      let at = -1;
      onTap(m, () => { snapshot(); pts.splice(i + 1, 0, m.getLatLng()); rebuild(); emit(); });
      m.on("dragstart", () => { snapshot(); at = i + 1; pts.splice(at, 0, m.getLatLng()); drawShape(); });
      m.on("drag", (e) => { pts[at] = e.target.getLatLng(); drawShape(); });
      m.on("dragend", () => { at = -1; rebuild(); emit(); });
      m.on("contextmenu", (e) => L.DomEvent.preventDefault(e.originalEvent));
      mids.push(m);
    }

    function rebuild() {
      layer.clearLayers(); shape = guide = null; mids = [];
      if (!mode) return;
      drawShape();
      if (mode === "edit") for (let i = 0; i < pts.length; i++) addMid(i);
      pts.forEach((_, i) => addVertex(i));
    }

    function onMapClick(e) {
      if (mode !== "draw") return;
      snapshot(); pts.push(e.latlng); rebuild(); emit();
    }
    function onMove(e) { if (mode === "draw") { cursor = e.latlng; drawGuide(); } }

    function listen(on) {
      const fn = on ? "on" : "off";
      map[fn]("click", onMapClick); map[fn]("mousemove", onMove);
      L.DomUtil[on ? "addClass" : "removeClass"](map.getContainer(), "app-map--drawing");
      if (on) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    }

    function draw() { cancel(); mode = "draw"; listen(true); rebuild(); emit(); }
    function edit(latlngs) {
      cancel(); mode = "edit"; pts = latlngs.map((p) => L.latLng(p.lat, p.lng));
      if (pts.length > 3 && pts[0].equals(pts[pts.length - 1])) pts.pop();   // a GeoJSON ring repeats its first corner
      rebuild(); emit();
    }
    function finish() {
      if (mode !== "draw" || pts.length < 3) return false;
      listen(false); cursor = null; mode = "edit"; rebuild(); emit(); return true;
    }
    function undo() { if (!hist.length) return; pts = hist.pop(); rebuild(); emit(); }
    function cancel() {
      if (mode === "draw") listen(false);
      mode = null; pts = []; hist = []; cursor = null; rebuild(); emit();
    }

    return {
      draw, edit, finish, undo, cancel,
      mode: () => mode,
      latlngs: () => copy(),
      geojson: () => ({ type: "Polygon", coordinates: [pts.concat([pts[0]]).map((p) => [Number(p.lng.toFixed(7)), Number(p.lat.toFixed(7))])] }),
      _area: area, _crosses: crosses,
    };
  };
})();
