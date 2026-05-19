document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") go();
  if (event.key === "Escape" && _currentAnalysisAbort) {
    event.preventDefault();
    stopCurrentAnalysis();
  }
});
document.getElementById("stopBtn")?.addEventListener("click", event => {
  event.preventDefault();
  stopCurrentAnalysis();
});

document.getElementById("q")?.addEventListener("input", () => {
  autoResizeQuestion();
  updateAnalyzeDisabled();
});
document.getElementById("q")?.addEventListener("keydown", event => {
  if (event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
  go();
});
document.getElementById("promptShell")?.addEventListener("pointerdown", () => setPromptSolid(true));
document.getElementById("q")?.addEventListener("focus", () => setPromptSolid(true));
document.addEventListener("pointerdown", event => {
  const prompt = document.getElementById("promptShell");
  if (!chatMode || !prompt || prompt.contains(event.target)) return;
  setPromptSolid(false);
});
document.getElementById("newChatBtn")?.addEventListener("click", newChat);
document.getElementById("newChatRail")?.addEventListener("click", newChat);
document.getElementById("shareBtn")?.addEventListener("click", event => {
  event.stopPropagation();
  toggleShareDropdown();
});
document.getElementById("shareDropdown")?.addEventListener("click", event => {
  event.stopPropagation();
});
document.getElementById("shareEmailForm")?.addEventListener("click", event => {
  event.stopPropagation();
});
document.getElementById("shareEmailOption")?.addEventListener("click", showShareEmailForm);
document.getElementById("shareLinkOption")?.addEventListener("click", copyShareLink);
document.getElementById("shareEmailTo")?.addEventListener("keydown", event => {
  if (event.key === "Enter") sendCurrentEmail("share");
});
document.getElementById("tm1SettingsBtn")?.addEventListener("click", event => {
  event.stopPropagation();
  toggleTm1Settings();
});
document.getElementById("tm1SettingsPopup")?.addEventListener("click", event => {
  event.stopPropagation();
});
document.getElementById("tm1SettingsClose")?.addEventListener("click", () => toggleTm1Settings(false));
document.getElementById("tm1SettingsForm")?.addEventListener("submit", saveTm1Settings);
document.getElementById("tm1PasswordToggle")?.addEventListener("click", toggleTm1PasswordVisibility);
document.getElementById("llmAdvancedToggle")?.addEventListener("click", () => toggleLlmAdvancedConfig());
document.getElementById("llmRefreshModels")?.addEventListener("click", refreshLlmModels);
document.getElementById("tm1ProfileGenerate")?.addEventListener("click", generateTm1Profile);
document.getElementById("cubeScopeBtn")?.addEventListener("click", event => {
  event.stopPropagation();
  toggleCubeScopePopup();
});
document.getElementById("cubeScopePopup")?.addEventListener("click", event => {
  event.stopPropagation();
});
document.getElementById("cubeScopeClose")?.addEventListener("click", () => toggleCubeScopePopup(false));
document.getElementById("cubeScopeClear")?.addEventListener("click", clearCubeScope);
document.getElementById("cubeScopeSearch")?.addEventListener("input", _renderCubeScopeList);
document.addEventListener("click", () => {
  toggleShareDropdown(false);
  toggleTm1Settings(false);
  toggleCubeScopePopup(false);
});
document.getElementById("collapseSidebarBtn")?.addEventListener("click", () => setSidebarCollapsed(true));
document.getElementById("openSearchRail")?.addEventListener("click", () => {
  setSidebarCollapsed(false);
  document.getElementById("chatSearch")?.focus();
});
document.getElementById("openChatsRail")?.addEventListener("click", () => {
  setSidebarCollapsed(false);
  setSectionCollapsed(CHATS_COLLAPSED_KEY, false);
});
document.getElementById("openEmailRail")?.addEventListener("click", () => {
  setSidebarCollapsed(false);
  setSectionCollapsed(EMAIL_COLLAPSED_KEY, false);
});
document.getElementById("sidebarToggle")?.addEventListener("click", () => {
  if (isSidebarCollapsed()) setSidebarCollapsed(false);
});
document.getElementById("toggleChatsBtn")?.addEventListener("click", () => {
  setSectionCollapsed(CHATS_COLLAPSED_KEY, !isSectionCollapsed(CHATS_COLLAPSED_KEY));
});
document.getElementById("toggleEmailBtn")?.addEventListener("click", () => {
  setSectionCollapsed(EMAIL_COLLAPSED_KEY, !isSectionCollapsed(EMAIL_COLLAPSED_KEY));
});
document.getElementById("chatSearch")?.addEventListener("input", renderHistory);
document.getElementById("clearHistoryBtn")?.addEventListener("click", () => {
  writeStore(HISTORY_KEY, [], MAX_HISTORY);
  renderHistory();
});
document.getElementById("clearEmailBtn")?.addEventListener("click", () => {
  writeStore(EMAIL_SENT_KEY, [], MAX_EMAIL_RECORDS);
  renderEmailRecords();
});

renderHistory();
renderEmailRecords();
applySidebarCollapsed(isSidebarCollapsed());
applySectionCollapsed(CHATS_COLLAPSED_KEY, isSectionCollapsed(CHATS_COLLAPSED_KEY));
applySectionCollapsed(EMAIL_COLLAPSED_KEY, isSectionCollapsed(EMAIL_COLLAPSED_KEY));
autoResizeQuestion();
updateAnalyzeDisabled();

// ── TM1 server status badge ───────────────────────────────────────────────────
function _formatSyncTime(isoStr) {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr.endsWith("Z") ? isoStr : isoStr + "Z");
    return "Last synced " + d.toLocaleString();
  } catch {
    return "Last synced " + isoStr;
  }
}

