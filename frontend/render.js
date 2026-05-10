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

function downloadCsv(msgIdx, srcIdx) {
  const msg = currentMessages[msgIdx];
  if (!msg) return;
  const sources = Array.isArray(msg.data_sources) && msg.data_sources.length
    ? msg.data_sources
    : [{ cube: msg.chosen_cube, data_preview: msg.data_preview || [] }];
  const source = sources[srcIdx];
  if (!source) return;

  let rows;
  const preview = source.structured_preview;
  if (preview?.rows?.length) {
    const headers = [...(preview.row_dimensions || []), ...(preview.columns || [])];
    rows = [headers, ...preview.rows.map(row => headers.map(h => row[h] ?? ""))];
  } else {
    const raw = source.data_preview || [];
    if (!raw.length) return;
    const keys = Object.keys(raw[0]);
    rows = [keys, ...raw.map(row => keys.map(k => row[k] ?? ""))];
  }

  const csv = rows.map(row =>
    row.map(v => {
      const s = String(v ?? "");
      return /[,"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    }).join(",")
  ).join("\n");

  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), {
    href: url,
    download: `${(source.cube || "data").replace(/[/\\?%*:|"<>]/g, "-")}.csv`,
  });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function _sourceCardsHtml(sources, reasoning, skippedSources) {
  const cards = sources.map((source, i) => `
    <div class="rounded-lg border border-cw-border bg-white px-4 py-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div class="${cls.label}">Source ${i + 1}</div>
        <span class="shrink-0 rounded-full border border-[#9de3c5] bg-cw-greenBg px-2 py-0.5 text-[10px] font-semibold text-[#0d7a4c]">${Number(source.data_row_count || 0).toLocaleString()} rows</span>
      </div>
      <div class="mb-1 font-mono text-[12.5px] font-medium text-cw-blue">${esc(source.cube)}</div>
      <div class="text-[12px] leading-5 text-cw-sub">${esc(source.reasoning || "")}</div>
    </div>`).join("");

  const skippedNotice = skippedSources.length
    ? `<div class="mt-3 rounded-lg border border-cw-border bg-white/70 px-4 py-3 text-[12px] leading-5 text-cw-muted">
        Skipped ${skippedSources.length} source${skippedSources.length === 1 ? "" : "s"} with no usable data: ${skippedSources.map(s => esc(s.cube)).join(", ")}.
      </div>`
    : "";

  return `<div class="grid grid-cols-1 gap-3">${cards}</div>
    <div class="mt-3 rounded-lg border border-cw-blueMid bg-cw-blueLite px-4 py-3 text-[13px] leading-relaxed text-cw-sub">${esc(reasoning)}</div>
    ${skippedNotice}`;
}

function _tableSectionsHtml(sources, msgIdx) {
  return sources.map((source, index) => {
    const chartId = `chart-${Date.now()}-${index}`;
    const csvBtn = msgIdx != null
      ? `<button type="button" onclick="downloadCsv(${msgIdx},${index})"
           class="flex items-center gap-1 rounded-md border border-cw-border bg-white px-2 py-0.5 text-[11px] text-cw-muted transition hover:border-cw-blue hover:text-cw-blue">
           <i class="fa-solid fa-download text-[9px]"></i> CSV
         </button>`
      : "";
    return `
    <div class="mb-4 last:mb-0">
      <div class="mb-2 flex items-baseline justify-between gap-3">
        <h3 class="text-[14px] font-semibold text-cw-text">${esc(source.cube)}</h3>
        <div class="flex items-center gap-2">
          ${csvBtn}
          <span class="shrink-0 rounded-full border border-[#9de3c5] bg-cw-greenBg px-2.5 py-0.5 text-[11px] font-semibold text-[#0d7a4c]">${Number(source.data_row_count || 0).toLocaleString()} rows</span>
        </div>
      </div>
      <div class="mb-2 text-xs text-cw-muted">Preview: first ${(source.structured_preview?.rows || source.data_preview || []).length} rows</div>
      ${buildTm1Preview(source)}
      ${buildChart(source, chartId)}
    </div>`;
  }).join("");
}

// Partial article rendered during streaming (no analysis text, no charts yet)
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

function renderSingleMessage(data, msgIdx) {
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
          ${data.suggestions.map(s => `<button type="button" onclick="setQ(${JSON.stringify(s)})"
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
        ${_tableSectionsHtml(sources, msgIdx)}
      </section>
      <section>
        <div class="text-[15px] leading-8 text-cw-sub [&_strong]:font-semibold [&_strong]:text-cw-text [&_em]:italic">${renderMarkdown(data.analysis)}</div>
        ${suggestionsHtml}
      </section>
    </article>
  </div>`;
}

function renderConversation() {
  setChatMode(true);
  updateShareStatus("");
  document.getElementById("out").innerHTML = currentMessages.map((msg, i) => renderSingleMessage(msg, i)).join("");
  initCharts();
}

function render(data) {
  currentMessages = [data];
  currentResult = data;
  renderConversation();
}
