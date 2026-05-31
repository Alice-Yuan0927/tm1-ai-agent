/* Anomaly-map design tokens.
 *
 * Loaded AFTER js/frontend.tailwind.js so the shared cw-* sidebar palette
 * stays defined — this only ADDS the map's own tokens + display fonts.
 * (The animation keyframes that used to live in the inline config moved to
 * css/anomaly.css, where the elements actually reference them.)
 */
tailwind.config = tailwind.config || { theme: { extend: {} } };
tailwind.config.theme = tailwind.config.theme || {};
tailwind.config.theme.extend = tailwind.config.theme.extend || {};

(function (ext) {
  ext.colors = Object.assign({}, ext.colors, {
    ink:         { DEFAULT: "#1b2433", dim: "#5a6679", faint: "#9aa6b8" },
    line:        { DEFAULT: "#d6dde7", soft: "#e6ebf1" },
    accent:      { DEFAULT: "#0d9488", 50: "#d9f3ef" },
    feeder:      "#d97706",
    ti:          "#7c3aed",
    badge:       "#ff3b30",
    "switch-on": "#34c759",
    canvas:      "#eef1f5",
    surface:     { DEFAULT: "#ffffff", 2: "#fbfcfe" },
    hi:          "#e11d48",
    med:         "#d97706",
    lo:          "#2563eb",
  });
  ext.fontFamily = Object.assign({}, ext.fontFamily, {
    mono: ['"IBM Plex Mono"', "ui-monospace", "monospace"],
    disp: ['"Bricolage Grotesque"', "system-ui", "sans-serif"],
  });
})(tailwind.config.theme.extend);
