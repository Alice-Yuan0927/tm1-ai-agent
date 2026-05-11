async function downloadExcel(msgIdx, srcIdx) {
  const msg = currentMessages[msgIdx];
  if (!msg) return;
  const sources = Array.isArray(msg.data_sources) && msg.data_sources.length
    ? msg.data_sources
    : [{ cube: msg.chosen_cube, data_preview: msg.data_preview || [] }];
  const source = sources[srcIdx];
  if (!source) return;

  const btn = document.querySelector(`[data-xlsx="${msgIdx}-${srcIdx}"]`);
  if (btn) { btn.disabled = true; btn.textContent = "Exporting…"; }

  try {
    const res = await fetch(`${API}/api/export-excel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        cube: source.cube,
        analysis_rows: source.analysis_rows || source.data_preview || [],
        structured_preview: source.structured_preview || {},
      }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({detail: res.statusText}))).detail);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), {
      href: url,
      download: `${(source.cube || "data").replace(/[/\\?%*:|"<>]/g, "-")}.xlsx`,
    });
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (err) {
    alert(`Export failed: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa-solid fa-file-excel text-[9px]"></i> Download Excel';
    }
  }
}

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
