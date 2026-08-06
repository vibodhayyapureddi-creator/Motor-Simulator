// Warning lights in the header + backend/rt/connection chips.

const LIGHTS = [
  ["overcurrent", "Overcurrent", "bad"],
  ["overheat", "Overheating", "bad"],
  ["stall", "Stalled", "bad"],
  ["sag", "Voltage sag", "warn"],
  ["numeric", "Numeric fault", "bad"],
];

export class Indicators {
  constructor() {
    const holder = document.getElementById("warning-lights");
    this.lights = {};
    for (const [key, label, severity] of LIGHTS) {
      const el = document.createElement("span");
      el.className = "warnlight";
      el.textContent = label;
      el.dataset.severity = severity;
      holder.appendChild(el);
      this.lights[key] = el;
    }
    this.chipBackend = document.getElementById("chip-backend");
    this.chipRt = document.getElementById("chip-rt");
    this.chipConn = document.getElementById("chip-conn");
  }

  update(frame) {
    for (const [key, el] of Object.entries(this.lights)) {
      const lit = !!frame.flags[key];
      el.classList.toggle(`lit-${el.dataset.severity}`, lit);
    }
    const ctl = frame.ctl;
    this.chipBackend.textContent =
      ctl.backend === "cpp" ? "C++ engine" : "Python engine";
    const rt = ctl.rt_factor;
    this.chipRt.textContent = `${rt.toFixed(2)}× real time`;
    this.chipRt.classList.toggle("warn", rt < 0.9);
  }

  setConnected(ok) {
    this.chipConn.textContent = ok ? "Connected" : "Offline";
    this.chipConn.classList.toggle("good", ok);
    this.chipConn.classList.toggle("bad", !ok);
  }
}
