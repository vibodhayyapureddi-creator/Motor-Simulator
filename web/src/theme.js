// Shared theme state. CSS handles DOM styling via [data-theme] variable
// overrides in styles.css; canvas/WebGL surfaces (gauges, charts, 3D
// scene) can't read CSS variables, so they pull their colors from this
// palette and re-render on change.

const PALETTES = {
  light: {
    // gauges (canvas)
    gaugeArc: "#e3e6ea", gaugeRed: "#f2c4c4",
    tickMinor: "#dde0e5", tickMajor: "#b6bcc6", gaugeNum: "#9aa1ac",
    needle: "#3b4252", needleRed: "#dc2626",
    gaugeLabel: "#6b7280", gaugeValue: "#1f2430", gaugeUnit: "#9aa1ac",
    // charts (uPlot)
    axis: "#8a919d", grid: "#eef0f3", ticks: "#dde0e5", marker: "#9aa1ac",
    // 3D scene
    sceneBg: 0xf0f1f4, ground: 0xe4e6ea, grid1: 0xd2d6dc, grid2: 0xdfe2e7,
  },
  dark: {
    gaugeArc: "#3a4658", gaugeRed: "#7a2626",
    tickMinor: "#2b3444", tickMajor: "#66748c", gaugeNum: "#8494ab",
    needle: "#9fb3cc", needleRed: "#ff5252",
    gaugeLabel: "#8494ab", gaugeValue: "#dbe4f0", gaugeUnit: "#8494ab",
    axis: "#8494ab", grid: "#242d3d", ticks: "#2b3444", marker: "#66748c",
    sceneBg: 0x0d1117, ground: 0x141a24, grid1: 0x2b3444, grid2: 0x1c2330,
  },
};

const listeners = [];

export function getTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function getPalette() { return PALETTES[getTheme()]; }

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("msp-theme", theme); } catch { /* private mode */ }
  for (const fn of listeners) fn(getPalette());
}

export function onThemeChange(fn) { listeners.push(fn); }

export function initTheme() {
  let saved = "light";
  try { saved = localStorage.getItem("msp-theme") || "light"; } catch { }
  document.documentElement.dataset.theme = saved;
}
