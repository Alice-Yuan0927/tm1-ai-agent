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

function fmt(value) {
  if (typeof value !== "number") return esc(String(value));
  return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
