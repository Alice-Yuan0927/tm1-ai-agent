function renderInlineMarkdown(text) {
  return esc(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
}

function plainMarkdownText(text) {
  return String(text ?? "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .trim();
}

function parseMarkdownTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.includes("|")) return null;
  return trimmed
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map(cell => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = parseMarkdownTableRow(line);
  return Boolean(cells?.length) && cells.every(cell => /^:?-{3,}:?$/.test(cell));
}

function _parseNum(str) {
  const s = String(str).replace(/[$,\s%]/g, "");
  const m = s.match(/^(-?\d+\.?\d*)([KMBkmb])?$/);
  if (!m) return null;
  const mult = { k: 1e3, m: 1e6, b: 1e9 }[m[2]?.toLowerCase()] ?? 1;
  return parseFloat(m[1]) * mult;
}

function _isMarkdownPercentCol(col) {
  const name = String(col.header).toLowerCase();
  if (/%|percent|percentage|pct|share|ratio|rate/.test(name)) return true;
  const values = col.values.filter(v => v !== null);
  if (!values.length) return false;
  const max = Math.max(...values.map(v => Math.abs(v)));
  const sum = values.reduce((acc, v) => acc + v, 0);
  return max <= 100 && sum >= 90 && sum <= 110;
}

function _markdownCanvas(id, config, height) {
  return `<div class="relative mb-2" style="height:${height}">
    <canvas id="${id}" data-config='${JSON.stringify(config).replace(/'/g, "&#39;")}'></canvas>
  </div>`;
}

function _markdownDoughnutConfig(labels, col, palette) {
  return {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        label: plainMarkdownText(col.header),
        data: col.values.map(v => v ?? 0),
        backgroundColor: labels.map((_, i) => palette[i % palette.length]),
        borderWidth: 2,
        borderColor: "#fff",
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 }, padding: 8 } },
        datalabels: typeof _doughnutDataLabelsConfig === "function"
          ? _doughnutDataLabelsConfig(col.header)
          : undefined,
      },
    },
  };
}

