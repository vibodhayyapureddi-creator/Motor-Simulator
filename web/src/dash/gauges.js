// Analog instrument gauges, hand-drawn on canvas.
// Each gauge sweeps 240 degrees with a needle, tick marks, a digital
// readout, and an optional red zone near the top of the range.
// Colors come from the shared theme palette (redrawn every frame, so a
// theme switch takes effect immediately).

import { getPalette } from "../theme.js";

const SWEEP = (Math.PI * 4) / 3;          // 240°
const START = Math.PI / 2 + SWEEP / 2;    // pointing down-left

function niceCeil(v) {
  if (v <= 0 || !isFinite(v)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * mag) return m * mag;
  return 10 * mag;
}

class Gauge {
  constructor(parent, { label, unit, min = 0, max = 100, redFrom = null, decimals = 0 }) {
    this.label = label; this.unit = unit;
    this.min = min; this.max = max; this.redFrom = redFrom;
    this.decimals = decimals;
    this.value = min;
    this.displayed = min;   // eased needle
    this.cell = document.createElement("div");
    this.cell.className = "gauge-cell";
    this.canvas = document.createElement("canvas");
    this.canvas.width = 300; this.canvas.height = 232;
    this.cell.appendChild(this.canvas);
    parent.appendChild(this.cell);
    this.ctx = this.canvas.getContext("2d");
  }

  setRange(min, max, redFrom = null) {
    this.min = min; this.max = niceCeil(max); this.redFrom = redFrom;
  }

  set(value) { this.value = value; }

  _angle(v) {
    const f = Math.min(1, Math.max(0, (v - this.min) / (this.max - this.min || 1)));
    return START - f * SWEEP + Math.PI;  // canvas: clockwise from left end
  }

  draw() {
    // ease the needle toward the target for an analog feel
    this.displayed += (this.value - this.displayed) * 0.25;
    const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
    const cx = W / 2, cy = H / 2 + 14, R = 88;
    ctx.clearRect(0, 0, W, H);
    const pal = getPalette();

    const a0 = this._angle(this.min), a1 = this._angle(this.max);
    // main arc
    ctx.beginPath(); ctx.arc(cx, cy, R, a0, a1);
    ctx.lineWidth = 6; ctx.strokeStyle = pal.gaugeArc; ctx.stroke();
    // red zone
    if (this.redFrom != null && this.redFrom < this.max) {
      ctx.beginPath(); ctx.arc(cx, cy, R, this._angle(this.redFrom), a1);
      ctx.strokeStyle = pal.gaugeRed; ctx.stroke();
    }
    // ticks + numbers
    ctx.fillStyle = pal.gaugeNum; ctx.font = "10px Segoe UI";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const nTicks = 8;
    for (let i = 0; i <= nTicks; i++) {
      const v = this.min + (i / nTicks) * (this.max - this.min);
      const a = this._angle(v);
      const c = Math.cos(a), s = Math.sin(a);
      ctx.beginPath();
      ctx.moveTo(cx + c * (R - 6), cy + s * (R - 6));
      ctx.lineTo(cx + c * (R + 4), cy + s * (R + 4));
      ctx.lineWidth = 1; ctx.strokeStyle = i % 2 ? pal.tickMinor : pal.tickMajor; ctx.stroke();
      if (i % 2 === 0) {
        const txt = Math.abs(v) >= 1000 ? `${Math.round(v / 100) / 10}k` : `${Math.round(v * 100) / 100}`;
        ctx.fillText(txt, cx + c * (R - 20), cy + s * (R - 20));
      }
    }
    // needle
    const av = this._angle(Math.min(this.max, Math.max(this.min, this.displayed)));
    const inRed = this.redFrom != null && this.displayed >= this.redFrom;
    ctx.beginPath();
    ctx.moveTo(cx - Math.cos(av) * 12, cy - Math.sin(av) * 12);
    ctx.lineTo(cx + Math.cos(av) * (R - 14), cy + Math.sin(av) * (R - 14));
    ctx.lineWidth = 2; ctx.strokeStyle = inRed ? pal.needleRed : pal.needle; ctx.stroke();
    ctx.beginPath(); ctx.arc(cx, cy, 4, 0, Math.PI * 2);
    ctx.fillStyle = pal.needle; ctx.fill();

    // labels
    ctx.fillStyle = pal.gaugeLabel; ctx.font = "11px Segoe UI";
    ctx.fillText(this.label, cx, cy - R / 2 - 4);
    ctx.fillStyle = pal.gaugeValue; ctx.font = "600 16px Segoe UI";
    ctx.fillText(this.value.toFixed(this.decimals), cx, cy + R / 2 + 10);
    ctx.fillStyle = pal.gaugeUnit; ctx.font = "10px Segoe UI";
    ctx.fillText(this.unit, cx, cy + R / 2 + 26);
  }
}

