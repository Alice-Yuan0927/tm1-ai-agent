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

  const rowDimensions = preview.row_dimensions || [];
  const measureColumns = preview.columns || [];
  const filters = preview.filters || [];
  const headers = [...rowDimensions, ...measureColumns];

  const filterChips = filters.length
    ? `<div class="mb-3 flex flex-wrap gap-2">
        ${filters.map(f => `
          <div class="inline-flex items-center gap-1.5 rounded-md border border-cw-border bg-white px-2.5 py-1 text-[12px] shadow-sm">
            <span class="font-medium text-cw-muted">${esc(f.dimension)}:</span>
            <span class="font-semibold text-cw-text">${esc(f.element)}</span>
          </div>`).join("")}
      </div>`
    : "";

  const head = headers.map((header, index) => {
    const align = index >= rowDimensions.length ? "text-right" : "text-left";
    return `<th class="${align} whitespace-nowrap border-b border-cw-border bg-cw-bg px-3 py-2 text-[11px] font-semibold text-cw-text">${esc(header)}</th>`;
  }).join("");

  const body = preview.rows.map(row => {
    const cells = headers.map((header, index) => {
      const isMeasure = index >= rowDimensions.length;
      const value = row[header] ?? "";
      const align = isMeasure ? "text-right font-mono text-cw-blueText" : "text-left text-cw-text";
      return `<td class="${align} whitespace-nowrap border-b border-cw-borderLow px-3 py-2">${fmt(value)}</td>`;
    }).join("");
    return `<tr class="last:[&_td]:border-b-0 hover:[&_td]:bg-cw-blueLite">${cells}</tr>`;
  }).join("");

  return `${filterChips}
    <div class="overflow-x-auto rounded-[10px] border border-cw-border bg-white">
      <table class="w-full border-collapse text-[12px] leading-5">
        <thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
}
