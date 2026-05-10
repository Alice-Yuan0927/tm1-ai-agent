async function go() {
  const question = document.getElementById("q").value.trim();
  if (!question) { document.getElementById("q").focus(); return; }

  if (!currentChatId) currentChatId = newId();
  setChatMode(true);
  setLoading(true);
  const output = document.getElementById("out");
  if (!currentMessages.length) output.innerHTML = "";
  output.insertAdjacentHTML("beforeend", skeleton(question));
  const thinkingTimer = startThinkingProgress();
  clearQuestionInput();
  scrollToLatest();

  let analysisEl = null;
  let analysisBuffer = "";

  try {
    const res = await fetch(`${API}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        history: currentMessages.map(m => ({
          question: m.question, analysis: m.analysis,
          chosen_cube: m.chosen_cube, type: m.type || "analysis",
        })),
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let sseBuffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      sseBuffer += decoder.decode(value, { stream: true });
      const parts = sseBuffer.split("\n\n");
      sseBuffer = parts.pop() ?? "";

      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        let event;
        try { event = JSON.parse(part.slice(6)); } catch { continue; }

        if (event.type === "sources") {
          window.clearInterval(thinkingTimer);
          output.querySelector('[data-thinking-state="true"]')?.remove();
          output.insertAdjacentHTML("beforeend", streamingArticle(event, question));
          analysisEl = document.getElementById("streaming-analysis");
          scrollToLatest();

        } else if (event.type === "chunk") {
          if (analysisEl) {
            analysisBuffer += event.text;
            analysisEl.textContent = analysisBuffer;
            const nearBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120;
            if (nearBottom) scrollToLatest();
          }

        } else if (event.type === "done") {
          const data = event.data;
          currentResult = data;
          currentMessages.push(data);
          saveCurrentConversation();
          renderConversation();
          scrollToLatest();

        } else if (event.type === "error") {
          throw new Error(event.message);
        }
      }
    }

  } catch (err) {
    output.querySelector('[data-thinking-state="true"]')?.remove();
    output.querySelector("#streaming-msg")?.remove();
    output.insertAdjacentHTML("beforeend",
      `<div class="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-[18px] py-3.5 text-[13px] text-red-700 shadow-soft">
        <span>Warning:</span><span>${esc(err.message)}</span>
      </div>`
    );
    scrollToLatest();
  } finally {
    window.clearInterval(thinkingTimer);
    setLoading(false);
  }
}