export class Dashboard {
  constructor(container) {
    // electrical/mechanical power + efficiency readout, spanning the grid
    this.powerStrip = document.createElement("div");
    this.powerStrip.className = "power-strip";
    this.powerCells = {};
    for (const [key, label] of [["p_in", "In"], ["p_out", "Out"],
                                ["p_loss", "Loss"], ["eff", "Efficiency"],
                                ["batt", "Battery"]]) {
      const cell = document.createElement("div");
      cell.className = "power-cell";
      const val = document.createElement("span");
      val.className = "power-val";
      val.textContent = "-";
      const lab = document.createElement("span");
      lab.className = "power-label";
      lab.textContent = label;
      cell.append(val, lab);
      this.powerStrip.appendChild(cell);
      this.powerCells[key] = val;
    }
    container.appendChild(this.powerStrip);

    this.gauges = {
      rpm:     new Gauge(container, { label: "Speed", unit: "RPM", max: 10000 }),
      current: new Gauge(container, { label: "Current", unit: "A", max: 20, decimals: 2 }),
      torque:  new Gauge(container, { label: "Torque", unit: "N·m", max: 1, decimals: 3 }),
      temp:    new Gauge(container, { label: "Winding", unit: "°C", min: 0, max: 160, redFrom: 120, decimals: 1 }),
      voltage: new Gauge(container, { label: "Voltage", unit: "V", max: 24, decimals: 1 }),
      load:    new Gauge(container, { label: "Load", unit: "N·m", max: 1, decimals: 3 }),
    };

    // click any gauge for its governing equation with live numbers
    this._lastFrame = null;
    this._params = null;
    this._pop = null;
    for (const [key, gauge] of Object.entries(this.gauges)) {
      gauge.canvas.style.cursor = "pointer";
      gauge.canvas.title = "Click for the equation behind this gauge";
      gauge.canvas.setAttribute("role", "img");
      gauge.canvas.setAttribute("aria-label", `${gauge.label} gauge`);
      gauge.canvas.addEventListener("click", () =>
        this._showFormula(key, gauge.cell));
    }
    this._ariaTick = 0;
  }

  // derive sensible full-scale values from the motor's own physics
  rescale(params, ctl) {
    this._params = params;
    const vmax = params.max_voltage ?? 24;
    const ke = params.back_emf_constant || 0.05;
    const r = params.resistance || 1;
    const kt = params.torque_constant || 0.05;
    const rpmMax = (vmax / ke) * (60 / (2 * Math.PI));
    const iStall = vmax / r;
    const iLimit = ctl?.limit_enabled && ctl?.limit_a > 0 ? ctl.limit_a : null;
    this.gauges.rpm.setRange(0, rpmMax * 1.1);
    this.gauges.current.setRange(0, iStall, iLimit ?? iStall * 0.8);
    this.gauges.torque.setRange(0, kt * iStall);
    this.gauges.load.setRange(0, kt * iStall);
    this.gauges.voltage.setRange(0, vmax * 1.05);
    const oh = ctl?.overheat_c ?? 120;
    this.gauges.temp.setRange(0, oh + 40, oh);
  }

  // ------------------------------------------ equation popovers (plan §6)

