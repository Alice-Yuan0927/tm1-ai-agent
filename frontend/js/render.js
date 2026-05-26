// ── Loading state ─────────────────────────────────────────────────────────────

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
        <span>Selecting the most relevant TM1 cube</span>
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
    <div id="agent-live-status" class="mt-4 hidden text-[12px] italic text-cw-sub">
      <span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-cw-blue"></span>
      <span data-live-text></span>
    </div>
    <details class="mt-2 text-[11px] text-cw-sub">
      <summary class="cursor-pointer select-none opacity-70 hover:opacity-100">Agent tool calls</summary>
      <ul id="agent-tool-history" class="mt-2 space-y-1 pl-4 font-mono"></ul>
    </details>
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

// ── Section builders ──────────────────────────────────────────────────────────

function _sourceCardsHtml(sources, reasoning, skippedSources) {
  const cards = sources.map((source, i) => `
    <div class="rounded-lg border border-cw-border bg-white px-4 py-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div class="${cls.label}">Source ${i + 1}</div>
        <span class="shrink-0 rounded-full border border-blue-300 bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-600">${_rowLabel(source.data_row_count || 0)}</span>
      </div>
      <div class="mb-1 font-mono text-[12.5px] font-medium text-cw-blue">${esc(source.cube)}</div>
      <div class="text-[12px] leading-5 text-cw-sub">${esc(source.reasoning || "")}</div>
      ${_toolTraceHtml(source.mdx_attempts)}
      ${_planAssumptionsHtml(source.plan)}
      ${_ragFeedbackHtml(source)}
    </div>`).join("");

  const skippedNotice = skippedSources.length
    ? `<details class="mt-3 rounded-lg border border-amber-200 bg-amber-50/70 px-4 py-3 text-[12px] leading-5 text-amber-800">
        <summary class="cursor-pointer font-medium">
          Skipped ${skippedSources.length} source${skippedSources.length === 1 ? "" : "s"} with no usable data: ${skippedSources.map(s => esc(s.cube)).join(", ")}.
        </summary>
        <div class="mt-3 space-y-3">
          ${skippedSources.map((source, index) => _skippedSourceHtml(source, index)).join("")}
        </div>
      </details>`
    : "";

  return `<div class="grid grid-cols-1 gap-3">${cards}</div>
    <div class="mt-3 rounded-lg border border-cw-blueMid bg-cw-blueLite px-4 py-3 text-[13px] leading-relaxed text-cw-sub">${esc(reasoning)}</div>
    ${skippedNotice}`;
}

function _toolTraceHtml(attempts) {
  if (!Array.isArray(attempts) || !attempts.length) return "";
  const items = attempts.slice(0, 8).map(raw => {
    const text = String(raw || "");
    const [tool, ...rest] = text.split(":");
    const detail = rest.join(":").trim();
    return `<li class="flex min-w-0 items-start gap-2">
      <span class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-violet-500 text-[9px] text-white">
        <i class="fa-solid fa-check"></i>
      </span>
      <span class="min-w-0">
        <span class="font-medium text-cw-text">Ran ${esc(tool.trim() || "tool")}</span>
        ${detail ? `<span class="ml-1 break-words text-cw-muted">${esc(detail.slice(0, 160))}</span>` : ""}
      </span>
    </li>`;
  }).join("");
  return `<details class="mt-3 rounded-md border border-violet-100 bg-violet-50/60 px-3 py-2 text-[11px] leading-5 text-cw-sub">
    <summary class="cursor-pointer font-medium text-cw-text">Tool trace</summary>
    <ul class="mt-2 space-y-1.5">${items}</ul>
  </details>`;
}

