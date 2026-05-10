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