function _setTm1Badge(status, name, lastSyncedAt) {
  const dot      = document.getElementById("tm1Dot");
  const ping     = document.getElementById("tm1Ping");
  const nameEl   = document.getElementById("tm1InstanceName");
  const syncText = document.getElementById("tm1LastSync");
  if (!dot || !ping || !nameEl) return;

  nameEl.textContent = name || "TM1";
  if (syncText) syncText.textContent = _formatSyncTime(lastSyncedAt);

  if (status === "up") {
    dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-green-500";
    ping.className = "absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-60";
    nameEl.className = "max-w-[200px] truncate text-[11px] font-semibold text-cw-text";
  } else if (status === "down") {
    dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-red-500";
    ping.className = "absolute inline-flex h-full w-full rounded-full opacity-0";
    nameEl.className = "max-w-[200px] truncate text-[11px] font-semibold text-red-500";
  } else {
    dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-gray-300 animate-pulse";
    ping.className = "absolute inline-flex h-full w-full rounded-full opacity-0";
    nameEl.className = "max-w-[200px] truncate text-[11px] font-semibold text-cw-muted";
  }
}

async function fetchTm1Status() {
  try {
    const res = await fetch(`${API}/api/health`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    _setTm1Badge(data.tm1_status || "unknown", data.tm1_name || "", data.last_synced_at);
  } catch {
    _setTm1Badge("down", document.getElementById("tm1InstanceName")?.textContent || "TM1");
  }
}

// Single fetch on page load — status is then kept up-to-date passively
// by real API calls (analyze / views / sync-schema) with no polling overhead.
fetchTm1Status();

// TM1 settings popup
function _setTm1SettingsStatus(message, colorClass = "text-cw-muted") {
  const status = document.getElementById("tm1SettingsStatus");
  if (!status) return;
  status.textContent = message;
  status.className = `min-h-4 max-w-full break-words text-[11px] font-medium leading-4 ${colorClass}`;
}

function _mergeLlmModelCatalog(catalog) {
  if (!catalog || typeof catalog !== "object") return;
  Object.entries(catalog).forEach(([provider, meta]) => {
    if (!meta || typeof meta !== "object") return;
    LLM_MODEL_CATALOG[provider] = {
      ...(LLM_MODEL_CATALOG[provider] || {}),
      ...meta,
      models: Array.isArray(meta.models) ? meta.models : (LLM_MODEL_CATALOG[provider]?.models || []),
    };
  });
}

function _formatLlmWarnings(warnings) {
  if (!Array.isArray(warnings) || warnings.length === 0) return "";
  return warnings.filter(Boolean).join(" ");
}

function _populateLlmProviders(selectedProvider = "openai") {
  const providerEl = document.getElementById("llmCfgProvider");
  if (!providerEl) return;
  providerEl.innerHTML = "";
  Object.entries(LLM_MODEL_CATALOG).forEach(([key, meta]) => {
    const option = document.createElement("option");
    option.value = key;
    option.textContent = meta.label;
    providerEl.appendChild(option);
  });
  providerEl.value = LLM_MODEL_CATALOG[selectedProvider] ? selectedProvider : "openai";
}

function _populateLlmModels(provider, selectedModel = "") {
  const modelEl = document.getElementById("llmCfgModel");
  if (!modelEl) return;
  const models = LLM_MODEL_CATALOG[provider]?.models || [];
  modelEl.innerHTML = "";
  models.forEach(model => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    modelEl.appendChild(option);
  });
  if (selectedModel && !models.includes(selectedModel)) {
    const option = document.createElement("option");
    option.value = selectedModel;
    option.textContent = selectedModel;
    modelEl.appendChild(option);
  }
  modelEl.value = selectedModel || models[0] || "";
}

