// Inline physics annotations + a guided tour.
//
// Annotations: when the sim crosses into an interesting condition (limiter
// engages, stall latches, supply sags, winding overheats, PWM turns on),
// a small dismissible card appears over the 3D scene explaining what is
// physically happening. Each explanation has a cooldown so it teaches
// without nagging.
//
// Tour: a short step-by-step walkthrough of the UI, launched from the
// header button. One floating card, the current target outlined.

const NOTES = {
  overcurrent: {
    title: "Current limiter engaged",
    text: "The winding current hit the configured ceiling, so the drive is " +
          "folding back its output voltage — exactly how a real controller " +
          "protects itself. Torque is capped along with the current.",
  },
  stall: {
    title: "Stall",
    text: "The load needs more torque than the motor can make, so the shaft " +
          "has stopped while full drive is still applied. With no back-EMF " +
          "opposing it, current heads toward V/R and everything turns to heat.",
  },
  sag: {
    title: "Supply voltage sag",
    text: "The bus voltage just dropped. Less voltage means less current and " +
          "torque, so speed falls until the back-EMF matches the weaker " +
          "supply — watch it recover as the bus comes back.",
  },
  overheat: {
    title: "Winding overheating",
    text: "I²R losses are heating the winding faster than it can shed heat " +
          "to ambient. If resistance feedback is on, copper's resistance " +
          "rises with temperature and the motor visibly derates.",
  },
  pwm: {
    title: "PWM drive",
    text: "The controller is now chopping the full bus voltage on and off. " +
          "Mean voltage = duty × bus, and the winding inductance turns the " +
          "chopping into a current ripple — drop to 0.02× time to see it.",
  },
  inrush: {
    title: "Inrush current",
    text: "At standstill there is no back-EMF, so the winding briefly draws " +
          "near its locked-rotor current. It decays as speed (and back-EMF) " +
          "builds. Slow motion makes this spike easy to watch.",
  },
};

const COOLDOWN_MS = 60_000;
const SHOW_MS = 14_000;

export class Annotations {
  constructor(container) {
    this.container = container;
    this.enabled = true;
    this._last = {};        // key -> last shown timestamp
    this._prevFlags = {};
    this._prevPwm = false;
    this._prevRunning = false;
  }

  update(frame) {
    if (!this.enabled) return;
    const flags = frame.flags || {};
    for (const key of ["overcurrent", "stall", "sag", "overheat"]) {
      if (flags[key] && !this._prevFlags[key]) this._show(key);
    }
    this._prevFlags = { ...flags };

    const pwmOn = !!frame.ctl.pwm?.enabled;
    if (pwmOn && !this._prevPwm && frame.ctl.running) this._show("pwm");
    this._prevPwm = pwmOn;

    // inrush: the moment the motor starts from (near) standstill
    const running = frame.ctl.running;
    if (running && !this._prevRunning && Math.abs(frame.rpm) < 50) this._show("inrush");
    this._prevRunning = running;
  }

  _show(key) {
    const now = performance.now();
    if (now - (this._last[key] || -Infinity) < COOLDOWN_MS) return;
    this._last[key] = now;
    const note = NOTES[key];
    if (!note) return;

    const card = document.createElement("div");
    card.className = "note-card";
    const head = document.createElement("div");
    head.className = "note-head";
    const title = document.createElement("span");
    title.textContent = note.title;
    const close = document.createElement("button");
    close.textContent = "×";
    close.addEventListener("click", () => card.remove());
    head.append(title, close);
    const body = document.createElement("p");
    body.textContent = note.text;
    card.append(head, body);
    this.container.appendChild(card);
    while (this.container.children.length > 3) this.container.firstChild.remove();
    setTimeout(() => card.remove(), SHOW_MS);
  }
}

// --------------------------------------------------------------------- tour

const TOUR_STEPS = [
  { sel: "#preset-select", title: "Presets",
    text: "Start from a real-world motor: gearmotor, drill, drone BLDC, PC " +
          "fan or e-bike hub. Loading one sets the motor, load, limits and " +
          "thermal model together." },
  { sel: "#bench-group", title: "Two benches",
    text: "A and B are fully independent simulations. Pick which one the " +
          "controls address here, and tick “both” to see them side by side " +
          "— handy for DC vs BLDC or before/after a parameter change." },
  { sel: "#tabs", title: "Controls",
    text: "Drive (voltage or PWM, live parameters), mechanical loads, " +
          "faults and limits, time control (slow motion!), scripted " +
          "scenarios, and run recording all live in these tabs." },
  { sel: "#dash-panel", title: "Instruments",
    text: "Live gauges plus electrical power in, mechanical power out, " +
          "losses and efficiency. Ranges rescale to the motor you load." },
  { sel: "#charts-panel", title: "Telemetry",
    text: "Scrolling charts of speed, current (with the per-frame peak), " +
          "torque and winding temperature. Compare mode overlays recorded " +
          "runs; dual mode overlays bench B." },
  { sel: "#scene-panel", title: "The motor",
    text: "Drag to orbit, scroll to zoom. The housing glows with heat, " +
          "copper pulses with current, the attached load spins on the " +
          "shaft, and a BLDC lights up its commutation sector." },
];

export class Tour {
  constructor(button) {
    this.button = button;
    this.idx = -1;
    this.card = null;
    this.target = null;
    button.addEventListener("click", () => this.start());
  }

  start() { this.idx = -1; this.next(); }

  next() { this._go(this.idx + 1); }
  back() { this._go(this.idx - 1); }

  end() {
    if (this.target) this.target.classList.remove("tour-target");
    if (this.card) this.card.remove();
    this.card = null;
    this.target = null;
    this.idx = -1;
  }

  _go(i) {
    if (i < 0 || i >= TOUR_STEPS.length) { this.end(); return; }
    if (this.target) this.target.classList.remove("tour-target");
    this.idx = i;
    const step = TOUR_STEPS[i];
    const target = document.querySelector(step.sel);
    if (!target) { this.end(); return; }
    this.target = target;
    target.classList.add("tour-target");

    if (!this.card) {
      this.card = document.createElement("div");
      this.card.className = "tour-card";
      document.body.appendChild(this.card);
    }
    this.card.innerHTML = "";
    const title = document.createElement("div");
    title.className = "tour-title";
    title.textContent = `${i + 1}/${TOUR_STEPS.length} — ${step.title}`;
    const body = document.createElement("p");
    body.textContent = step.text;
    const row = document.createElement("div");
    row.className = "btn-row";
    const mk = (label, fn, primary = false) => {
      const btn = document.createElement("button");
      btn.className = "action" + (primary ? " primary" : "");
      btn.textContent = label;
      btn.addEventListener("click", fn);
      row.appendChild(btn);
    };
    if (i > 0) mk("Back", () => this.back());
    mk(i === TOUR_STEPS.length - 1 ? "Done" : "Next", () => this.next(), true);
    mk("Skip", () => this.end());
    this.card.append(title, body, row);

    // place the card near the target, clamped to the viewport
    const r = target.getBoundingClientRect();
    const cw = 320, pad = 10;
    let left = Math.min(Math.max(r.left, pad), window.innerWidth - cw - pad);
    let top = r.bottom + pad;
    if (top > window.innerHeight - 220) top = Math.max(pad, r.top - 200);
    this.card.style.left = `${left}px`;
    this.card.style.top = `${top}px`;
  }
}