function _ragFeedbackHtml(source) {
  const cube = source.cube || "";
  const mdx = source.generated_mdx || "";
  const rows = source.data_row_count || 0;
  if (!cube || !mdx) return "";
  // Pass question via data attribute on the wrapping article (looked up at click time).
  return `
    <div class="mt-3 flex items-center justify-end gap-2 border-t border-cw-borderLow pt-2 text-[11px] text-cw-muted"
         data-rag-feedback
         data-cube="${esc(cube)}"
         data-mdx="${esc(mdx)}"
         data-row-count="${rows}">
      <span class="mr-1">Was this answer right?</span>
      <button type="button" data-rag-action="save"
        class="inline-flex items-center gap-1 rounded-md border border-cw-borderLow bg-white px-2 py-0.5 transition hover:border-green-400 hover:text-green-700">
        <i class="fa-regular fa-thumbs-up"></i><span>Save</span>
      </button>
      <button type="button" data-rag-action="forget"
        class="inline-flex items-center gap-1 rounded-md border border-cw-borderLow bg-white px-2 py-0.5 transition hover:border-red-400 hover:text-red-700">
        <i class="fa-regular fa-thumbs-down"></i><span>Wrong — forget</span>
      </button>
      <span data-rag-status class="ml-2 hidden italic"></span>
    </div>`;
}


function _planAssumptionsHtml(plan) {
  if (!plan || !Array.isArray(plan.assumptions) || !plan.assumptions.length) return "";
  const renderBold = text =>
    esc(text).replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-amber-900">$1</strong>');
  const items = plan.assumptions.map(a => `<li>${renderBold(String(a))}</li>`).join("");
  const conf = typeof plan.confidence === "number" ? Math.round(plan.confidence * 100) : null;
  const confLabel = conf !== null ? ` (${conf}% confident)` : "";
  return `
    <div class="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-900">
      <div class="mb-1 flex items-center gap-1.5 font-semibold">
        <i class="fa-solid fa-wand-magic-sparkles text-[10px]"></i>
        <span>I made these assumptions${confLabel} — reply with adjustments if you need different ones.</span>
      </div>
      <ul class="list-disc space-y-0.5 pl-4">${items}</ul>
    </div>`;
}

function _skippedSourceHtml(source, index) {
  const attempts = Array.isArray(source.mdx_attempts) && source.mdx_attempts.length
    ? `<details class="mt-2"><summary class="cursor-pointer font-medium">Repair attempts</summary>
        <pre class="mt-1 max-h-44 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-white/80 p-2 font-mono text-[11px] text-amber-900">${esc(source.mdx_attempts.join("\n"))}</pre>
      </details>`
    : "";
  const mdx = source.generated_mdx
    ? `<details class="mt-2"><summary class="cursor-pointer font-medium">Show MDX</summary>
        <pre class="mt-1 max-h-56 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-md bg-white/80 p-2 font-mono text-[11px] text-amber-900">${esc(source.generated_mdx)}</pre>
      </details>`
    : `<div class="mt-2 max-w-full break-all rounded-md bg-white/70 p-2 font-mono text-[11px] text-amber-700">No MDX generated for this source.</div>`;
  return `<div class="min-w-0 rounded-md border border-amber-200 bg-white/70 p-3">
    <div class="flex min-w-0 items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="text-[10px] font-semibold uppercase tracking-[0.08em] text-amber-600">Skipped source ${index + 1}</div>
        <div class="mt-0.5 break-words font-mono text-[12px] font-medium text-amber-900">${esc(source.cube || "")}</div>
      </div>
      <span class="min-w-0 max-w-[70%] rounded-md border border-amber-300 bg-amber-100 px-2 py-0.5 text-left text-[10px] font-semibold leading-4 text-amber-700 break-all">${esc(source.status || "skipped")}</span>
    </div>
    ${source.reasoning ? `<div class="mt-2 break-words text-[11px] text-amber-700">${esc(source.reasoning)}</div>` : ""}
    ${mdx}
    ${attempts}
  </div>`;
}

function _previewCount(source) {
  const p = source.structured_preview;
  if (!p) return (source.data_preview || []).length;
  return p.transpose ? (p.columns?.length ?? 0) : (p.rows?.length ?? 0);
}

function _rowLabel(n) {
  return n === 1 ? "1 row" : `${Number(n).toLocaleString()} rows`;
}

