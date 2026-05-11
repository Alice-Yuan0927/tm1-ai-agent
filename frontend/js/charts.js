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

function _makeChartConfig(isTime, labels, datasets) {
  const xRotation = !isTime && labels.length > 6 ? 40 : 0;
  return {
    type: isTime ? "line" : "bar",
    data: { labels, datasets },
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
}

function _numValue(value) {
  if (typeof value === "number") return value;
  const parsed = parseFloat(String(value ?? "").replace(/[$€£¥,\s%]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function _isPercentColumn(col, rows) {
  const name = String(col).toLowerCase();
  if (/%|percent|percentage|pct|share|ratio|rate/.test(name)) return true;
  const values = rows.map(row => _numValue(row[col]));
  const max = Math.max(...values.map(v => Math.abs(v)));
  const sum = values.reduce((acc, v) => acc + v, 0);
  return max <= 100 && sum >= 90 && sum <= 110;
}

function _rowLabels(rows, rowDims) {
  return rows.map((row, i) =>
    rowDims.map(d => row[d]).filter(Boolean).join(" / ") || `Row ${i + 1}`
  );
}

function _makeMeasureBarConfig(labels, cols, rows, palette) {
  return {
    type: "bar",
    data: {
      labels,
      datasets: cols.map((col, i) => ({
        label: col,
        data: rows.map(row => _numValue(row[col])),
        backgroundColor: palette[i % palette.length] + "cc",
        borderWidth: 0,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
      },
      scales: {
        x: { ticks: { font: { size: 10 }, maxRotation: labels.length > 6 ? 40 : 0 }, grid: { display: false } },
        y: { ticks: { font: { size: 10 }, maxTicksLimit: 5 }, grid: { color: "#f0f0f0" }, beginAtZero: true },
      },
    },
  };
}

function _makeDoughnutConfig(labels, data, label, palette) {
  return {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        label,
        data,
        backgroundColor: labels.map((_, i) => palette[i % palette.length] + "cc"),
        borderColor: "#fff",
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 }, padding: 8 } },
      },
    },
  };
}

function _makeDatasets(isTime, cols, rows, rowDims, palette) {
  return rows.map((row, i) => {
    const label = rowDims.map(d => row[d]).filter(Boolean).join(" / ") || `Row ${i + 1}`;
    const data = cols.map(col => _numValue(row[col]));
    const color = palette[i % palette.length];
    return {
      label, data,
      borderColor: color,
      backgroundColor: isTime ? color + "18" : color + "cc",
      borderWidth: isTime ? 2 : 0,
      pointRadius: isTime ? 3 : 0,
      fill: isTime,
      tension: 0.35,
    };
  });
}

function _canvasHtml(id, config, height) {
  return `<div class="relative" style="height:${height}px">
    <canvas id="${id}" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'></canvas>
  </div>`;
}

function buildChart(source, chartId) {
  const preview = source.structured_preview;
  if (!preview?.rows?.length || !preview?.columns?.length) return "";
  const { columns, rows, row_dimensions: rowDims = [] } = preview;
  if (columns.length < 2) return "";

  const isTime = typeof preview.column_dim_is_time === "boolean"
    ? preview.column_dim_is_time
    : _isTimeSeriesFallback(columns);

  const palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];
  const chartRows = rows.slice(0, 6);

  // Skip chart when no column contains numeric data (e.g. attribute-only tables)
  const hasNumeric = chartRows.some(row =>
    columns.some(col => {
      const v = row[col];
      if (typeof v === "number") return true;
      if (typeof v === "string" && v.trim() !== "") return !isNaN(parseFloat(v.replace(/,/g, "")));
      return false;
    })
  );
  if (!hasNumeric) return "";

  // ── Time-series: single line chart, no splitting needed ───────────────────
  if (isTime) {
    const datasets = _makeDatasets(true, columns, chartRows, rowDims, palette);
    const config = _makeChartConfig(true, columns, datasets);
    return `<div class="mt-4">${_canvasHtml(chartId, config, 200)}</div>`;
  }

  if (rowDims.length && chartRows.length >= 2) {
    const labels = _rowLabels(chartRows, rowDims);
    const numericCols = columns.filter(col =>
      chartRows.some(row => Number.isFinite(_numValue(row[col])))
    );
    const percentCols = numericCols.filter(col => _isPercentColumn(col, chartRows));
    const valueCols = numericCols.filter(col => !percentCols.includes(col));
    const sections = [];

    if (valueCols.length) {
      const config = _makeMeasureBarConfig(labels, valueCols, chartRows, palette);
      sections.push(`<div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">Values</p>
        ${_canvasHtml(chartId + "-values", config, labels.length > 6 ? 230 : 200)}
      </div>`);
    }

    percentCols.forEach((col, i) => {
      const config = _makeDoughnutConfig(
        labels,
        chartRows.map(row => _numValue(row[col])),
        col,
        palette,
      );
      sections.push(`<div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">${esc(col)}</p>
        ${_canvasHtml(`${chartId}-pct-${i}`, config, 200)}
      </div>`);
    });

    if (sections.length >= 2 || percentCols.length) {
      return `<div class="mt-4 space-y-4">${sections.join("")}</div>`;
    }
  }

  // ── Categorical: detect consolidated vs leaf elements ─────────────────────
  const consSet = new Set(preview.consolidated_columns || []);
  const _consName = /^(all\b|total$|totals$)/i;
  const isCons = c => consSet.has(c) || _consName.test(String(c).trim());

  const consCols = columns.filter(isCons);
  const leafCols = columns.filter(c => !isCons(c));

  // Both consolidated and leaf present → dual-chart split
  // Require ≥2 consolidated so a single-bar "Summary" chart isn't rendered alone
  if (consCols.length >= 2 && leafCols.length >= 2) {
    const summaryDatasets  = _makeDatasets(false, consCols, chartRows, rowDims, palette);
    const breakdownDatasets = _makeDatasets(false, leafCols, chartRows, rowDims, palette);

    const summaryConfig   = _makeChartConfig(false, consCols, summaryDatasets);
    const breakdownConfig = _makeChartConfig(false, leafCols, breakdownDatasets);

    const breakdownHeight = leafCols.length > 6 ? 230 : 200;
    const summaryId  = chartId + "-s";
    const breakdownId = chartId + "-b";

    return `<div class="mt-4 space-y-4">
      <div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">Summary</p>
        ${_canvasHtml(summaryId, summaryConfig, 160)}
      </div>
      <div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">Breakdown</p>
        ${_canvasHtml(breakdownId, breakdownConfig, breakdownHeight)}
      </div>
    </div>`;
  }

  // Only consolidated elements (no leaves) — show as-is
  if (leafCols.length < 2) {
    const datasets = _makeDatasets(false, columns, chartRows, rowDims, palette);
    const config = _makeChartConfig(false, columns, datasets);
    const height = columns.length > 6 ? 230 : 200;
    return `<div class="mt-4">${_canvasHtml(chartId, config, height)}</div>`;
  }

  // Only leaf elements (no consolidated) — show as-is
  const datasets = _makeDatasets(false, leafCols, chartRows, rowDims, palette);
  const config = _makeChartConfig(false, leafCols, datasets);
  const height = leafCols.length > 6 ? 230 : 200;
  const note = consCols.length
    ? `<p class="mt-1 text-[10px] text-cw-muted">${consCols.length} consolidated element(s) excluded from chart</p>`
    : "";
  return `<div class="mt-4">${_canvasHtml(chartId, config, height)}</div>${note}`;
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
