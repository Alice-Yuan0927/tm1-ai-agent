document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") go();
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
document.getElementById("tm1ProfileGenerate")?.addEventListener("click", generateTm1Profile);
document.addEventListener("click", () => {
  toggleShareDropdown(false);
  toggleTm1Settings(false);
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

// ── Schema sync ───────────────────────────────────────────────────────────────
async function syncSchema() {
  const badge   = document.getElementById("tm1Badge");
  const dot     = document.getElementById("tm1Dot");
  const ping    = document.getElementById("tm1Ping");
  const label   = document.getElementById("tm1SyncLabel");
  if (!badge || badge.disabled) return;

  badge.disabled = true;
  dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-cw-blue animate-spin";
  ping.className = "absolute inline-flex h-full w-full rounded-full opacity-0";
  label.textContent = "Syncing…";
  label.classList.remove("hidden");

  try {
    const res = await fetch(`${API}/api/sync-schema`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Sync failed");

    // Success — flash green briefly then restore normal badge
    dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-green-500";
    ping.className = "absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-60";
    label.textContent = `Done · ${data.cubes ?? ""} cubes`;
    const syncText = document.getElementById("tm1LastSync");
    if (syncText) syncText.textContent = _formatSyncTime(data.last_synced_at);
    // Refresh suggestions since schema may have changed
    _suggestionsLoaded = false;
    fetchSuggestions();
  } catch (err) {
    dot.className  = "relative inline-flex h-2 w-2 rounded-full bg-red-500";
    ping.className = "absolute inline-flex h-full w-full rounded-full opacity-0";
    label.textContent = "Sync failed";
  } finally {
    badge.disabled = false;
    // Restore normal status badge after 3 seconds
    setTimeout(() => {
      label.textContent = "";
      label.classList.add("hidden");
      fetchTm1Status();
    }, 3000);
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
  status.className = `min-h-4 text-[11px] font-medium ${colorClass}`;
}

function _setTm1ConfigForm(config) {
  document.getElementById("tm1CfgAddress").value = config.address || "";
  document.getElementById("tm1CfgPort").value = config.port || "";
  document.getElementById("tm1CfgUser").value = config.user || "";
  document.getElementById("tm1CfgPassword").value = config.password || "";
  document.getElementById("tm1CfgNamespace").value = config.namespace || "";
  document.getElementById("tm1CfgSsl").checked = Boolean(config.ssl);
  document.getElementById("tm1CfgVerify").checked = Boolean(config.verify);
  document.getElementById("tm1CfgAsync").checked = Boolean(config.async_requests_mode);
}

function _getTm1ConfigForm() {
  return {
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
    _setTm1ConfigForm(config);
    _setTm1SettingsStatus("");
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
    _setTm1ConfigForm(data.config || _getTm1ConfigForm());
    _setTm1SettingsStatus(`Saved. Synced ${data.cubes ?? 0} cubes.`, "text-cw-green");
    _suggestionsLoaded = false;
    fetchTm1Status();
    fetchSuggestions();
  } catch (err) {
    _setTm1SettingsStatus(err.message || "Save failed", "text-red-600");
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
    _setTm1SettingsStatus(`Profile saved: ${data.profile_file}`, "text-cw-green");
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
