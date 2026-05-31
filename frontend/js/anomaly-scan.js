import { TM1_DATA, st, selected, nodeEls } from "./anomaly-state.js";
import { fetchScans, getScanPeriodLabel } from "./anomaly-data.js";

const scanBtn     = document.getElementById("scanBtn");
const scanLabel   = document.getElementById("scanLabel");
const statusEl    = document.getElementById("status");
const totalEl     = document.getElementById("totalN");
const sweep       = document.getElementById("sweep");
const scanSummary = document.getElementById("scanSummary");
const confirmModal  = document.getElementById("confirmModal");
const confirmCard   = document.getElementById("confirmCard");
const confirmPeriod = document.getElementById("confirmPeriod");

// Set by initScan() so scan can call openDrawer without a circular import.
let _onOpenDrawer = null;
export function initScan(onOpenDrawer) {
  _onOpenDrawer = onOpenDrawer;
}

export function openConfirmModal() {
  confirmPeriod.textContent = getScanPeriodLabel();
  confirmModal.classList.remove("opacity-0", "pointer-events-none");
  confirmModal.classList.add("opacity-100");
  confirmCard.classList.remove("scale-95");
  confirmCard.classList.add("scale-100");
}

export function closeConfirmModal() {
  confirmModal.classList.add("opacity-0", "pointer-events-none");
  confirmModal.classList.remove("opacity-100");
  confirmCard.classList.add("scale-95");
  confirmCard.classList.remove("scale-100");
}

export function resetScores() {
  st.scanned = false;
  totalEl.textContent = "—";
  scanSummary.classList.add("hidden");
  Object.values(nodeEls).forEach(el => {
    el.classList.remove("scored", "zero");
    const b = el.querySelector("[data-badge]");
    b.classList.remove("show", "pulse"); b.textContent = "";
  });
}

export async function runScan() {
  const targets = st.selectMode ? TM1_DATA.cubes.filter(c => selected.has(c.id)) : TM1_DATA.cubes;
  if (st.selectMode && targets.length === 0) {
    statusEl.innerHTML = '⚠ Please select at least one cube';
    return;
  }
  scanBtn.disabled = true;
  scanBtn.classList.add("scanning");
  scanLabel.textContent = "Scanning…";
  statusEl.innerHTML = `Running rules across <b class="text-ink font-semibold">${targets.length}</b> cube${targets.length === 1 ? "" : "s"}…`;
  resetScores();
  totalEl.textContent = "0";

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
        _countUp(totalEl, runningTotal);
      }
    }, 900 + i * 180);
  });

  setTimeout(() => {
    scanBtn.disabled = false;
    scanBtn.classList.remove("scanning");
    scanLabel.textContent = "Re-scan";
    st.scanned = true;
    statusEl.innerHTML = stub
      ? `Scan endpoint is a stub (backend Phase 3) · 0 anomalies across ${targets.length} cube${targets.length === 1 ? "" : "s"}`
      : `Scan complete · <b class="text-ink font-semibold">${runningTotal}</b> anomalies in <b class="text-ink font-semibold">${cubesWithAnom}</b> / ${targets.length} cube${targets.length === 1 ? "" : "s"}`;
    _renderScanSummary(targets, runningTotal, cubesWithAnom);
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

function _renderScanSummary(targets, runningTotal, cubesWithAnom) {
  const highCount = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "high").length, 0);
  const medCount  = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "med").length, 0);
  const lowCount  = targets.reduce((sum, c) => sum + c.anomalies.filter(a => a.severity === "low").length, 0);

  document.getElementById("summaryTitle").textContent = `Period ${getScanPeriodLabel()} · ${runningTotal} anomal${runningTotal === 1 ? "y" : "ies"} detected`;
  document.getElementById("summarySub").textContent   = `Scanned ${targets.length} cube${targets.length === 1 ? "" : "s"}. ${cubesWithAnom} cube${cubesWithAnom === 1 ? "" : "s"} contain anomalies · severity split: ${highCount} high, ${medCount} medium, ${lowCount} low.`;
  document.getElementById("summaryTotal").textContent = runningTotal;
  document.getElementById("summaryCubes").textContent = `${cubesWithAnom}/${targets.length}`;
  document.getElementById("summaryHigh").textContent  = highCount;
  document.getElementById("summaryByCube").innerHTML  = targets.map(c => {
    const count = c.anomalies.length;
    const high  = c.anomalies.filter(a => a.severity === "high").length;
    const badgeClass = count === 0
      ? "text-accent bg-accent-50 border-accent/20"
      : high > 0 ? "text-hi bg-hi/10 border-hi/30" : "text-med bg-med/10 border-med/30";
    return `<button class="${badgeClass} rounded-full border px-3 py-1.5 text-[10.5px] font-semibold tracking-[.2px] hover:-translate-y-px transition" data-open-cube="${c.id}">${c.name}: ${count}</button>`;
  }).join("");
  scanSummary.classList.remove("hidden");
}

function _countUp(el, to) {
  const from = parseInt(el.textContent) || 0;
  const steps = Math.min(to - from, 8); let cur = from, i = 0;
  if (steps <= 0) { el.textContent = to; return; }
  const inc = (to - from) / steps;
  const t = setInterval(() => { cur += inc; i++; el.textContent = Math.round(cur); if (i >= steps) { el.textContent = to; clearInterval(t); } }, 30);
}

// Event wiring
scanBtn.addEventListener("click", openConfirmModal);
document.getElementById("confirmScanBtn").addEventListener("click", () => { closeConfirmModal(); runScan(); });
document.getElementById("cancelScanBtn").addEventListener("click", closeConfirmModal);
confirmModal.addEventListener("click", e => { if (e.target === confirmModal) closeConfirmModal(); });
document.getElementById("summaryByCube")?.addEventListener("click", e => {
  const btn = e.target.closest("[data-open-cube]");
  if (btn && _onOpenDrawer) _onOpenDrawer(btn.dataset.openCube);
});
