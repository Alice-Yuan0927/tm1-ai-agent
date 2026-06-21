export const ANOMALY_API = "";
export const SEVERITY_LABEL = v => (v >= 66 ? "high" : v >= 33 ? "med" : "low");
export const SCAN_PERIOD = { year: "2026", month: "Mar" };

// Collections mutated in-place — safe to export directly
export const TM1_DATA = { cubes: [], edges: [], processLinks: [] };
export const pos = {};
export const nodeEls = {};
export const edgeEls = [];
export const selected = new Set();

// Scalar / reassignable state wrapped in one object to avoid ES-module live-binding issues
export const st = {
  cubeById: {},
  layers: {},
  maxLayer: 0, maxRows: 1, midY: 0, mapH: 0, canvasH: 0, canvasW: 0,
  mapZoom: 1,
  scanned: false,
  drawerOpen: false,
  selectMode: false,
};
