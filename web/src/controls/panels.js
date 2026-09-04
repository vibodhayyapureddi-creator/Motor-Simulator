// Control panels: Drive / Load / Faults / Time / Record
// tabs built into #tabs / #tab-bodies. Each control maps 1:1 onto a
// protocol command (docs/PROTOCOL.md); incoming ctl state is mirrored back
// into the widgets so every client stays in sync, without fighting the
// user's own drags (focused/dragging inputs are never overwritten).

const PARAM_DEFS = [
  { key: "resistance",        label: "R (Ω)",        step: 0.01 },
  { key: "inductance",        label: "L (H)",        step: 0.0001 },
  { key: "torque_constant",   label: "Kt (N·m/A)",   step: 0.001 },
  { key: "back_emf_constant", label: "Ke (V·s/rad)", step: 0.001 },
  { key: "inertia",           label: "J (kg·m²)",    step: 0.0001 },
  { key: "viscous_friction",  label: "B (N·m·s)",    step: 0.0001 },
  { key: "static_friction",   label: "T_fric (N·m)", step: 0.001 },
  { key: "max_voltage",       label: "V max (V)",    step: 1 },
  { key: "pole_pairs",        label: "Pole pairs",   step: 1, only: ["bldc", "induction"] },
  { key: "ripple_depth",      label: "Ripple 0-1",   step: 0.01, only: ["bldc"] },
  { key: "holding_torque",    label: "Hold τ (N·m)", step: 0.05, only: ["stepper"] },
  { key: "step_angle_deg",    label: "Step (°)",     step: 0.45, only: ["stepper"] },
  { key: "rated_current",     label: "I rated (A)",  step: 0.1, only: ["stepper"] },
  { key: "pullout_corner",    label: "Pull-out (st/s)", step: 50, only: ["stepper"] },
  { key: "breakdown_torque",  label: "τ break (N·m)", step: 0.5, only: ["induction"] },
  { key: "breakdown_slip",    label: "Slip break",   step: 0.01, only: ["induction"] },
  { key: "magnetizing_current", label: "I magnetize (A)", step: 0.1, only: ["induction"] },
  { key: "rated_frequency",   label: "f rated (Hz)", step: 1, only: ["induction"] },
];

const MOTOR_TYPE_LABELS = [["dc", "DC"], ["bldc", "BLDC"],
                           ["stepper", "Step"], ["induction", "AC"]];

const LOAD_DEFS = {
  none:     { label: "None (free shaft)", params: [] },
  constant: { label: "Constant torque (brake)", params: [
    { key: "torque", label: "Torque (N·m)", def: 0.01, step: 0.005 }] },
  viscous:  { label: "Viscous drag (brake)", params: [
    { key: "coefficient", label: "c (N·m·s/rad)", def: 1e-4, step: 1e-5 }] },
  fan:      { label: "Fan / propeller", params: [
    { key: "coefficient", label: "k (N·m·s²)", def: 2e-7, step: 1e-8 }] },
  pump:     { label: "Pump", params: [
    { key: "static_torque", label: "Static head (N·m)", def: 0.01, step: 0.005 },
    { key: "coefficient", label: "b (N·m·s²)", def: 2e-7, step: 1e-8 }] },
  wheel:    { label: "Wheel / vehicle", params: [
    { key: "mass", label: "Mass (kg)", def: 20, step: 1 },
    { key: "radius", label: "Wheel radius (m)", def: 0.15, step: 0.01 },
    { key: "gear_ratio", label: "Gear (wheel/motor)", def: 0.2, step: 0.01 },
    { key: "rolling_coeff", label: "Rolling coeff", def: 0.015, step: 0.001 },
    { key: "drag_area", label: "Cd·A (m²)", def: 0.4, step: 0.05 },
    { key: "wheel_inertia", label: "Wheel J (kg·m²)", def: 0.05, step: 0.01 }] },
  flywheel: { label: "Flywheel", params: [
    { key: "mass", label: "Disk mass (kg)", def: 2.0, step: 0.1 },
    { key: "radius", label: "Disk radius (m)", def: 0.08, step: 0.01 },
    { key: "bearing_drag", label: "Bearing c", def: 1e-5, step: 1e-6 }] },
};

const TABS = [
  ["drive", "Drive"], ["load", "Load"], ["control", "PID"],
  ["faults", "Faults"], ["time", "Time"], ["script", "Script"],
  ["record", "Record"],
];

