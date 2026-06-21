import { ANOMALY_API, st, nodeEls, edgeEls } from "./anomaly-state.js";
import { htmlAttr, rulesFromRuleSet } from "./anomaly-data.js";

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

export function openDrawer(id) {
  const c = st.cubeById[id]; curCube = c; st.drawerOpen = true;
  _rulesEditMode = false; _editingRuleIdx = -1; _rulesetSnapshot = null;
  _refElements = []; _refLoaded = false;
  edgeEls.forEach(({ el }) => el.classList.remove("lit", "dim"));
  if (!st.selectMode) {
    Object.entries(nodeEls).forEach(([nid, el]) => {
      el.classList.toggle("active", nid === id);
      el.classList.toggle("dim", nid !== id);
    });
  } else {
    nodeEls[id].classList.add("active");
  }
  drName.textContent = c.name;
  drDims.innerHTML   = c.dims.map(d => `<span class="text-[10px] text-ink-dim bg-[#f0f3f7] border border-line-soft rounded-[5px] px-1.5 py-0.5">${d}</span>`).join("");
  ctAnom.textContent  = `(${c.anomalies.length})`;
  ctRules.textContent = `(${c.rules.length})`;
  _setTab(st.scanned ? "anom" : "rules");
  drawer.classList.add("open"); backdrop.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

export function closeDrawer() {
  st.drawerOpen = false;
  _rulesEditMode = false; _editingRuleIdx = -1; _rulesetSnapshot = null;
  drawer.classList.remove("open"); backdrop.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  edgeEls.forEach(({ el }) => el.classList.remove("lit", "dim"));
  Object.values(nodeEls).forEach(el => el.classList.remove("active", "dim"));
}

function _setTab(tab) {
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
  _renderBody();
}

function _sevPill(sev) {
  const m = { high: "bg-hi/10 text-hi border-hi/30", med: "bg-med/10 text-med border-med/30", low: "bg-lo/10 text-lo border-lo/30" };
  return `${m[sev]} text-[9.5px] font-semibold px-[7px] py-0.5 rounded-full tracking-[.4px] uppercase border`;
}

function _highlightExpr(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\b(AND|OR|NOT|BETWEEN|OVER|FLAG|DISTINCT)\b/g, '<span class="kw">$1</span>')
    .replace(/\b(SUM|COUNT|ABS|AVG12|STDEV12|MOD|feeder)\b/g, '<span class="fn">$1</span>')
    .replace(/(\d+(?:\.\d+)?%?|3σ)/g, '<span class="num">$1</span>');
}

