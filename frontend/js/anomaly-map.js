import { TM1_DATA, st, pos, nodeEls, edgeEls } from "./anomaly-state.js";
import { htmlAttr } from "./anomaly-data.js";

const BASE_COL_GAP = 268, NODE_W = 196, NODE_H = 96, ROW_GAP = 130, PAD_X = 70, PAD_Y = 60;
const MIN_COL_GAP = NODE_W + 82;
const SUMMARY_GAP = 34, SUMMARY_H = 132;
const ZOOM_MIN = 0.45, ZOOM_MAX = 1.6, ZOOM_STEP = 0.1;

const canvas     = document.getElementById("canvas");
const scanSummary = document.getElementById("scanSummary");
const svg        = document.getElementById("edges");

const CUBE_ICON = `<svg viewBox="0 0 24 24" fill="none" class="w-[19px] h-[19px] flex-shrink-0">
  <path d="M12 2 21 7v10l-9 5-9-5V7z" stroke="#5a6679" stroke-width="1.3"/>
  <path d="M12 2 21 7l-9 5-9-5z" fill="#0d9488" fill-opacity=".16"/>
  <path d="M12 12v10M3 7l9 5 9-5" stroke="#5a6679" stroke-width="1.3"/></svg>`;
const CLEAN_ICON = `<svg viewBox="0 0 24 24" fill="none" class="clean absolute -top-[9px] -right-[9px] w-6 h-6">
  <circle cx="12" cy="12" r="11" fill="#d9f3ef"/>
  <circle cx="12" cy="12" r="11" stroke="#0d9488" stroke-width="1.2"/>
  <path d="m8 12 3 3 5-6" stroke="#0d9488" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const TICK_ICON  = `<svg viewBox="0 0 24 24" fill="none" class="w-[14px] h-[14px]"><path d="m6 12 4 4 8-9" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export function recomputeLayoutConstants() {
  st.cubeById = Object.fromEntries(TM1_DATA.cubes.map(c => [c.id, c]));
  st.layers = {};
  TM1_DATA.cubes.forEach(c => { (st.layers[c.layer] ??= []).push(c); });
  const layerKeys = Object.keys(st.layers).map(Number);
  st.maxLayer = layerKeys.length ? Math.max(...layerKeys) : 0;
  const counts = Object.values(st.layers).map(a => a.length);
  st.maxRows  = counts.length ? Math.max(...counts) : 1;
  st.midY     = PAD_Y + ((st.maxRows - 1) * ROW_GAP) / 2 + NODE_H / 2;
  st.mapH     = PAD_Y * 2 + (st.maxRows - 1) * ROW_GAP + NODE_H;
  st.canvasH  = st.mapH + SUMMARY_GAP + SUMMARY_H;
}

export function computeLayout() {
  const viewportW = canvas.parentElement?.clientWidth || window.innerWidth;
  const minW = PAD_X * 2 + st.maxLayer * MIN_COL_GAP + NODE_W;
  const targetW = Math.max(viewportW, minW);
  const colGap = st.maxLayer > 0
    ? Math.max(MIN_COL_GAP, (targetW - PAD_X * 2 - NODE_W) / st.maxLayer)
    : BASE_COL_GAP;

  st.canvasW = PAD_X * 2 + st.maxLayer * colGap + NODE_W;

  Object.entries(st.layers).forEach(([L, arr]) => {
    const n = arr.length;
    arr.forEach((c, i) => {
      const x = PAD_X + Number(L) * colGap;
      const y = st.midY - ((n - 1) * ROW_GAP) / 2 + i * ROW_GAP - NODE_H / 2;
      pos[c.id] = { x, y, cx: x + NODE_W / 2, cy: y + NODE_H / 2 };
    });
  });

  applyCanvasZoom();
  scanSummary.style.top = (st.mapH + SUMMARY_GAP) + "px";
}

export function applyCanvasZoom() {
  canvas.style.width  = `${st.canvasW * st.mapZoom}px`;
  canvas.style.height = `${st.canvasH * st.mapZoom}px`;
  canvas.style.transform = `scale(${st.mapZoom})`;
  canvas.style.transformOrigin = "top left";
  document.getElementById("zoomLabel").textContent = `${Math.round(st.mapZoom * 100)}%`;
}

export function setMapZoom(value) {
  st.mapZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Number(value) || 1));
  applyCanvasZoom();
}

export function buildEdges() {
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

export function updateSvgAndEdges() {
  svg.setAttribute("viewBox", `0 0 ${st.canvasW} ${st.canvasH}`);
  svg.setAttribute("width",  st.canvasW);
  svg.setAttribute("height", st.canvasH);
  edgeEls.forEach(({ el, from, to }) => {
    const a = pos[from], b = pos[to];
    const x1 = a.x + NODE_W, y1 = a.cy, x2 = b.x, y2 = b.cy, mx = (x1 + x2) / 2;
    el.setAttribute("d", `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`);
  });
}

// onPickClick / onNodeClick / onMouseEnter / onMouseLeave are passed from init()
// to avoid circular imports with drawer and selection modules.
export function buildNodes(onPickClick, onNodeClick, onMouseEnter, onMouseLeave) {
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

    el.querySelector("[data-pick]").addEventListener("click", ev => { ev.stopPropagation(); onPickClick(c.id); });
    el.addEventListener("click", () => onNodeClick(c.id));
    el.addEventListener("mouseenter", () => onMouseEnter(c.id));
    el.addEventListener("mouseleave", () => onMouseLeave(c.id));
  });
}

export function applyNodePositions() {
  Object.entries(nodeEls).forEach(([id, el]) => {
    el.style.left = pos[id].x + "px";
    el.style.top  = pos[id].y + "px";
  });
}

export function applyResponsiveLayout() {
  computeLayout();
  applyNodePositions();
  updateSvgAndEdges();
}

export function highlightPath(id, on) {
  if (st.drawerOpen) return;
  const connected = new Set([id]);
  edgeEls.forEach(({ el, from, to }) => {
    const hit = from === id || to === id;
    el.classList.toggle("lit", on && hit);
    el.classList.toggle("dim", on && !hit);
    if (hit) { connected.add(from); connected.add(to); }
  });
  if (!st.selectMode) Object.entries(nodeEls).forEach(([nid, el]) => el.classList.toggle("dim", on && !connected.has(nid)));
}

// Zoom controls
document.getElementById("zoomOutBtn")?.addEventListener("click", () => setMapZoom(st.mapZoom - ZOOM_STEP));
document.getElementById("zoomInBtn")?.addEventListener("click", () => setMapZoom(st.mapZoom + ZOOM_STEP));
document.getElementById("zoomResetBtn")?.addEventListener("click", () => setMapZoom(1));

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(applyResponsiveLayout, 80);
});
