const API = "http://localhost:8000";
const HISTORY_KEY = "tm1-ai-chat-history";
const EMAIL_SENT_KEY = "tm1-ai-email-sent";
const SIDEBAR_COLLAPSED_KEY = "tm1-ai-sidebar-collapsed";
const CHATS_COLLAPSED_KEY = "tm1-ai-chats-collapsed";
const EMAIL_COLLAPSED_KEY = "tm1-ai-email-collapsed";
const MAX_HISTORY = 20;
const MAX_EMAIL_RECORDS = 30;

let currentResult = null;
let chatMode = false;

const cls = {
  pill: "rounded-full border border-cw-blueMid bg-white px-3 py-[3px] text-xs font-medium text-cw-blueText transition hover:border-cw-blue hover:bg-cw-blueLite hover:text-cw-blue",
  card: "overflow-hidden rounded-2xl border border-cw-border bg-white shadow-soft",
  head: "flex items-center gap-2.5 border-b border-cw-borderLow bg-cw-bg px-5 py-[11px]",
  title: "text-[13px] font-semibold text-cw-text",
  body: "px-[22px] py-[18px]",
  label: "mb-[3px] text-[10px] font-semibold uppercase tracking-[0.1em] text-cw-muted",
  monoValue: "break-all font-mono text-[12.5px] font-medium text-cw-blue",
  badge: "ml-auto rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-[0.02em]",
};

document.querySelectorAll(".pill").forEach(el => {
  el.className = cls.pill;
});

