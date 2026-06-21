import { ANOMALY_API, SEVERITY_LABEL, SCAN_PERIOD, TM1_DATA } from "./anomaly-state.js";

export function htmlAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function getScanPeriodLabel() {
  return `${SCAN_PERIOD.year}-${SCAN_PERIOD.month}`;
}

export function rulesFromRuleSet(rs) {
  const out = [];
  (rs.integrity || []).forEach((r, idx) => {
    if (r.enabled === false) return;
    out.push({
      name: r.name,
      expression: (r.columns && r.columns.length)
        ? `columns: ${r.columns.join(", ")}`
        : "built-in deterministic check",
      threshold: "integrity",
      desc: `Built-in integrity rule "${r.name}".`,
      type: "integrity",
      srcIndex: idx,
    });
  });
  (rs.variance || []).forEach((r, idx) => {
    if (r.enabled === false) return;
    const pct = Math.round((r.rel_pct || 0) * 100);
    const ref = r.reference_label || r.reference || "reference";
    out.push({
      name: r.name,
      expression: `ABS( ${r.measure} - ${ref} ) > ${pct}% AND > ${r.abs_value}`,
      threshold: `${pct}% / ${r.abs_value}`,
      desc: r.description || `Variance of ${r.measure} vs ${ref}.`,
      type: "variance",
      srcIndex: idx,
    });
  });
  return out;
}

export function dimsFromRuleSet(rs) {
  const dims = new Set();
  (rs.variance || []).forEach(r => (r.group_by || []).forEach(d => dims.add(d)));
  (rs.integrity || []).forEach(r => (r.group_by || []).forEach(d => dims.add(d)));
  return [...dims];
}

export function mapFlag(f) {
  const key = f.key || {};
  const loc = Object.entries(key).map(([, v]) => `[${v}]`).join(".")
    || f.dedupe_id || f.rule || "-";
  const detail = f.detail || {};
  const sev = typeof f.severity === "number"
    ? SEVERITY_LABEL(f.severity)
    : (f.severity || "low");
  return {
    location: loc,
    measure: f.measure || Object.keys(detail)[0] || f.rule || "",
    value: f.value != null ? f.value : (Object.values(detail)[0] ?? ""),
    expected: f.expected || "",
    deviation: f.deviation || "",
    rule: f.rule || "",
    severity: sev,
  };
}

export async function loadData() {
  let cubes = [];
  try {
    const res = await fetch(`${ANOMALY_API}/api/anomaly/cubes`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cubes = data.cubes || [];
    TM1_DATA.edges = (data.relationships || []).map(rel => ({
      from: rel.from,
      to: rel.to,
      type: rel.type === "process" ? "ti" : rel.type,
      source: rel.source || "",
      snippet: rel.snippet || "",
    }));
    TM1_DATA.processLinks = (data.process_links || []).map(link => ({
      process: link.process || "",
      cube: link.cube || "",
      role: link.role || "",
      datasource_type: link.datasource_type || "",
      object: link.object || "",
      snippet: link.snippet || "",
    }));
  } catch (err) {
    console.warn("[anomaly] /api/anomaly/cubes failed, falling back to /api/views", err);
    TM1_DATA.processLinks = [];
    const res = await fetch(`${ANOMALY_API}/api/views`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cubes = (data.cubes || []).map(c => ({
      cube: c.cube,
      description: c.description || c.cube,
      dimensions: [],
      rules: { cube: c.cube, integrity: [], variance: [] },
    }));
  }
  const cubesPerLayer = 4;
  const processLinksByCube = TM1_DATA.processLinks.reduce((acc, link) => {
    if (link.cube) (acc[link.cube] ??= []).push(link);
    return acc;
  }, {});
  TM1_DATA.cubes = cubes.map((item, i) => {
    const cubeName = item.cube || item.name || `Cube ${i + 1}`;
    const rs = item.rules || { cube: cubeName, integrity: [], variance: [] };
    const dims = Array.isArray(item.dimensions)
      ? item.dimensions.map(d => d.name).filter(Boolean)
      : dimsFromRuleSet(rs);
    return {
      id: cubeName,
      name: cubeName,
      description: item.description || cubeName,
      layer: Math.floor(i / cubesPerLayer),
      dims,
      dimensions: item.dimensions || [],
      processLinks: processLinksByCube[cubeName] || [],
      rawRuleset: rs,
      rules: rulesFromRuleSet(rs),
      anomalies: [],
    };
  });
  TM1_DATA.edges = TM1_DATA.edges.filter(e =>
    TM1_DATA.cubes.some(c => c.id === e.from) &&
    TM1_DATA.cubes.some(c => c.id === e.to)
  );
}

export async function fetchScans(targets) {
  let stub = false;
  await Promise.all(targets.map(async c => {
    c.anomalies = [];
    try {
      const res = await fetch(`${ANOMALY_API}/api/anomaly/scan/${encodeURIComponent(c.id)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ period: getScanPeriodLabel() }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.stub) { stub = true; return; }
      const flags = data.flags || (data.result && data.result.flags) || [];
      c.anomalies = flags.map(mapFlag);
    } catch {
      /* leave this cube with no anomalies */
    }
  }));
  return { stub };
}
