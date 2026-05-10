const _chartInstances = {};

function buildChart(source, chartId) {
  const preview = source.structured_preview;
  if (!preview?.rows?.length || !preview?.columns?.length) return "";
  const { columns, rows, row_dimensions: rowDims = [] } = preview;
  if (columns.length < 2) return "";

  const chartRows = rows.slice(0, 6);
  const isTime = columns.some(c => /^(0[1-9]|1[0-2])$/.test(String(c).trim()));
  const palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];

  const datasets = chartRows.map((row, i) => {
    const label = rowDims.map(d => row[d]).filter(Boolean).join(" / ") || `Row ${i + 1}`;
    const data = columns.map(col => {
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

  const config = {
    type: isTime ? "line" : "bar",
    data: { labels: columns, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
      },
      scales: {
        x: { ticks: { font: { size: 10 }, maxRotation: 0 }, grid: { display: false } },
        y: { ticks: { font: { size: 10 }, maxTicksLimit: 5 }, grid: { color: "#f0f0f0" } },
      },
    },
  };

  return `<div class="mt-4 relative" style="height:200px">
    <canvas id="${chartId}" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'></canvas>
  </div>`;
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