const esc = value => String(value ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

const setQ = text => {
  const input = document.getElementById("q");
  input.value = text;
  autoResizeQuestion();
  updateAnalyzeDisabled();
  input.focus();
};

function autoResizeQuestion() {
  const input = document.getElementById("q");
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 176)}px`;
}

function updateAnalyzeDisabled() {
  const input = document.getElementById("q");
  const button = document.getElementById("runBtn");
  if (!input || !button) return;
  button.disabled = input.value.trim().length === 0;
}

function readStore(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "[]");
  } catch {
    return [];
  }
}

function writeStore(key, items, max) {
  localStorage.setItem(key, JSON.stringify(items.slice(0, max)));
}

function getHistory() {
  return readStore(HISTORY_KEY);
}

function getEmailRecords() {
  return readStore(EMAIL_SENT_KEY);
}

function saveHistory(result) {
  const item = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    createdAt: new Date().toISOString(),
    result,
  };
  writeStore(HISTORY_KEY, [item, ...getHistory()], MAX_HISTORY);
  renderHistory();
}

function saveEmailRecord(to) {
  if (!currentResult) return;
  const item = {
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    sentAt: new Date().toISOString(),
    to,
    question: currentResult.question,
    chosenCube: currentResult.chosen_cube,
    chosenView: currentResult.chosen_view,
  };
  writeStore(EMAIL_SENT_KEY, [item, ...getEmailRecords()], MAX_EMAIL_RECORDS);
  renderEmailRecords();
}

function updateShareStatus(message, colorClass = "text-cw-muted") {
  const status = document.getElementById("shareEmailStatus");
  if (!status) return;
  status.textContent = message;
  status.className = `mt-2 min-h-4 px-1 text-[11px] font-medium ${colorClass}`;
}

function toggleShareDropdown(forceOpen) {
  const dropdown = document.getElementById("shareDropdown");
  const button = document.getElementById("shareBtn");
  if (!dropdown || !button) return;

  const shouldOpen = forceOpen ?? dropdown.classList.contains("hidden");
  dropdown.classList.toggle("hidden", !shouldOpen);
  button.setAttribute("aria-expanded", String(shouldOpen));
  if (!shouldOpen) return;
  updateShareStatus("");
}

function showShareEmailForm() {
  const form = document.getElementById("shareEmailForm");
  if (!form) return;
  const shouldOpen = form.classList.contains("hidden");
  form.classList.toggle("hidden", !shouldOpen);
  updateShareStatus("");
  if (shouldOpen) {
    document.getElementById("shareEmailTo")?.focus();
  }
}

async function copyShareLink() {
  const link = window.location.href;
  try {
    await navigator.clipboard.writeText(link);
    updateShareStatus("Link copied.", "text-cw-green");
  } catch {
    updateShareStatus(link, "text-cw-muted");
  }
}

function setLoading(on) {
  const button = document.getElementById("runBtn");
  if (button) {
    button.disabled = on || !document.getElementById("q")?.value.trim();
  }
  document.getElementById("sp")?.classList.toggle("hidden", !on);
  const label = document.getElementById("bl");
  if (label) label.textContent = on ? "" : "->";
}

function updatePromptDock() {
  const prompt = document.getElementById("promptShell");
  if (!prompt || !chatMode) return;
  const sidebarWidth = isSidebarCollapsed() ? "3rem" : "260px";
  prompt.style.left = `calc(${sidebarWidth} + 1.5rem)`;
  prompt.style.width = `calc(100vw - ${sidebarWidth} - 3rem)`;
}

function setPromptSolid(solid) {
  const prompt = document.getElementById("promptShell");
  if (!prompt || !chatMode) return;

  prompt.classList.toggle("opacity-60", !solid);
  prompt.classList.toggle("opacity-100", solid);
  prompt.style.opacity = solid ? "1" : "0.62";
}

function clearQuestionInput() {
  const input = document.getElementById("q");
  if (!input) return;
  input.value = "";
  autoResizeQuestion();
  updateAnalyzeDisabled();
}

function setChatMode(on) {
  chatMode = on;

  const main = document.getElementById("mainContent");
  const wrap = document.getElementById("chatWrap");
  const intro = document.getElementById("heroIntro");
  const suggested = document.getElementById("suggestedContent");
  const prompt = document.getElementById("promptShell");
  const out = document.getElementById("out");
  const planningBadge = document.getElementById("planningBadge");
  const shareMenu = document.getElementById("shareMenu");

  intro?.classList.toggle("hidden", on);
  suggested?.classList.toggle("hidden", on);
  planningBadge?.classList.toggle("hidden", on);
  shareMenu?.classList.toggle("hidden", !on);
  if (intro) intro.style.display = on ? "none" : "";
  if (suggested) suggested.style.display = on ? "none" : "";
  if (planningBadge) planningBadge.style.display = on ? "none" : "";
  if (shareMenu) shareMenu.style.display = on ? "" : "none";
  if (!on) {
    toggleShareDropdown(false);
    document.getElementById("shareEmailForm")?.classList.add("hidden");
  }
  main?.classList.toggle("items-center", !on);
  main?.classList.toggle("items-stretch", on);
  main?.classList.toggle("pb-20", !on);
  main?.classList.toggle("pb-36", on);
  main?.classList.toggle("pt-8", !on);
  main?.classList.toggle("pt-20", on);
  wrap?.classList.toggle("-translate-y-8", !on);
  wrap?.classList.toggle("max-w-[760px]", !on);
  wrap?.classList.toggle("max-w-[900px]", on);
  out?.classList.toggle("pb-12", on);

  prompt?.classList.toggle("fixed", on);
  prompt?.classList.toggle("bottom-6", on);
  prompt?.classList.toggle("right-6", on);
  prompt?.classList.toggle("z-30", on);
  prompt?.classList.toggle("mx-auto", on);
  prompt?.classList.toggle("max-w-[780px]", on);
  prompt?.classList.toggle("mb-6", !on);
  prompt?.classList.toggle("mb-0", on);
  prompt?.classList.toggle("backdrop-blur-md", on);

  if (prompt) {
    if (on) {
      updatePromptDock();
      prompt.style.position = "fixed";
      prompt.style.right = "1.5rem";
      prompt.style.bottom = "1.5rem";
      prompt.style.zIndex = "30";
      prompt.style.maxWidth = "780px";
      setPromptSolid(false);
    } else {
      prompt.style.removeProperty("left");
      prompt.style.removeProperty("width");
      prompt.style.removeProperty("opacity");
      prompt.style.removeProperty("position");
      prompt.style.removeProperty("right");
      prompt.style.removeProperty("bottom");
      prompt.style.removeProperty("z-index");
      prompt.style.removeProperty("max-width");
      prompt.classList.remove("opacity-60", "opacity-100", "backdrop-blur-md");
    }
  }
}

function fmt(value) {
  if (typeof value !== "number") return esc(String(value));
  return Number.isInteger(value)
    ? value.toLocaleString()
    : value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function buildTable(rows) {
  if (!rows?.length) {
    return '<p class="text-[13px] text-cw-muted">No preview available</p>';
  }

  const keys = Object.keys(rows[0]);
  const head = keys.map(key => `<th class="whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-left text-[10.5px] font-semibold uppercase tracking-[0.04em] text-cw-muted">${esc(key)}</th>`).join("");
  const body = rows.map(row => {
    const cells = keys.map(key => {
      const numeric = typeof row[key] === "number";
      const align = numeric ? "text-right font-medium text-cw-blueText" : "text-cw-text";
      return `<td class="whitespace-nowrap border-b border-cw-borderLow px-3 py-[7px] ${align}">${fmt(row[key])}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${cells}</tr>`;
  }).join("");

  return `<div class="overflow-x-auto rounded-[10px] border border-cw-border"><table class="w-full border-collapse font-mono text-[11.5px]"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function isSidebarCollapsed() {
  return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
}