document.getElementById("llmCfgProvider")?.addEventListener("change", event => {
  _populateLlmModels(event.target.value);
});

function toggleLlmAdvancedConfig(forceOpen) {
  const panel = document.getElementById("llmAdvancedConfig");
  const button = document.getElementById("llmAdvancedToggle");
  const icon = document.getElementById("llmAdvancedIcon");
  if (!panel || !button) return;
  const shouldOpen = forceOpen ?? panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  if (icon) icon.className = shouldOpen ? "fa-solid fa-chevron-up text-[10px]" : "fa-solid fa-chevron-down text-[10px]";
}

function _setTm1ConfigForm(config) {
  const provider = config.llm_provider || "openai";
  _populateLlmProviders(provider);
  _populateLlmModels(provider, config.llm_model || "");
  _setNumberInput("llmCfgCubeSelectTemp", config.cube_select_temperature, 0);
  _setNumberInput("llmCfgMdxTemp", config.mdx_temperature, 0);
  _setNumberInput("llmCfgAttributeTemp", config.attribute_intent_temperature, 0);
  _setNumberInput("llmCfgProfileTemp", config.semantic_profile_temperature, 0.2);
  _setNumberInput("llmCfgAnalysisTemp", config.analysis_temperature, 0.2);
  _setNumberInput("llmCfgSuggestionsTemp", config.suggestions_temperature, 0.4);
  document.getElementById("tm1CfgAddress").value = config.address || "";
  document.getElementById("tm1CfgPort").value = config.port || "";
  document.getElementById("tm1CfgUser").value = config.user || "";
  const passwordInput = document.getElementById("tm1CfgPassword");
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.placeholder = config.has_password ? "Saved password unchanged" : "";
  }
  document.getElementById("tm1CfgNamespace").value = config.namespace || "";
  document.getElementById("tm1CfgSsl").checked = Boolean(config.ssl);
  document.getElementById("tm1CfgVerify").checked = Boolean(config.verify);
  document.getElementById("tm1CfgAsync").checked = Boolean(config.async_requests_mode);
}

function _setNumberInput(id, value, fallback) {
  const input = document.getElementById(id);
  if (!input) return;
  input.value = String(Number.isFinite(Number(value)) ? Number(value) : fallback);
}

function _numberInput(id, fallback) {
  const value = Number(document.getElementById(id)?.value);
  return Number.isFinite(value) ? value : fallback;
}

