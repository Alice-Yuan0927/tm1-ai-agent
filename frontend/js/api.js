async function downloadExcel(msgId, srcIdx) {
  const msg = currentMessages.find(m => m._id === msgId);
  if (!msg) return;
  const sources = Array.isArray(msg.data_sources) && msg.data_sources.length
    ? msg.data_sources
    : [{ cube: msg.chosen_cube, data_preview: msg.data_preview || [] }];
  const source = sources[srcIdx];
  if (!source) return;

  const btn = document.querySelector(`[data-msg-id="${msgId}"][data-src-idx="${srcIdx}"]`);
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

let _currentAnalysisAbort = null;

function stopCurrentAnalysis() {
  if (_currentAnalysisAbort) {
    _currentAnalysisAbort.abort();
    _currentAnalysisAbort = null;
  }
}

async function go(overrideOptions = {}) {
  const question = overrideOptions.question ?? document.getElementById("q").value.trim();
  if (!question) { document.getElementById("q").focus(); return; }

  const scope = Array.isArray(overrideOptions.selectedCubes)
    ? overrideOptions.selectedCubes
    : (typeof getSelectedCubeScope === "function" ? getSelectedCubeScope() : []);

  // Cancel any previous in-flight analysis before starting a new one.
  stopCurrentAnalysis();
  const abortController = new AbortController();
  _currentAnalysisAbort = abortController;

  if (!currentChatId) currentChatId = newId();
  setChatMode(true);
  setLoading(true);
  const output = document.getElementById("out");
  if (!currentMessages.length) output.innerHTML = "";
  output.insertAdjacentHTML("beforeend", skeleton(question));
  const thinkingTimer = startThinkingProgress();
  if (!overrideOptions.question) clearQuestionInput();
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
          data_sources: (m.data_sources || []).map(s => ({
            cube: s.cube,
            generated_mdx: s.generated_mdx,
            structured_preview: s.structured_preview ? {
              row_dimensions: s.structured_preview.row_dimensions || [],
              column_dimensions: s.structured_preview.column_dimensions || [],
              filters: s.structured_preview.filters || [],
              measure_dimension: s.structured_preview.measure_dimension || "",
              columns: s.structured_preview.columns || [],
            } : {},
          })),
        })),
        selected_cubes: scope,
      }),
      signal: abortController.signal,
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
          if (!data._id) data._id = newId();
          currentResult = data;
          currentMessages.push(data);
          saveCurrentConversation();
          renderConversation();
          scrollToLatest();

        } else if (event.type === "error") {
          const err = new Error(event.message);
          err.detail = event.detail || "";
          err.skipped = Array.isArray(event.skipped) ? event.skipped : [];
          err.scopeFiltered = Boolean(event.scope_filtered);
          err.scopedCubes = Array.isArray(event.scoped_cubes) ? event.scoped_cubes : [];
          throw err;
        }
      }
    }

  } catch (err) {
    output.querySelector('[data-thinking-state="true"]')?.remove();
    output.querySelector("#streaming-msg")?.remove();
    // User clicked Stop or a newer go() superseded this one → silent unwind.
    if (err.name === "AbortError" || abortController.signal.aborted) {
      output.insertAdjacentHTML("beforeend",
        `<div class="rounded-[10px] border border-cw-borderLow bg-white/60 px-[18px] py-2.5 text-[12px] italic text-cw-muted">Stopped.</div>`
      );
      scrollToLatest();
      return;
    }
    const skippedDebug = Array.isArray(err.skipped) && err.skipped.length
      ? `<details class="mt-3 min-w-0 max-w-full rounded-lg border border-amber-200 bg-white/60 px-3 py-2 text-[12px] text-amber-800">
          <summary class="cursor-pointer font-medium">Skipped source debug</summary>
          <div class="mt-3 min-w-0 space-y-3">
            ${err.skipped.map((source, index) => _skippedSourceHtml(source, index)).join("")}
          </div>
        </details>`
      : "";
    const scopePrompt = err.scopeFiltered
      ? `<div class="mt-3 min-w-0 rounded-lg border border-amber-300 bg-white/70 px-3 py-2.5 text-[12px] text-amber-900">
          <div class="font-medium">Your selected cubes returned no usable data.</div>
          ${err.scopedCubes?.length ? `<div class="mt-1 break-words text-[11px] text-amber-700">Scope: ${err.scopedCubes.map(c => esc(c)).join(", ")}</div>` : ""}
          <div class="mt-2">Want me to expand the search to the rest of the cubes?</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button type="button" id="expandCubeScopeBtn"
              class="h-7 rounded-md bg-cw-blue px-3 text-[11px] font-semibold text-white shadow-md shadow-cw-blue/20 transition hover:bg-cw-blueHover">
              Search all cubes
            </button>
            <button type="button" id="keepCubeScopeBtn"
              class="h-7 rounded-md border border-cw-border bg-white px-3 text-[11px] font-medium text-cw-text transition hover:bg-cw-bg">
              Keep scope, try a different question
            </button>
          </div>
        </div>`
      : "";
    output.insertAdjacentHTML("beforeend",
      `<div class="max-w-full rounded-[10px] border border-amber-200 bg-amber-50 px-[18px] py-3.5 text-[13px] text-amber-800 shadow-soft">
        <div class="flex min-w-0 items-start gap-2.5">
          <i class="fa-solid fa-triangle-exclamation mt-0.5 text-[12px] text-amber-500"></i>
          <div class="min-w-0 flex-1">
            <div class="font-semibold text-amber-900">No matching data found</div>
            <div class="mt-0.5 break-words">${esc(err.message)}</div>
            ${err.detail ? `<details class="mt-2 min-w-0 text-[11px] text-amber-700"><summary class="cursor-pointer font-medium">Technical details</summary><div class="mt-1 max-w-full whitespace-pre-wrap break-all">${esc(err.detail)}</div></details>` : ""}
            ${skippedDebug}
            ${scopePrompt}
          </div>
        </div>
      </div>`
    );
    if (err.scopeFiltered) {
      document.getElementById("expandCubeScopeBtn")?.addEventListener("click", () => {
        if (typeof clearCubeScope === "function") clearCubeScope();
        go({ question, selectedCubes: [] });
      });
      document.getElementById("keepCubeScopeBtn")?.addEventListener("click", () => {
        document.getElementById("q")?.focus();
      });
    }
    scrollToLatest();
  } finally {
    window.clearInterval(thinkingTimer);
    setLoading(false);
    if (_currentAnalysisAbort === abortController) _currentAnalysisAbort = null;
  }
}
