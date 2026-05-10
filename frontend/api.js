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
