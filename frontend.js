const API = "http://localhost:8000";
const HISTORY_KEY = "tm1-ai-chat-history";
const EMAIL_SENT_KEY = "tm1-ai-email-sent";
const SIDEBAR_COLLAPSED_KEY = "tm1-ai-sidebar-collapsed";
const CHATS_COLLAPSED_KEY = "tm1-ai-chats-collapsed";
const EMAIL_COLLAPSED_KEY = "tm1-ai-email-collapsed";
const MAX_HISTORY = 20;
const MAX_EMAIL_RECORDS = 30;

let currentResult = null;

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
  document.getElementById("q").value = text;
  document.getElementById("q").focus();
};

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

function setLoading(on) {
  document.getElementById("runBtn").disabled = on;
  document.getElementById("sp").classList.toggle("hidden", !on);
  document.getElementById("bl").textContent = on ? "Analyzing..." : "Analyze ->";
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
  const toggle = document.getElementById("sidebarToggle");

  sidebar?.classList.toggle("lg:w-72", !collapsed);
  sidebar?.classList.toggle("lg:w-16", collapsed);
  document.querySelectorAll(".sidebar-expanded").forEach(el => el.classList.toggle("hidden", collapsed));
  document.querySelectorAll(".sidebar-collapsed").forEach(el => el.classList.toggle("hidden", !collapsed));

  if (toggle) {
    toggle.title = collapsed ? "Expand sidebar" : "Cubewise";
  }
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
  document.getElementById("q").value = "";
  document.getElementById("out").innerHTML = "";
  document.getElementById("q").focus();
}

function openHistory(id) {
  const item = getHistory().find(entry => entry.id === id);
  if (!item) return;
  currentResult = item.result;
  document.getElementById("q").value = item.result.question || "";
  render(item.result);
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

  list.innerHTML = history.map(item => {
    const date = new Date(item.createdAt).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
    return `<button type="button" class="mb-1 block w-full rounded-lg px-3 py-2.5 text-left transition hover:bg-cw-bg" onclick="openHistory('${esc(item.id)}')">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(item.result.question)}</div>
      <div class="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-cw-muted">
        <span class="truncate">${esc(item.result.chosen_cube)} / ${esc(item.result.chosen_view)}</span>
        <span class="shrink-0">${esc(date)}</span>
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
    const date = new Date(record.sentAt).toLocaleString([], {
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

function skeleton() {
  return `<div class="${cls.card} p-[22px]">
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

  setLoading(true);
  document.getElementById("out").innerHTML = skeleton();

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

function emailPanel() {
  return `<div class="${cls.card}">
    <div class="${cls.head}">
      <div class="${cls.title}">Send result by email</div>
      <span class="ml-auto text-[11px] font-medium text-cw-muted" id="emailStatus"></span>
    </div>
    <div class="flex flex-col gap-2 p-4 sm:flex-row">
      <input id="emailTo" type="email" class="min-h-10 flex-1 rounded-md border border-cw-border bg-white px-3 text-sm text-cw-text outline-none transition placeholder:text-cw-placeholder focus:border-cw-blue focus:ring-4 focus:ring-cw-blue/10" placeholder="name@company.com" />
      <button type="button" id="emailBtn" class="shrink-0 rounded-md bg-cw-blue px-4 py-2 text-[13px] font-semibold text-white shadow-md shadow-cw-blue/30 transition hover:bg-cw-blueHover disabled:cursor-not-allowed disabled:bg-cw-border disabled:text-cw-muted disabled:shadow-none" onclick="sendCurrentEmail()">Send email</button>
    </div>
  </div>`;
}

async function sendCurrentEmail() {
  if (!currentResult) return;

  const input = document.getElementById("emailTo");
  const status = document.getElementById("emailStatus");
  const button = document.getElementById("emailBtn");
  const to = input.value.trim();

  if (!to) {
    input.focus();
    status.textContent = "Enter recipient";
    status.className = "ml-auto text-[11px] font-medium text-red-600";
    return;
  }

  button.disabled = true;
  status.textContent = "Sending...";
  status.className = "ml-auto text-[11px] font-medium text-cw-muted";

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
    status.textContent = "Sent";
    status.className = "ml-auto text-[11px] font-medium text-cw-green";
  } catch (err) {
    status.textContent = err.message;
    status.className = "ml-auto text-[11px] font-medium text-red-600";
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  document.getElementById("out").innerHTML =
  `<div class="${cls.card} border-l-[3px] border-l-cw-blue">
    <div class="${cls.head}">
      <div class="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-cw-blueLite text-[11px] font-bold text-cw-blueText">1</div>
      <div class="${cls.title}">View selected by AI</div>
      <span class="${cls.badge} border border-cw-blueMid bg-cw-blueLite text-cw-blueText">AI Selection</span>
    </div>
    <div class="${cls.body}">
      <div class="mb-3.5 grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        <div><div class="${cls.label}">Cube</div><div class="${cls.monoValue}">${esc(data.chosen_cube)}</div></div>
        <div><div class="${cls.label}">View</div><div class="${cls.monoValue}">${esc(data.chosen_view)}</div></div>
      </div>
      <div class="${cls.label} mb-[5px]">Reasoning</div>
      <div class="rounded-[10px] border border-cw-blueMid bg-cw-blueLite px-3.5 py-[11px] text-[13px] leading-relaxed text-cw-sub">${esc(data.reasoning)}</div>
    </div>
  </div>

  <div class="${cls.card} border-l-[3px] border-l-cw-green">
    <div class="${cls.head}">
      <div class="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-cw-greenBg text-[11px] font-bold text-[#0d7a4c]">2</div>
      <div class="${cls.title}">Data retrieved from TM1</div>
      <span class="${cls.badge} border border-[#9de3c5] bg-cw-greenBg text-[#0d7a4c]">${data.data_row_count.toLocaleString()} rows</span>
    </div>
    <div class="${cls.body}">
      <div class="mb-3.5 flex items-baseline gap-2">
        <span class="font-mono text-[28px] font-medium leading-none text-cw-green">${data.data_row_count.toLocaleString()}</span>
        <span class="text-xs text-cw-muted">rows fetched &nbsp;&middot;&nbsp; preview: first ${data.data_preview.length}</span>
      </div>
      ${buildTable(data.data_preview)}
    </div>
  </div>

  <div class="${cls.card} border-l-[3px] border-l-cw-purple">
    <div class="${cls.head}">
      <div class="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-cw-purpleBg text-[11px] font-bold text-cw-purple">3</div>
      <div class="${cls.title}">Financial analysis</div>
      <span class="${cls.badge} border border-[#c9bef5] bg-cw-purpleBg text-cw-purple">Claude AI</span>
    </div>
    <div class="${cls.body}">
      <div class="whitespace-pre-wrap text-sm leading-[1.85] text-cw-sub">${esc(data.analysis)}</div>
    </div>
  </div>

  ${emailPanel()}`;
}

document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") go();
});

document.getElementById("newChatBtn")?.addEventListener("click", newChat);
document.getElementById("newChatRail")?.addEventListener("click", newChat);
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

fetch(`${API}/api/health`)
  .then(res => res.ok ? res.json() : Promise.reject())
  .catch(() => {});