function el(tag, cls = "", text = "") {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function numField(parent, label, value, step, onCommit) {
  const field = el("div", "ctl-field");
  field.appendChild(el("label", "", label));
  const input = el("input");
  input.type = "number";
  input.step = step;
  input.value = value;
  input.addEventListener("change", () => {
    const v = parseFloat(input.value);
    if (Number.isFinite(v)) onCommit(v);
  });
  field.appendChild(input);
  parent.appendChild(field);
  return input;
}

function sliderRow(parent, label, { min, max, step, value, fmt, onInput }) {
  const row = el("div", "ctl-row");
  row.appendChild(el("label", "", label));
  const input = el("input");
  input.type = "range";
  input.min = min; input.max = max; input.step = step; input.value = value;
  const val = el("span", "val", fmt(value));
  input.addEventListener("input", () => {
    const v = parseFloat(input.value);
    val.textContent = fmt(v);
    onInput(v);
  });
  row.append(input, val);
  parent.appendChild(row);
  return { input, val, set(v) { input.value = v; val.textContent = fmt(v); } };
}

// a range/number input the user is interacting with must not be synced over
function busy(input) {
  return document.activeElement === input || input.matches(":active");
}

// Parse a telemetry CSV (a real motor log, a scope export, or this app's
// own run export) into chart-overlay frames. Needs a header row with a
// `t` column; any recognized channel columns come along (docs/PROTOCOL.md).
function parseCsvLog(text) {
  const lines = text.split(/\r?\n/).filter(l => l.trim());
  if (lines.length < 3) return null;
  const cols = lines[0].split(",").map(s => s.trim().toLowerCase());
  const ti = cols.indexOf("t");
  if (ti < 0) return null;
  const known = ["rpm", "current", "current_peak", "torque", "load_torque",
                 "temperature", "voltage", "omega", "p_in", "p_out", "efficiency"];
  const idx = {};
  for (const f of known) {
    const i = cols.indexOf(f);
    if (i >= 0) idx[f] = i;
  }
  if (!Object.keys(idx).length) return null;
  const frames = [];
  for (let li = 1; li < lines.length; li++) {
    const parts = lines[li].split(",");
    const t = parseFloat(parts[ti]);
    if (!Number.isFinite(t)) continue;
    const frame = { t };
    for (const [f, i] of Object.entries(idx)) {
      const v = parseFloat(parts[i]);
      if (Number.isFinite(v)) frame[f] = v;
    }
    frames.push(frame);
  }
  return frames.length >= 2 ? frames : null;
}

export class ControlPanels {
  constructor(socket, { onCompareSelectionChange, onShare, onSaveSession,
                        onLoadSession, onStepTest, onAutoTune, onReplay,
                        onReport, onCommand } = {}) {
    this.socket = socket;
    this.onCompareSelectionChange = onCompareSelectionChange || (() => {});
    this.onShare = onShare || (() => {});
    this.onSaveSession = onSaveSession || (() => {});
    this.onLoadSession = onLoadSession || (() => {});
    this.onStepTest = onStepTest || (async () => "no measurement hook");
    this.onAutoTune = onAutoTune || (async () => "no tuning hook");
    this.onReplay = onReplay || (() => {});
    this.onReport = onReport || (() => {});
    this.onCommand = onCommand || (() => {});
    this.roomQS = (p) => p;      // main.js swaps in the room-aware version
    this.hwConnected = false;
    this.bench = "A";          // which bench the panels address
    this.lastT = 0;            // latest sim time of the active bench
    this._macro = null;        // {t0, steps} while recording actions
    this._lastCtl = {};
    this._runs = [];
    this._scenarios = [];
    this.importedRuns = new Map();   // name -> frames (client-side logs)
    this._compareSel = new Set();

    const tabsNav = document.getElementById("tabs");
    const bodies = document.getElementById("tab-bodies");
    this.bodies = {};
    for (const [key, label] of TABS) {
      const btn = el("button", "", label);
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", "false");
      btn.addEventListener("click", () => {
        tabsNav.querySelectorAll("button").forEach(b => {
          b.classList.remove("active");
          b.setAttribute("aria-selected", "false");
        });
        Object.values(this.bodies).forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        this.bodies[key].classList.add("active");
      });
      tabsNav.appendChild(btn);
      const body = el("div", "tab-body");
      body.setAttribute("role", "tabpanel");
      body.setAttribute("aria-label", label);
      bodies.appendChild(body);
      this.bodies[key] = body;
    }
    // arrow keys walk the tabs, per the ARIA tabs pattern
    tabsNav.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      const btns = [...tabsNav.querySelectorAll("button")];
      const cur = btns.findIndex(b => b.classList.contains("active"));
      const next = (cur + (ev.key === "ArrowRight" ? 1 : -1) + btns.length) % btns.length;
      btns[next].click();
      btns[next].focus();
      ev.preventDefault();
    });
    const first = tabsNav.querySelector("button");
    first.classList.add("active");
    first.setAttribute("aria-selected", "true");
    this.bodies.drive.classList.add("active");

    this._buildDrive();
    this._buildLoad();
    this._buildControl();
    this._buildFaults();
    this._buildTime();
    this._buildScript();
    this._buildRecord();
  }

  // every command is addressed to the bench these panels control
  send(msg) {
    this._recordMacro(msg);
    this.onCommand(msg, this.bench);
    this.socket.send({ ...msg, bench: this.bench });
  }
  sendDebounced(msg, key = null, delay = 60) {
    this._recordMacro(msg);
    this.onCommand(msg, this.bench);
    this.socket.sendDebounced({ ...msg, bench: this.bench },
                              `${this.bench}:${key || msg.type}`, delay);
  }

  // ------------------------------------------------- action macro recorder

  _recordMacro(msg) {
    const macro = this._macro;
    if (!macro || msg.type === "scenario" || msg.type === "record") return;
    const t = Math.max(0, Math.round((this.lastT - macro.t0) * 1000) / 1000);
    const last = macro.steps[macro.steps.length - 1];
    // collapse slider spam: same command type within 150 ms keeps the latest
    if (last && last.do.type === msg.type && t - last.t < 0.15) {
      last.do = { ...msg };
      return;
    }
    macro.steps.push({ t, do: { ...msg } });
  }

  _toggleMacro() {
    if (this._macro) {
      const steps = this._macro.steps;
      this._macro = null;
      this.btnMacro.textContent = "Record actions";
      this.btnMacro.classList.remove("armed");
      if (steps.length) {
        this.scriptText.value = JSON.stringify(steps, null, 2);
        this.setScenarioStatus(
          `Captured ${steps.length} step${steps.length > 1 ? "s" : ""}. Edit it, then press Run.`);
      } else {
        this.setScenarioStatus("Nothing captured. Drive the motor while the recorder is running.");
      }
    } else {
      this._macro = { t0: this.lastT, steps: [] };
      this.btnMacro.textContent = "Stop capturing";
      this.btnMacro.classList.add("armed");
      this.setScenarioStatus("Capturing your actions as scenario steps");
    }
  }

  setBench(bench) {
    this.bench = bench;
    this._lastCtl = {};   // force a full widget resync from the next ctl
  }

  // ---------------------------------------------------------------- Drive

  _buildDrive() {
    const b = this.bodies.drive;

    const modeRow = el("div", "ctl-row");
    modeRow.appendChild(el("label", "", "Drive mode"));
    this.modeGroup = el("div", "btn-group");
    for (const [txt, on] of [["Voltage", false], ["PWM", true]]) {
      const btn = el("button", "", txt);
      btn.dataset.pwm = on ? "1" : "";
      btn.addEventListener("click", () =>
        this.send({ type: "set_pwm", enabled: on }));
      this.modeGroup.appendChild(btn);
    }
    modeRow.appendChild(this.modeGroup);
    b.appendChild(modeRow);

    this.voltage = sliderRow(b, "Throttle (V)", {
      min: 0, max: 24, step: 0.1, value: 0,
      fmt: v => `${v.toFixed(1)} V`,
      onInput: v => this.sendDebounced({ type: "set_voltage", value: v }),
    });
    this.voltageRow = this.voltage.input.parentElement;

    // PWM controls (drive chops the full bus voltage at duty x frequency)
    this.duty = sliderRow(b, "Duty cycle", {
      min: 0, max: 100, step: 1, value: 50,
      fmt: v => `${v} %`,
      onInput: v => this.sendDebounced({ type: "set_pwm", duty: v / 100 }, "pwm_duty"),
    });
    this.dutyRow = this.duty.input.parentElement;
    this.pwmFreq = sliderRow(b, "Switching (Hz)", {
      min: 50, max: 2000, step: 10, value: 500,
      fmt: v => `${v} Hz`,
      onInput: v => this.sendDebounced({ type: "set_pwm", frequency: v }, "pwm_freq"),
    });
    this.pwmFreqRow = this.pwmFreq.input.parentElement;
    this.dutyRow.style.display = "none";
    this.pwmFreqRow.style.display = "none";

    const row = el("div", "btn-row");
    this.btnRun = el("button", "action primary", "Start");
    this.btnRun.addEventListener("click", () =>
      this.send({ type: "set_running", on: !this._lastCtl.running }));
    this.btnBrake = el("button", "action", "Brake");
    this.btnBrake.addEventListener("click", () =>
      this.send({ type: "set_brake", on: !this._lastCtl.brake,
                  mode: this.regenCheck.checked ? "regen" : "short" }));
    row.append(this.btnRun, this.btnBrake);
    b.appendChild(row);

    const regenRow = el("div", "ctl-row");
    const regenLabel = el("label", "", " Regen braking (needs battery)");
    this.regenCheck = el("input");
    this.regenCheck.type = "checkbox";
    this.regenCheck.addEventListener("change", () =>
      this.send({ type: "set_brake", on: !!this._lastCtl.brake,
                  mode: this.regenCheck.checked ? "regen" : "short" }));
    regenLabel.prepend(this.regenCheck);
    regenRow.appendChild(regenLabel);
    b.appendChild(regenRow);

    const dirRow = el("div", "ctl-row");
    dirRow.appendChild(el("label", "", "Direction"));
    this.dirGroup = el("div", "btn-group");
    for (const [txt, v] of [["Forward", 1], ["Reverse", -1]]) {
      const btn = el("button", "", txt);
      btn.dataset.dir = v;
      btn.addEventListener("click", () =>
        this.send({ type: "set_direction", value: v }));
      this.dirGroup.appendChild(btn);
    }
    dirRow.appendChild(this.dirGroup);
    b.appendChild(dirRow);

    const typeRow = el("div", "ctl-row");
    typeRow.appendChild(el("label", "", "Motor type"));
    this.typeGroup = el("div", "btn-group");
    for (const [t, label] of MOTOR_TYPE_LABELS) {
      const btn = el("button", "", label);
      btn.dataset.type = t;
      btn.addEventListener("click", () => {
        if (t === this._lastCtl.motor_type) return;
        this.send({ type: "set_motor", motor_type: t,
                           params: this._collectParams(t) });
      });
      this.typeGroup.appendChild(btn);
    }
    typeRow.appendChild(this.typeGroup);
    b.appendChild(typeRow);

    // per-type drive inputs: step rate (stepper), AC frequency (induction),
    // commutation scheme (BLDC)
    this.stepRate = sliderRow(b, "Step rate", {
      min: 0, max: 3000, step: 10, value: 200,
      fmt: v => `${v} st/s`,
      onInput: v => this.sendDebounced({ type: "set_step_rate", rate: v }, "steprate"),
    });
    this.stepRateRow = this.stepRate.input.parentElement;
    this.stepRateRow.style.display = "none";
    this.acFreq = sliderRow(b, "AC frequency", {
      min: 0, max: 120, step: 1, value: 60,
      fmt: v => `${v} Hz`,
      onInput: v => this.sendDebounced({ type: "set_supply_frequency", hz: v }, "achz"),
    });
    this.acFreqRow = this.acFreq.input.parentElement;
    this.acFreqRow.style.display = "none";

    const commRow = el("div", "ctl-row");
    commRow.appendChild(el("label", "", "Commutation"));
    this.commGroup = el("div", "btn-group");
    for (const [label, mode] of [["Six-step", "six_step"], ["FOC", "foc"]]) {
      const btn = el("button", "", label);
      btn.dataset.comm = mode;
      btn.title = mode === "foc"
        ? "Idealized field-oriented control: sinusoidal commutation, no torque ripple"
        : "Classic trapezoidal six-step commutation (with ripple)";
      btn.addEventListener("click", () =>
        this.send({ type: "set_commutation", mode }));
      this.commGroup.appendChild(btn);
    }
    commRow.appendChild(this.commGroup);
    b.appendChild(commRow);
    this.commRow = commRow;
    this.commRow.style.display = "none";

    b.appendChild(el("p", "section-title", "Motor parameters (live)"));
    const grid = el("div", "ctl-grid");
    this.paramInputs = {};
    for (const def of PARAM_DEFS) {
      const input = numField(grid, def.label, "", def.step, v => {
        this.send({ type: "set_params", params: { [def.key]: v } });
      });
      input.dataset.bldc = def.bldc ? "1" : "";
      this.paramInputs[def.key] = input;
    }
    b.appendChild(grid);
    b.appendChild(el("p", "hint",
      "Edits apply instantly to the running motor (the session hot-swaps " +
      "to the mutable backend when needed. The engine chip shows which is active)."));

    const actions = el("div", "btn-row");
    const btnReset = el("button", "action", "Reset motor");
    btnReset.addEventListener("click", () => this.send({ type: "reset" }));
    const btnSave = el("button", "action", "Save preset");
    btnSave.addEventListener("click", () => this._savePreset());
    actions.append(btnReset, btnSave);
    b.appendChild(actions);
  }

  _collectParams(motorType) {
    const params = {};
    for (const def of PARAM_DEFS) {
      if (def.only && !def.only.includes(motorType)) continue;
      const v = parseFloat(this.paramInputs[def.key].value);
      if (Number.isFinite(v)) params[def.key] = v;
    }
    return params;
  }

  // which drive rows / parameter fields exist for this motor type
  _updateTypeVisibility(type, pwm) {
    const pwmOn = !!pwm?.enabled;
    const dcish = type === "dc" || type === "bldc";
    for (const def of PARAM_DEFS)
      this.paramInputs[def.key].parentElement.style.display =
        def.only && !def.only.includes(type) ? "none" : "";
    this.modeGroup.parentElement.style.display = dcish ? "" : "none";
    this.voltageRow.style.display =
      (type === "stepper" || (dcish && pwmOn)) ? "none" : "";
    this.dutyRow.style.display = dcish && pwmOn ? "" : "none";
    this.pwmFreqRow.style.display = dcish && pwmOn ? "" : "none";
    this.stepRateRow.style.display = type === "stepper" ? "" : "none";
    this.acFreqRow.style.display = type === "induction" ? "" : "none";
    this.commRow.style.display = type === "bldc" ? "" : "none";
  }

  async _savePreset() {
    const name = prompt("Preset name:", this._lastCtl.preset || "my motor");
    if (!name) return;
    const ctl = this._lastCtl;
    const preset = {
      name,
      motor_type: ctl.motor_type,
      params: this._collectParams(ctl.motor_type),
      load: ctl.load,
      limits: { current_limit: ctl.limit_a, enabled: ctl.limit_enabled },
      thermal: { ambient_c: ctl.ambient_c, overheat_c: ctl.overheat_c,
                 resistance_feedback: ctl.thermal_feedback },
      drive: { voltage: ctl.throttle_v },
    };
    const res = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preset),
    });
    if (res.ok) this.socket._fire("event", { event: "preset_saved", name });
  }

  // ----------------------------------------------------------------- Load

  _buildLoad() {
    const b = this.bodies.load;
    const row = el("div", "ctl-field");
    row.appendChild(el("label", "", "Attached load"));
    this.loadSelect = el("select");
    for (const [kind, def] of Object.entries(LOAD_DEFS)) {
      const opt = el("option", "", def.label);
      opt.value = kind;
      this.loadSelect.appendChild(opt);
    }
    this.loadSelect.addEventListener("change", () => this._loadChanged(true));
    row.appendChild(this.loadSelect);
    b.appendChild(row);

    this.loadParamsBox = el("div", "ctl-grid");
    b.appendChild(this.loadParamsBox);
    b.appendChild(el("p", "hint",
      "Fan/pump torque grows with speed²; wheel and flywheel also add " +
      "reflected inertia, so engaging one on a spinning shaft visibly drops " +
      "the RPM (conservation of angular momentum)."));
    this.loadInputs = {};
    this._renderLoadParams("none", {});
  }

  _renderLoadParams(kind, current) {
    this.loadParamsBox.innerHTML = "";
    this.loadInputs = {};
    for (const p of LOAD_DEFS[kind].params) {
      const v = current[p.key] ?? p.def;
      this.loadInputs[p.key] = numField(this.loadParamsBox, p.label, v, p.step,
        () => this._loadChanged(false));
    }
  }

  _loadChanged(kindSwitched) {
    const kind = this.loadSelect.value;
    if (kindSwitched) this._renderLoadParams(kind, {});
    const params = {};
    for (const [key, input] of Object.entries(this.loadInputs)) {
      const v = parseFloat(input.value);
      if (Number.isFinite(v)) params[key] = v;
    }
    this.sendDebounced({ type: "set_load", kind, params }, "set_load", 120);
  }

  // ------------------------------------------------------------------ PID

  _buildControl() {
    const b = this.bodies.control;
    const modeRow = el("div", "ctl-row");
    modeRow.appendChild(el("label", "", "Mode"));
    this.pidModeGroup = el("div", "btn-group");
    for (const [label, mode] of [["Off", "off"], ["Speed", "speed"],
                                 ["Torque", "torque"], ["Position", "position"]]) {
      const btn = el("button", "", label);
      btn.dataset.mode = mode;
      btn.addEventListener("click", () => {
        this._pidMode = mode;
        this._sendController();
      });
      this.pidModeGroup.appendChild(btn);
    }
    modeRow.appendChild(this.pidModeGroup);
    b.appendChild(modeRow);
    this._pidMode = "off";

    const grid = el("div", "ctl-grid");
    this.pidSetpoint = numField(grid, "Setpoint", 2000, 10, () => this._sendController());
    this.pidKp = numField(grid, "Kp", 0.01, 0.001, () => this._sendController());
    this.pidKi = numField(grid, "Ki", 0.05, 0.005, () => this._sendController());
    this.pidKd = numField(grid, "Kd", 0, 0.0005, () => this._sendController());
    b.appendChild(grid);
    this.pidStatus = el("p", "hint", "Controller off. The throttle is open-loop.");
    b.appendChild(this.pidStatus);
    b.appendChild(el("p", "hint",
      "Setpoint units: RPM (speed), N·m (torque), revolutions (position). " +
      "While active, the controller owns the drive voltage or PWM duty, so " +
      "the throttle slider follows it. Ki removes steady-state error; too " +
      "much Kp overshoots. Start/stop still applies."));

    b.appendChild(el("p", "section-title", "Step response & tuning"));
    const stepRow = el("div", "btn-row");
    const btnStep = el("button", "action", "Step +25% and measure");
    btnStep.addEventListener("click", async () => {
      this.stepResult.textContent = "Measuring, 3 s";
      this.stepResult.textContent = await this.onStepTest();
    });
    const btnTune = el("button", "action", "Auto-tune");
    btnTune.title = "Measure the motor's gain and time constant with a " +
                    "voltage step, then compute PI gains (IMC method)";
    btnTune.addEventListener("click", async () => {
      this.stepResult.textContent = "Identifying the plant, about 3 s";
      this.stepResult.textContent = await this.onAutoTune();
    });
    stepRow.append(btnStep, btnTune);
    b.appendChild(stepRow);
    this.stepResult = el("p", "hint", "");
    b.appendChild(this.stepResult);
  }

  _sendController() {
    const num = (input, fb) => {
      const v = parseFloat(input.value);
      return Number.isFinite(v) ? v : fb;
    };
    this.send({
      type: "set_controller",
      mode: this._pidMode,
      kp: num(this.pidKp, 0.01),
      ki: num(this.pidKi, 0.0),
      kd: num(this.pidKd, 0.0),
      setpoint: num(this.pidSetpoint, 0),
    });
  }

  // --------------------------------------------------------------- Faults

  _buildFaults() {
    const b = this.bodies.faults;

    b.appendChild(el("p", "section-title", "Supply voltage sag"));
    this.sagDepth = sliderRow(b, "Depth", {
      min: 0.1, max: 1, step: 0.05, value: 0.5,
      fmt: v => `−${Math.round(v * 100)}%`, onInput: () => {},
    });
    this.sagDur = sliderRow(b, "Duration", {
      min: 0.1, max: 10, step: 0.1, value: 1.5,
      fmt: v => `${v.toFixed(1)} s`, onInput: () => {},
    });
    const btnSag = el("button", "action", "Trigger sag");
    btnSag.addEventListener("click", () => this.send({
      type: "fault", kind: "sag",
      depth: parseFloat(this.sagDepth.input.value),
      duration: parseFloat(this.sagDur.input.value),
    }));
    b.appendChild(btnSag);

    b.appendChild(el("p", "section-title", "Mechanical"));
    const jamRow = el("div", "btn-row");
    this.btnJam = el("button", "action danger", "Jam rotor");
    this.btnJam.addEventListener("click", () => this.send({
      type: "fault", kind: "jam", on: !this._lastCtl.jammed }));
    const btnClear = el("button", "action", "Clear faults");
    btnClear.addEventListener("click", () =>
      this.send({ type: "fault", kind: "clear" }));
    jamRow.append(this.btnJam, btnClear);
    b.appendChild(jamRow);

    b.appendChild(el("p", "section-title", "Current limit"));
    const limRow = el("div", "ctl-row");
    const limLabel = el("label", "", "Limiter");
    this.limitEnable = el("input");
    this.limitEnable.type = "checkbox";
    this.limitEnable.addEventListener("change", () => this.send({
      type: "set_limits", limit_enabled: this.limitEnable.checked }));
    limLabel.prepend(this.limitEnable);
    limRow.appendChild(limLabel);
    b.appendChild(limRow);
    this.limitA = sliderRow(b, "Limit (A)", {
      min: 0.5, max: 60, step: 0.5, value: 30,
      fmt: v => `${v.toFixed(1)} A`,
      onInput: v => this.sendDebounced(
        { type: "set_limits", current_limit: v }, "limit"),
    });

    b.appendChild(el("p", "section-title", "Thermal"));
    this.ambient = sliderRow(b, "Ambient (°C)", {
      min: -20, max: 60, step: 1, value: 25,
      fmt: v => `${v} °C`,
      onInput: v => this.sendDebounced(
        { type: "set_limits", ambient_c: v }, "ambient"),
    });
    this.overheat = sliderRow(b, "Overheat at", {
      min: 60, max: 220, step: 5, value: 120,
      fmt: v => `${v} °C`,
      onInput: v => this.sendDebounced(
        { type: "set_limits", overheat_c: v }, "overheat"),
    });
    const fbRow = el("div", "ctl-row");
    const fbLabel = el("label", "", " R rises with temperature");
    this.thermalFb = el("input");
    this.thermalFb.type = "checkbox";
    this.thermalFb.addEventListener("change", () => this.send({
      type: "set_limits", thermal_feedback: this.thermalFb.checked }));
    fbLabel.prepend(this.thermalFb);
    fbRow.appendChild(fbLabel);
    b.appendChild(fbRow);
    b.appendChild(el("p", "hint",
      "Copper derating: with feedback on, a hot winding raises R, which " +
      "cuts torque and current, and the motor slows as it heats."));

    b.appendChild(el("p", "section-title", "Power source"));
    const battRow = el("div", "ctl-row");
    const battLabel = el("label", "", " Battery (finite supply)");
    this.battEnable = el("input");
    this.battEnable.type = "checkbox";
    this.battEnable.addEventListener("change", () => this._sendBattery());
    battLabel.prepend(this.battEnable);
    battRow.appendChild(battLabel);
    b.appendChild(battRow);
    const battGrid = el("div", "ctl-grid");
    this.battCap = numField(battGrid, "Capacity (Ah)", 2.0, 0.1, () => this._sendBattery());
    this.battRint = numField(battGrid, "Internal R (Ω)", 0.05, 0.01, () => this._sendBattery());
    this.battVnom = numField(battGrid, "Nominal (V)", 12, 1, () => this._sendBattery());
    this.battRegenA = numField(battGrid, "Regen limit (A)", 10, 1, () => this._sendBattery());
    b.appendChild(battGrid);
    b.appendChild(el("p", "hint",
      "With a battery, the bus voltage sags under load and the pack " +
      "drains (state of charge shows above the gauges). Applying settings " +
      "installs a fresh, fully-charged pack."));
  }

  _sendBattery() {
    if (!this.battEnable.checked) {
      this.send({ type: "set_battery", enabled: false });
      return;
    }
    const num = (input, fallback) => {
      const v = parseFloat(input.value);
      return Number.isFinite(v) ? v : fallback;
    };
    this.send({
      type: "set_battery", enabled: true,
      capacity_ah: num(this.battCap, 2.0),
      internal_resistance: num(this.battRint, 0.05),
      nominal_voltage: num(this.battVnom, 12),
      regen_limit: num(this.battRegenA, 10),
    });
  }

  // ----------------------------------------------------------------- Time

  _buildTime() {
    const b = this.bodies.time;
    const row = el("div", "btn-row");
    this.btnPause = el("button", "action", "Pause");
    this.btnPause.addEventListener("click", () => this.send({
      type: "time", action: this._lastCtl.paused ? "play" : "pause" }));
    this.btnStep = el("button", "action", "Step 1 ms");
    this.btnStep.addEventListener("click", () =>
      this.send({ type: "time", action: "step", step_s: 0.001 }));
    row.append(this.btnPause, this.btnStep);
    b.appendChild(row);

    const scaleRow = el("div", "ctl-row");
    scaleRow.appendChild(el("label", "", "Time scale"));
    this.scaleGroup = el("div", "btn-group");
    scaleRow.appendChild(this.scaleGroup);
    b.appendChild(scaleRow);
    this._renderScales([1.0, 0.25, 0.1, 0.02]);

    b.appendChild(el("p", "hint",
      "Electrical transients are over in milliseconds at 1x. Drop to " +
      "0.1x or 0.02x to see the inrush spike and BLDC " +
      "commutation ripple, or single-step through them."));
  }

  _renderScales(scales) {
    this.scaleGroup.innerHTML = "";
    for (const s of scales) {
      const btn = el("button", "", `${s}×`);
      btn.dataset.scale = s;
      btn.addEventListener("click", () =>
        this.send({ type: "time", scale: s }));
      this.scaleGroup.appendChild(btn);
    }
  }

  // --------------------------------------------------------------- Script

  _buildScript() {
    const b = this.bodies.script;
    const field = el("div", "ctl-field");
    field.appendChild(el("label", "", "Scenario"));
    this.scenarioSelect = el("select");
    this.scenarioSelect.addEventListener("change", () => this._scenarioPicked());
    field.appendChild(this.scenarioSelect);
    b.appendChild(field);

    this.scenarioDesc = el("p", "hint", "");
    b.appendChild(this.scenarioDesc);

    const btns = el("div", "btn-row");
    this.btnRunScript = el("button", "action primary", "Run scenario");
    this.btnRunScript.addEventListener("click", () => this._runScript());
    this.btnStopScript = el("button", "action", "Stop");
    this.btnStopScript.addEventListener("click", () =>
      this.send({ type: "scenario", action: "stop" }));
    this.btnMacro = el("button", "action", "Record actions");
    this.btnMacro.title = "Capture what you do live as scenario steps";
    this.btnMacro.addEventListener("click", () => this._toggleMacro());
    btns.append(this.btnRunScript, this.btnStopScript, this.btnMacro);
    b.appendChild(btns);

    this.scriptStatus = el("p", "hint", "");
    b.appendChild(this.scriptStatus);

    b.appendChild(el("p", "section-title", "Steps (editable JSON)"));
    this.scriptText = el("textarea", "script-json");
    this.scriptText.spellcheck = false;
    b.appendChild(this.scriptText);
    b.appendChild(el("p", "hint",
      "Each step is { \"t\": seconds, \"do\": <command> }, using any command the " +
      "panels can send (set_voltage, set_load, fault, time, set_pwm, ...). " +
      "Times run on simulation time, so pause and slow-motion hold the " +
      "script too. Edit freely and press Run."));

    // draw-a-load-profile editor: sketch torque vs time with the mouse,
    // convert into set_load steps
    b.appendChild(el("p", "section-title", "Draw a load profile"));
    this.profCanvas = el("canvas", "load-canvas");
    this.profCanvas.width = 276;
    this.profCanvas.height = 110;
    b.appendChild(this.profCanvas);
    const N = 92;
    this._profile = new Array(N).fill(0.25);
    this._profLastIdx = null;
    const cvs = this.profCanvas;
    const drawAt = (ev) => {
      const r = cvs.getBoundingClientRect();
      const idx = Math.max(0, Math.min(N - 1,
        Math.floor((ev.clientX - r.left) / r.width * N)));
      const val = Math.max(0, Math.min(1, 1 - (ev.clientY - r.top) / r.height));
      // interpolate from the previous sample so fast drags stay smooth
      if (this._profLastIdx !== null && Math.abs(idx - this._profLastIdx) > 1) {
        const from = this._profLastIdx, fv = this._profile[from];
        const stepDir = idx > from ? 1 : -1;
        for (let i = from; i !== idx; i += stepDir) {
          const f = (i - from) / (idx - from);
          this._profile[i] = fv + f * (val - fv);
        }
      }
      this._profile[idx] = val;
      this._profLastIdx = idx;
      this._drawProfile();
    };
    cvs.addEventListener("pointerdown", (ev) => {
      cvs.setPointerCapture(ev.pointerId);
      this._profLastIdx = null;
      drawAt(ev);
    });
    cvs.addEventListener("pointermove", (ev) => {
      if (ev.buttons & 1) drawAt(ev);
    });
    cvs.addEventListener("pointerup", () => { this._profLastIdx = null; });

    const profGrid = el("div", "ctl-grid");
    this.profDuration = numField(profGrid, "Duration (s)", 10, 1, () => {});
    this.profMax = numField(profGrid, "Max torque (N·m)", 0.05, 0.01, () => {});
    b.appendChild(profGrid);
    const profRow = el("div", "btn-row");
    const btnProf = el("button", "action", "Insert as steps");
    btnProf.addEventListener("click", () => this._profileToSteps());
    profRow.appendChild(btnProf);
    b.appendChild(profRow);
    this._drawProfile();
  }

  _drawProfile() {
    const cvs = this.profCanvas, g = cvs.getContext("2d");
    const W = cvs.width, H = cvs.height, N = this._profile.length;
    const styles = getComputedStyle(document.documentElement);
    g.fillStyle = styles.getPropertyValue("--panel-2").trim() || "#f3f4f6";
    g.fillRect(0, 0, W, H);
    g.strokeStyle = styles.getPropertyValue("--edge").trim() || "#e3e6ea";
    for (const fy of [0.25, 0.5, 0.75]) {
      g.beginPath(); g.moveTo(0, H * fy); g.lineTo(W, H * fy); g.stroke();
    }
    g.strokeStyle = styles.getPropertyValue("--accent").trim() || "#2563eb";
    g.lineWidth = 2;
    g.beginPath();
    this._profile.forEach((v, i) => {
      const x = (i + 0.5) / N * W, y = (1 - v) * H;
      i ? g.lineTo(x, y) : g.moveTo(x, y);
    });
    g.stroke();
    g.lineWidth = 1;
  }

  _profileToSteps() {
    const dur = Math.max(1, parseFloat(this.profDuration.value) || 10);
    const maxT = Math.max(1e-4, parseFloat(this.profMax.value) || 0.05);
    const N = this._profile.length;
    const count = 24;
    const steps = [];
    let last = null;
    for (let i = 0; i < count; i++) {
      const torque = Math.round(
        this._profile[Math.floor(i * N / count)] * maxT * 1e5) / 1e5;
      if (torque === last) continue;      // skip redundant steps
      last = torque;
      steps.push({ t: Math.round(i * dur / count * 100) / 100,
                   do: { type: "set_load", kind: "constant",
                         params: { torque } } });
    }
    this.scriptText.value = JSON.stringify(steps, null, 2);
    this.setScenarioStatus(
      `Load profile → ${steps.length} steps over ${dur}s. Press Run to play it.`);
  }

  setScenarios(list) {
    this._scenarios = list || [];
    this.scenarioSelect.innerHTML = "";
    this._scenarios.forEach((sc, i) => {
      const opt = el("option", "", sc.name);
      opt.value = i;
      this.scenarioSelect.appendChild(opt);
    });
    this._scenarioPicked();
  }

  _scenarioPicked() {
    const sc = this._scenarios[+this.scenarioSelect.value];
    if (!sc) return;
    this.scenarioDesc.textContent = sc.description || "";
    this.scriptText.value = JSON.stringify(sc.steps, null, 2);
  }

  _runScript() {
    let steps;
    try {
      steps = JSON.parse(this.scriptText.value);
    } catch {
      this.scriptStatus.textContent = "Steps are not valid JSON.";
      return;
    }
    const sc = this._scenarios[+this.scenarioSelect.value];
    this.send({ type: "scenario", action: "start",
                name: sc ? sc.name : "custom", steps });
  }

  setScenarioStatus(text) { this.scriptStatus.textContent = text; }

  // --------------------------------------------------------------- Record

  _buildRecord() {
    const b = this.bodies.record;
    const nameField = el("div", "ctl-field");
    nameField.appendChild(el("label", "", "Run name"));
    this.runName = el("input");
    this.runName.type = "text";
    this.runName.placeholder = "e.g. fan-load-12V";
    nameField.appendChild(this.runName);
    b.appendChild(nameField);

    this.btnRecord = el("button", "action", "Start recording");
    this.btnRecord.addEventListener("click", () => {
      if (this._lastCtl.recording) {
        this.send({ type: "record", action: "stop" });
      } else {
        this.send({ type: "record", action: "start",
                           name: this.runName.value });
      }
    });
    b.appendChild(this.btnRecord);

    b.appendChild(el("p", "section-title", "Session"));
    const sess = el("div", "btn-row");
    const btnShare = el("button", "action", "Share link");
    btnShare.title = "Copy a URL that reproduces both benches' setup";
    btnShare.addEventListener("click", () => this.onShare());
    const btnSave = el("button", "action", "Save session");
    btnSave.addEventListener("click", () => this.onSaveSession());
    const btnLoad = el("button", "action", "Load session");
    const filePick = el("input");
    filePick.type = "file";
    filePick.accept = ".json,application/json";
    filePick.style.display = "none";
    filePick.addEventListener("change", () => {
      if (filePick.files[0]) this.onLoadSession(filePick.files[0]);
      filePick.value = "";
    });
    btnLoad.addEventListener("click", () => filePick.click());
    sess.append(btnShare, btnSave, btnLoad);
    b.append(sess, filePick);

    // hardware-in-the-loop (log flavor): overlay a real logged run
    const hilRow = el("div", "btn-row");
    const btnImport = el("button", "action", "Import log (CSV)");
    btnImport.title = "Overlay a real measurement (scope/ESC log or an " +
                      "exported run) on the charts via compare mode";
    const csvPick = el("input");
    csvPick.type = "file";
    csvPick.accept = ".csv,text/csv";
    csvPick.style.display = "none";
    csvPick.addEventListener("change", () => {
      if (csvPick.files[0]) this._importLog(csvPick.files[0]);
      csvPick.value = "";
    });
    btnImport.addEventListener("click", () => csvPick.click());
    const btnReport = el("button", "action", "Generate report");
    btnReport.title = "Download a standalone HTML report of the current " +
                      "bench: scene, charts, stats and parameters";
    btnReport.addEventListener("click", () => this.onReport());
    hilRow.append(btnImport, btnReport);
    b.append(hilRow, csvPick);

    // hardware-in-the-loop (live serial flavor)
    b.appendChild(el("p", "section-title", "Hardware (live serial)"));
    const hwGrid = el("div", "ctl-grid");
    const portField = el("div", "ctl-field");
    portField.appendChild(el("label", "", "Port (e.g. COM3)"));
    this.hwPort = el("input");
    this.hwPort.type = "text";
    this.hwPort.placeholder = "COM3";
    portField.appendChild(this.hwPort);
    hwGrid.appendChild(portField);
    this.hwBaud = numField(hwGrid, "Baud", 115200, 1, () => {});
    b.appendChild(hwGrid);
    const hwRow = el("div", "btn-row");
    this.btnHw = el("button", "action", "Connect");
    this.btnHw.addEventListener("click", () =>
      this._hw(this.hwConnected ? "disconnect" : "connect"));
    hwRow.appendChild(this.btnHw);
    b.appendChild(hwRow);
    this.hwStatus = el("p", "hint",
      "Stream live telemetry from a real motor (Arduino/ESC over USB) and " +
      "overlay it as the 'hardware-live' run in compare mode. Needs " +
      "pyserial on the server; see tools/hil_arduino_example.");
    b.appendChild(this.hwStatus);

    b.appendChild(el("p", "section-title", "Saved runs"));
    this.runsList = el("div");
    this.runsList.id = "runs-list";
    b.appendChild(this.runsList);
    b.appendChild(el("p", "hint",
      "Tick runs to overlay them on the charts (enable 'compare recorded " +
      "runs' in the chart toolbar). CSV downloads match the batch CLI's " +
      "column format plus temperature and fault flags."));
  }

  _importLog(file) {
    const reader = new FileReader();
    reader.onload = () => {
      const frames = parseCsvLog(reader.result);
      if (!frames) {
        this.runsList.prepend(el("p", "hint",
          "Could not read that CSV. It needs a header row with a 't' " +
          "column plus telemetry columns (rpm, current, torque and so on)."));
        return;
      }
      let name = file.name.replace(/\.csv$/i, "");
      while (this.importedRuns.has(name) || this._runs.some(r => r.name === name))
        name += "-2";
      this.importedRuns.set(name, frames);
      this.setRuns(this._runs);
    };
    reader.readAsText(file);
  }

  async _hw(action) {
    try {
      const res = await fetch("/api/hardware", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, port: this.hwPort.value.trim(),
                               baud: parseInt(this.hwBaud.value, 10) || 115200 }),
      });
      const data = await res.json();
      if (data.error) {
        this.hwStatus.textContent = `⚠ ${data.error}`;
        return;
      }
      this.setHardwareStatus(data);
      this.onCompareSelectionChange([...this._compareSel]);
    } catch {
      this.hwStatus.textContent = "⚠ could not reach the server";
    }
  }

  setHardwareStatus(status) {
    this.hwConnected = !!status.connected;
    this.btnHw.textContent = this.hwConnected ? "Disconnect" : "Connect";
    this.btnHw.classList.toggle("armed", this.hwConnected);
    if (this.hwConnected) {
      const latest = status.latest;
      this.hwStatus.textContent =
        `Connected to ${status.port}, ${status.frames} samples` +
        (latest && latest.rpm != null ? ` · latest rpm ${latest.rpm}` : "") +
        ". Overlay 'hardware-live' in compare mode.";
    } else if (status.error) {
      this.hwStatus.textContent = `Disconnected. Last error: ${status.error}`;
    }
  }

  getImportedRun(name) {
    const frames = this.importedRuns.get(name);
    return frames ? { name, frames } : null;
  }

  setRuns(runs) {
    this._runs = runs;
    for (const name of [...this._compareSel])
      if (!runs.some(r => r.name === name) && !this.importedRuns.has(name))
        this._compareSel.delete(name);
    this.runsList.innerHTML = "";
    if (!runs.length && !this.importedRuns.size) {
      this.runsList.appendChild(el("p", "hint", "No recorded runs yet."));
      return;
    }
    for (const [name, frames] of this.importedRuns) {
      const item = el("div", "run-item");
      const check = el("input");
      check.type = "checkbox";
      check.checked = this._compareSel.has(name);
      check.title = "Overlay on charts (compare mode)";
      check.addEventListener("change", () => {
        check.checked ? this._compareSel.add(name)
                      : this._compareSel.delete(name);
        this.onCompareSelectionChange([...this._compareSel]);
      });
      const dur = frames[frames.length - 1].t - frames[0].t;
      const label = el("span", "run-name",
        `${name}, ${dur.toFixed(1)} s (imported log)`);
      const del = el("button", "", "×");
      del.title = "Remove imported log";
      del.addEventListener("click", () => {
        this.importedRuns.delete(name);
        this._compareSel.delete(name);
        this.setRuns(this._runs);
        this.onCompareSelectionChange([...this._compareSel]);
      });
      item.append(check, label, del);
      this.runsList.appendChild(item);
    }
    for (const run of runs) {
      const item = el("div", "run-item");
      const check = el("input");
      check.type = "checkbox";
      check.checked = this._compareSel.has(run.name);
      check.title = "Overlay on charts (compare mode)";
      check.addEventListener("change", () => {
        check.checked ? this._compareSel.add(run.name)
                      : this._compareSel.delete(run.name);
        this.onCompareSelectionChange([...this._compareSel]);
      });
      const label = el("span", "run-name",
        `${run.name}${run.bench && run.bench !== "A" ? ` · bench ${run.bench}` : ""}` +
        `, ${run.duration.toFixed(1)} s` +
        (run.complete ? "" : " (recording)"));
      const rep = el("button", "", "▶");
      rep.title = "Replay this run through the 3D scene and gauges";
      rep.addEventListener("click", () => this.onReplay(run.name));
      const csv = el("a", "", "CSV");
      csv.href = this.roomQS(`/api/runs/${encodeURIComponent(run.name)}/csv`);
      csv.download = `${run.name}.csv`;
      const del = el("button", "", "×");
      del.title = "Delete run";
      del.addEventListener("click", async () => {
        await fetch(this.roomQS(`/api/runs/${encodeURIComponent(run.name)}`),
                    { method: "DELETE" });
        this._compareSel.delete(run.name);
        this.setRuns(this._runs.filter(r => r.name !== run.name));
        this.onCompareSelectionChange([...this._compareSel]);
      });
      item.append(check, label, rep, csv, del);
      this.runsList.appendChild(item);
    }
  }

  compareSelection() { return [...this._compareSel]; }

  // ------------------------------------------------------------------ sync

  // full state: on hello and preset_loaded (params + ranges)
  syncState(state) {
    const p = state.params;
    for (const def of PARAM_DEFS) {
      const input = this.paramInputs[def.key];
      if (!busy(input) && p[def.key] !== undefined) input.value = p[def.key];
    }
    this._updateTypeVisibility(state.motor_type, state.ctl.pwm);
    if (!busy(this.voltage.input)) {
      this.voltage.input.max = p.max_voltage;
      this.voltage.set(state.ctl.throttle_v);
    }
    if (state.time_scales) this._renderScales(state.time_scales);
    const load = state.ctl.load || { kind: "none", params: {} };
    if (!busy(this.loadSelect)) {
      this.loadSelect.value = load.kind;
      this._renderLoadParams(load.kind, load.params || {});
    }
    this.syncCtl(state.ctl);
  }

  // ctl echo: every telemetry frame, but only touch DOM on change
  syncCtl(ctl) {
    const prev = this._lastCtl;
    this._lastCtl = ctl;

    if (prev.running !== ctl.running) {
      this.btnRun.textContent = ctl.running ? "Stop" : "Start";
      this.btnRun.classList.toggle("armed", ctl.running);
      this.btnRun.classList.toggle("primary", !ctl.running);
    }
    if (prev.brake !== ctl.brake)
      this.btnBrake.classList.toggle("armed", ctl.brake);
    if (prev.brake_mode !== ctl.brake_mode)
      this.regenCheck.checked = ctl.brake_mode === "regen";
    if (prev.battery_enabled !== ctl.battery_enabled)
      this.battEnable.checked = !!ctl.battery_enabled;
    if (prev.direction !== ctl.direction)
      this.dirGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", +btn.dataset.dir === ctl.direction));
    if (prev.motor_type !== ctl.motor_type) {
      this.typeGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", btn.dataset.type === ctl.motor_type));
      this._updateTypeVisibility(ctl.motor_type, ctl.pwm);
    }
    if (prev.step_rate !== ctl.step_rate && !busy(this.stepRate.input))
      this.stepRate.set(ctl.step_rate);
    if (prev.supply_hz !== ctl.supply_hz && !busy(this.acFreq.input))
      this.acFreq.set(ctl.supply_hz);
    if (prev.commutation !== ctl.commutation)
      this.commGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", btn.dataset.comm === ctl.commutation));
    if (prev.throttle_v !== ctl.throttle_v && !busy(this.voltage.input))
      this.voltage.set(ctl.throttle_v);
    if (prev.jammed !== ctl.jammed) {
      this.btnJam.textContent = ctl.jammed ? "Release jam" : "Jam rotor";
      this.btnJam.classList.toggle("armed", ctl.jammed);
    }
    if (prev.limit_enabled !== ctl.limit_enabled)
      this.limitEnable.checked = ctl.limit_enabled;
    if (prev.limit_a !== ctl.limit_a && !busy(this.limitA.input))
      this.limitA.set(ctl.limit_a);
    if (prev.ambient_c !== ctl.ambient_c && !busy(this.ambient.input))
      this.ambient.set(ctl.ambient_c);
    if (prev.overheat_c !== ctl.overheat_c && !busy(this.overheat.input))
      this.overheat.set(ctl.overheat_c);
    if (prev.thermal_feedback !== ctl.thermal_feedback)
      this.thermalFb.checked = ctl.thermal_feedback;
    if (prev.paused !== ctl.paused) {
      this.btnPause.textContent = ctl.paused ? "Resume" : "Pause";
      this.btnPause.classList.toggle("armed", ctl.paused);
    }
    if (prev.time_scale !== ctl.time_scale)
      this.scaleGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", +btn.dataset.scale === ctl.time_scale));
    if (prev.recording !== ctl.recording) {
      this.btnRecord.textContent = ctl.recording
        ? `Stop recording "${ctl.recording}"` : "Start recording";
      this.btnRecord.classList.toggle("armed", !!ctl.recording);
    }
    if (prev.load?.kind !== ctl.load?.kind && !busy(this.loadSelect)) {
      this.loadSelect.value = ctl.load.kind;
      this._renderLoadParams(ctl.load.kind, ctl.load.params || {});
    }

    const pwm = ctl.pwm || {};
    const prevPwm = prev.pwm || {};
    if (prevPwm.enabled !== pwm.enabled) {
      this.modeGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", (btn.dataset.pwm === "1") === !!pwm.enabled));
      this._updateTypeVisibility(ctl.motor_type, pwm);
    }
    if (prevPwm.duty !== pwm.duty && !busy(this.duty.input))
      this.duty.set(Math.round((pwm.duty ?? 0.5) * 100));
    if (prevPwm.frequency !== pwm.frequency && !busy(this.pwmFreq.input))
      this.pwmFreq.set(pwm.frequency ?? 500);

    const pid = ctl.controller;
    const prevPid = prev.controller;
    if ((prevPid?.mode ?? "off") !== (pid?.mode ?? "off")) {
      this._pidMode = pid?.mode ?? "off";
      this.pidModeGroup.querySelectorAll("button").forEach(btn =>
        btn.classList.toggle("active", btn.dataset.mode === this._pidMode));
    }
    if (pid && prevPid?.setpoint !== pid.setpoint && !busy(this.pidSetpoint))
      this.pidSetpoint.value = pid.setpoint;
    if (pid) {
      this.pidStatus.textContent =
        `Output: ${pid.output.toFixed(2)} V (${pid.mode} mode)`;
    } else if (prevPid) {
      this.pidStatus.textContent = "Controller off. The throttle is open-loop.";
    }

    if (prev.scenario !== ctl.scenario) {
      if (ctl.scenario) {
        this.scriptStatus.textContent = `Running "${ctl.scenario}"`;
      } else if (prev.scenario) {
        this.scriptStatus.textContent = `"${prev.scenario}" finished.`;
      }
    }
  }
}