function _getTm1ConfigForm() {
  return {
    llm_provider: document.getElementById("llmCfgProvider")?.value || "openai",
    llm_model: document.getElementById("llmCfgModel")?.value.trim() || "",
    cube_select_temperature: _numberInput("llmCfgCubeSelectTemp", 0),
    mdx_temperature: _numberInput("llmCfgMdxTemp", 0),
    attribute_intent_temperature: _numberInput("llmCfgAttributeTemp", 0),
    semantic_profile_temperature: _numberInput("llmCfgProfileTemp", 0.2),
    analysis_temperature: _numberInput("llmCfgAnalysisTemp", 0.2),
    suggestions_temperature: _numberInput("llmCfgSuggestionsTemp", 0.4),
    address: document.getElementById("tm1CfgAddress")?.value.trim() || "localhost",
    port: Number(document.getElementById("tm1CfgPort")?.value || 9510),
    user: document.getElementById("tm1CfgUser")?.value.trim() || "admin",
    password: document.getElementById("tm1CfgPassword")?.value || "",
    namespace: document.getElementById("tm1CfgNamespace")?.value.trim() || "",
    ssl: Boolean(document.getElementById("tm1CfgSsl")?.checked),
    verify: Boolean(document.getElementById("tm1CfgVerify")?.checked),
    async_requests_mode: Boolean(document.getElementById("tm1CfgAsync")?.checked),
  };
}

function toggleTm1PasswordVisibility() {
  const input = document.getElementById("tm1CfgPassword");
  const button = document.getElementById("tm1PasswordToggle");
  const icon = button?.querySelector("i");
  if (!input || !button || !icon) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  button.setAttribute("aria-label", show ? "Hide password" : "Show password");
  button.setAttribute("aria-pressed", String(show));
  icon.className = show ? "fa-regular fa-eye-slash text-[12px]" : "fa-regular fa-eye text-[12px]";
}

async function loadTm1Settings() {
  _setTm1SettingsStatus("Loading...");
  try {
    const res = await fetch(`${API}/api/tm1-config`);
    const config = await res.json();
    if (!res.ok) throw new Error(config.detail || "Load failed");
    _mergeLlmModelCatalog(config.llm_model_catalog);
    _setTm1ConfigForm(config);
    const warnings = _formatLlmWarnings(config.llm_warnings);
    _setTm1SettingsStatus(warnings, warnings ? "text-amber-600" : "text-cw-muted");
  } catch (err) {
    _setTm1SettingsStatus(err.message || "Load failed", "text-red-600");
  }
}

