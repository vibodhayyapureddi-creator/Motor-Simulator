// Live scrolling telemetry charts (uPlot) + recorded-run compare mode.
// Live mode: four charts (RPM, current, torque, temperature) sliding over
// a selectable window. Compare mode: the same four charts showing selected
// recorded runs, each normalized to start at t = 0.

import uPlot from "uplot";
import { getPalette, onThemeChange } from "../theme.js";
import { spectrum } from "./fft.js";

const MAX_SECONDS = 70;   // ring buffer depth (covers the 60 s window)

const CHART_DEFS = [
  { key: "rpm", label: "RPM", color: "#2563eb",
    series: [{ field: "rpm", label: "rpm" },
             { field: "setpoint_rpm", label: "setpoint", dash: [2, 3], width: 1 }] },
  { key: "current", label: "Current (A)", color: "#b45309",
    series: [{ field: "current", label: "A" },
             { field: "current_peak", label: "peak", dash: [4, 4], width: 1 }] },
  { key: "torque", label: "Torque (N·m)", color: "#15803d",
    series: [{ field: "torque", label: "motor" },
             { field: "load_torque", label: "load", dash: [4, 4], width: 1 }] },
  { key: "temperature", label: "Winding °C", color: "#dc2626",
    series: [{ field: "temperature", label: "winding" },
             { field: "housing_temp", label: "housing", dash: [4, 4], width: 1 }] },
];

const OVERLAY_COLORS = ["#2563eb", "#b45309", "#15803d", "#7c3aed", "#db2777", "#0e7490"];

function axisStyle() {
  const pal = getPalette();
  return {
    stroke: pal.axis,
    grid: { stroke: pal.grid, width: 1 },
    ticks: { stroke: pal.ticks },
    font: "10px Segoe UI",
  };
}

function baseOpts(title, width, height) {
  return {
    width, height,
    title: undefined,
    legend: { show: false },
    cursor: { show: true, y: false },
    scales: { x: { time: false } },
    axes: [
      { ...axisStyle(), size: 24 },
      { ...axisStyle(), size: 46 },
    ],
    padding: [8, 8, 0, 0],
  };
}

