/* Anomaly module — Phase 1 + 2.
 *
 *  Phase 1: mode switch + tab routing + sidebar rules list.
 *  Phase 2: cube picker + rules editor (integrity toggles + variance CRUD)
 *           + save/delete with dirty-state tracking.
 *
 *  Real scan execution / Teams push come in later phases.
 */

const APP_MODE_KEY      = "appMode";          // "chat" | "anomaly"
const ANOMALY_TAB_KEY   = "anomalyTab";       // "rules" | "scan" | "review" | "push"
const ANOMALY_ACTIVE_RULES_KEY = "anomalyActiveRules";

/* Built-in integrity rules — descriptions shown in the toggle list.
 * Must stay in sync with backend/anomaly/rules/builtin.py::BUILTIN_INTEGRITY. */
const ANOMALY_INTEGRITY_DEFS = [
  { name: "negative_values",     label: "Negative values",     desc: "Flag rows where a numeric measure is below zero." },
  { name: "headcount_no_salary", label: "Headcount without salary", desc: "Headcount > 0 but Gross Salary = 0 → missing data." },
  { name: "fte_gt_headcount",    label: "FTE exceeds headcount", desc: "FTE > Headcount in the same row → allocation error." },
  { name: "alloc_not_100pct",    label: "Allocation ≠ 100%",   desc: "AllocPct across a grouping must sum to 1.0." },
];

/* In-memory editor state. The DOM is the rendered view; this is the truth. */
const _editorState = {
  cube: null,           // string | null
  description: "",
  integrity: [],        // [{name, enabled, columns?, group_by?}]
  variance: [],         // [{name, description, measure, reference, reference_label, rel_pct, abs_value, group_by, reason_template, enabled}]
  dirty: false,
  saving: false,
};

let _allCubes = [];   // cached for the cube picker; loaded lazily

/* ── Mode switch ────────────────────────────────────────────────────────── */

function getAppMode()      { return localStorage.getItem(APP_MODE_KEY) || "chat"; }
function setAppMode(mode)  {
  const next = (mode === "anomaly") ? "anomaly" : "chat";
  localStorage.setItem(APP_MODE_KEY, next);
  applyAppMode(next);
}

function applyAppMode(mode) {
  // Use inline style.display so visibility wins over sidebar.js stripping `hidden`.
  document.querySelectorAll("[data-mode-panel]").forEach(el => {
    el.style.display = (el.dataset.modePanel === mode) ? "" : "none";
  });
  document.querySelectorAll("[data-mode-main]").forEach(el => {
    el.style.display = (el.dataset.modeMain === mode) ? "" : "none";
  });
  // Highlight active mode button (works for both expanded pills and collapsed rail icons).
  document.querySelectorAll("[data-mode-btn]").forEach(btn => {
    const active = btn.dataset.modeBtn === mode;
    btn.classList.toggle("bg-cw-blueLite", active);
    btn.classList.toggle("text-cw-blue",   active);
  });
  const planning = document.getElementById("planningBadge");
  if (planning) planning.style.display = (mode === "chat") ? "" : "none";

  if (mode === "anomaly") {
    setAnomalyTab(localStorage.getItem(ANOMALY_TAB_KEY) || "rules");
    refreshAnomalyRulesList();
    const active = localStorage.getItem(ANOMALY_ACTIVE_RULES_KEY);
    if (active && _editorState.cube !== active) {
      loadAnomalyRuleSet(active);
    } else {
      renderEditor();
    }
  }
}

/* ── Tab router ─────────────────────────────────────────────────────────── */

function setAnomalyTab(tab) {
  const valid = ["rules", "scan", "review", "push"];
  const next = valid.includes(tab) ? tab : "rules";
  localStorage.setItem(ANOMALY_TAB_KEY, next);
  document.querySelectorAll("[data-anomaly-view]").forEach(v => {
    v.classList.toggle("hidden", v.dataset.anomalyView !== next);
  });
  document.querySelectorAll(".anomaly-tab").forEach(btn => {
    const active = btn.dataset.anomalyTab === next;
    btn.classList.toggle("border-cw-blue", active);
    btn.classList.toggle("text-cw-text",   active);
    btn.classList.toggle("text-cw-muted", !active);
  });
}