function _buildMarkdownChart(header, body) {
  const labels = body.map(row => plainMarkdownText(row[0]));
  const palette = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4", "#84cc16"];

  const numericCols = [];
  for (let ci = 1; ci < header.length; ci++) {
    const values = body.map(row => _parseNum(row[ci] ?? ""));
    if (values.some(v => v !== null)) numericCols.push({ header: header[ci], values });
  }
  if (!numericCols.length) return "";

  const isTime = labels.some(l => /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|\d{4})/i.test(l.trim()));
  const chartId = `mdchart-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  if (!isTime) {
    const percentCols = numericCols.filter(_isMarkdownPercentCol);
    const valueCols = numericCols.filter(col => !percentCols.includes(col));
    const sections = [];

    if (valueCols.length) {
      const datasets = valueCols.map((col, i) => {
        const color = palette[i % palette.length];
        return {
          label: plainMarkdownText(col.header),
          data: col.values.map(v => v ?? null),
          backgroundColor: color + "bb",
          borderWidth: 0,
        };
      });
      const config = {
        type: "bar",
        data: { labels, datasets },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
            datalabels: typeof _barDataLabelsConfig === "function" ? _barDataLabelsConfig() : undefined,
          },
          scales: {
            x: { ticks: { font: { size: 10 }, maxRotation: labels.length > 6 ? 40 : 0 }, grid: { display: false } },
            y: { ticks: { font: { size: 10 }, maxTicksLimit: 5 }, grid: { color: "#f0f0f0" }, beginAtZero: true },
          },
        },
      };
      sections.push(`<div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">Values</p>
        ${_markdownCanvas(`${chartId}-values`, config, "200px")}
      </div>`);
    }

    percentCols.forEach((col, i) => {
      sections.push(`<div>
        <p class="mb-1 text-[10px] font-semibold uppercase tracking-wide text-cw-muted">${renderInlineMarkdown(col.header)}</p>
        ${_markdownCanvas(`${chartId}-share-${i}`, _markdownDoughnutConfig(labels, col, palette), "200px")}
      </div>`);
    });

    if (sections.length >= 2 || percentCols.length) {
      return `<div class="space-y-4">${sections.join("")}</div>`;
    }
  }

  const isPie  = numericCols.length === 1 && labels.length <= 12 && !isTime;

  let config;
  if (isPie) {
    config = {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ label: plainMarkdownText(numericCols[0].header), data: numericCols[0].values.map(v => v ?? 0), backgroundColor: palette.slice(0, labels.length), borderWidth: 2, borderColor: "#fff" }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { position: "right", labels: { boxWidth: 10, font: { size: 11 }, padding: 8 } },
          datalabels: typeof _doughnutDataLabelsConfig === "function"
            ? _doughnutDataLabelsConfig(numericCols[0].header)
            : undefined,
        },
      },
    };
  } else {
    const datasets = numericCols.map((col, i) => {
      const color = palette[i % palette.length];
      return {
        label: plainMarkdownText(col.header),
        data: col.values.map(v => v ?? null),
        spanGaps: true,
        borderColor: color,
        backgroundColor: isTime ? color + "18" : color + "bb",
        borderWidth: isTime ? 2 : 0,
        pointRadius: isTime ? 3 : 0,
        fill: isTime, tension: 0.35,
      };
    });
    config = {
      type: isTime ? "line" : "bar",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 10, font: { size: 11 }, padding: 10 } },
          datalabels: typeof _barDataLabelsConfig === "function" ? _barDataLabelsConfig() : undefined,
        },
        scales: {
          x: { ticks: { font: { size: 10 }, maxRotation: 0 }, grid: { display: false } },
          y: { ticks: { font: { size: 10 }, maxTicksLimit: 5 }, grid: { color: "#f0f0f0" } },
        },
      },
    };
  }

  const height = isPie ? "180px" : "200px";
  return _markdownCanvas(chartId, config, height);
}

function renderMarkdownTable(rows) {
  if (rows.length < 2) return "";
  const header = rows[0];
  const body = rows.slice(1);

  const head = header.map((cell, index) => {
    const align = index === 0 ? "text-left" : "text-right";
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-cw-muted">${renderInlineMarkdown(cell)}</th>`;
  }).join("");

  const bodyRows = body.map(row => {
    const cells = header.map((_, index) => {
      const value = row[index] ?? "";
      const align = index === 0 ? "text-left font-medium text-cw-text" : "text-right font-mono text-cw-blueText";
      return `<td class="${align} whitespace-nowrap border-b border-cw-borderLow px-3 py-2">${renderInlineMarkdown(value)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite/60">${cells}</tr>`;
  }).join("");

  const tableHtml = `<div class="my-3 overflow-x-auto rounded-xl border border-cw-border bg-white shadow-soft">
    <table class="w-full border-collapse text-[12.5px] leading-5">
      <thead><tr>${head}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
  </div>`;

  return tableHtml + _buildMarkdownChart(header, body);
}

function renderMarkdown(text) {
  const lines = String(text ?? "").split(/\r?\n/);
  const html = [];
  let listItems = [];

  const flushList = () => {
    if (!listItems.length) return;
    html.push(`<ul class="my-3 list-disc space-y-1 pl-6">${listItems.join("")}</ul>`);
    listItems = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { flushList(); continue; }

    if (/^---+$/.test(trimmed)) {
      flushList();
      html.push('<hr class="my-5 border-cw-border" />');
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = heading[1].length;
      const classes = {
        1: "mb-4 mt-1 text-[22px] font-semibold leading-tight text-cw-text",
        2: "mb-3 mt-6 text-[18px] font-semibold leading-tight text-cw-text",
        3: "mb-2 mt-5 text-[15px] font-semibold leading-tight text-cw-text",
      }[level];
      html.push(`<h${level} class="${classes}">${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const tableStart = parseMarkdownTableRow(trimmed);
    const separatorIndex = (() => {
      let probe = i + 1;
      while (probe < lines.length && !lines[probe].trim()) probe += 1;
      return isMarkdownTableSeparator(lines[probe] || "") ? probe : -1;
    })();
    if (tableStart && separatorIndex !== -1) {
      flushList();
      const tableRows = [tableStart];
      i = separatorIndex;
      while (i + 1 < lines.length) {
        const next = lines[i + 1].trim();
        if (!next) {
          let probe = i + 2;
          while (probe < lines.length && !lines[probe].trim()) probe += 1;
          if (!parseMarkdownTableRow(lines[probe] || "")) break;
          i = probe - 1;
          continue;
        }
        const row = parseMarkdownTableRow(next);
        if (!row || isMarkdownTableSeparator(next)) break;
        tableRows.push(row);
        i += 1;
      }
      html.push(renderMarkdownTable(tableRows));
      continue;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listItems.push(`<li>${renderInlineMarkdown(bullet[1])}</li>`);
      continue;
    }

    flushList();
    html.push(`<p class="mb-2 last:mb-0">${renderInlineMarkdown(trimmed)}</p>`);
  }

  flushList();
  return html.join("");
}
