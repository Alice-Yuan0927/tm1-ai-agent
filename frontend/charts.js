const _chartInstances = {};

// Regex fallback for when backend dim_metadata is not yet available
// (e.g. old cached messages or pre-sync state)
const _TIME_PATTERNS_FB = [
  /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i,
  /^(january|february|march|april|may|june|july|august|september|october|november|december)/i,
  /^(0[1-9]|1[0-2])$/,
  /^q[1-4](\b|$)/i,
  /^(19|20)\d{2}$/,
  /^(fy|cy)\d{2,4}$/i,
  /^(period|month|quarter|week|wk)\s*\d+/i,
];

function _isTimeSeriesFallback(columns) {
  const matches = columns.filter(c =>
    _TIME_PATTERNS_FB.some(p => p.test(String(c).trim()))
  ).length;
  // Require majority to avoid numeric employee IDs being mistaken for months
  return matches >= Math.ceil(columns.length * 0.6);
}

function buildChart(source, chartId) {
  const preview = source.structured_preview;
  if (!preview?.rows?.length || !preview?.columns?.length) return "";
  const { columns, rows, row_dimensions: rowDims = [] } = preview;
  if (columns.length < 2) return "";

  // ── Determine chart type ──────────────────────────────────────────────────
  // Prefer backend-supplied flag (populated after sync-schema with real TM1
  // element attributes).  Fall back to regex heuristic for old cached data.
  const isTime = typeof preview.column_dim_is_time === "boolean"
    ? preview.column_dim_is_time
    : _isTimeSeriesFallback(columns);

  // ── Filter columns for non-time (categorical) charts ─────────────────────
  // Exclude Consolidated elements (TM1 rollup parents) — backend-supplied list.
  // If after exclusion fewer than 2 remain, fall back to all columns.
  let chartColumns = columns;
  if (!isTime) {
    const consSet = new Set(preview.consolidated_columns || []);
    const filtered = columns.filter(c => !consSet.has(c));
    chartColumns = filtered.length >= 2 ? filtered : columns;
  }

  if (chartColumns.length < 2) return "";

  const palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];
  const chartRows = rows.slice(0, 6);

  const datasets = chartRows.map((row, i) => {
    const label = rowDims.map(d => row[d]).filter(Boolean).join(" / ") || `Row ${i + 1}`;
    const data = chartColumns.map(col => {
      const v = row[col];
      return typeof v === "number" ? v : parseFloat(String(v ?? "").replace(/,/g, "")) || 0;
    });
    const color = palette[i % palette.length];
    return {
      label,
      data,
      borderColor: color,
      backgroundColor: isTime ? color + "18" : color + "cc",
      borderWidth: isTime ? 2 : 0,
      pointRadius: isTime ? 3 : 0,
      fill: isTime,
      tension: 0.35,
    };
  });

  const xRotation = !isTime && chartColumns.length > 6 ? 40 : 0;

  const config = {
    type: isTime ? "line" : "bar",
    data: { labels: chartColumns, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
      },
      scales: {
        x: {
          ticks: { font: { size: 10 }, maxRotation: xRotation, minRotation: xRotation },
          grid: { display: false },
        },
        y: {
          ticks: { font: { size: 10 }, maxTicksLimit: 5 },
          grid: { color: "#f0f0f0" },
          beginAtZero: false,
        },
      },
    },
  };

  const removedCons = (preview.consolidated_columns || []).filter(c => columns.includes(c));
  const truncated   = !isTime && columns.length > chartColumns.length;
  const parts = [];
  if (removedCons.length) parts.push(`${removedCons.length} consolidated element(s) excluded`);
  if (truncated && chartColumns.length < columns.length - removedCons.length)
    parts.push(`showing top ${chartColumns.length} of ${columns.length - removedCons.length}`);
  const note = parts.length
    ? `<p class="mt-1 text-[10px] text-cw-muted">${parts.join(" · ")}</p>`
    : "";

  const height = !isTime && xRotation > 0 ? 230 : 200;
  return `<div class="mt-4 relative" style="height:${height}px">
    <canvas id="${chartId}" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'></canvas>
  </div>${note}`;
}

function initCharts() {
  document.querySelectorAll("canvas[data-config]").forEach(canvas => {
    const id = canvas.id;
    if (_chartInstances[id]) { _chartInstances[id].destroy(); }
    try {
      const config = JSON.parse(canvas.dataset.config);
      _chartInstances[id] = new Chart(canvas, config);
    } catch { /* skip if Chart.js not loaded */ }
    canvas.removeAttribute("data-config");
  });
}
