(() => {
  "use strict";

  const CFG = Object.assign({ DATA_BASE: "./data" }, window.SENTINEL_CONFIG || {});
  const COLORS = { police: "#4cc9ff", swe_mil: "#ffb020", foreign_mil: "#ff6b5e" };
  const LABELS = { police: "POLICE", swe_mil: "SWEDISH MIL", foreign_mil: "FOREIGN MIL" };
  const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const REGIONS = {
    all: { center: [62.5, 16.5], zoom: 5 },
    sth: { center: [59.33, 18.07], zoom: 9 },
    got: { center: [57.71, 11.97], zoom: 9 },
    mal: { center: [55.6, 13.0], zoom: 9 },
    lul: { center: [65.58, 22.15], zoom: 9 }
  };
  const STOCKHOLM = [59.33, 18.07];

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");

  const state = {
    days: [],            // [{date, police, swe_mil, foreign_mil}]
    windows: [],         // [{id, label, dates}]
    win: null,
    aircraft: [],        // merged for the current window
    cats: { police: true, swe_mil: true, foreign_mil: true },
    typesOff: new Set(),
    heat: false,
    grid: true,
    selected: null
  };

  // ---------------------------------------------------------------- helpers
  function dateLabel(iso) { const [, m, d] = iso.split("-"); return `${d} ${MONTHS[+m - 1]}`; }
  function timeLabel(t) { return `${pad(Math.floor(t / 3600) % 24)}:${pad(Math.floor((t % 3600) / 60))}`; }
  function stamp(date, t) { return `${dateLabel(date)} ${timeLabel(t)}Z`; }
  function tkey(a) { return a.type || "?"; }
  function name(a) { return a.reg || a.hex.toUpperCase(); }

  function haversine(a, b) {
    const R = 6371, rad = Math.PI / 180;
    const dLat = (b[1] - a[1]) * rad, dLon = (b[2] - a[2]) * rad;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * rad) * Math.cos(b[1] * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  }

  function shapeSvg(cat, size = 16) {
    const shapes = {
      police: '<path d="M0,-6 L6,0 L0,6 L-6,0 Z"/>',
      swe_mil: '<path d="M0,-6.5 L6.5,5 L-6.5,5 Z"/>',
      foreign_mil: '<rect x="-4.6" y="-4.6" width="9.2" height="9.2" transform="rotate(45)"/>'
    };
    return `<svg width="${size}" height="${size}" viewBox="-8 -8 16 16" fill="#04080a" stroke="${COLORS[cat]}" stroke-width="1.6" aria-hidden="true">${shapes[cat]}</svg>`;
  }

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${url} returned ${r.status}`);
    return r.json();
  }
  const dayCache = new Map();
  function loadDay(date) {
    if (!dayCache.has(date)) {
      const p = getJSON(`${CFG.DATA_BASE}/${date}.json`);
      p.catch(() => dayCache.delete(date));
      dayCache.set(date, p);
    }
    return dayCache.get(date);
  }

  function showMsg(text) {
    const el = $("#msg");
    if (!text) { el.hidden = true; return; }
    el.textContent = text;
    el.hidden = false;
  }

  // ------------------------------------------------------------------- data
  function mergeDays(docs) {
    const byHex = new Map();
    for (const doc of docs) {
      for (const a of doc.aircraft) {
        let m = byHex.get(a.hex);
        if (!m) {
          m = { hex: a.hex, reg: a.reg, type: a.type, desc: a.desc, op: a.op, cat: a.cat, segs: [] };
          byHex.set(a.hex, m);
        }
        for (const pts of a.segs) m.segs.push({ date: doc.date, pts });
      }
    }
    const list = Array.from(byHex.values());
    for (const a of list) {
      a.segs.sort((x, y) => (x.date < y.date ? -1 : x.date > y.date ? 1 : x.pts[0][0] - y.pts[0][0]));
      let n = 0, km = 0, maxAlt = null;
      for (const s of a.segs) {
        for (let i = 0; i < s.pts.length; i++) {
          const p = s.pts[i];
          n++;
          if (p[3] != null && (maxAlt == null || p[3] > maxAlt)) maxAlt = p[3];
          if (i) km += haversine(s.pts[i - 1], p);
        }
      }
      const first = a.segs[0], last = a.segs[a.segs.length - 1];
      a.n = n; a.km = km; a.maxAlt = maxAlt;
      a.first = { date: first.date, t: first.pts[0][0] };
      a.last = { date: last.date, t: last.pts[last.pts.length - 1][0] };
      a.lastPt = last.pts[last.pts.length - 1];
    }
    return list;
  }

  function buildWindows(dates) {
    const latest = dates[dates.length - 1];
    const wins = [];
    dates.slice(-3).reverse().forEach((d) => wins.push({ id: d, label: dateLabel(d), dates: [d] }));
    const back = (n) => {
      const t = new Date(latest + "T00:00:00Z");
      t.setUTCDate(t.getUTCDate() - (n - 1));
      const from = t.toISOString().slice(0, 10);
      return dates.filter((d) => d >= from);
    };
    const d7 = back(7), d30 = back(30);
    if (d7.length > 1) wins.push({ id: "7d", label: "7D", dates: d7 });
    if (d30.length > d7.length) wins.push({ id: "30d", label: "30D", dates: d30 });
    return wins;
  }

  function windowText(w) {
    if (w.dates.length === 1) return `${dateLabel(w.dates[0])} ${w.dates[0].slice(0, 4)} UTC`;
    return `${dateLabel(w.dates[0])} - ${dateLabel(w.dates[w.dates.length - 1])} ${w.dates[w.dates.length - 1].slice(0, 4)} UTC`;
  }

  // -------------------------------------------------------------------- map
  const map = L.map("map", {
    zoomControl: false, minZoom: 4, maxZoom: 13, zoomSnap: 0.5, preferCanvas: true
  }).setView(REGIONS.all.center, REGIONS.all.zoom);

  // Esri's keyless legacy endpoint. CARTO's dark tiles started requiring an API key in 2026-10.
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    maxNativeZoom: 16, maxZoom: 19,
    attribution: 'Powered by <a href="https://www.esri.com">Esri</a> | Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, and the GIS user community'
  }).addTo(map);

  map.createPane("grid").style.zIndex = 350;
  map.getPane("grid").style.pointerEvents = "none";

  const ringLayer = L.layerGroup().addTo(map);
  const gridLayer = L.layerGroup().addTo(map);
  const trackLayer = L.layerGroup().addTo(map);
  let heatLayer = null;

  function drawRings() {
    ringLayer.clearLayers();
    for (const nm of [100, 200, 300, 400]) {
      const r = nm * 1852;
      L.circle(STOCKHOLM, { pane: "grid", radius: r, color: "#2a5a52", weight: 1, dashArray: "2 6", fill: false, interactive: false }).addTo(ringLayer);
      const dLat = (r * Math.SQRT1_2) / 111320;
      const dLon = (r * Math.SQRT1_2) / (111320 * Math.cos(STOCKHOLM[0] * Math.PI / 180));
      L.marker([STOCKHOLM[0] + dLat, STOCKHOLM[1] + dLon], {
        pane: "grid", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", html: `<span class="ringlab">${nm} NM</span>`, iconSize: [0, 0] })
      }).addTo(ringLayer);
    }
  }

  function niceStep(degPerPx) {
    const target = degPerPx * 120;
    const steps = [20, 10, 5, 2, 1, 0.5, 0.25, 0.1, 0.05, 0.02, 0.01];
    return steps.reduce((best, s) => (Math.abs(Math.log(s / target)) < Math.abs(Math.log(best / target)) ? s : best));
  }

  function drawGrid() {
    gridLayer.clearLayers();
    if (!state.grid) return;
    const exact = map.getBounds();
    const b = exact.pad(0.1);
    const step = niceStep((exact.getEast() - exact.getWest()) / map.getSize().x);
    const fmt = (v, hemi) => `${step >= 1 ? Math.abs(v).toFixed(0) : Math.abs(v).toFixed(step < 0.05 ? 2 : 1)}${hemi}`;
    const lineOpts = (major) => ({ pane: "grid", color: "#3dffa8", weight: 1, opacity: major ? 0.3 : 0.13, interactive: false });
    const label = (latlng, text, anchor) => L.marker(latlng, {
      pane: "grid", interactive: false, keyboard: false,
      icon: L.divIcon({ className: "", html: `<span class="glab">${text}</span>`, iconSize: [0, 0], iconAnchor: anchor })
    }).addTo(gridLayer);

    for (let i = Math.ceil(b.getSouth() / step); i * step <= b.getNorth(); i++) {
      const lat = +(i * step).toFixed(4);
      L.polyline([[lat, b.getWest()], [lat, b.getEast()]], lineOpts(i % 2 === 0)).addTo(gridLayer);
      if (lat >= exact.getSouth() && lat <= exact.getNorth()) {
        label([lat, exact.getWest() + (exact.getEast() - exact.getWest()) * 0.012], fmt(lat, lat >= 0 ? "N" : "S"), [0, 12]);
      }
    }
    for (let i = Math.ceil(b.getWest() / step); i * step <= b.getEast(); i++) {
      const lon = +(i * step).toFixed(4);
      L.polyline([[b.getSouth(), lon], [b.getNorth(), lon]], lineOpts(i % 2 === 0)).addTo(gridLayer);
      if (lon >= exact.getWest() && lon <= exact.getEast()) {
        label([exact.getNorth() - (exact.getNorth() - exact.getSouth()) * 0.02, lon], fmt(lon, lon >= 0 ? "E" : "W"), [-4, 0]);
      }
    }
  }

  // ----------------------------------------------------------------- render
  function visibleAircraft() {
    return state.aircraft.filter((a) => state.cats[a.cat] && !state.typesOff.has(tkey(a)));
  }

  function renderMap() {
    trackLayer.clearLayers();
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    const vis = visibleAircraft();

    if (state.heat) {
      const pts = [];
      for (const a of vis) for (const s of a.segs) for (const p of s.pts) pts.push([p[1], p[2], 0.5]);
      heatLayer = L.heatLayer(pts, {
        radius: 14, blur: 18, maxZoom: 10, minOpacity: 0.25,
        gradient: { 0.2: "#0b3d33", 0.5: "#1fbf86", 0.8: "#3dffa8", 1: "#eafff6" }
      }).addTo(map);
    }

    for (const a of vis) {
      const color = COLORS[a.cat];
      const sel = a.hex === state.selected;
      a.segs.forEach((s, i) => {
        const ll = s.pts.map((p) => [p[1], p[2]]);
        const isLast = i === a.segs.length - 1;
        const onClick = () => select(a.hex);
        if (isLast || sel) {
          L.polyline(ll, { color, weight: 7, opacity: 0.12, interactive: false }).addTo(trackLayer);
        }
        L.polyline(ll, isLast || sel
          ? { color, weight: sel ? 3 : 2.2, opacity: 0.95 }
          : { color, weight: 1.3, opacity: 0.4, dashArray: "3 5" }
        ).on("click", onClick).addTo(trackLayer);
      });

      const [, lat, lon] = a.lastPt;
      if (sel) {
        L.marker([lat, lon], {
          interactive: false, keyboard: false, zIndexOffset: -100,
          icon: L.divIcon({ className: "", html: '<div class="ret" style="--police:' + color + '"><i></i><i></i><i></i><i></i></div>', iconSize: [0, 0] })
        }).addTo(trackLayer);
      }
      L.marker([lat, lon], {
        keyboard: false, title: name(a),
        icon: L.divIcon({
          className: "",
          html: `<div class="mkw">${shapeSvg(a.cat)}<span class="lab" style="color:${color}">${esc(name(a))}<small>${esc(a.type || "")}</small></span></div>`,
          iconSize: [0, 0]
        })
      }).on("click", () => select(a.hex)).addTo(trackLayer);
    }
  }

  function renderPanels() {
    const vis = visibleAircraft();

    for (const cat of Object.keys(COLORS)) {
      $(`#n-${cat}`).textContent = pad(state.aircraft.filter((a) => a.cat === cat).length);
    }

    const list = $("#contacts");
    list.innerHTML = "";
    $("#contactCount").textContent = `${vis.length} SHOWN`;
    if (!vis.length) {
      list.innerHTML = '<li class="empty">Nothing matches the current filters.</li>';
    }
    [...vis].sort((a, b) => b.n - a.n).forEach((a) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "row" + (a.hex === state.selected ? " sel" : "");
      btn.innerHTML = `${shapeSvg(a.cat)}<span class="reg">${esc(name(a))}</span><span class="ty">${esc(a.type || "")}</span><span class="np">${a.n.toLocaleString("en-US")}</span>`;
      btn.addEventListener("click", () => { select(a.hex); focusAircraft(a); });
      li.appendChild(btn);
      list.appendChild(li);
    });

    const a = state.aircraft.find((x) => x.hex === state.selected);
    const rows = $("#selRows");
    if (!a) {
      $("#selReg").textContent = "NONE";
      $("#selDesc").textContent = "Pick a contact";
      $("#selCat").textContent = "";
      rows.innerHTML = "";
      return;
    }
    $("#selReg").textContent = name(a);
    $("#selDesc").textContent = a.desc || a.type || "";
    const catEl = $("#selCat");
    catEl.textContent = LABELS[a.cat];
    catEl.style.color = COLORS[a.cat];
    const kv = [
      ["HEX", a.hex.toUpperCase()],
      ["TYPE", a.type || "n/a"],
      ["OPERATOR", a.op || "n/a"],
      ["TRACE POINTS", a.n.toLocaleString("en-US")],
      ["DISTANCE", `${Math.round(a.km).toLocaleString("en-US")} km`],
      ["MAX ALT", a.maxAlt == null ? "n/a" : `${a.maxAlt.toLocaleString("en-US")} ft`],
      ["FIRST SEEN", stamp(a.first.date, a.first.t)],
      ["LAST SEEN", stamp(a.last.date, a.last.t)]
    ];
    rows.innerHTML = kv.map(([k, v]) => `<div class="kv"><span>${k}</span><span>${esc(v)}</span></div>`).join("");
  }

  function renderDrawer() {
    const win = $("#winChips");
    win.innerHTML = "";
    for (const w of state.windows) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = w.label;
      b.setAttribute("aria-pressed", String(w === state.win));
      b.addEventListener("click", () => setWindow(w));
      win.appendChild(b);
    }

    const counts = new Map();
    for (const a of state.aircraft) counts.set(tkey(a), (counts.get(tkey(a)) || 0) + 1);
    const types = Array.from(counts.keys()).sort((x, y) => counts.get(y) - counts.get(x) || x.localeCompare(y));
    const box = $("#typeChips");
    box.innerHTML = "";
    for (const t of types) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = t === "?" ? "N/A" : t;
      b.setAttribute("aria-pressed", String(!state.typesOff.has(t)));
      b.addEventListener("click", () => {
        if (state.typesOff.has(t)) state.typesOff.delete(t); else state.typesOff.add(t);
        render();
      });
      box.appendChild(b);
    }
    const on = types.filter((t) => !state.typesOff.has(t)).length;
    $("#drawerCount").textContent = types.length ? `${on}/${types.length}` : "";
  }

  function render() {
    // keep a valid selection
    const vis = visibleAircraft();
    if (!vis.some((a) => a.hex === state.selected)) {
      const pick = vis.find((a) => a.cat === "police") || vis[0];
      state.selected = pick ? pick.hex : null;
    }
    renderMap();
    renderPanels();
    renderDrawer();
  }

  function select(hex) {
    state.selected = hex;
    renderMap();
    renderPanels();
  }

  function focusAircraft(a) {
    const pts = [];
    for (const s of a.segs) for (const p of s.pts) pts.push([p[1], p[2]]);
    if (!pts.length) return;
    map.flyToBounds(L.latLngBounds(pts).pad(0.3), { maxZoom: 10, duration: 0.8 });
  }

  async function setWindow(w) {
    state.win = w;
    $("#winLabel").textContent = windowText(w);
    showMsg("");
    try {
      const docs = await Promise.all(w.dates.map(loadDay));
      if (state.win !== w) return;         // a newer click superseded this one
      state.aircraft = mergeDays(docs);
    } catch (err) {
      console.error(err);
      state.aircraft = [];
      showMsg("Could not load flight data for this window. " + err.message);
    }
    render();
  }

  // ------------------------------------------------------------------ wiring
  function wire() {
    $("#zin").addEventListener("click", () => map.zoomIn());
    $("#zout").addEventListener("click", () => map.zoomOut());
    $("#zhome").addEventListener("click", () => flyRegion("all"));
    const zr = () => { $("#zread").textContent = "z" + map.getZoom(); };
    map.on("zoomend", zr); zr();

    const flyRegion = (k) => map.flyTo(REGIONS[k].center, REGIONS[k].zoom, { duration: 0.8 });
    $$("#regions [data-region]").forEach((b) => b.addEventListener("click", () => flyRegion(b.dataset.region)));

    const gridBtn = $("#gridBtn"), heatBtn = $("#heatBtn");
    gridBtn.addEventListener("click", () => {
      state.grid = !state.grid;
      gridBtn.classList.toggle("on", state.grid);
      gridBtn.setAttribute("aria-pressed", String(state.grid));
      drawGrid();
    });
    heatBtn.addEventListener("click", () => {
      state.heat = !state.heat;
      heatBtn.classList.toggle("on", state.heat);
      heatBtn.setAttribute("aria-pressed", String(state.heat));
      renderMap();
    });

    $$("[data-cat]").forEach((cb) => cb.addEventListener("change", () => {
      state.cats[cb.dataset.cat] = cb.checked;
      render();
    }));
    $$("[data-shape]").forEach((el) => { el.innerHTML = shapeSvg(el.dataset.shape); });

    const dbtn = $("#drawerBtn"), dbody = $("#drawerBody");
    dbtn.addEventListener("click", () => {
      const open = dbtn.getAttribute("aria-expanded") !== "true";
      dbtn.setAttribute("aria-expanded", String(open));
      dbody.hidden = !open;
      $("#chev").setAttribute("d", open ? "M3 9l4-4 4 4" : "M3 5l4 4 4-4");
    });
    // clicks and drags on the overlays must not pan or zoom the map
    for (const el of $$(".drawer, .ctl")) {
      L.DomEvent.disableClickPropagation(el);
      L.DomEvent.disableScrollPropagation(el);
    }

    map.on("moveend zoomend", drawGrid);
  }

  // -------------------------------------------------------------------- boot
  async function boot() {
    drawRings();
    wire();
    drawGrid();
    let index;
    try {
      index = await getJSON(`${CFG.DATA_BASE}/index.json`);
    } catch (err) {
      console.error(err);
      showMsg("No data found yet. Run the \"Update data\" workflow, then point DATA_BASE in config.js at the data branch.");
      $("#winLabel").textContent = "NO DATA";
      return;
    }
    state.days = (index.days || []).slice().sort((a, b) => (a.date < b.date ? -1 : 1));
    if (!state.days.length) {
      showMsg("The data index is empty. Run the \"Update data\" workflow.");
      $("#winLabel").textContent = "NO DATA";
      return;
    }
    if (index.updated) $("#updated").textContent = "INDEX UPDATED " + index.updated.replace("T", " ").replace("Z", " UTC");
    state.windows = buildWindows(state.days.map((d) => d.date));
    await setWindow(state.windows[0]);
  }

  boot();
})();
