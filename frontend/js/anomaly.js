/* ================================================================
   DATA INTEGRATION
   ----------------------------------------------------------------
   Replace TM1_DATA with your FastAPI response, e.g.:
     const TM1_DATA = await fetch('/api/anomaly/scan').then(r => r.json());
   Schema:
     cubes[]: { id, name, layer, dims[], rules[], anomalies[] }
       layer       — data-flow layer (0 = source), used for auto layout
       anomalies[] — { location, measure, value, expected, deviation, rule, severity:'high'|'med'|'low' }
       rules[]     — { name, expression, threshold, desc }
     edges[]:  { from, to, type:'rule'|'feeder'|'ti' }
     processLinks[]: { process, cube, role:'source'|'target', datasource_type, object, snippet }
   The red badge number = cube.anomalies.length; a cube with 0 anomalies
   shows a green check instead.
   ================================================================ */
/* ================================================================
   DATA INTEGRATION — live backend
   ----------------------------------------------------------------
   This page is served from the same origin as the API, so all calls
   use relative URLs.

   Cubes + their rules come from the schema-backed anomaly API:
     GET  /api/anomaly/cubes          -> { cubes: [{ cube, dimensions, rules }, ...] }
   Anomalies are produced by Run Scan:
     POST /api/anomaly/scan/{cube}    -> ScanResponse (Phase 3; STUB today)

   Cube-to-cube relationships are extracted during schema sync from
   rules, feeders, and TI process code when those artefacts are readable.
   ================================================================ */
const ANOMALY_API = ""; // same origin
let TM1_DATA = { cubes: [], edges: [], processLinks: [] };

const SEVERITY_LABEL = v => (v >= 66 ? "high" : v >= 33 ? "med" : "low");

/* Backend RuleSet -> the node drawer's rules[] display shape. */
function rulesFromRuleSet(rs) {
  const out = [];
  (rs.integrity || []).forEach((r, idx) => {
    if (r.enabled === false) return;
    out.push({
      name: r.name,
      expression: (r.columns && r.columns.length)
        ? `columns: ${r.columns.join(", ")}`
        : "built-in deterministic check",
      threshold: "integrity",
      desc: `Built-in integrity rule "${r.name}".`,
      type: "integrity",
      srcIndex: idx,
    });
  });
  (rs.variance || []).forEach((r, idx) => {
    if (r.enabled === false) return;
    const pct = Math.round((r.rel_pct || 0) * 100);
    const ref = r.reference_label || r.reference || "reference";
    out.push({
      name: r.name,
      expression: `ABS( ${r.measure} - ${ref} ) > ${pct}% AND > ${r.abs_value}`,
      threshold: `${pct}% / ${r.abs_value}`,
      desc: r.description || `Variance of ${r.measure} vs ${ref}.`,
      type: "variance",
      srcIndex: idx,
    });
  });
  return out;
}

/* Best-effort dim list (no cube-schema endpoint here) from the rule set. */
function dimsFromRuleSet(rs) {
  const dims = new Set();
  (rs.variance || []).forEach(r => (r.group_by || []).forEach(d => dims.add(d)));
  (rs.integrity || []).forEach(r => (r.group_by || []).forEach(d => dims.add(d)));
  return [...dims];
}

/* Heuristic Flag -> anomaly-card shape used by the drawer. */
function mapFlag(f) {
  const key = f.key || {};
  const loc = Object.entries(key).map(([, v]) => `[${v}]`).join(".")
    || f.dedupe_id || f.rule || "-";
  const detail = f.detail || {};
  const sev = typeof f.severity === "number"
    ? SEVERITY_LABEL(f.severity)
    : (f.severity || "low");
  return {
    location: loc,
    measure: f.measure || Object.keys(detail)[0] || f.rule || "",
    value: f.value != null ? f.value : (Object.values(detail)[0] ?? ""),
    expected: f.expected || "",
    deviation: f.deviation || "",
    rule: f.rule || "",
    severity: sev,
  };
}

function htmlAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function loadData() {
  let cubes = [];
  try {
    const res = await fetch(`${ANOMALY_API}/api/anomaly/cubes`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cubes = data.cubes || [];
    TM1_DATA.edges = (data.relationships || []).map(rel => ({
      from: rel.from,
      to: rel.to,
      type: rel.type === "process" ? "ti" : rel.type,
      source: rel.source || "",
      snippet: rel.snippet || "",
    }));
    TM1_DATA.processLinks = (data.process_links || []).map(link => ({
      process: link.process || "",
      cube: link.cube || "",
      role: link.role || "",
      datasource_type: link.datasource_type || "",
      object: link.object || "",
      snippet: link.snippet || "",
    }));
  } catch (err) {
    console.warn("[anomaly] /api/anomaly/cubes failed, falling back to /api/views", err);
    TM1_DATA.processLinks = [];
    const res = await fetch(`${ANOMALY_API}/api/views`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cubes = (data.cubes || []).map(c => ({
      cube: c.cube,
      description: c.description || c.cube,
      dimensions: [],
      rules: { cube: c.cube, integrity: [], variance: [] },
    }));
  }
  const cubesPerLayer = 4;
  const processLinksByCube = TM1_DATA.processLinks.reduce((acc, link) => {
    if (link.cube) (acc[link.cube] ??= []).push(link);
    return acc;
  }, {});
  TM1_DATA.cubes = cubes.map((item, i) => {
    const cubeName = item.cube || item.name || `Cube ${i + 1}`;
    const rs = item.rules || { cube: cubeName, integrity: [], variance: [] };
    const dims = Array.isArray(item.dimensions)
      ? item.dimensions.map(d => d.name).filter(Boolean)
      : dimsFromRuleSet(rs);
    return {
      id: cubeName,
      name: cubeName,
      description: item.description || cubeName,
      layer: Math.floor(i / cubesPerLayer),
      dims,
      dimensions: item.dimensions || [],
      processLinks: processLinksByCube[cubeName] || [],
      rawRuleset: rs,
      rules: rulesFromRuleSet(rs),
      anomalies: [],
    };
  });
  TM1_DATA.edges = TM1_DATA.edges.filter(e =>
    TM1_DATA.cubes.some(c => c.id === e.from) &&
    TM1_DATA.cubes.some(c => c.id === e.to)
  );
}

/* Run the configured scan for each target cube. The scan endpoint is a
   Phase-3 stub today, so this tolerates {stub:true} and empty results. */
async function fetchScans(targets) {
  let stub = false;
  await Promise.all(targets.map(async c => {
    c.anomalies = [];
    try {
      const res = await fetch(`${ANOMALY_API}/api/anomaly/scan/${encodeURIComponent(c.id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ period: getScanPeriodLabel() }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.stub) { stub = true; return; }
      const flags = data.flags || (data.result && data.result.flags) || [];
      c.anomalies = flags.map(mapFlag);
    } catch {
      /* leave this cube with no anomalies */
    }
  }));
  return { stub };
}


/* ---------- layout ---------- */
const BASE_COL_GAP = 268, NODE_W = 196, NODE_H = 96, ROW_GAP = 130, PAD_X = 70, PAD_Y = 60;
const MIN_COL_GAP = NODE_W + 82; // prevents cards from overlapping on narrow screens
let cubeById = {};
let layers = {};
let maxLayer = 0, maxRows = 1, midY = 0, mapH = 0, canvasH = 0;
const canvas = document.getElementById("canvas");
const scanSummary = document.getElementById("scanSummary");
const pos = {};
let canvasW = 0;
const SUMMARY_GAP = 34, SUMMARY_H = 132;
const SCAN_PERIOD = { year: "2026", month: "Mar" }; // TODO: wire to a real period selector / API.
const ZOOM_MIN = 0.45, ZOOM_MAX = 1.6, ZOOM_STEP = 0.1;
let mapZoom = 1;

/* Recompute every data-derived layout constant. Run after data loads. */
function recomputeLayoutConstants() {
  cubeById = Object.fromEntries(TM1_DATA.cubes.map(c => [c.id, c]));
  layers = {};
  TM1_DATA.cubes.forEach(c => { (layers[c.layer] ??= []).push(c); });
  const layerKeys = Object.keys(layers).map(Number);
  maxLayer = layerKeys.length ? Math.max(...layerKeys) : 0;
  const counts = Object.values(layers).map(a => a.length);
  maxRows  = counts.length ? Math.max(...counts) : 1;
  midY     = PAD_Y + ((maxRows - 1) * ROW_GAP) / 2 + NODE_H / 2;
  mapH     = PAD_Y * 2 + (maxRows - 1) * ROW_GAP + NODE_H;
  canvasH  = mapH + SUMMARY_GAP + SUMMARY_H;
}

function calculateCanvasWidth() {
  const viewportW = canvas.parentElement?.clientWidth || window.innerWidth;
  const minW = PAD_X * 2 + maxLayer * MIN_COL_GAP + NODE_W;
  return Math.max(viewportW, minW);
}

function computeLayout() {
  const targetW = calculateCanvasWidth();
  const colGap = maxLayer > 0
    ? Math.max(MIN_COL_GAP, (targetW - PAD_X * 2 - NODE_W) / maxLayer)
    : BASE_COL_GAP;

  canvasW = PAD_X * 2 + maxLayer * colGap + NODE_W;

  Object.entries(layers).forEach(([L, arr]) => {
    const n = arr.length;
    arr.forEach((c, i) => {
      const x = PAD_X + Number(L) * colGap;
      const y = midY - ((n - 1) * ROW_GAP) / 2 + i * ROW_GAP - NODE_H / 2;
      pos[c.id] = { x, y, cx: x + NODE_W / 2, cy: y + NODE_H / 2 };
    });
  });

  applyCanvasZoom();
  scanSummary.style.top = (mapH + SUMMARY_GAP) + "px";
}

function applyCanvasZoom() {
  canvas.style.width  = `${canvasW * mapZoom}px`;
  canvas.style.height = `${canvasH * mapZoom}px`;
  canvas.style.transform = `scale(${mapZoom})`;
  canvas.style.transformOrigin = "top left";
  document.getElementById("zoomLabel").textContent = `${Math.round(mapZoom * 100)}%`;
}

function setMapZoom(value) {
  mapZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Number(value) || 1));
  applyCanvasZoom();
}

const CUBE_ICON = `<svg viewBox="0 0 24 24" fill="none" class="w-[19px] h-[19px] flex-shrink-0">
  <path d="M12 2 21 7v10l-9 5-9-5V7z" stroke="#5a6679" stroke-width="1.3"/>
  <path d="M12 2 21 7l-9 5-9-5z" fill="#0d9488" fill-opacity=".16"/>
  <path d="M12 12v10M3 7l9 5 9-5" stroke="#5a6679" stroke-width="1.3"/></svg>`;
const CLEAN_ICON = `<svg viewBox="0 0 24 24" fill="none" class="clean absolute -top-[9px] -right-[9px] w-6 h-6">
  <circle cx="12" cy="12" r="11" fill="#d9f3ef"/>
  <circle cx="12" cy="12" r="11" stroke="#0d9488" stroke-width="1.2"/>
  <path d="m8 12 3 3 5-6" stroke="#0d9488" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const TICK_ICON  = `<svg viewBox="0 0 24 24" fill="none" class="w-[14px] h-[14px]"><path d="m6 12 4 4 8-9" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

/* ---------- edges ---------- */
const svg = document.getElementById("edges");
const edgeEls = [];
function buildEdges() {
  TM1_DATA.edges.forEach(e => {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", `edge ${e.type}`);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = [e.source, e.snippet].filter(Boolean).join(" — ") || e.type;
    path.appendChild(title);
    svg.appendChild(path);
    edgeEls.push({ el: path, from: e.from, to: e.to });
  });
}

function updateSvgAndEdges() {
  svg.setAttribute("viewBox", `0 0 ${canvasW} ${canvasH}`);
  svg.setAttribute("width",  canvasW);
  svg.setAttribute("height", canvasH);
  edgeEls.forEach(({ el, from, to }) => {
    const a = pos[from], b = pos[to];
    const x1 = a.x + NODE_W, y1 = a.cy, x2 = b.x, y2 = b.cy, mx = (x1 + x2) / 2;
    el.setAttribute("d", `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`);
  });
}

/* ---------- nodes ---------- */
const nodeEls = {};
function buildNodes() {
TM1_DATA.cubes.forEach(c => {
  const p = pos[c.id];
  const el = document.createElement("div");
  const processLinks = c.processLinks || [];
  const processTitle = processLinks
    .map(link => `${link.role}: ${link.process}${link.object ? ` (${link.object})` : ""}`)
    .join("\n");
  const tiBadge = processLinks.length
    ? `<span title="${htmlAttr(processTitle)}" class="text-[9.5px] text-[#6d28d9] bg-[#f1ecff] border border-[#d8cafe] rounded-[5px] px-1.5 py-px">${processLinks.length} TI</span>`
    : "";
  el.className = "node absolute w-[196px] cursor-pointer select-none";
  el.style.left = p.x + "px";
  el.style.top  = p.y + "px";
  el.dataset.id = c.id;
  el.innerHTML = `
    <div class="pick absolute -top-2 -left-2 w-[23px] h-[23px] rounded-[7px] bg-white border-[1.5px] border-line shadow-[0_2px_6px_-1px_rgba(27,36,51,.25)] flex items-center justify-center cursor-pointer z-[9]" data-pick>${TICK_ICON}</div>
    <div class="card relative rounded-[14px] border border-line px-[14px] pt-[13px] pb-3 transition-[border-color,box-shadow] duration-200"
         style="background: linear-gradient(165deg,#ffffff,#fbfcfe); box-shadow: 5px 7px 0 -3px rgba(27,36,51,.06), 0 14px 28px -16px rgba(27,36,51,.35);">
      ${CLEAN_ICON}
      <div class="flex items-center gap-2 mb-[9px]">${CUBE_ICON}<span class="font-disp font-bold text-[14px] tracking-[-.2px] leading-tight text-ink">${c.name}</span></div>
      <div class="flex flex-wrap gap-1">
        <span class="text-[9.5px] text-ink-dim bg-[#f0f3f7] border border-line-soft rounded-[5px] px-1.5 py-px">${c.dims.length} dims</span>
        <span class="text-[9.5px] text-ink-dim bg-[#f0f3f7] border border-line-soft rounded-[5px] px-1.5 py-px">${c.rules.length} rules</span>
        ${tiBadge}
      </div>
    </div>
    <div class="badge absolute -top-[9px] -right-[9px] min-w-6 h-6 px-1.5 bg-badge text-white font-disp font-extrabold text-[12.5px] leading-6 text-center rounded-xl shadow-[0_0_0_2.5px_#eef1f5,0_4px_12px_-2px_rgba(255,59,48,.6)] z-[8] pointer-events-none" data-badge></div>`;
  canvas.appendChild(el);
  nodeEls[c.id] = el;

  el.querySelector("[data-pick]").addEventListener("click", ev => { ev.stopPropagation(); toggleSelect(c.id); });
  el.addEventListener("click", () => openDrawer(c.id));
  el.addEventListener("mouseenter", () => highlightPath(c.id, true));
  el.addEventListener("mouseleave", () => highlightPath(c.id, false));
});
}

function applyNodePositions() {
  Object.entries(nodeEls).forEach(([id, el]) => {
    el.style.left = pos[id].x + "px";
    el.style.top  = pos[id].y + "px";
  });
}

function applyResponsiveLayout() {
  computeLayout();
  applyNodePositions();
  updateSvgAndEdges();
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(applyResponsiveLayout, 80);
});

document.getElementById("zoomOutBtn")?.addEventListener("click", () => setMapZoom(mapZoom - ZOOM_STEP));
document.getElementById("zoomInBtn")?.addEventListener("click", () => setMapZoom(mapZoom + ZOOM_STEP));
document.getElementById("zoomResetBtn")?.addEventListener("click", () => setMapZoom(1));

/* ---------- selection ---------- */
const selected = new Set();
function toggleSelect(id) {
  if (selected.has(id)) selected.delete(id); else selected.add(id);
  nodeEls[id].classList.toggle("selected", selected.has(id));
  updateModeLabel();
  if (scanned) resetScores();
}
function updateModeLabel() {
  const m = document.getElementById("modeM"), s = document.getElementById("modeSub");
  if (selectMode) {
    m.textContent = "Selected cubes";
    s.textContent = selected.size ? `${selected.size} cube${selected.size === 1 ? "" : "s"} selected` : "Tick a cube to include it";
  } else {
    m.textContent = "All cubes";
    s.textContent = "Scanning all cubes";
  }
}

/* ---------- mode switch ---------- */
let selectMode = false;
const modeSwitch = document.getElementById("modeSwitch");
modeSwitch.addEventListener("click", () => {
  selectMode = !selectMode;
  modeSwitch.classList.toggle("on", selectMode);
  modeSwitch.setAttribute("aria-checked", selectMode);
  canvas.classList.toggle("select-mode", selectMode);
  resetScores();
  updateModeLabel();
  statusEl.innerHTML = selectMode
    ? 'Tick cubes to scan, then click <b class="text-ink font-semibold">Run Scan</b>'
    : 'Ready — click <b class="text-ink font-semibold">Run Scan</b> to start';
});

/* ---------- hover path highlight ---------- */
function highlightPath(id, on) {
  if (drawerOpen) return;
  const connected = new Set([id]);
  edgeEls.forEach(({ el, from, to }) => {
    const hit = from === id || to === id;
    el.classList.toggle("lit", on && hit);
    el.classList.toggle("dim", on && !hit);
    if (hit) { connected.add(from); connected.add(to); }
  });
  if (!selectMode) Object.entries(nodeEls).forEach(([nid, el]) => el.classList.toggle("dim", on && !connected.has(nid)));
}

/* ---------- scan ---------- */
const scanBtn   = document.getElementById("scanBtn");
const scanLabel = document.getElementById("scanLabel");
const statusEl  = document.getElementById("status");
const totalEl   = document.getElementById("totalN");
const sweep     = document.getElementById("sweep");
let scanned = false, drawerOpen = false;

scanBtn.addEventListener("click", openConfirmModal);

const confirmModal = document.getElementById("confirmModal");
const confirmCard = document.getElementById("confirmCard");
const confirmPeriod = document.getElementById("confirmPeriod");
const confirmScanBtn = document.getElementById("confirmScanBtn");
const cancelScanBtn = document.getElementById("cancelScanBtn");

function getScanPeriodLabel() {
  return `${SCAN_PERIOD.year}-${SCAN_PERIOD.month}`;
}

function openConfirmModal() {
  confirmPeriod.textContent = getScanPeriodLabel();
  confirmModal.classList.remove("opacity-0", "pointer-events-none");
  confirmModal.classList.add("opacity-100");
  confirmCard.classList.remove("scale-95");
  confirmCard.classList.add("scale-100");
}

function closeConfirmModal() {
  confirmModal.classList.add("opacity-0", "pointer-events-none");
  confirmModal.classList.remove("opacity-100");
  confirmCard.classList.add("scale-95");
  confirmCard.classList.remove("scale-100");
}

confirmScanBtn.addEventListener("click", () => { closeConfirmModal(); runScan(); });
cancelScanBtn.addEventListener("click", closeConfirmModal);
confirmModal.addEventListener("click", e => { if (e.target === confirmModal) closeConfirmModal(); });

function resetScores() {
  scanned = false;
  totalEl.textContent = "—";
  scanSummary.classList.add("hidden");
  Object.values(nodeEls).forEach(el => {
    el.classList.remove("scored", "zero");
    const b = el.querySelector("[data-badge]");
    b.classList.remove("show", "pulse"); b.textContent = "";
  });
}

async function runScan() {
  const targets = selectMode ? TM1_DATA.cubes.filter(c => selected.has(c.id)) : TM1_DATA.cubes;
  if (selectMode && targets.length === 0) {
    statusEl.innerHTML = '⚠ Please select at least one cube';
    return;
  }
  scanBtn.disabled = true;
  scanBtn.classList.add("scanning");
  scanLabel.textContent = "Scanning…";
  statusEl.innerHTML = `Running rules across <b class="text-ink font-semibold">${targets.length}</b> cube${targets.length === 1 ? "" : "s"}…`;
  resetScores();
  totalEl.textContent = "0";

  // Pull live results from the backend before animating, so badges are real.
  const { stub } = await fetchScans(targets);

  sweep.classList.remove("go"); void sweep.offsetWidth; sweep.classList.add("go");

  const ordered = [...targets].sort((a, b) => a.layer - b.layer);
  let runningTotal = 0, cubesWithAnom = 0;

  ordered.forEach((c, i) => {
    setTimeout(() => {
      const el = nodeEls[c.id];
      el.classList.add("scan");
      setTimeout(() => el.classList.remove("scan"), 540);
      el.classList.add("scored");
      const count = c.anomalies.length;
      if (count === 0) {
        el.classList.add("zero");
      } else {
        cubesWithAnom++; runningTotal += count;
        const b = el.querySelector("[data-badge]");
        if (c.anomalies.some(a => a.severity === "high")) b.classList.add("pulse");
        b.textContent = count > 99 ? "99+" : count;
        b.classList.add("show");
        countUp(totalEl, runningTotal);
      }
    }, 900 + i * 180);
  });

  setTimeout(() => {
    scanBtn.disabled = false;
    scanBtn.classList.remove("scanning");
    scanLabel.textContent = "Re-scan";
    scanned = true;
    statusEl.innerHTML = stub
      ? `Scan endpoint is a stub (backend Phase 3) · 0 anomalies across ${targets.length} cube${targets.length === 1 ? "" : "s"}`
      : `Scan complete · <b class="text-ink font-semibold">${runningTotal}</b> anomalies in <b class="text-ink font-semibold">${cubesWithAnom}</b> / ${targets.length} cube${targets.length === 1 ? "" : "s"}`;
    renderScanSummary(targets, runningTotal, cubesWithAnom);
    if (typeof saveDetectionHistory === "function") {
      saveDetectionHistory({
        period: getScanPeriodLabel(),
        total: runningTotal,
        cubesScanned: targets.length,
        cubesWithAnomalies: cubesWithAnom,
        high: targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "high").length, 0),
        medium: targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "med").length, 0),
        low: targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "low").length, 0),
        stub,
      });
    }
  }, 900 + ordered.length * 180 + 200);
}

function renderScanSummary(targets, runningTotal, cubesWithAnom) {
  const highCount = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "high").length, 0);
  const medCount = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "med").length, 0);
  const lowCount = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "low").length, 0);

  document.getElementById("summaryTitle").textContent = `Period ${getScanPeriodLabel()} · ${runningTotal} anomal${runningTotal === 1 ? "y" : "ies"} detected`;
  document.getElementById("summarySub").textContent = `Scanned ${targets.length} cube${targets.length === 1 ? "" : "s"}. ${cubesWithAnom} cube${cubesWithAnom === 1 ? "" : "s"} contain anomalies · severity split: ${highCount} high, ${medCount} medium, ${lowCount} low.`;
  document.getElementById("summaryTotal").textContent = runningTotal;
  document.getElementById("summaryCubes").textContent = `${cubesWithAnom}/${targets.length}`;
  document.getElementById("summaryHigh").textContent = highCount;
  document.getElementById("summaryByCube").innerHTML = targets.map(c => {
    const count = c.anomalies.length;
    const high = c.anomalies.filter(a => a.severity === "high").length;
    const badgeClass = count === 0 ? "text-accent bg-accent-50 border-accent/20" : high > 0 ? "text-hi bg-hi/10 border-hi/30" : "text-med bg-med/10 border-med/30";
    return `<button class="${badgeClass} rounded-full border px-3 py-1.5 text-[10.5px] font-semibold tracking-[.2px] hover:-translate-y-px transition" onclick="openDrawer('${c.id}')">${c.name}: ${count}</button>`;
  }).join("");
  scanSummary.classList.remove("hidden");
}

function countUp(el, to) {
  const from = parseInt(el.textContent) || 0;
  const steps = Math.min(to - from, 8); let cur = from, i = 0;
  if (steps <= 0) { el.textContent = to; return; }
  const inc = (to - from) / steps;
  const t = setInterval(() => { cur += inc; i++; el.textContent = Math.round(cur); if (i >= steps) { el.textContent = to; clearInterval(t); } }, 30);
}

/* ---------- drawer ---------- */
const drawer   = document.getElementById("drawer");
const backdrop = document.getElementById("backdrop");
const drName   = document.getElementById("drName");
const drDims   = document.getElementById("drDims");
const drBody   = document.getElementById("drBody");
const ctAnom   = document.getElementById("ctAnom");
const ctRules  = document.getElementById("ctRules");
let curCube = null, curTab = "anom";
let _rulesEditMode = false, _editingRuleIdx = -1, _rulesetSnapshot = null;
let _refElements = [], _refLoaded = false;
const STD_REF = new Set(["budget", "forecast", "prior_year", "prior_week"]);

function openDrawer(id) {
  const c = cubeById[id]; curCube = c; drawerOpen = true;
  _rulesEditMode = false; _editingRuleIdx = -1; _rulesetSnapshot = null;
  _refElements = []; _refLoaded = false;
  edgeEls.forEach(({ el }) => el.classList.remove("lit", "dim"));
  if (!selectMode) Object.entries(nodeEls).forEach(([nid, el]) => { el.classList.toggle("active", nid === id); el.classList.toggle("dim", nid !== id); });
  else nodeEls[id].classList.add("active");
  drName.textContent = c.name;
  drDims.innerHTML   = c.dims.map(d => `<span class="text-[10px] text-ink-dim bg-[#f0f3f7] border border-line-soft rounded-[5px] px-1.5 py-0.5">${d}</span>`).join("");
  ctAnom.textContent  = `(${c.anomalies.length})`;
  ctRules.textContent = `(${c.rules.length})`;
  setTab(scanned ? "anom" : "rules");
  drawer.classList.add("open"); backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  drawerOpen = false;
  _rulesEditMode = false; _editingRuleIdx = -1; _rulesetSnapshot = null;
  drawer.classList.remove("open"); backdrop.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  edgeEls.forEach(({ el }) => el.classList.remove("lit", "dim"));
  Object.values(nodeEls).forEach(el => el.classList.remove("active", "dim"));
}

function setTab(tab) {
  curTab = tab;
  document.querySelectorAll(".tab").forEach(t => {
    const active = t.dataset.tab === tab;
    t.classList.toggle("text-ink", active);
    t.classList.toggle("text-ink-dim", !active);
    t.classList.toggle("border-accent", active);
    t.classList.toggle("border-transparent", !active);
    const span = t.querySelector("span");
    if (span) {
      span.classList.toggle("text-accent", active);
      span.classList.toggle("text-ink-faint", !active);
    }
  });
  renderBody();
}

function renderBody() {
  const c = curCube;
  if (curTab === "anom") {
    if (!scanned) {
      drBody.innerHTML = `<div class="text-ink-faint text-center px-2.5 py-10 text-[12px] leading-relaxed"><span class="text-[34px] block mb-2.5">◍</span>No scan yet. Click <b class="text-ink">Run Scan</b> in the top-left to populate anomaly details here.</div>`;
      return;
    }
    if (c.anomalies.length === 0) {
      drBody.innerHTML = `<div class="text-ink-faint text-center px-2.5 py-10 text-[12px] leading-relaxed"><span class="text-[34px] block mb-2.5">✓</span>No anomalies detected in this cube.</div>`;
      return;
    }
    const order = { high: 0, med: 1, low: 2 };
    drBody.innerHTML = [...c.anomalies].sort((a, b) => order[a.severity] - order[b.severity]).map(a => `
      <div class="border border-line-soft rounded-[10px] px-3 py-[11px] mb-[9px] bg-white hover:border-line transition-colors">
        <div class="flex items-center gap-2 mb-2">
          <span class="${sevPill(a.severity)}">${a.severity}</span>
          <span class="text-accent text-[11.5px] break-all flex-1">${a.location}</span>
        </div>
        <div class="grid grid-cols-3 gap-2 text-[11px]">
          <div><span class="text-ink-faint text-[9.5px] uppercase tracking-[.4px] block mb-0.5">value</span><span class="text-hi font-semibold">${a.value}</span></div>
          <div><span class="text-ink-faint text-[9.5px] uppercase tracking-[.4px] block mb-0.5">expected</span><span class="text-ink">${a.expected}</span></div>
          <div><span class="text-ink-faint text-[9.5px] uppercase tracking-[.4px] block mb-0.5">deviation</span><span class="text-ink">${a.deviation}</span></div>
        </div>
        <div class="mt-[9px] text-[10.5px] text-ink-dim">Triggered rule · <b class="text-ink font-semibold">${a.rule}</b></div>
      </div>`).join("");
  } else {
    const ruleCards = c.rules.length === 0
      ? `<div class="text-ink-faint text-center py-10 text-[12px]">No rules configured.</div>`
      : c.rules.map((r, i) => {
          // Inline edit form for the selected variance rule
          if (_editingRuleIdx === i && r.type === "variance") {
            const raw = curCube.rawRuleset.variance[r.srcIndex] || {};
            const refVal = STD_REF.has(raw.reference) ? raw.reference : (raw.reference_label || raw.reference || "budget");
            return `
              <div class="border-2 border-accent rounded-[10px] p-[14px] mb-2.5 bg-white">
                <div class="text-[11.5px] font-semibold text-ink mb-3">Edit Rule</div>
                <div class="flex flex-col gap-[10px]">
                  ${_ruleFormFields({ name: raw.name || "", desc: raw.description || "", measure: raw.measure || "", refVal, pct: Math.round((raw.rel_pct || 0) * 100), abs: raw.abs_value ?? 5000, idPrefix: "re" })}
                </div>
                <div class="flex gap-2 mt-4">
                  <button id="btnDiscardEdit" class="flex-1 text-[12px] text-ink-dim border border-line rounded-lg py-[7px] bg-white hover:border-line-soft transition cursor-pointer">Discard</button>
                  <button id="btnUpdateRule" data-idx="${i}" class="flex-1 text-[12px] text-white bg-accent border border-accent rounded-lg py-[7px] hover:opacity-90 transition font-semibold cursor-pointer">Update</button>
                </div>
              </div>`;
          }
          // Normal display card — variance rules are clickable to open inline edit
          const clickable = r.type === "variance";
          return `
            <div class="relative border border-line-soft border-l-[3px] border-l-accent rounded-[10px] px-[13px] py-3 mb-2.5 bg-white${clickable ? " cursor-pointer hover:border-accent/40 transition-colors" : ""}"
                 ${clickable ? `data-open-edit="${i}"` : ""}>
              ${_rulesEditMode ? `<button data-del="${i}" class="del-rule-btn absolute top-[9px] right-[9px] w-[20px] h-[20px] rounded-full bg-[#f0f3f7] hover:bg-red-100 text-[#8898aa] hover:text-red-500 text-[14px] leading-none flex items-center justify-center transition-colors cursor-pointer border-0 p-0">×</button>` : ""}
              <h4 class="font-disp font-bold text-[13.5px] m-0 mb-1 ${_rulesEditMode ? "pr-7" : ""}">${htmlAttr(r.name)}</h4>
              <div class="text-ink-dim text-[11px] leading-relaxed mb-2.5">${htmlAttr(r.desc)}</div>
              <pre class="m-0 bg-[#f4f6f9] border border-line-soft rounded-lg px-[11px] py-[9px] overflow-x-auto text-[11px] text-[#3a465c] leading-relaxed">${highlightExpr(r.expression)}</pre>
              <div class="flex gap-3.5 mt-[9px] text-[10px] text-ink-faint"><span><b class="text-ink-dim">threshold:</b> ${htmlAttr(r.threshold)}</span></div>
            </div>`;
        }).join("");

    const addSection = _rulesEditMode && _editingRuleIdx < 0 ? `
      <button id="btnAddRule" class="w-full mt-1 py-[10px] border-2 border-dashed border-line rounded-[10px] text-[12px] text-ink-faint hover:border-accent hover:text-accent transition-colors cursor-pointer bg-transparent">+ Add Rule</button>
      <div id="addRuleForm" class="hidden mt-2 border border-line-soft rounded-[10px] p-[16px] bg-white">
        <div class="text-[11.5px] font-semibold text-ink mb-3">New Variance Rule</div>
        <div class="flex flex-col gap-[10px]">
          ${_ruleFormFields({ idPrefix: "rn" })}
        </div>
        <div class="flex gap-2 mt-4">
          <button id="btnCancelAdd" class="flex-1 text-[12px] text-ink-dim border border-line rounded-lg py-[7px] bg-white hover:border-line-soft transition cursor-pointer">Cancel</button>
          <button id="btnConfirmAdd" class="flex-1 text-[12px] text-white bg-accent border border-accent rounded-lg py-[7px] hover:opacity-90 transition font-semibold cursor-pointer">Add</button>
        </div>
      </div>` : "";

    drBody.innerHTML = `
      <div class="flex items-center justify-between mb-3 px-0.5">
        <span class="text-ink-faint text-[11px]">${c.rules.length} rule${c.rules.length !== 1 ? "s" : ""}</span>
        ${_rulesEditMode
          ? `<div class="flex gap-[7px]">
               <button id="btnCancelEdit" class="text-[11.5px] text-ink-dim border border-line rounded-lg px-3 py-[5px] bg-white hover:border-line-soft transition cursor-pointer">Cancel</button>
               <button id="btnSaveRules" class="text-[11.5px] text-white bg-accent border border-accent rounded-lg px-3 py-[5px] hover:opacity-90 transition cursor-pointer font-semibold">Save</button>
             </div>`
          : `<button id="btnEditRules" class="text-[11.5px] text-ink-dim border border-line rounded-lg px-3 py-[5px] bg-white hover:border-accent hover:text-accent transition cursor-pointer">Edit</button>`
        }
      </div>
      ${ruleCards}
      ${addSection}`;
  }
}

function sevPill(sev) {
  const m = {
    high: "bg-hi/10 text-hi border-hi/30",
    med:  "bg-med/10 text-med border-med/30",
    low:  "bg-lo/10 text-lo border-lo/30",
  };
  return `${m[sev]} text-[9.5px] font-semibold px-[7px] py-0.5 rounded-full tracking-[.4px] uppercase border`;
}

function highlightExpr(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\b(AND|OR|NOT|BETWEEN|OVER|FLAG|DISTINCT)\b/g, '<span class="kw">$1</span>')
    .replace(/\b(SUM|COUNT|ABS|AVG12|STDEV12|MOD|feeder)\b/g, '<span class="fn">$1</span>')
    .replace(/(\d+(?:\.\d+)?%?|3σ)/g, '<span class="num">$1</span>');
}

// ── Rules edit/save/delete/add ─────────────────────────────────────────────

function _syncRules() {
  curCube.rules = rulesFromRuleSet(curCube.rawRuleset);
  ctRules.textContent = `(${curCube.rules.length})`;
}

function _snapshotIfNeeded() {
  if (!_rulesetSnapshot) _rulesetSnapshot = JSON.parse(JSON.stringify(curCube.rawRuleset));
}

// Fetch Scenario/Version elements for the current cube and update selects in-place.
async function _fetchRefElements() {
  if (_refLoaded) return;
  try {
    const res = await fetch(`${ANOMALY_API}/api/anomaly/rules/${encodeURIComponent(curCube.id)}/reference-elements`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _refElements = data.dims || [];
  } catch { _refElements = []; }
  _refLoaded = true;
  // Update all ref selects already in the DOM without a full re-render
  document.querySelectorAll(".ref-select").forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = _buildRefOptions(cur);
    sel.value = cur;
  });
}

function _buildRefOptions(currentVal = "budget") {
  const stdOpts = [
    ["budget", "Budget"], ["forecast", "Forecast"],
    ["prior_year", "Prior Year"], ["prior_week", "Prior Week"],
  ];
  const o = (v, lbl) => `<option value="${htmlAttr(v)}"${v === currentVal ? " selected" : ""}>${htmlAttr(lbl)}</option>`;
  let html = "";
  if (_refLoaded && _refElements.length) {
    _refElements.forEach(({ dim, elements }) => {
      html += `<optgroup label="${htmlAttr(dim)}">`;
      elements.forEach(e => { html += o(e, e); });
      html += "</optgroup>";
    });
    html += `<optgroup label="Standard">`;
    stdOpts.forEach(([v, l]) => { html += o(v, l); });
    html += "</optgroup>";
  } else {
    if (!_refLoaded) html += `<option value="" disabled>Loading…</option>`;
    stdOpts.forEach(([v, l]) => { html += o(v, l); });
  }
  return html;
}

function _ruleFormFields({ name = "", desc = "", measure = "", refVal = "budget", pct = 10, abs = 5000, idPrefix = "rn" } = {}) {
  const inp = (id, type, val, placeholder, extra = "") =>
    `<input id="${idPrefix}${id}" type="${type}" value="${htmlAttr(String(val))}" placeholder="${placeholder}" class="w-full border border-line-soft rounded-lg px-3 py-[7px] text-[12px] outline-none focus:border-accent" ${extra}/>`;
  return `
    <div>
      <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Name *</label>
      ${inp("Name", "text", name, "e.g. Salary Variance")}
    </div>
    <div>
      <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Description</label>
      ${inp("Desc", "text", desc, "Optional description")}
    </div>
    <div>
      <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Measure *</label>
      ${inp("Measure", "text", measure, "e.g. Salary")}
    </div>
    <div class="grid grid-cols-2 gap-[10px]">
      <div>
        <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Reference</label>
        <select id="${idPrefix}Ref" class="ref-select w-full border border-line-soft rounded-lg px-3 py-[7px] text-[12px] outline-none focus:border-accent bg-white">
          ${_buildRefOptions(refVal)}
        </select>
      </div>
      <div>
        <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Threshold %</label>
        ${inp("Pct", "number", pct, "", 'min="0" max="100" step="1"')}
      </div>
    </div>
    <div>
      <label class="text-[10px] text-ink-faint uppercase tracking-[.4px] block mb-1">Absolute Threshold</label>
      ${inp("Abs", "number", abs, "", 'min="0" step="100"')}
    </div>`;
}

function _readForm(idPrefix) {
  const v = id => document.getElementById(`${idPrefix}${id}`)?.value ?? "";
  const name    = v("Name").trim();
  const desc    = v("Desc").trim();
  const measure = v("Measure").trim();
  const refVal  = v("Ref") || "budget";
  const pct     = parseFloat(v("Pct") || "10") / 100;
  const abs     = parseFloat(v("Abs") || "5000");
  const reference       = STD_REF.has(refVal) ? refVal : "custom";
  const reference_label = STD_REF.has(refVal) ? "" : refVal;
  return { name, desc, measure, reference, reference_label, pct, abs };
}

function _validateForm(idPrefix) {
  let ok = true;
  [`${idPrefix}Name`, `${idPrefix}Measure`].forEach(id => {
    const el = document.getElementById(id);
    const empty = !el?.value.trim();
    el?.classList.toggle("border-red-400", empty);
    if (empty) ok = false;
  });
  return ok;
}

function deleteRule(displayIdx) {
  const r = curCube.rules[displayIdx];
  if (!r) return;
  _snapshotIfNeeded();
  if (r.type === "integrity") curCube.rawRuleset.integrity.splice(r.srcIndex, 1);
  else curCube.rawRuleset.variance.splice(r.srcIndex, 1);
  _syncRules();
  if (_editingRuleIdx === displayIdx) _editingRuleIdx = -1;
  renderBody();
}

function addRule() {
  if (!_validateForm("rn")) return;
  const { name, desc, measure, reference, reference_label, pct, abs } = _readForm("rn");
  _snapshotIfNeeded();
  curCube.rawRuleset.variance.push({ name, description: desc, measure, reference, reference_label, rel_pct: pct, abs_value: abs, group_by: [], enabled: true });
  _syncRules();
  renderBody();
}

function updateRule(displayIdx) {
  const r = curCube.rules[displayIdx];
  if (!r || r.type !== "variance") return;
  if (!_validateForm("re")) return;
  const { name, desc, measure, reference, reference_label, pct, abs } = _readForm("re");
  const raw = curCube.rawRuleset.variance[r.srcIndex];
  if (!raw) return;
  Object.assign(raw, { name, description: desc, measure, reference, reference_label, rel_pct: pct, abs_value: abs });
  _syncRules();
  _editingRuleIdx = -1;
  renderBody();
}

async function saveRules() {
  const btn = document.getElementById("btnSaveRules");
  if (btn) { btn.textContent = "Saving…"; btn.disabled = true; }
  try {
    const res = await fetch(`${ANOMALY_API}/api/anomaly/rules/${encodeURIComponent(curCube.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(curCube.rawRuleset),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _rulesetSnapshot = null;
    _rulesEditMode = false; _editingRuleIdx = -1;
    renderBody();
  } catch (err) {
    if (btn) { btn.textContent = "Save"; btn.disabled = false; }
    const errEl = document.createElement("div");
    errEl.className = "text-red-500 text-[11px] mt-2 text-center";
    errEl.textContent = `Save failed: ${err.message}`;
    btn?.closest("div")?.appendChild(errEl);
  }
}

function _cancelEdit() {
  if (_rulesetSnapshot) {
    curCube.rawRuleset = _rulesetSnapshot;
    _rulesetSnapshot = null;
    _syncRules();
  }
  _rulesEditMode = false; _editingRuleIdx = -1;
  renderBody();
}

// Event delegation for the drawer body (rules edit UI)
drBody.addEventListener("click", e => {
  const t = e.target;
  if (t.id === "btnEditRules") {
    _rulesEditMode = true; _snapshotIfNeeded();
    _fetchRefElements();
    renderBody(); return;
  }
  if (t.id === "btnCancelEdit")  { _cancelEdit(); return; }
  if (t.id === "btnSaveRules")   { saveRules(); return; }
  if (t.id === "btnAddRule")     { document.getElementById("addRuleForm")?.classList.toggle("hidden"); return; }
  if (t.id === "btnCancelAdd")   { document.getElementById("addRuleForm")?.classList.add("hidden"); return; }
  if (t.id === "btnConfirmAdd")  { addRule(); return; }
  if (t.id === "btnDiscardEdit") { _editingRuleIdx = -1; renderBody(); return; }
  const updateBtn = t.closest("#btnUpdateRule");
  if (updateBtn) { updateRule(parseInt(updateBtn.dataset.idx, 10)); return; }
  const delBtn = t.closest(".del-rule-btn");
  if (delBtn) { deleteRule(parseInt(delBtn.dataset.del, 10)); return; }
  // Clicking a variance rule card opens inline edit
  const card = t.closest("[data-open-edit]");
  if (card && !t.closest(".del-rule-btn")) {
    const idx = parseInt(card.dataset.openEdit, 10);
    _snapshotIfNeeded(); _rulesEditMode = true; _editingRuleIdx = idx;
    _fetchRefElements();
    renderBody(); return;
  }
});

document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => setTab(t.dataset.tab)));
document.getElementById("closeBtn").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && drawerOpen) closeDrawer();
  if (e.key === "Escape" && !confirmModal.classList.contains("pointer-events-none")) closeConfirmModal();
});

/* ---------- bootstrap ---------- */
async function init() {
  statusEl.innerHTML = 'Loading cubes from schema...';
  try {
    await loadData();
  } catch (err) {
    statusEl.innerHTML = `<span class="text-hi">Could not load cubes: ${String(err && err.message || err)}</span>`;
    return;
  }
  if (!TM1_DATA.cubes.length) {
    statusEl.innerHTML = 'No cubes found in the synced schema. Run Save & sync from TM1 settings, then return here.';
    return;
  }
  recomputeLayoutConstants();
  computeLayout();
  buildNodes();
  buildEdges();
  updateSvgAndEdges();
  statusEl.innerHTML = `Loaded <b class="text-ink font-semibold">${TM1_DATA.cubes.length}</b> cube${TM1_DATA.cubes.length === 1 ? "" : "s"} — click <b class="text-ink font-semibold">Run Scan</b>`;
}
init();
