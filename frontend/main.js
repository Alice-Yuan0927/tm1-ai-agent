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
document.addEventListener("click", () => toggleShareDropdown(false));
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
  if (!isoStr) return "Never synced · Click to sync";
  try {
    const d = new Date(isoStr.endsWith("Z") ? isoStr : isoStr + "Z");
    return "Last synced: " + d.toLocaleString();
  } catch {
    return "Last synced: " + isoStr;
  }
}

function _setTm1Badge(status, name, lastSyncedAt) {
  const dot      = document.getElementById("tm1Dot");
  const ping     = document.getElementById("tm1Ping");
  const nameEl   = document.getElementById("tm1InstanceName");
  const badge    = document.getElementById("tm1Badge");
  if (!dot || !ping || !nameEl) return;

  nameEl.textContent = name || "TM1";
  if (badge) badge.title = _formatSyncTime(lastSyncedAt);

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
    if (badge) badge.title = _formatSyncTime(data.last_synced_at);
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
