const API = "http://localhost:8000";
const HISTORY_KEY = "tm1-ai-chat-history";
const EMAIL_SENT_KEY = "tm1-ai-email-sent";
const SIDEBAR_COLLAPSED_KEY = "tm1-ai-sidebar-collapsed";
const CHATS_COLLAPSED_KEY = "tm1-ai-chats-collapsed";
const EMAIL_COLLAPSED_KEY = "tm1-ai-email-collapsed";
const DEV_MODE_KEY = "tm1-ai-developer-mode";
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

const LLM_MODEL_CATALOG = {
  openai: {
    label: "OpenAI / GPT",
    models: [
      "gpt-5.5", "gpt-5.5-pro",
      "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro",
      "gpt-5.2", "gpt-5.2-pro",
      "gpt-5.1",
      "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro",
      "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
      "o3", "o3-mini", "o4-mini"
    ],
    source: "https://platform.openai.com/docs/models"
  },
  anthropic: {
    label: "Anthropic / Claude",
    models: [
      "claude-opus-4-7",
      "claude-opus-4-6",
      "claude-opus-4-5-20251101",
      "claude-opus-4-1-20250805",
      "claude-sonnet-4-6",
      "claude-sonnet-4-5-20250929",
      "claude-haiku-4-5-20251001"
    ],
    source: "https://docs.anthropic.com/en/docs/about-claude/models/overview"
  },
  google: {
    label: "Google / Gemini",
    models: [
      "gemini-3-pro-preview",
      "gemini-2.5-pro",
      "gemini-2.5-flash",
      "gemini-2.5-flash-lite-preview-09-2025",
      "gemini-2.0-flash"
    ],
    source: "https://ai.google.dev/gemini-api/docs/models/gemini"
  },
  xai: {
    label: "xAI / Grok",
    models: ["grok-4.20", "grok-4", "grok-4-latest", "grok-3", "grok-3-latest"],
    source: "https://docs.x.ai/docs/models/"
  },
  deepseek: {
    label: "DeepSeek",
    models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    source: "https://api-docs.deepseek.com/api/list-models"
  }
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