function _tableSectionsHtml(sources, msgId) {
  return sources.map((source, index) => {
    const chartId   = `chart-${Date.now()}-${index}`;
    const total     = source.data_row_count || 0;
    const preview   = _previewCount(source);
    const previewText = preview >= total
      ? _rowLabel(total)
      : `${_rowLabel(preview)} of ${_rowLabel(total)}`;
    const csvBtn = msgId != null
      ? `<button type="button" data-action="download-excel" data-msg-id="${esc(msgId)}" data-src-idx="${index}"
           class="flex items-center gap-1 rounded-md border border-green-600 bg-white px-2 py-0.5 text-[11px] text-green-600 transition hover:bg-green-50">
           <i class="fa-solid fa-file-excel text-[9px]"></i> Download Excel
         </button>`
      : "";
    return `
    <div class="mb-4 last:mb-0">
      <div class="mb-2 flex items-baseline justify-between gap-3">
        <h3 class="text-[14px] font-semibold text-cw-text">${esc(source.cube)}</h3>
        <div class="flex items-center gap-2">
          ${csvBtn}
          <span class="shrink-0 rounded-full border border-blue-300 bg-blue-50 px-2.5 py-0.5 text-[11px] font-semibold text-blue-600">${_rowLabel(total)}</span>
        </div>
      </div>
      <div class="mb-2 text-xs text-cw-muted">Showing ${previewText}</div>
      ${buildTm1Preview(source)}
      ${buildChart(source, chartId)}
    </div>`;
  }).join("");
}

// ── Full message templates ────────────────────────────────────────────────────

function streamingArticle(event, question) {
  const sources = event.sources || [];
  const skipped = event.skipped || [];
  return `<div class="mb-8 w-full" id="streaming-msg">
    <div class="flex justify-end mb-3">
      <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(question)}</div>
    </div>
    <article class="w-full max-w-[860px] text-cw-text">
      <div class="mb-4 flex items-center gap-2 text-[12px] text-cw-muted">
        <span class="h-1.5 w-1.5 animate-pulse rounded-full bg-cw-blue"></span>
        <span>Analyzing data...</span>
      </div>
      <section class="mb-5">
        <h2 class="mb-2 text-[17px] font-semibold text-cw-text">Sources selected by AI</h2>
        ${_sourceCardsHtml(sources, event.reasoning || "", skipped)}
      </section>
      <section class="mb-5">
        <h2 class="mb-3 text-[17px] font-semibold text-cw-text">Data retrieved from TM1</h2>
        ${_tableSectionsHtml(sources, null)}
      </section>
      <section>
        <div id="streaming-analysis" class="whitespace-pre-wrap font-mono text-[13px] leading-7 text-cw-sub"></div>
      </section>
    </article>
  </div>`;
}