function isSectionCollapsed(key) {
  return localStorage.getItem(key) === "true";
}

function setSidebarCollapsed(collapsed) {
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
  applySidebarCollapsed(collapsed);
}

function applySidebarCollapsed(collapsed) {
  const sidebar = document.getElementById("appSidebar");
  const header = document.getElementById("sidebarHeader");
  const main = document.getElementById("mainContent");
  const toggle = document.getElementById("sidebarToggle");

  sidebar?.classList.toggle("w-[260px]", !collapsed);
  sidebar?.classList.toggle("w-12", collapsed);
  header?.classList.toggle("px-4", !collapsed);
  header?.classList.toggle("px-1", collapsed);
  main?.style.removeProperty("transform");
  document.querySelectorAll(".sidebar-expanded").forEach(el => el.classList.toggle("hidden", collapsed));
  document.querySelectorAll(".sidebar-collapsed").forEach(el => el.classList.toggle("hidden", !collapsed));

  if (toggle) {
    toggle.title = collapsed ? "Expand sidebar" : "Cubewise";
  }
  updatePromptDock();
}

function setSectionCollapsed(key, collapsed) {
  localStorage.setItem(key, String(collapsed));
  applySectionCollapsed(key, collapsed);
}

function applySectionCollapsed(key, collapsed) {
  const isChats = key === CHATS_COLLAPSED_KEY;
  const list = document.getElementById(isChats ? "historyList" : "emailList");
  const button = document.getElementById(isChats ? "toggleChatsBtn" : "toggleEmailBtn");
  const clear = document.getElementById(isChats ? "clearHistoryBtn" : "clearEmailBtn");
  const icon = button?.querySelector("i");

  list?.classList.toggle("hidden", collapsed);
  clear?.classList.toggle("hidden", collapsed);
  button?.setAttribute("aria-expanded", String(!collapsed));
  if (icon) {
    icon.className = collapsed ? "fa-solid fa-chevron-down" : "fa-solid fa-chevron-up";
  }
}

function newChat() {
  currentResult = null;
  setChatMode(false);
  document.getElementById("q").value = "";
  updateAnalyzeDisabled();
  autoResizeQuestion();
  document.getElementById("out").innerHTML = "";
  document.getElementById("q").focus();
  updateShareStatus("");
}

function openHistory(id) {
  const item = getHistory().find(entry => entry.id === id);
  if (!item) return;
  currentResult = item.result;
  setChatMode(true);
  document.getElementById("q").value = item.result.question || "";
  updateAnalyzeDisabled();
  autoResizeQuestion();
  render(item.result);
}

function formatHistoryGroup(date) {
  const itemDate = new Date(date);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);

  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();

  if (sameDay(itemDate, today)) return "Today";
  if (sameDay(itemDate, yesterday)) return "Yesterday";

  return itemDate.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: itemDate.getFullYear() === today.getFullYear() ? undefined : "numeric",
  });
}

function formatHistoryTime(date) {
  return new Date(date).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
  });
}

function renderHistory() {
  const list = document.getElementById("historyList");
  if (!list) return;

  const query = document.getElementById("chatSearch")?.value.trim().toLowerCase() || "";
  const history = getHistory().filter(item => {
    const text = [
      item.result.question,
      item.result.chosen_cube,
      item.result.chosen_view,
      item.result.analysis,
    ].join(" ").toLowerCase();
    return text.includes(query);
  });

  if (!history.length) {
    list.innerHTML = `<div class="px-3 py-5 text-center text-xs text-cw-muted">${query ? "No matching chats" : "No chats yet"}</div>`;
    return;
  }

  let lastGroup = "";
  list.innerHTML = history.map(item => {
    const group = formatHistoryGroup(item.createdAt);
    const time = formatHistoryTime(item.createdAt);
    const groupHeader = group === lastGroup
      ? ""
      : `<div class="px-3 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-cw-muted">${esc(group)}</div>`;
    lastGroup = group;

    return `${groupHeader}<button type="button" class="mb-1 block w-full rounded-lg px-3 py-2.5 text-left transition hover:bg-cw-bg" onclick="openHistory('${esc(item.id)}')">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(item.result.question)}</div>
      <div class="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-cw-muted">
        <span class="truncate">${esc(item.result.chosen_cube)} / ${esc(item.result.chosen_view)}</span>
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
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    return `<div class="mb-1 rounded-lg px-3 py-2.5 transition hover:bg-cw-bg">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(record.to)}</div>
      <div class="mt-0.5 truncate text-[11px] text-cw-muted">${esc(record.question)}</div>
      <div class="mt-1 text-[11px] text-cw-muted">${esc(date)}</div>
    </div>`;
  }).join("");
}

function skeleton(question = "") {
  return `<div class="flex justify-end">
    <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(question)}</div>
  </div>
  <div class="max-w-[760px] rounded-2xl bg-white p-[22px] shadow-soft">
    <div class="mb-2.5 h-2.5 w-2/5 animate-pulse rounded-full bg-cw-border"></div>
    <div class="mb-2.5 h-2.5 w-4/5 animate-pulse rounded-full bg-cw-border"></div>
    <div class="mb-2.5 h-2.5 w-2/3 animate-pulse rounded-full bg-cw-border"></div>
    <div class="h-2.5 w-4/5 animate-pulse rounded-full bg-cw-border"></div>
  </div>`;
}

async function go() {
  const question = document.getElementById("q").value.trim();
  if (!question) {
    document.getElementById("q").focus();
    return;
  }

  setChatMode(true);
  setLoading(true);
  document.getElementById("out").innerHTML = skeleton(question);
  clearQuestionInput();

  try {
    const res = await fetch(`${API}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Failed");
    }

    const data = await res.json();
    currentResult = data;
    saveHistory(data);
    render(data);
  } catch (err) {
    document.getElementById("out").innerHTML = `<div class="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-[18px] py-3.5 text-[13px] text-red-700 shadow-soft"><span>Warning:</span><span>${esc(err.message)}</span></div>`;
  } finally {
    setLoading(false);
  }
}