  _formula(key) {
    const f = this._lastFrame, p = this._params;
    if (!f || !p) return null;
    const r = p.resistance, ke = p.back_emf_constant, kt = p.torque_constant;
    const V = f.voltage, I = f.current, w = f.omega;
    const n = (v, d = 3) => Number(v).toFixed(d).replace(/\.?0+$/, "") || "0";
    switch (key) {
      case "rpm": {
        const west = (V - I * r) / ke;
        return { title: "Speed: voltage balance", lines: [
          "ω = (V − I·R) / Ke",
          `ω = (${n(V, 2)} − ${n(I, 2)} × ${n(r)}) / ${n(ke)} = ${n(west, 1)} rad/s`,
          `= ${n(west * 60 / (2 * Math.PI), 0)} RPM`,
          "The applied voltage splits between the winding drop I·R and the " +
          "back-EMF Ke·ω; at steady state they balance exactly." ] };
      }
      case "current":
        return { title: "Current: Ohm's law with back-EMF", lines: [
          "I = (V − Ke·ω) / R",
          `I = (${n(V, 2)} − ${n(ke)} × ${n(w, 1)}) / ${n(r)} = ${n((V - ke * w) / r, 2)} A`,
          "At standstill (ω = 0) this is the full inrush V/R; back-EMF " +
          "chokes it off as speed builds." ] };
      case "torque":
        return { title: "Torque: motor constant", lines: [
          "τ = Kt · I",
          `τ = ${n(kt)} × ${n(I, 2)} = ${n(kt * I, 4)} N·m`,
          "Torque is directly proportional to winding current, which is " +
          "why the current limit is also a torque limit." ] };
      case "temp": {
        const heat = I * I * r;
        return { title: "Winding temperature: lumped thermal model", lines: [
          "C·dT/dt = I²R − (T − T_amb)/R_th",
          `heating I²R = ${n(I, 2)}² × ${n(r)} = ${n(heat, 2)} W`,
          `cooling (T − T_amb)/R_th = (${n(f.temperature, 1)} − ${n(f.ctl.ambient_c, 0)})/R_th`,
          "Temperature settles where resistive heating equals heat shed " +
          "to ambient." ] };
      }
      case "voltage":
        return { title: "Applied voltage", lines: [
          "V = min(throttle, V_bus) × sag − limiter fold-back",
          `now: ${n(V, 2)} V (throttle ${n(f.ctl.throttle_v, 1)} V)`,
          "What the drive actually puts across the winding after supply " +
          "sag, battery droop and current-limit fold-back." ] };
      case "load": {
        const kind = f.ctl.load.kind, prm = f.ctl.load.params || {};
        const forms = {
          none: ["τ_load = 0", "Free shaft. Only internal friction opposes."],
          constant: [`τ_load = ${n(prm.torque ?? 0)} N·m (fixed)`,
            "Speed-independent opposing torque, like a friction brake."],
          viscous: ["τ = c·ω",
            `= ${n(prm.coefficient ?? 0)} × ${n(w, 1)} = ${n((prm.coefficient ?? 0) * w, 4)} N·m`],
          fan: ["τ = k·ω²",
            `= ${n(prm.coefficient ?? 0)} × ${n(w, 1)}² = ${n((prm.coefficient ?? 0) * w * w, 4)} N·m`,
            "Aerodynamic load: torque grows with the square of speed."],
          pump: ["τ = a + b·ω²",
            `= ${n(prm.static_torque ?? 0)} + ${n(prm.coefficient ?? 0)}·ω² = ${n(f.load_torque, 4)} N·m`],
          wheel: ["τ = n·(Crr·m·g·r + ½ρ·CdA·v²·r),  v = n·ω·r",
            `now ${n(f.load_torque, 4)} N·m, from rolling resistance plus aero drag through the gearing.`],
          flywheel: ["τ ≈ c_bearing·ω  (inertia dominates)",
            `J_extra = ½·m·r². Large stored energy, very little steady drag.`],
        };
        return { title: `Load: ${kind}`, lines: forms[kind] || ["τ_load"] };
      }
    }
    return null;
  }

  _showFormula(key, cell) {
    const info = this._formula(key);
    if (!info) return;
    this._pop?.remove();
    const pop = document.createElement("div");
    pop.className = "note-card gauge-pop";
    const head = document.createElement("div");
    head.className = "note-head";
    const title = document.createElement("span");
    title.textContent = info.title;
    const close = document.createElement("button");
    close.textContent = "×";
    close.addEventListener("click", () => pop.remove());
    head.append(title, close);
    pop.appendChild(head);
    for (const line of info.lines) {
      const p = document.createElement("p");
      p.textContent = line;
      pop.appendChild(p);
    }
    document.body.appendChild(pop);
    const r = cell.getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 330))}px`;
    pop.style.top = `${Math.min(r.top + 20, innerHeight - pop.offsetHeight - 12)}px`;
    this._pop = pop;
    setTimeout(() => pop.remove(), 20000);
  }

  update(frame) {
    this._lastFrame = frame;
    const watts = w => (Math.abs(w) >= 100 ? w.toFixed(0) : w.toFixed(1)) + " W";
    this.powerCells.p_in.textContent = watts(frame.p_in ?? 0);
    this.powerCells.p_out.textContent = watts(frame.p_out ?? 0);
    this.powerCells.p_loss.textContent = watts(Math.max(0, (frame.p_in ?? 0) - (frame.p_out ?? 0)));
    this.powerCells.eff.textContent = frame.efficiency > 0
      ? `${(frame.efficiency * 100).toFixed(0)} %` : "-";
    const batt = frame.battery;
    this.powerCells.batt.textContent = batt
      ? `${Math.round(batt.soc * 100)}% · ${batt.voltage.toFixed(1)} V` : "-";
    this.powerCells.batt.title = batt && batt.energy_recovered_wh > 0
      ? `${batt.energy_recovered_wh.toFixed(2)} Wh recovered by regen braking` : "";
    // refresh screen-reader labels about once a second, not per frame
    if (++this._ariaTick >= 60) {
      this._ariaTick = 0;
      for (const gauge of Object.values(this.gauges))
        gauge.canvas.setAttribute("aria-label",
          `${gauge.label}: ${gauge.value.toFixed(gauge.decimals)} ${gauge.unit}`);
    }
    this.gauges.rpm.set(Math.abs(frame.rpm));
    this.gauges.current.set(Math.abs(frame.current));
    this.gauges.torque.set(Math.abs(frame.torque));
    this.gauges.temp.set(frame.temperature);
    this.gauges.voltage.set(Math.abs(frame.voltage));
    this.gauges.load.set(Math.abs(frame.load_torque));
  }

  draw() { for (const g of Object.values(this.gauges)) g.draw(); }
}
