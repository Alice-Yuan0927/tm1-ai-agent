function readStore(key) {
  try { return JSON.parse(localStorage.getItem(key) || "[]"); }
  catch { return []; }
}

function writeStore(key, items, max) {
  localStorage.setItem(key, JSON.stringify(items.slice(0, max)));
}

function getHistory()      { return readStore(HISTORY_KEY); }
function getEmailRecords() { return readStore(EMAIL_SENT_KEY); }

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

function _stripLargeFields(msg) {
  if (!msg || msg.type !== "analysis") return msg;
  const sources = (msg.data_sources || []).map(s => {
    const { analysis_rows, ...rest } = s;
    return rest;
  });
  return { ...msg, data_sources: sources };
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
    result: _stripLargeFields(currentMessages[currentMessages.length - 1]),
    messages: currentMessages.map(_stripLargeFields),
  };
  writeStore(HISTORY_KEY, [item, ...history.filter(e => e.id !== currentChatId)], MAX_HISTORY);
  renderHistory();
}

function saveEmailRecord(to) {
  if (!currentResult) return;
  const item = {
    id: newId(),
    sentAt: new Date().toISOString(),
    to,
    question: currentResult.question,
    chosenCube: currentResult.chosen_cube,
  };
  writeStore(EMAIL_SENT_KEY, [item, ...getEmailRecords()], MAX_EMAIL_RECORDS);
  renderEmailRecords();
}