function _renderBody() {
  const c = curCube;
  if (curTab === "anom") {
    if (!st.scanned) {
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
          <span class="${_sevPill(a.severity)}">${a.severity}</span>
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
          const clickable = r.type === "variance";
          return `
            <div class="relative border border-line-soft border-l-[3px] border-l-accent rounded-[10px] px-[13px] py-3 mb-2.5 bg-white${clickable ? " cursor-pointer hover:border-accent/40 transition-colors" : ""}"
                 ${clickable ? `data-open-edit="${i}"` : ""}>
              ${_rulesEditMode ? `<button data-del="${i}" class="del-rule-btn absolute top-[9px] right-[9px] w-[20px] h-[20px] rounded-full bg-[#f0f3f7] hover:bg-red-100 text-[#8898aa] hover:text-red-500 text-[14px] leading-none flex items-center justify-center transition-colors cursor-pointer border-0 p-0">×</button>` : ""}
              <h4 class="font-disp font-bold text-[13.5px] m-0 mb-1 ${_rulesEditMode ? "pr-7" : ""}">${htmlAttr(r.name)}</h4>
              <div class="text-ink-dim text-[11px] leading-relaxed mb-2.5">${htmlAttr(r.desc)}</div>
              <pre class="m-0 bg-[#f4f6f9] border border-line-soft rounded-lg px-[11px] py-[9px] overflow-x-auto text-[11px] text-[#3a465c] leading-relaxed">${_highlightExpr(r.expression)}</pre>
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

// ── Rules reference elements ───────────────────────────────────────────────

async function _fetchRefElements() {
  if (_refLoaded) return;
  try {
    const res = await fetch(`${ANOMALY_API}/api/anomaly/rules/${encodeURIComponent(curCube.id)}/reference-elements`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _refElements = data.dims || [];
  } catch { _refElements = []; }
  _refLoaded = true;
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

function _syncRules() {
  curCube.rules = rulesFromRuleSet(curCube.rawRuleset);
  ctRules.textContent = `(${curCube.rules.length})`;
}

function _snapshotIfNeeded() {
  if (!_rulesetSnapshot) _rulesetSnapshot = JSON.parse(JSON.stringify(curCube.rawRuleset));
}

function _deleteRule(displayIdx) {
  const r = curCube.rules[displayIdx];
  if (!r) return;
  _snapshotIfNeeded();
  if (r.type === "integrity") curCube.rawRuleset.integrity.splice(r.srcIndex, 1);
  else curCube.rawRuleset.variance.splice(r.srcIndex, 1);
  _syncRules();
  if (_editingRuleIdx === displayIdx) _editingRuleIdx = -1;
  _renderBody();
}

function _addRule() {
  if (!_validateForm("rn")) return;
  const { name, desc, measure, reference, reference_label, pct, abs } = _readForm("rn");
  _snapshotIfNeeded();
  curCube.rawRuleset.variance.push({ name, description: desc, measure, reference, reference_label, rel_pct: pct, abs_value: abs, group_by: [], enabled: true });
  _syncRules();
  _renderBody();
}

function _updateRule(displayIdx) {
  const r = curCube.rules[displayIdx];
  if (!r || r.type !== "variance") return;
  if (!_validateForm("re")) return;
  const { name, desc, measure, reference, reference_label, pct, abs } = _readForm("re");
  const raw = curCube.rawRuleset.variance[r.srcIndex];
  if (!raw) return;
  Object.assign(raw, { name, description: desc, measure, reference, reference_label, rel_pct: pct, abs_value: abs });
  _syncRules();
  _editingRuleIdx = -1;
  _renderBody();
}

async function _saveRules() {
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
    _renderBody();
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
  _renderBody();
}

// Event delegation for the drawer body (rules edit UI)
drBody.addEventListener("click", e => {
  const t = e.target;
  if (t.id === "btnEditRules")    { _rulesEditMode = true; _snapshotIfNeeded(); _fetchRefElements(); _renderBody(); return; }
  if (t.id === "btnCancelEdit")   { _cancelEdit(); return; }
  if (t.id === "btnSaveRules")    { _saveRules(); return; }
  if (t.id === "btnAddRule")      { document.getElementById("addRuleForm")?.classList.toggle("hidden"); return; }
  if (t.id === "btnCancelAdd")    { document.getElementById("addRuleForm")?.classList.add("hidden"); return; }
  if (t.id === "btnConfirmAdd")   { _addRule(); return; }
  if (t.id === "btnDiscardEdit")  { _editingRuleIdx = -1; _renderBody(); return; }
  const updateBtn = t.closest("#btnUpdateRule");
  if (updateBtn) { _updateRule(parseInt(updateBtn.dataset.idx, 10)); return; }
  const delBtn = t.closest(".del-rule-btn");
  if (delBtn) { _deleteRule(parseInt(delBtn.dataset.del, 10)); return; }
  const card = t.closest("[data-open-edit]");
  if (card && !t.closest(".del-rule-btn")) {
    const idx = parseInt(card.dataset.openEdit, 10);
    _snapshotIfNeeded(); _rulesEditMode = true; _editingRuleIdx = idx;
    _fetchRefElements();
    _renderBody(); return;
  }
});

// Tab switching and close button
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => _setTab(t.dataset.tab)));
document.getElementById("closeBtn").addEventListener("click", closeDrawer);
backdrop.addEventListener("click", closeDrawer);