function renderSingleMessage(data) {
  if (data.type === "clarification") {
    return `<div class="mb-8 w-full">
      <div class="flex justify-end mb-3">
        <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(data.question)}</div>
      </div>
      <article class="w-full max-w-[760px] text-cw-text">
        <section>
          <div class="text-[15px] leading-6 text-cw-sub [&_strong]:font-semibold [&_strong]:text-cw-text [&_em]:italic">${renderMarkdown(data.analysis)}</div>
        </section>
      </article>
    </div>`;
  }

  const sources = Array.isArray(data.data_sources) && data.data_sources.length
    ? data.data_sources
    : [{
        cube: data.chosen_cube,
        reasoning: data.reasoning,
        data_row_count: data.data_row_count,
        data_preview: data.data_preview || [],
      }];
  const skippedSources = Array.isArray(data.skipped_sources) ? data.skipped_sources : [];

  const suggestionsHtml = Array.isArray(data.suggestions) && data.suggestions.length
    ? `<div class="mt-6 border-t border-cw-borderLow pt-5">
        <p class="mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-cw-muted">Ask a follow-up</p>
        <div class="flex flex-wrap gap-2">
          ${data.suggestions.map(s => `<button type="button" data-followup="${esc(s)}"
            class="rounded-full border border-cw-blueMid bg-white px-3 py-1.5 text-[12px] text-cw-blue transition hover:border-cw-blue hover:bg-cw-blueLite">${esc(s)}</button>`).join("")}
        </div>
      </div>`
    : "";

  return `<div class="mb-8 w-full">
    <div class="flex justify-end mb-3">
      <div class="max-w-[76%] rounded-2xl bg-white px-5 py-3 text-[15px] leading-7 text-cw-text shadow-soft">${esc(data.question)}</div>
    </div>
    <article class="w-full max-w-[860px] text-cw-text">
      <div class="mb-4 flex items-center gap-2 text-[12px] text-cw-muted">
        <span>Analyzed TM1 data and prepared a financial response.</span>
      </div>
      <section class="mb-5">
        <h2 class="mb-2 text-[17px] font-semibold text-cw-text">Sources selected by AI</h2>
        ${_sourceCardsHtml(sources, data.reasoning || "", skippedSources)}
      </section>
      <section class="mb-5">
        <h2 class="mb-3 text-[17px] font-semibold text-cw-text">Data retrieved from TM1</h2>
        ${_tableSectionsHtml(sources, data._id ?? null)}
      </section>
      <section>
        <div class="text-[15px] leading-8 text-cw-sub [&_strong]:font-semibold [&_strong]:text-cw-text [&_em]:italic">${renderMarkdown(data.analysis)}</div>
        ${suggestionsHtml}
      </section>
    </article>
  </div>`;
}

// ── Entry points ──────────────────────────────────────────────────────────────

function renderConversation() {
  setChatMode(true);
  updateShareStatus("");
  document.getElementById("out").innerHTML = currentMessages.map(msg => renderSingleMessage(msg)).join("");
  bindFollowUpButtons();
  bindRagFeedback();
  initCharts();
}

function bindFollowUpButtons() {
  document.querySelectorAll("[data-followup]").forEach(button => {
    if (button.dataset.boundFollowup === "true") return;
    button.dataset.boundFollowup = "true";
    button.addEventListener("click", () => setQ(button.dataset.followup || ""));
  });
}

function bindRagFeedback() {
  document.querySelectorAll("[data-rag-feedback]").forEach(container => {
    if (container.dataset.boundRag === "true") return;
    container.dataset.boundRag = "true";
    const cube = container.dataset.cube || "";
    const mdx = container.dataset.mdx || "";
    const rowCount = parseInt(container.dataset.rowCount || "0", 10) || 0;
    const statusEl = container.querySelector("[data-rag-status]");
    const question = container.closest("article")?.previousElementSibling?.querySelector("div")?.textContent
      || container.closest("[data-question]")?.dataset.question
      || "";

    container.querySelectorAll("[data-rag-action]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.ragAction;
        const url = action === "save" ? "/api/rag/save" : "/api/rag/forget";
        const body = action === "save"
          ? { question, cube, mdx, row_count: rowCount }
          : { cube, mdx };
        btn.disabled = true;
        if (statusEl) {
          statusEl.classList.remove("hidden");
          statusEl.textContent = action === "save" ? "Saving…" : "Removing…";
        }
        try {
          const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          if (statusEl) {
            statusEl.textContent = action === "save"
              ? "Saved to knowledge base ✓"
              : `Removed ${data.removed ?? 0} entr${(data.removed ?? 0) === 1 ? "y" : "ies"} ✓`;
            statusEl.classList.remove("italic");
            statusEl.classList.add("text-green-700");
          }
          container.querySelectorAll("[data-rag-action]").forEach(b => b.disabled = true);
        } catch (err) {
          if (statusEl) {
            statusEl.textContent = "Failed — try again";
            statusEl.classList.add("text-red-600");
          }
          btn.disabled = false;
        }
      });
    });
  });
}

function render(data) {
  if (!data._id) data._id = newId();
  currentMessages = [data];
  currentResult = data;
  renderConversation();
}
