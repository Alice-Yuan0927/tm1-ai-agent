// Transpose decision is made by the backend (build_structured_preview sets
// preview.transpose = true when column count exceeds the configured threshold).
// The frontend just reads the flag — no client-side threshold here.

function buildTable(rows) {
  if (!rows?.length) {
    return '<p class="text-[13px] text-cw-muted">No preview available</p>';
  }
  const keys = Object.keys(rows[0]);
  const head = keys.map(key =>
    `<th class="whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-left text-[10.5px] font-semibold uppercase tracking-[0.04em] text-cw-muted">${esc(key)}</th>`
  ).join("");
  const body = rows.map(row => {
    const cells = keys.map(key => {
      const numeric = typeof row[key] === "number";
      const align = numeric ? "text-right font-medium text-cw-blueText" : "text-cw-text";
      return `<td class="whitespace-nowrap border-b border-cw-borderLow px-3 py-[7px] ${align}">${fmt(row[key])}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${cells}</tr>`;
  }).join("");
  return `<div class="overflow-x-auto rounded-[10px] border border-cw-border"><table class="w-full border-collapse font-mono text-[11.5px]"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function buildTm1Preview(source) {
  const preview = source.structured_preview;
  if (!preview?.rows?.length) return buildTable(source.data_preview || []);

  const rowDimensions  = preview.row_dimensions || [];
  const measureColumns = preview.columns || [];
  const filters        = preview.filters || [];

  const filterChips = filters.length
    ? `<div class="mb-3 flex flex-wrap gap-2">
        ${filters.map(f => `
          <div class="inline-flex items-center gap-1.5 rounded-md border border-cw-border bg-white px-2.5 py-1 text-[12px] shadow-sm">
            <span class="font-medium text-cw-muted">${esc(f.dimension)}:</span>
            <span class="font-semibold text-cw-text">${esc(f.element)}</span>
          </div>`).join("")}
      </div>`
    : "";

  const table = preview.transpose
    ? _transposedTable(preview, rowDimensions, measureColumns)
    : _normalTable(preview, rowDimensions, measureColumns);

  return filterChips + table;
}

// ── Normal layout ─────────────────────────────────────────────────────────────

function _normalTable(preview, rowDimensions, measureColumns) {
  const headers = [...rowDimensions, ...measureColumns];

  const head = headers.map((h, i) => {
    const align = i >= rowDimensions.length ? "text-right" : "text-left";
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[11px] font-semibold text-cw-text">${esc(h)}</th>`;
  }).join("");

  const body = preview.rows.map(row => {
    const cells = headers.map((h, i) => {
      const isMeasure = i >= rowDimensions.length;
      const val   = row[h] ?? "";
      const align = isMeasure
        ? "text-right font-mono text-cw-blueText"
        : "text-left text-cw-text";
      return `<td class="${align} whitespace-nowrap border-b border-cw-borderLow px-3 py-2">${fmt(val)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${cells}</tr>`;
  }).join("");

  return _tableWrap(head, body);
}

// ── Transposed layout — measures become rows, original rows become columns ────

function _transposedTable(preview, rowDimensions, measureColumns) {
  // Build a label for each original data row (becomes a column header)
  const colLabels = preview.rows.map(row =>
    rowDimensions.map(d => row[d]).filter(Boolean).join(" / ") || "Value"
  );
  const measureHeader = preview.measure_dimension || "Measure";
  const allHeaders    = [measureHeader, ...colLabels];

  const head = allHeaders.map((h, i) => {
    const align = i === 0 ? "text-left" : "text-right";
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[11px] font-semibold text-cw-text">${esc(h)}</th>`;
  }).join("");

  const body = measureColumns.map((col, ri) => {
    const stripe = ri % 2 === 1 ? "bg-blue-50/40" : "";
    const labelCell = `<td class="whitespace-nowrap border-b border-cw-borderLow px-3 py-2 text-left font-medium text-cw-text ${stripe}">${esc(col)}</td>`;
    const valueCells = preview.rows.map(row => {
      const val = row[col] ?? "";
      return `<td class="whitespace-nowrap border-b border-cw-borderLow px-3 py-2 text-right font-mono text-cw-blueText ${stripe}">${fmt(val)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${labelCell}${valueCells}</tr>`;
  }).join("");

  return _tableWrap(head, body);
}

function _tableWrap(head, body) {
  return `<div class="overflow-x-auto rounded-[10px] border border-cw-border bg-white">
    <table class="w-full border-collapse text-[12px] leading-5">
      <thead><tr>${head}</tr></thead>
      <tbody>${body}</tbody>
    </table>
  </div>`;
}