function toggleTm1Settings(forceOpen) {
  const popup = document.getElementById("tm1SettingsPopup");
  const button = document.getElementById("tm1SettingsBtn");
  if (!popup || !button) return;
  const shouldOpen = forceOpen ?? popup.classList.contains("hidden");
  popup.classList.toggle("hidden", !shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  if (shouldOpen) loadTm1Settings();
}

async function saveTm1Settings(event) {
  event.preventDefault();
  const button = document.getElementById("tm1SettingsSave");
  if (!button || button.disabled) return;
  button.disabled = true;
  _setTm1SettingsStatus("Saving and syncing...");
  try {
    const res = await fetch(`${API}/api/tm1-config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_getTm1ConfigForm()),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Save failed");
    _mergeLlmModelCatalog(data.llm_model_catalog);
    _setTm1ConfigForm(data.config || _getTm1ConfigForm());
    const warnings = _formatLlmWarnings(data.llm_warnings);
    if (warnings) {
      _setTm1SettingsStatus(`Saved. Synced ${data.cubes ?? 0} cubes. Warning: ${warnings}`, "text-amber-600");
    } else {
      _setTm1SettingsStatus(`Saved. Synced ${data.cubes ?? 0} cubes.`, "text-cw-green");
    }
    _setTm1Badge(data.tm1_status || "up", data.tm1_name || "", data.last_synced_at);
    _suggestionsLoaded = false;
    invalidateCubeScopeCache();
    clearCubeScope();
    fetchTm1Status();
    fetchSuggestions();
  } catch (err) {
    _setTm1SettingsStatus(err.message || "Save failed", "text-red-600");
  } finally {
    button.disabled = false;
  }
}

async function refreshLlmModels() {
  const button = document.getElementById("llmRefreshModels");
  if (!button || button.disabled) return;
  const provider = document.getElementById("llmCfgProvider")?.value || "openai";
  const currentModel = document.getElementById("llmCfgModel")?.value || "";
  button.disabled = true;
  _setTm1SettingsStatus(`Refreshing ${provider} model list...`);
  try {
    const res = await fetch(`${API}/api/llm-models/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model: currentModel }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Refresh failed");
    _mergeLlmModelCatalog(data.llm_model_catalog);
    _populateLlmModels(provider, currentModel);
    const warnings = _formatLlmWarnings(data.llm_warnings);
    const count = Array.isArray(data.models) ? data.models.length : 0;
    const source = data.source_type === "official_api" ? "official API" : "cached/fallback list";
    const stamp = data.refreshed_at ? ` (refreshed ${new Date(data.refreshed_at).toLocaleTimeString()})` : "";
    const baseMsg = `${count} ${provider} model${count === 1 ? "" : "s"} from ${source}${stamp}.`;
    if (warnings) {
      _setTm1SettingsStatus(`${baseMsg} ${warnings}`, "text-amber-600");
    } else {
      _setTm1SettingsStatus(baseMsg, "text-cw-green");
    }
  } catch (err) {
    _setTm1SettingsStatus(err.message || "Refresh failed", "text-red-600");
  } finally {
    button.disabled = false;
  }
}

async function generateTm1Profile() {
  const button = document.getElementById("tm1ProfileGenerate");
  if (!button || button.disabled) return;
  button.disabled = true;
  _setTm1SettingsStatus("Generating semantic profile...");
  try {
    const res = await fetch(`${API}/api/model-profile/generate`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Profile generation failed");
    if (data.fallback) {
      _setTm1SettingsStatus(
        `Profile saved with warning: ${data.warning || "AI profile generation failed; using fallback + finance semantics."} ${data.profile_file}`,
        "text-amber-600"
      );
    } else if (data.warning) {
      _setTm1SettingsStatus(`Profile saved with warning: ${data.warning}`, "text-amber-600");
    } else {
      _setTm1SettingsStatus(`Profile saved: ${data.profile_file}`, "text-cw-green");
    }
  } catch (err) {
    _setTm1SettingsStatus(err.message || "Profile generation failed", "text-red-600");
  } finally {
    button.disabled = false;
  }
}

// ── Suggested questions ───────────────────────────────────────────────────────
let _suggestionsLoaded = false;

async function fetchSuggestions() {
  if (_suggestionsLoaded) return;
  try {
    const res = await fetch(`${API}/api/suggestions`);
    if (!res.ok) throw new Error();
    const { suggestions } = await res.json();
    const container = document.getElementById("suggestedContent");
    const loading   = document.getElementById("suggestedLoading");
    if (!container || !Array.isArray(suggestions)) return;
    // Remove loading text and any previously rendered pills
    loading?.remove();
    container.querySelectorAll("button.pill").forEach(b => b.remove());
    suggestions.forEach(q => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = cls.pill;
      btn.textContent = q;
      btn.addEventListener("click", () => setQ(q));
      container.appendChild(btn);
    });
    _suggestionsLoaded = true;
  } catch {
    document.getElementById("suggestedLoading")?.remove();
  }
}

fetchSuggestions();

// ── Event delegation for dynamically rendered content ─────────────────────────

document.getElementById("out")?.addEventListener("click", event => {
  const btn = event.target.closest("[data-action='download-excel']");
  if (!btn) return;
  const msgId = btn.dataset.msgId;
  const srcIdx = Number(btn.dataset.srcIdx);
  if (msgId) downloadExcel(msgId, srcIdx);
});

document.getElementById("historyList")?.addEventListener("click", event => {
  const btn = event.target.closest("[data-action='open-history']");
  if (!btn) return;
  const id = btn.dataset.id;
  if (id) openHistory(id);
});