/* ── Sidebar rules list ─────────────────────────────────────────────────── */

async function refreshAnomalyRulesList() {
  const list = document.getElementById("anomalyRulesList");
  if (!list) return;
  try {
    const res = await fetch(`${API}/api/anomaly/rules`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const cubes = data.cubes || [];
    if (!cubes.length) {
      list.innerHTML = `<div class="px-3 py-3 text-center text-xs text-cw-muted">No rule sets yet</div>`;
      return;
    }
    const active = localStorage.getItem(ANOMALY_ACTIVE_RULES_KEY) || "";
    list.innerHTML = cubes.map(cube => {
      const safe = esc(cube);
      const isActive = cube === active;
      const cls = isActive
        ? "bg-cw-blueLite text-cw-blue"
        : "text-cw-text hover:bg-cw-bg";
      return `<button type="button" data-anomaly-rules-cube="${safe}"
        class="mb-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] font-medium transition ${cls}">
        <i class="fa-solid fa-cube text-[12px] text-cw-muted"></i>
        <span class="truncate">${safe}</span>
      </button>`;
    }).join("");
  } catch (err) {
    list.innerHTML = `<div class="px-3 py-3 text-center text-xs text-red-600">Could not load rule sets</div>`;
    console.warn("[anomaly] rules list failed:", err);
  }
}

function selectAnomalyRulesCube(cube) {
  if (_editorState.dirty && !confirm("Discard unsaved changes?")) return;
  localStorage.setItem(ANOMALY_ACTIVE_RULES_KEY, cube);
  refreshAnomalyRulesList();
  setAnomalyTab("rules");
  loadAnomalyRuleSet(cube);
}

/* ── Cube picker ─────────────────────────────────────────────────────────── */

function openCubePicker() {
  const popup = document.getElementById("anomalyCubePickerPopup");
  if (!popup) return;
  popup.classList.remove("hidden");
  popup.classList.add("flex");
  document.getElementById("anomalyCubePickerSearch")?.focus();
  loadAllCubes();
}

function closeCubePicker() {
  const popup = document.getElementById("anomalyCubePickerPopup");
  if (!popup) return;
  popup.classList.add("hidden");
  popup.classList.remove("flex");
}

async function loadAllCubes() {
  const list = document.getElementById("anomalyCubePickerList");
  if (!list) return;
  if (_allCubes.length) { renderCubePickerList(); return; }
  try {
    const res = await fetch(`${API}/api/views`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _allCubes = (data.cubes || []).map(c => ({
      cube: String(c.cube || ""),
      description: String(c.description || ""),
    }));
    renderCubePickerList();
  } catch (err) {
    list.innerHTML = `<div class="px-2 py-3 text-[11px] italic text-red-600">Could not load cubes: ${esc(err.message)}</div>`;
  }
}

function renderCubePickerList() {
  const list = document.getElementById("anomalyCubePickerList");
  if (!list) return;
  const q = (document.getElementById("anomalyCubePickerSearch")?.value || "").trim().toLowerCase();
  const items = _allCubes.filter(c =>
    !q || c.cube.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)
  );
  if (!items.length) {
    list.innerHTML = `<div class="px-2 py-3 text-[11px] italic text-cw-muted">No cubes match.</div>`;
    return;
  }
  list.innerHTML = items.map(c => `
    <button type="button" data-cube-pick="${esc(c.cube)}"
      class="mb-1 flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left transition hover:bg-cw-bg">
      <i class="fa-solid fa-cube mt-0.5 text-[11px] text-cw-muted"></i>
      <div class="min-w-0 flex-1">
        <div class="truncate text-[12px] font-medium text-cw-text">${esc(c.cube)}</div>
        ${c.description && c.description !== c.cube
          ? `<div class="truncate text-[10px] text-cw-muted">${esc(c.description)}</div>`
          : ""}
      </div>
    </button>
  `).join("");
}

function pickCubeForNewRuleSet(cube) {
  closeCubePicker();
  if (_editorState.dirty && !confirm("Discard unsaved changes?")) return;
  localStorage.setItem(ANOMALY_ACTIVE_RULES_KEY, cube);
  setAnomalyTab("rules");
  // Pre-populate a sensible starter set so the editor is not blank.
  _editorState.cube = cube;
  _editorState.description = "";
  _editorState.integrity = ANOMALY_INTEGRITY_DEFS.map(d => ({
    name: d.name, enabled: false,
  }));
  _editorState.variance = [];
  _editorState.dirty = true;
  renderEditor();
  setEditorStatus("Unsaved — new rule set", "warn");
}

/* ── Load / render the editor ───────────────────────────────────────────── */

async function loadAnomalyRuleSet(cube) {
  setEditorStatus("Loading…", "muted");
  try {
    const res = await fetch(`${API}/api/anomaly/rules/${encodeURIComponent(cube)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    _editorState.cube = data.cube || cube;
    _editorState.description = data.description || "";
    _editorState.integrity = normaliseIntegrity(data.integrity || []);
    _editorState.variance = (data.variance || []).map(normaliseVariance);
    _editorState.dirty = false;
    renderEditor();
    setEditorStatus("", "muted");
  } catch (err) {
    setEditorStatus(`Load failed: ${err.message}`, "error");
  }
}

/* Ensure every built-in integrity rule appears in the list (disabled by default
 * if it wasn't saved before), so the user sees the full set of toggles. */
function normaliseIntegrity(saved) {
  const bySaved = new Map(saved.map(r => [r.name, r]));
  return ANOMALY_INTEGRITY_DEFS.map(def => {
    const s = bySaved.get(def.name);
    if (s) return { ...s, name: def.name };
    return { name: def.name, enabled: false };
  });
}

function normaliseVariance(r) {
  return {
    name: r.name || "",
    description: r.description || "",
    measure: r.measure || "",
    reference: r.reference || "budget",
    reference_label: r.reference_label || "",
    rel_pct: typeof r.rel_pct === "number" ? r.rel_pct : 0.10,
    abs_value: typeof r.abs_value === "number" ? r.abs_value : 5000,
    group_by: Array.isArray(r.group_by) ? r.group_by.slice() : [],
    reason_template: r.reason_template || "",
    enabled: r.enabled !== false,
  };
}

function renderEditor() {
  const empty = document.getElementById("anomalyRulesEmpty");
  const editor = document.getElementById("anomalyRulesEditor");
  if (!empty || !editor) return;

  if (!_editorState.cube) {
    empty.classList.remove("hidden");
    editor.classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  editor.classList.remove("hidden");

  document.getElementById("anomalyEditorCube").textContent = _editorState.cube;
  document.getElementById("anomalyEditorDescription").value = _editorState.description;
  renderIntegrityList();
  renderVarianceList();
  updateSaveButton();
}

function renderIntegrityList() {
  const list = document.getElementById("anomalyIntegrityList");
  if (!list) return;
  list.innerHTML = _editorState.integrity.map((rule, idx) => {
    const def = ANOMALY_INTEGRITY_DEFS.find(d => d.name === rule.name) || {};
    const enabled = !!rule.enabled;
    return `<div class="flex items-start gap-4 px-5 py-3">
      <label class="mt-0.5 flex shrink-0 cursor-pointer items-center">
        <input type="checkbox" data-integrity-toggle="${idx}" ${enabled ? "checked" : ""}
          class="h-4 w-4 rounded border-cw-border text-cw-blue focus:ring-cw-blue/20" />
      </label>
      <div class="min-w-0 flex-1">
        <div class="text-[12px] font-semibold text-cw-text">${esc(def.label || rule.name)}</div>
        <div class="mt-0.5 text-[11px] text-cw-muted">${esc(def.desc || "")}</div>
        ${enabled && rule.name === "negative_values" ? renderColumnsInput(idx, rule.columns) : ""}
        ${enabled && rule.name === "alloc_not_100pct" ? renderGroupByInput(idx, rule.group_by, "alloc") : ""}
      </div>
    </div>`;
  }).join("");
}

function renderColumnsInput(idx, columns) {
  const value = (columns || []).join(", ");
  return `<label class="mt-2 block">
    <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Columns to check (comma-separated)</span>
    <input type="text" data-integrity-columns="${idx}" value="${esc(value)}"
      placeholder="GrossSalary, FTE, Headcount"
      class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
  </label>`;
}

function renderGroupByInput(idx, groupBy, hint) {
  const value = (groupBy || []).join(", ");
  return `<label class="mt-2 block">
    <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Group by (dim names, comma-separated)</span>
    <input type="text" data-integrity-groupby="${idx}" value="${esc(value)}"
      placeholder="Employee"
      class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
  </label>`;
}

function renderVarianceList() {
  const list = document.getElementById("anomalyVarianceList");
  if (!list) return;
  if (!_editorState.variance.length) {
    list.innerHTML = `<div class="px-5 py-6 text-center text-[12px] italic text-cw-muted">No variance rules yet. Click "Add variance rule".</div>`;
    return;
  }
  list.innerHTML = _editorState.variance.map((r, idx) => renderVarianceCard(r, idx)).join("");
}

function renderVarianceCard(rule, idx) {
  return `<div class="px-5 py-4">
    <div class="mb-3 flex items-center gap-3">
      <label class="flex shrink-0 cursor-pointer items-center">
        <input type="checkbox" data-variance-enabled="${idx}" ${rule.enabled ? "checked" : ""}
          class="h-4 w-4 rounded border-cw-border text-cw-blue focus:ring-cw-blue/20" />
      </label>
      <input type="text" data-variance-field="${idx}|name" value="${esc(rule.name)}"
        placeholder="rule_id"
        class="h-8 min-w-0 flex-1 rounded-md border border-cw-border bg-white px-2 text-[12px] font-semibold outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
      <button type="button" data-variance-delete="${idx}"
        class="flex h-7 w-7 items-center justify-center rounded-md text-cw-muted transition hover:bg-red-50 hover:text-red-600" title="Delete this rule">
        <i class="fa-solid fa-trash text-[11px]"></i>
      </button>
    </div>

    <label class="mb-3 block">
      <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Description</span>
      <input type="text" data-variance-field="${idx}|description" value="${esc(rule.description)}"
        placeholder="One sentence on what this rule looks for"
        class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
    </label>

    <div class="mb-3 grid grid-cols-2 gap-3">
      <label class="block">
        <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Measure</span>
        <input type="text" data-variance-field="${idx}|measure" value="${esc(rule.measure)}"
          placeholder="GrossSalary"
          class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
      </label>
      <label class="block">
        <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Reference</span>
        <select data-variance-field="${idx}|reference"
          class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10">
          ${["budget","forecast","prior_week","prior_year","custom"].map(k =>
            `<option value="${k}" ${rule.reference===k?"selected":""}>${esc(k)}</option>`).join("")}
        </select>
      </label>
    </div>

    <div class="mb-3 grid grid-cols-2 gap-3">
      <label class="block">
        <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Relative threshold (e.g. 0.15 = 15%)</span>
        <input type="number" step="0.01" min="0" max="10"
          data-variance-field="${idx}|rel_pct" value="${rule.rel_pct}"
          class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
      </label>
      <label class="block">
        <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Absolute threshold (units)</span>
        <input type="number" step="100" min="0"
          data-variance-field="${idx}|abs_value" value="${rule.abs_value}"
          class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
      </label>
    </div>

    <label class="mb-3 block">
      <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Group by (dim names, comma-separated)</span>
      <input type="text" data-variance-field="${idx}|group_by" value="${esc((rule.group_by || []).join(", "))}"
        placeholder="CostCenter"
        class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
    </label>

    <label class="block">
      <span class="mb-1 block text-[10px] font-medium uppercase tracking-[0.06em] text-cw-muted">Reason template (optional)</span>
      <input type="text" data-variance-field="${idx}|reason_template" value="${esc(rule.reason_template || "")}"
        placeholder="{CostCenter} {measure} {direction} {rel_delta_pct} ({abs_delta_money}) vs {reference_label}"
        class="h-8 w-full rounded-md border border-cw-border bg-white px-2 text-[12px] outline-none focus:border-cw-blue focus:ring-2 focus:ring-cw-blue/10" />
    </label>
  </div>`;
}

/* ── Edits → state ──────────────────────────────────────────────────────── */

function setDirty() {
  _editorState.dirty = true;
  updateSaveButton();
  setEditorStatus("Unsaved changes", "warn");
}

function updateSaveButton() {
  const save = document.getElementById("anomalyEditorSave");
  if (!save) return;
  save.disabled = !_editorState.dirty || _editorState.saving;
}

function setEditorStatus(text, kind = "muted") {
  const el = document.getElementById("anomalyEditorStatus");
  if (!el) return;
  el.textContent = text;
  el.className = "min-h-4 text-right text-[11px] font-medium";
  el.classList.add(
    kind === "error" ? "text-red-600" :
    kind === "warn"  ? "text-amber-600" :
    kind === "ok"    ? "text-emerald-600" :
                       "text-cw-muted"
  );
}

function onEditorInput(event) {
  const t = event.target;
  if (t.id === "anomalyEditorDescription") {
    _editorState.description = t.value;
    setDirty();
    return;
  }
  // Integrity toggles
  if (t.dataset.integrityToggle != null) {
    const idx = +t.dataset.integrityToggle;
    _editorState.integrity[idx].enabled = !!t.checked;
    renderIntegrityList();           // re-render so per-rule inputs appear/disappear
    setDirty();
    return;
  }
  if (t.dataset.integrityColumns != null) {
    const idx = +t.dataset.integrityColumns;
    _editorState.integrity[idx].columns = parseCSV(t.value);
    setDirty();
    return;
  }
  if (t.dataset.integrityGroupby != null) {
    const idx = +t.dataset.integrityGroupby;
    _editorState.integrity[idx].group_by = parseCSV(t.value);
    setDirty();
    return;
  }
  // Variance fields
  if (t.dataset.varianceEnabled != null) {
    const idx = +t.dataset.varianceEnabled;
    _editorState.variance[idx].enabled = !!t.checked;
    setDirty();
    return;
  }
  if (t.dataset.varianceField) {
    const [idxStr, field] = t.dataset.varianceField.split("|");
    const idx = +idxStr;
    const rule = _editorState.variance[idx];
    if (!rule) return;
    if (field === "rel_pct" || field === "abs_value") {
      rule[field] = parseFloat(t.value);
      if (Number.isNaN(rule[field])) rule[field] = 0;
    } else if (field === "group_by") {
      rule.group_by = parseCSV(t.value);
    } else {
      rule[field] = t.value;
    }
    setDirty();
  }
}

function onEditorClick(event) {
  const del = event.target.closest("[data-variance-delete]");
  if (del) {
    const idx = +del.dataset.varianceDelete;
    if (!confirm(`Delete variance rule "${_editorState.variance[idx]?.name || ""}"?`)) return;
    _editorState.variance.splice(idx, 1);
    renderVarianceList();
    setDirty();
  }
}

function parseCSV(text) {
  return String(text || "")
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);
}

function addVarianceRule() {
  _editorState.variance.push({
    name: `rule_${_editorState.variance.length + 1}`,
    description: "",
    measure: "",
    reference: "budget",
    reference_label: "",
    rel_pct: 0.10,
    abs_value: 5000,
    group_by: [],
    reason_template: "",
    enabled: true,
  });
  renderVarianceList();
  setDirty();
}

/* ── Save / Delete ──────────────────────────────────────────────────────── */

async function saveCurrentRuleSet() {
  if (!_editorState.cube || !_editorState.dirty || _editorState.saving) return;
  _editorState.saving = true;
  updateSaveButton();
  setEditorStatus("Saving…", "muted");

  const payload = {
    cube: _editorState.cube,
    version: 1,
    description: _editorState.description,
    integrity: _editorState.integrity
      .filter(r => r.enabled || (r.columns && r.columns.length) || (r.group_by && r.group_by.length))
      .map(r => {
        const out = { name: r.name, enabled: !!r.enabled };
        if (r.columns && r.columns.length) out.columns = r.columns;
        if (r.group_by && r.group_by.length) out.group_by = r.group_by;
        return out;
      }),
    variance: _editorState.variance,
  };

  try {
    const res = await fetch(`${API}/api/anomaly/rules/${encodeURIComponent(_editorState.cube)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(`HTTP ${res.status}: ${detail}`);
    }
    _editorState.dirty = false;
    setEditorStatus("Saved", "ok");
    refreshAnomalyRulesList();
  } catch (err) {
    setEditorStatus(`Save failed: ${err.message}`, "error");
  } finally {
    _editorState.saving = false;
    updateSaveButton();
  }
}

async function deleteCurrentRuleSet() {
  if (!_editorState.cube) return;
  if (!confirm(`Delete the rule set for "${_editorState.cube}"? This cannot be undone.`)) return;
  try {
    const res = await fetch(`${API}/api/anomaly/rules/${encodeURIComponent(_editorState.cube)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _editorState.cube = null;
    _editorState.integrity = [];
    _editorState.variance = [];
    _editorState.description = "";
    _editorState.dirty = false;
    localStorage.removeItem(ANOMALY_ACTIVE_RULES_KEY);
    renderEditor();
    refreshAnomalyRulesList();
  } catch (err) {
    setEditorStatus(`Delete failed: ${err.message}`, "error");
  }
}

/* ── Wire up ──────────────────────────────────────────────────────────────
 *
 * Bound eagerly (no DOMContentLoaded wrapper) to match the rest of the app
 * (main.js / sidebar.js bind at parse time). Uses event delegation on
 * `document` so timing of element creation does not matter.
 */

document.addEventListener("click", event => {
  const modeBtn = event.target.closest("[data-mode-btn]");
  if (modeBtn) {
    event.preventDefault();
    setAppMode(modeBtn.dataset.modeBtn);
    return;
  }

  const tabBtn = event.target.closest(".anomaly-tab");
  if (tabBtn) {
    event.preventDefault();
    setAnomalyTab(tabBtn.dataset.anomalyTab);
    return;
  }

  const ruleCubeBtn = event.target.closest("[data-anomaly-rules-cube]");
  if (ruleCubeBtn) {
    selectAnomalyRulesCube(ruleCubeBtn.dataset.anomalyRulesCube);
    return;
  }

  const cubePick = event.target.closest("[data-cube-pick]");
  if (cubePick) {
    pickCubeForNewRuleSet(cubePick.dataset.cubePick);
    return;
  }

  // Buttons identified by id
  const tid = event.target.closest("button")?.id;
  if (tid === "anomalyNewRulesBtn"      || tid === "anomalyEmptyNewBtn")    { openCubePicker(); return; }
  if (tid === "anomalyCubePickerClose")                                      { closeCubePicker(); return; }
  if (tid === "anomalyNewScanBtn")                                           { setAnomalyTab("scan"); return; }
  if (tid === "anomalyAddVarianceBtn")                                       { addVarianceRule(); return; }
  if (tid === "anomalyEditorSave")                                           { saveCurrentRuleSet(); return; }
  if (tid === "anomalyEditorDelete")                                         { deleteCurrentRuleSet(); return; }

  // Variance card delete buttons (still want to handle these in onEditorClick
  // for the confirm dialog), so don't intercept here.

  // Close cube picker when clicking outside it.
  const popup = document.getElementById("anomalyCubePickerPopup");
  if (popup && !popup.classList.contains("hidden") && !popup.contains(event.target)) {
    closeCubePicker();
  }
});

document.addEventListener("input", event => {
  if (event.target.id === "anomalyCubePickerSearch") {
    renderCubePickerList();
    return;
  }
  const editor = document.getElementById("anomalyRulesEditor");
  if (editor?.contains(event.target)) onEditorInput(event);
});

document.addEventListener("change", event => {
  const editor = document.getElementById("anomalyRulesEditor");
  if (editor?.contains(event.target)) onEditorInput(event);
});

// Stop the cube picker popup from closing itself when clicked inside.
document.getElementById("anomalyCubePickerPopup")?.addEventListener("click", e => e.stopPropagation());

// Variance card delete buttons (have a confirm dialog inside).
document.getElementById("anomalyRulesEditor")?.addEventListener("click", onEditorClick);

// Apply current mode now that the DOM nodes exist (scripts live at end of body).
applyAppMode(getAppMode());
