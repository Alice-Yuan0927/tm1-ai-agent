function isSidebarCollapsed()    { return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true"; }
function isSectionCollapsed(key) { return localStorage.getItem(key) === "true"; }

function setSidebarCollapsed(collapsed) {
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
  applySidebarCollapsed(collapsed);
}

function applySidebarCollapsed(collapsed) {
  const sidebar = document.getElementById("appSidebar");
  const header  = document.getElementById("sidebarHeader");
  const main    = document.getElementById("mainContent");
  const toggle  = document.getElementById("sidebarToggle");

  sidebar?.classList.toggle("w-[260px]", !collapsed);
  sidebar?.classList.toggle("w-12",       collapsed);
  header?.classList.toggle("px-4",       !collapsed);
  header?.classList.toggle("px-1",        collapsed);
  main?.style.removeProperty("transform");
  document.querySelectorAll(".sidebar-expanded").forEach(el => el.classList.toggle("hidden",  collapsed));
  document.querySelectorAll(".sidebar-collapsed").forEach(el => el.classList.toggle("hidden", !collapsed));
  if (toggle) toggle.title = collapsed ? "Expand sidebar" : "Cubewise";
  updatePromptDock();
}

function setSectionCollapsed(key, collapsed) {
  localStorage.setItem(key, String(collapsed));
  applySectionCollapsed(key, collapsed);
}

function applySectionCollapsed(key, collapsed) {
  const isChats = key === CHATS_COLLAPSED_KEY;
  const list    = document.getElementById(isChats ? "historyList" : "emailList");
  const button  = document.getElementById(isChats ? "toggleChatsBtn" : "toggleEmailBtn");
  const clear   = document.getElementById(isChats ? "clearHistoryBtn" : "clearEmailBtn");
  const icon    = button?.querySelector("i");
  list?.classList.toggle("hidden",  collapsed);
  clear?.classList.toggle("hidden", collapsed);
  button?.setAttribute("aria-expanded", String(!collapsed));
  if (icon) icon.className = collapsed ? "fa-solid fa-chevron-down" : "fa-solid fa-chevron-up";
}

function formatHistoryGroup(date) {
  const itemDate  = new Date(date);
  const today     = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth()    === b.getMonth()    &&
    a.getDate()     === b.getDate();
  if (sameDay(itemDate, today))     return "Today";
  if (sameDay(itemDate, yesterday)) return "Yesterday";
  return itemDate.toLocaleDateString("en-US", {
    month: "long", day: "numeric",
    year: itemDate.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

function formatHistoryTime(date) {
  return new Date(date).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function renderHistory() {
  const list = document.getElementById("historyList");
  if (!list) return;
  const query = document.getElementById("chatSearch")?.value.trim().toLowerCase() || "";
  const history = getHistory().filter(item => {
    const latest = getLatestResult(item);
    const text = [
      getItemMessages(item).map(m => m.question).join(" "),
      latest.chosen_cube || "",
      latest.analysis,
    ].join(" ").toLowerCase();
    return text.includes(query);
  });

  if (!history.length) {
    list.innerHTML = `<div class="px-3 py-5 text-center text-xs text-cw-muted">${query ? "No matching chats" : "No chats yet"}</div>`;
    return;
  }

  let lastGroup = "";
  list.innerHTML = history.map(item => {
    const latest    = getLatestResult(item);
    const messages  = getItemMessages(item);
    const first     = messages[0] || latest;
    const timestamp = item.updatedAt || item.createdAt;
    const group     = formatHistoryGroup(timestamp);
    const time      = formatHistoryTime(timestamp);
    const groupHeader = group === lastGroup ? "" :
      `<div class="px-3 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-cw-muted">${esc(group)}</div>`;
    lastGroup = group;
    return `${groupHeader}<button type="button" class="mb-1 block w-full rounded-lg px-3 py-2.5 text-left transition hover:bg-cw-bg" data-action="open-history" data-id="${esc(item.id)}">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(first.question)}</div>
      <div class="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-cw-muted">
        <span class="truncate">${latest.type === "clarification" ? "Clarification needed" : esc(latest.chosen_cube)}</span>
        <span class="shrink-0">${esc(time)}</span>
      </div>
    </button>`;
  }).join("");
}

function renderEmailRecords() {
  const list = document.getElementById("emailList");
  if (!list) return;
  const records = getEmailRecords();
  if (!records.length) {
    list.innerHTML = '<div class="px-3 py-5 text-center text-xs text-cw-muted">No sent emails yet</div>';
    return;
  }
  list.innerHTML = records.map(record => {
    const date = new Date(record.sentAt).toLocaleString("en-US", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    return `<div class="mb-1 rounded-lg px-3 py-2.5 transition hover:bg-cw-bg">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(record.to)}</div>
      <div class="mt-0.5 truncate text-[11px] text-cw-muted">${esc(record.question)}</div>
      <div class="mt-1 text-[11px] text-cw-muted">${esc(date)}</div>
    </div>`;
  }).join("");
}

function newChat() {
  currentResult   = null;
  currentChatId   = null;
  currentMessages = [];
  setChatMode(false);
  document.getElementById("q").value = "";
  updateAnalyzeDisabled();
  autoResizeQuestion();
  document.getElementById("out").innerHTML = "";
  document.getElementById("q").focus();
  updateShareStatus("");
}

function openHistory(id) {
  const item = getHistory().find(e => e.id === id);
  if (!item) return;
  currentChatId   = item.id;
  currentMessages = getItemMessages(item);
  currentResult   = currentMessages[currentMessages.length - 1] || null;
  setChatMode(true);
  document.getElementById("q").value = "";
  updateAnalyzeDisabled();
  autoResizeQuestion();
  renderConversation();
  scrollToLatest();
}
