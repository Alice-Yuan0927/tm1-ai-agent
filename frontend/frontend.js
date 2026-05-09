const API = "http://localhost:8000";
const HISTORY_KEY = "tm1-ai-chat-history";
const EMAIL_SENT_KEY = "tm1-ai-email-sent";
const SIDEBAR_COLLAPSED_KEY = "tm1-ai-sidebar-collapsed";
const CHATS_COLLAPSED_KEY = "tm1-ai-chats-collapsed";
const EMAIL_COLLAPSED_KEY = "tm1-ai-email-collapsed";
const MAX_HISTORY = 20;
const MAX_EMAIL_RECORDS = 30;

let currentResult = null;
let currentChatId = null;
let currentMessages = [];
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

function renderInlineMarkdown(text) {
  return esc(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
}

function parseMarkdownTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  return trimmed
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map(cell => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = parseMarkdownTableRow(line);
  return Boolean(cells?.length) && cells.every(cell => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdownTable(rows) {
  if (rows.length < 2) return "";
  const header = rows[0];
  const body = rows.slice(1);

  const head = header.map((cell, index) => {
    const align = index === 0 ? "text-left" : "text-right";
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-cw-muted">${renderInlineMarkdown(cell)}</th>`;
  }).join("");

  const bodyRows = body.map(row => {
    const cells = header.map((_, index) => {
      const value = row[index] ?? "";
      const align = index === 0 ? "text-left font-medium text-cw-text" : "text-right font-mono text-cw-blueText";
      return `<td class="${align} whitespace-nowrap border-b border-cw-borderLow px-3 py-2">${renderInlineMarkdown(value)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite/60">${cells}</tr>`;
  }).join("");

  return `<div class="my-5 overflow-x-auto rounded-xl border border-cw-border bg-white shadow-soft">
    <table class="w-full border-collapse text-[12.5px] leading-5">
      <thead><tr>${head}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
  </div>`;
}

function renderMarkdown(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const html = [];
  let listItems = [];

  const flushList = () => {
    if (!listItems.length) return;
    html.push(`<ul class="my-3 list-disc space-y-1 pl-6">${listItems.join("")}</ul>`);
    listItems = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      continue;
    }

    if (/^---+$/.test(trimmed)) {
      flushList();
      html.push('<hr class="my-5 border-cw-border" />');
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      const classes = {
        1: "mb-4 mt-1 text-[22px] font-semibold leading-tight text-cw-text",
        2: "mb-3 mt-6 text-[18px] font-semibold leading-tight text-cw-text",
        3: "mb-2 mt-5 text-[15px] font-semibold leading-tight text-cw-text",
      }[level];
      html.push(`<h${level} class="${classes}">${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const tableStart = parseMarkdownTableRow(trimmed);
    const separatorIndex = (() => {
      let probe = i + 1;
      while (probe < lines.length && !lines[probe].trim()) probe += 1;
      return isMarkdownTableSeparator(lines[probe] || "") ? probe : -1;
    })();
    if (tableStart && separatorIndex !== -1) {
      flushList();
      const tableRows = [tableStart];
      i = separatorIndex;
      while (i + 1 < lines.length) {
        const next = lines[i + 1].trim();
        if (!next) {
          let probe = i + 2;
          while (probe < lines.length && !lines[probe].trim()) probe += 1;
          if (!parseMarkdownTableRow(lines[probe] || "")) break;
          i = probe - 1;
          continue;
        }
        const row = parseMarkdownTableRow(next);
        if (!row || isMarkdownTableSeparator(next)) break;
        tableRows.push(row);
        i += 1;
      }
      html.push(renderMarkdownTable(tableRows));
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listItems.push(`<li>${renderInlineMarkdown(bullet[1])}</li>`);
      continue;
    }

    flushList();
    html.push(`<p class="mb-2 last:mb-0">${renderInlineMarkdown(trimmed)}</p>`);
  }

  flushList();
  return html.join("");
}

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

function newId() {
  return crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
}

function getItemMessages(item) {
  if (Array.isArray(item.messages) && item.messages.length) return item.messages;
  return item.result ? [item.result] : [];
}

function getLatestResult(item) {
  const messages = getItemMessages(item);
  return messages[messages.length - 1] || item.result || {};
}

function saveCurrentConversation() {
  if (!currentMessages.length) return;

  const now = new Date().toISOString();
  if (!currentChatId) currentChatId = newId();

  const history = getHistory();
  const existing = history.find(item => item.id === currentChatId);
  const item = {
    id: currentChatId,
    createdAt: existing?.createdAt || now,
    updatedAt: now,
    result: currentMessages[currentMessages.length - 1],
    messages: currentMessages,
  };

  writeStore(HISTORY_KEY, [item, ...history.filter(entry => entry.id !== currentChatId)], MAX_HISTORY);
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

function scrollToLatest() {
  window.requestAnimationFrame(() => {
    window.scrollTo({
      top: document.documentElement.scrollHeight,
      behavior: "smooth",
    });
  });
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
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

function buildTm1Preview(source) {
  const preview = source.structured_preview;
  if (!preview?.rows?.length) {
    return buildTable(source.data_preview || []);
  }

  const rowDimensions = preview.row_dimensions || [];
  const measureColumns = preview.columns || [];
  const filters = preview.filters || [];
  const headers = [...rowDimensions, ...measureColumns];

  const filterChips = filters.length
    ? `<div class="mb-3 flex flex-wrap gap-2">
        ${filters.map(filter => `
          <div class="inline-flex items-center gap-1.5 rounded-md border border-cw-border bg-white px-2.5 py-1 text-[12px] shadow-sm">
            <span class="font-medium text-cw-muted">${esc(filter.dimension)}:</span>
            <span class="font-semibold text-cw-text">${esc(filter.element)}</span>
          </div>
        `).join("")}
      </div>`
    : "";

  const head = headers.map((header, index) => {
    const isMeasure = index >= rowDimensions.length;
    const align = isMeasure ? "text-right" : "text-left";
    const label = isMeasure ? header : header;
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[11px] font-semibold text-cw-text">${esc(label)}</th>`;
  }).join("");

  const body = preview.rows.map(row => {
    const cells = headers.map((header, index) => {
      const isMeasure = index >= rowDimensions.length;
      const value = row[header] ?? "";
      const align = isMeasure ? "text-right font-mono text-cw-blueText" : "text-left text-cw-text";
      return `<td class="${align} whitespace-nowrap border-b border-cw-borderLow px-3 py-2">${fmt(value)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${cells}</tr>`;
  }).join("");

  return `${filterChips}
    <div class="overflow-x-auto rounded-[10px] border border-cw-border bg-white">
      <table class="w-full border-collapse text-[12px] leading-5">
        <thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
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
  currentChatId = null;
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
  const item = getHistory().find(entry => entry.id === id);
  if (!item) return;
  currentChatId = item.id;
  currentMessages = getItemMessages(item);
  currentResult = currentMessages[currentMessages.length - 1] || null;
  setChatMode(true);
  document.getElementById("q").value = "";
  updateAnalyzeDisabled();
  autoResizeQuestion();
  renderConversation();
  scrollToLatest();
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
    const latest = getLatestResult(item);
    const text = [
      getItemMessages(item).map(message => message.question).join(" "),
      latest.chosen_cube || "",
      latest.chosen_view || "",
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
    const latest = getLatestResult(item);
    const messages = getItemMessages(item);
    const first = messages[0] || latest;
    const timestamp = item.updatedAt || item.createdAt;
    const group = formatHistoryGroup(timestamp);
    const time = formatHistoryTime(timestamp);
    const groupHeader = group === lastGroup
      ? ""
      : `<div class="px-3 pb-1 pt-3 text-[10px] font-semibold uppercase tracking-[0.08em] text-cw-muted">${esc(group)}</div>`;
    lastGroup = group;

    return `${groupHeader}<button type="button" class="mb-1 block w-full rounded-lg px-3 py-2.5 text-left transition hover:bg-cw-bg" onclick="openHistory('${esc(item.id)}')">
      <div class="truncate text-[13px] font-medium text-cw-text">${esc(first.question)}</div>
      <div class="mt-0.5 flex items-center justify-between gap-2 text-[11px] text-cw-muted">
        <span class="truncate">${latest.type === "clarification" ? "Clarification needed" : `${esc(latest.chosen_cube)} / ${esc(latest.chosen_view)}`}</span>
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
  <div class="max-w-[760px] rounded-2xl border border-white/70 bg-white/60 p-[22px] shadow-soft backdrop-blur-md" data-thinking-state="true">
    <div class="mb-4 flex items-center gap-2 text-[13px] font-semibold text-cw-text">
      <span class="h-2 w-2 animate-pulse rounded-full bg-cw-blue"></span>
      <span>Thinking...</span>
    </div>
    <div class="space-y-3 text-[13px] text-cw-sub">
      <div class="thinking-step flex items-center gap-3">
        <span class="flex h-5 w-5 items-center justify-center rounded-full bg-cw-blueLite text-[10px] font-semibold text-cw-blue">1</span>
        <span>Understanding the question</span>
      </div>
      <div class="thinking-step flex items-center gap-3 opacity-55">
        <span class="flex h-5 w-5 items-center justify-center rounded-full bg-cw-blueLite text-[10px] font-semibold text-cw-blue">2</span>
        <span>Selecting the most relevant TM1 cube view</span>
      </div>
      <div class="thinking-step flex items-center gap-3 opacity-55">
        <span class="flex h-5 w-5 items-center justify-center rounded-full bg-cw-blueLite text-[10px] font-semibold text-cw-blue">3</span>
        <span>Retrieving and previewing TM1 data</span>
      </div>
      <div class="thinking-step flex items-center gap-3 opacity-55">
        <span class="flex h-5 w-5 items-center justify-center rounded-full bg-cw-blueLite text-[10px] font-semibold text-cw-blue">4</span>
        <span>Preparing the financial response</span>
      </div>
    </div>
  </div>`;
}

function startThinkingProgress() {
  let index = 0;
  return window.setInterval(() => {
    const steps = Array.from(document.querySelectorAll(".thinking-step"));
    if (!steps.length) return;
    index = Math.min(index + 1, steps.length - 1);
    steps.forEach((step, stepIndex) => {
      step.classList.toggle("opacity-55", stepIndex > index);
      step.classList.toggle("font-medium", stepIndex === index);
    });
  }, 1100);
}

async function go() {
  const question = document.getElementById("q").value.trim();
  if (!question) {
    document.getElementById("q").focus();
    return;
  }

  if (!currentChatId) currentChatId = newId();
  setChatMode(true);
  setLoading(true);
  const output = document.getElementById("out");
  if (!currentMessages.length) output.innerHTML = "";
  output.insertAdjacentHTML("beforeend", skeleton(question));
  const thinkingTimer = startThinkingProgress();
  clearQuestionInput();
  scrollToLatest();

  try {
    const res = await fetch(`${API}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        history: currentMessages.map(message => ({
          question: message.question,
          analysis: message.analysis,
          chosen_cube: message.chosen_cube,
          chosen_view: message.chosen_view,
          type: message.type || "analysis",
        })),
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Failed");
    }

    const data = await res.json();
    currentResult = data;
    currentMessages.push(data);
    saveCurrentConversation();
    renderConversation();
    scrollToLatest();
  } catch (err) {
    output.querySelector('[data-thinking-state="true"]')?.remove();
    document.getElementById("out").insertAdjacentHTML("beforeend", `<div class="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-[18px] py-3.5 text-[13px] text-red-700 shadow-soft"><span>Warning:</span><span>${esc(err.message)}</span></div>`);
    scrollToLatest();
  } finally {
    window.clearInterval(thinkingTimer);
    setLoading(false);
  }
}

async function sendCurrentEmail(source = "share") {
  if (!currentResult) {
    updateShareStatus("Analyze a question before sharing.", "text-cw-muted");
    return;
  }
  if (currentResult.type === "clarification") {
    updateShareStatus("No TM1 analysis to email yet.", "text-red-600");
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

function renderSingleMessage(data) {
  if (data.type === "clarification") {
    return `<div class="flex justify-end">
      <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(data.question)}</div>
    </div>

    <article class="max-w-[760px] text-cw-text">
      <section>
        <div class="text-[15px] leading-6 text-cw-sub [&_strong]:font-semibold [&_strong]:text-cw-text [&_em]:italic">${renderMarkdown(data.analysis)}</div>
      </section>
    </article>`;
  }

  const sources = Array.isArray(data.data_sources) && data.data_sources.length
    ? data.data_sources
    : [{
        cube: data.chosen_cube,
        view: data.chosen_view,
        reasoning: data.reasoning,
        data_row_count: data.data_row_count,
        data_preview: data.data_preview || [],
      }];
  const skippedSources = Array.isArray(data.skipped_sources) ? data.skipped_sources : [];
  const sourceCards = sources.map((source, index) => `
    <div class="rounded-lg border border-cw-border bg-white px-4 py-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div class="${cls.label}">Source ${index + 1}</div>
        <span class="shrink-0 rounded-full border border-[#9de3c5] bg-cw-greenBg px-2 py-0.5 text-[10px] font-semibold text-[#0d7a4c]">${Number(source.data_row_count || 0).toLocaleString()} rows</span>
      </div>
      <div class="mb-1 font-mono text-[12.5px] font-medium text-cw-blue">${esc(source.cube)} / ${esc(source.view)}</div>
      <div class="text-[12px] leading-5 text-cw-sub">${esc(source.reasoning || "")}</div>
    </div>
  `).join("");
  const skippedNotice = skippedSources.length
    ? `<div class="mt-3 rounded-lg border border-cw-border bg-white/70 px-4 py-3 text-[12px] leading-5 text-cw-muted">
        Skipped ${skippedSources.length} source${skippedSources.length === 1 ? "" : "s"} with no usable data: ${skippedSources.map(source => `${esc(source.cube)} / ${esc(source.view)}`).join(", ")}.
      </div>`
    : "";
  const tableSections = sources.map((source, index) => `
    <div class="mb-4 last:mb-0">
      <div class="mb-2 flex items-baseline justify-between gap-3">
        <h3 class="text-[14px] font-semibold text-cw-text">${esc(source.cube)} / ${esc(source.view)}</h3>
        <span class="shrink-0 rounded-full border border-[#9de3c5] bg-cw-greenBg px-2.5 py-0.5 text-[11px] font-semibold text-[#0d7a4c]">${Number(source.data_row_count || 0).toLocaleString()} rows</span>
      </div>
      <div class="mb-2 text-xs text-cw-muted">Preview: first ${(source.structured_preview?.rows || source.data_preview || []).length} rows</div>
      ${buildTm1Preview(source)}
    </div>
  `).join("");

  return `<div class="flex justify-end">
    <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(data.question)}</div>
  </div>

  <article class="max-w-[860px] text-cw-text">
    <div class="mb-4 flex items-center gap-2 text-[12px] text-cw-muted">
      <span>Analyzed TM1 data and prepared a financial response.</span>
    </div>

    <section class="mb-5">
      <h2 class="mb-2 text-[17px] font-semibold text-cw-text">Sources selected by AI</h2>
      <div class="grid grid-cols-1 gap-3">
        ${sourceCards}
      </div>
      <div class="mt-3 rounded-lg border border-cw-blueMid bg-cw-blueLite px-4 py-3 text-[13px] leading-relaxed text-cw-sub">${esc(data.reasoning)}</div>
      ${skippedNotice}
    </section>

    <section class="mb-5">
      <h2 class="mb-3 text-[17px] font-semibold text-cw-text">Data retrieved from TM1</h2>
      ${tableSections}
    </section>

    <section>
      <div class="text-[15px] leading-8 text-cw-sub [&_strong]:font-semibold [&_strong]:text-cw-text [&_em]:italic">${renderMarkdown(data.analysis)}</div>
    </section>
  </article>`;
}

function renderConversation() {
  setChatMode(true);
  updateShareStatus("");
  document.getElementById("out").innerHTML = currentMessages.map(renderSingleMessage).join("");
}

function render(data) {
  currentMessages = [data];
  currentResult = data;
  renderConversation();
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