function seriesStats(values) {
  let n = 0, sum = 0, min = Infinity, max = -Infinity;
  for (const v of values) {
    if (v == null || !isFinite(v)) continue;
    n++; sum += v;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (!n) return null;
  const mean = sum / n;
  let sq = 0;
  for (const v of values) {
    if (v == null || !isFinite(v)) continue;
    sq += (v - mean) * (v - mean);
  }
  return { min, max, mean, std: Math.sqrt(sq / n) };
}

const fmtStat = v => Math.abs(v) >= 1000 ? v.toFixed(0)
  : Math.abs(v) >= 10 ? v.toFixed(1)
  : Math.abs(v) >= 0.01 || v === 0 ? v.toFixed(3) : v.toExponential(1);

function makeBuf() {
  const buf = { t: [] };
  for (const def of CHART_DEFS)
    for (const s of def.series) buf[s.field] = [];
  return buf;
}

export class Charts {
  constructor(container) {
    this.container = container;
    this.windowS = 5;
    this.compare = false;
    this.compareRuns = [];   // [{name, frames}]
    this.dual = false;       // overlay bench B on the live charts
    this.activeBench = "A";
    this.bufs = { A: makeBuf(), B: makeBuf() };
    this.cells = [];
    this.plots = [];
    this.statEls = [];
    this.spectrumOn = CHART_DEFS.map(() => false);   // per-cell FFT view
    this.markers = { A: [], B: [] };   // {t, label} event markers per bench
    this._dirty = true;
    this._buildCells();
    this._ro = new ResizeObserver(() => this._rebuild());
    this._ro.observe(container);
    onThemeChange(() => this._rebuild());
  }

  _buildCells() {
    this.container.innerHTML = "";
    this.cells = CHART_DEFS.map(() => {
      const cell = document.createElement("div");
      cell.className = "chart-cell";
      this.container.appendChild(cell);
      return cell;
    });
    this._rebuild();
  }

  _cellSize() {
    const rect = this.container.getBoundingClientRect();
    return {
      w: Math.max(120, rect.width / 2 - 14),
      h: Math.max(80, rect.height / 2 - 12 - 16),  // 16px for the stats line
    };
  }

  addMarker(bench, t, label) {
    const list = this.markers[bench];
    if (!list) return;
    list.push({ t, label });
    // trim anything past the ring-buffer horizon
    const buf = this.bufs[bench];
    const now = buf.t.length ? buf.t[buf.t.length - 1] : t;
    while (list.length && list[0].t < now - MAX_SECONDS) list.shift();
    if (list.length > 60) list.shift();
    this._dirty = true;
  }

  _drawMarkers(u) {
    if (this.compare) return;
    const list = this.markers[this.activeBench];
    if (!list || !list.length) return;
    const pal = getPalette();
    const { min, max } = u.scales.x;
    const ctx = u.ctx;
    ctx.save();
    ctx.strokeStyle = pal.marker;
    ctx.fillStyle = pal.marker;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.font = "9px Segoe UI";
    ctx.textAlign = "left";
    for (const m of list) {
      if (m.t < min || m.t > max) continue;
      const x = u.valToPos(m.t, "x", true);
      ctx.beginPath();
      ctx.moveTo(x, u.bbox.top);
      ctx.lineTo(x, u.bbox.top + u.bbox.height);
      ctx.stroke();
      ctx.fillText(m.label, x + 2, u.bbox.top + 8);
    }
    ctx.restore();
  }

  _rebuild() {
    const { w, h } = this._cellSize();
    for (const p of this.plots) p.destroy();
    this.plots = [];
    this.statEls = [];
    CHART_DEFS.forEach((def, idx) => {
      const opts = baseOpts(def.label, w, h);
      opts.hooks = { draw: [u => this._drawMarkers(u)] };
      if (this.spectrumOn[idx] && !this.compare) {
        // frequency-domain view of this channel
        opts.series = [
          { label: "Hz" },
          { label: `|${def.label}|`, stroke: def.color, width: 1.5,
            points: { show: false }, fill: def.color + "22" },
        ];
        opts.hooks = {};
        this.cells[idx].innerHTML = "";
        this.plots.push(new uPlot(opts, [[], []], this.cells[idx]));
        this._addCellExtras(idx, def);
        return;
      }
      if (this.compare) {
        opts.legend = { show: true };
        opts.series = [
          { label: "t (s)" },
          ...this.compareRuns.map((run, i) => ({
            label: `${def.label}: ${run.name}`,
            stroke: OVERLAY_COLORS[i % OVERLAY_COLORS.length],
            width: 1.5, points: { show: false },
          })),
        ];
      } else if (this.dual) {
        // both benches, primary field only, A solid / B dashed
        opts.legend = { show: true };
        opts.series = [
          { label: "t (s)" },
          { label: `${def.label} A`, stroke: def.color, width: 1.5,
            points: { show: false } },
          { label: `${def.label} B`, stroke: def.color + "99", width: 1.5,
            dash: [6, 4], points: { show: false } },
        ];
      } else {
        opts.series = [
          { label: "t (s)" },
          ...def.series.map((s, i) => ({
            label: s.label,
            stroke: i === 0 ? def.color : def.color + "88",
            width: s.width ?? 1.5,
            dash: s.dash,
            points: { show: false },
          })),
        ];
      }
      this.cells[idx].innerHTML = "";
      this.plots.push(new uPlot(opts, [[], []], this.cells[idx]));
      this._addCellExtras(idx, def);
    });
    this._dirty = true;
  }

  _addCellExtras(idx, def) {
    const cell = this.cells[idx];
    const stats = document.createElement("div");
    stats.className = "chart-stats";
    cell.appendChild(stats);
    this.statEls.push(stats);
    if (!this.compare) {
      const btn = document.createElement("button");
      btn.className = "fft-btn" + (this.spectrumOn[idx] ? " active" : "");
      btn.textContent = this.spectrumOn[idx] ? "t" : "Hz";
      btn.title = this.spectrumOn[idx]
        ? "Back to the time-series view"
        : "Frequency spectrum of this channel (use slow motion to reach PWM frequencies)";
      btn.addEventListener("click", () => {
        this.spectrumOn[idx] = !this.spectrumOn[idx];
        this._rebuild();
      });
      cell.appendChild(btn);
    }
  }

  setWindow(seconds) { this.windowS = seconds; this._dirty = true; }

  setCompare(on, runs = []) {
    this.compare = on;
    this.compareRuns = runs;
    this._rebuild();
  }

  setDual(on) {
    if (this.dual === on) return;
    this.dual = on;
    this._rebuild();
  }

  setActiveBench(bench) {
    this.activeBench = bench;
    this._dirty = true;
  }

  push(frame) {
    const b = this.bufs[frame.bench || "A"];
    if (!b) return;
    const n = b.t.length;
    if (n && frame.t < b.t[n - 1]) {           // sim was reset: start over
      for (const k of Object.keys(b)) b[k].length = 0;
      const marks = this.markers[frame.bench || "A"];
      if (marks) marks.length = 0;
    }
    b.t.push(frame.t);
    for (const def of CHART_DEFS)
      for (const s of def.series) b[s.field].push(frame[s.field]);
    // trim ring buffer
    const cutoff = frame.t - MAX_SECONDS;
    let drop = 0;
    while (drop < b.t.length && b.t[drop] < cutoff) drop++;
    if (drop > 200) for (const k of Object.keys(b)) b[k].splice(0, drop);
    this._dirty = true;
  }

  _drawSpectrum(idx, def) {
    const b = this.bufs[this.activeBench];
    const field = def.series[0].field;
    const spec = spectrum(b.t, b[field]);
    const plot = this.plots[idx];
    if (!plot) return;
    if (!spec) { plot.setData([[], []]); return; }
    plot.setData([spec.freqs, spec.mags]);
    plot.setScale("x", { min: 0, max: spec.freqs[spec.freqs.length - 1] });
    let pk = 1;   // dominant bin, skipping DC
    for (let i = 2; i < spec.mags.length; i++)
      if (spec.mags[i] > spec.mags[pk]) pk = i;
    if (this.statEls[idx])
      this.statEls[idx].textContent =
        `spectrum · fs ${spec.fs.toFixed(0)} Hz (sim) · peak ${spec.freqs[pk].toFixed(1)} Hz`;
  }

  draw() {
    if (!this._dirty) return;
    this._dirty = false;
    if (this.compare) { this._drawCompare(); return; }
    CHART_DEFS.forEach((def, idx) => {
      if (this.spectrumOn[idx]) this._drawSpectrum(idx, def);
    });
    const b = this.bufs[this.activeBench];
    const n = b.t.length;
    if (!n) return;
    const tEnd = b.t[n - 1];
    const tStart = tEnd - this.windowS;
    let lo = 0, hi = n;                        // binary search window start
    while (lo < hi) { const mid = (lo + hi) >> 1; (b.t[mid] < tStart) ? lo = mid + 1 : hi = mid; }
    const x = b.t.slice(lo);
    if (this.dual) {
      // the other bench runs its own sim clock; both stream at the same
      // frame cadence, so align "now with now" by matching tail samples
      const other = this.bufs[this.activeBench === "A" ? "B" : "A"];
      CHART_DEFS.forEach((def, idx) => {
        if (this.spectrumOn[idx]) return;
        const field = def.series[0].field;
        const own = b[field].slice(lo);
        const tail = other[field].slice(-own.length);
        const aligned = own.length > tail.length
          ? new Array(own.length - tail.length).fill(null).concat(tail)
          : tail;
        const data = this.activeBench === "A"
          ? [x, own, aligned] : [x, aligned, own];
        this.plots[idx].setData(data);
        this.plots[idx].setScale("x", { min: Math.max(0, tStart), max: Math.max(this.windowS, tEnd) });
        this._updateStats(idx, own);
      });
      return;
    }
    CHART_DEFS.forEach((def, idx) => {
      if (this.spectrumOn[idx]) return;
      const primary = b[def.series[0].field].slice(lo);
      const data = [x, ...def.series.map(s => b[s.field].slice(lo))];
      this.plots[idx].setData(data);
      this.plots[idx].setScale("x", { min: Math.max(0, tStart), max: Math.max(this.windowS, tEnd) });
      this._updateStats(idx, primary);
    });
  }

  _updateStats(idx, values) {
    const el = this.statEls[idx];
    if (!el) return;
    const s = seriesStats(values);
    el.textContent = s
      ? `min ${fmtStat(s.min)}   max ${fmtStat(s.max)}   mean ${fmtStat(s.mean)}   σ ${fmtStat(s.std)}`
      : "";
  }

  _drawCompare() {
    if (!this.compareRuns.length) {
      for (const p of this.plots) p.setData([[], []]);
      return;
    }
    // common relative-time grid, linear interpolation per run
    const runs = this.compareRuns.map(run => {
      const t0 = run.frames.length ? run.frames[0].t : 0;
      return { ...run, rel: run.frames.map(f => f.t - t0) };
    });
    const maxDur = Math.max(...runs.map(r => r.rel[r.rel.length - 1] || 0), 0.001);
    const N = 800;
    const x = Array.from({ length: N }, (_, i) => (i / (N - 1)) * maxDur);
    CHART_DEFS.forEach((def, idx) => {
      const field = def.series[0].field;
      const data = [x, ...runs.map(run => resample(run.rel, run.frames, field, x))];
      this.plots[idx].setData(data);
      this.plots[idx].setScale("x", { min: 0, max: maxDur });
    });
  }
}

function resample(rel, frames, field, xs) {
  const out = new Array(xs.length).fill(null);
  if (!rel.length) return out;
  let j = 0;
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i];
    if (x > rel[rel.length - 1]) break;
    while (j < rel.length - 1 && rel[j + 1] < x) j++;
    const a = rel[j], bT = rel[Math.min(j + 1, rel.length - 1)];
    const va = frames[j][field], vb = frames[Math.min(j + 1, rel.length - 1)][field];
    if (!Number.isFinite(va)) continue;   // imported logs may lack a channel
    out[i] = bT > a && Number.isFinite(vb)
      ? va + ((x - a) / (bT - a)) * (vb - va) : va;
  }
  return out;
}