async function sendCurrentEmail(source = "share") {
  if (!currentResult) {
    updateShareStatus("Analyze a question before sharing.", "text-cw-muted");
    return;
  }

  const input = source === "share" ? document.getElementById("shareEmailTo") : document.getElementById("emailTo");
  const button = source === "share" ? document.getElementById("shareEmailBtn") : document.getElementById("emailBtn");
  if (!input || !button) return;
  const to = input.value.trim();

  if (!to) {
    input.focus();
    updateShareStatus("Enter recipient.", "text-red-600");
    return;
  }

  button.disabled = true;
  updateShareStatus("Sending...", "text-cw-muted");

  try {
    const res = await fetch(`${API}/api/send-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to,
        question: currentResult.question,
        chosen_cube: currentResult.chosen_cube,
        chosen_view: currentResult.chosen_view,
        reasoning: currentResult.reasoning,
        data_row_count: currentResult.data_row_count,
        data_preview: currentResult.data_preview || [],
        analysis: currentResult.analysis,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Failed");
    }

    saveEmailRecord(to);
    updateShareStatus("Sent.", "text-cw-green");
  } catch (err) {
    updateShareStatus(err.message, "text-red-600");
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  setChatMode(true);
  updateShareStatus("");
  document.getElementById("out").innerHTML =
  `<div class="flex justify-end">
    <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(data.question)}</div>
  </div>

  <article class="max-w-[860px] text-cw-text">
    <div class="mb-4 flex items-center gap-2 text-[12px] text-cw-muted">
      <span>Analyzed TM1 data and prepared a financial response.</span>
    </div>

    <section class="mb-5">
      <h2 class="mb-2 text-[17px] font-semibold text-cw-text">View selected by AI</h2>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div class="rounded-lg border border-cw-border bg-white px-4 py-3">
          <div class="${cls.label}">Cube</div>
          <div class="${cls.monoValue}">${esc(data.chosen_cube)}</div>
        </div>
        <div class="rounded-lg border border-cw-border bg-white px-4 py-3">
          <div class="${cls.label}">View</div>
          <div class="${cls.monoValue}">${esc(data.chosen_view)}</div>
        </div>
      </div>
      <div class="mt-3 rounded-lg border border-cw-blueMid bg-cw-blueLite px-4 py-3 text-[13px] leading-relaxed text-cw-sub">${esc(data.reasoning)}</div>
    </section>

    <section class="mb-5">
      <div class="mb-2 flex items-baseline justify-between gap-3">
        <h2 class="text-[17px] font-semibold text-cw-text">Data retrieved from TM1</h2>
        <span class="shrink-0 rounded-full border border-[#9de3c5] bg-cw-greenBg px-2.5 py-0.5 text-[11px] font-semibold text-[#0d7a4c]">${data.data_row_count.toLocaleString()} rows</span>
      </div>
      <div class="mb-3 text-xs text-cw-muted">Preview: first ${data.data_preview.length} rows</div>
      ${buildTable(data.data_preview)}
    </section>

    <section>
      <h2 class="mb-3 text-[17px] font-semibold text-cw-text">Financial analysis</h2>
      <div class="whitespace-pre-wrap text-[15px] leading-8 text-cw-sub">${esc(data.analysis)}</div>
    </section>
  </article>`;
}

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

fetch(`${API}/api/health`)
  .then(res => res.ok ? res.json() : Promise.reject())
  .catch(() => {});
