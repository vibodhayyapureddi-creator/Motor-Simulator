// App entry point: builds every component and wires telemetry through them.
//
//   SimSocket  ->  MotorScene (3D, one rig per bench)
//              ->  Dashboard (gauges + power strip)     -- active bench
//              ->  Charts (uPlot, dual-bench overlay)
//              ->  Indicators (warning badges + chips)  -- active bench
//              ->  ControlPanels (commands, bench-addressed)
//              ->  MotorSound (Web Audio)               -- active bench
//              ->  Annotations (physics callouts)       -- active bench
//
// Two benches (A/B) stream interleaved telemetry; the header switcher picks
// which one the instruments and controls address, and "both" shows the two
// motors side by side with bench B overlaid on the charts.

import { initTheme, setTheme, getTheme } from "./theme.js";
initTheme();   // before any canvas/WebGL construction reads the palette

import { SimSocket } from "./net/socket.js";
import { MotorScene } from "./scene/motor3d.js";
import { Dashboard } from "./dash/gauges.js";
import { Charts } from "./dash/charts.js";
import { Indicators } from "./dash/indicators.js";
import { ControlPanels } from "./controls/panels.js";
import { initKeyboard } from "./controls/keyboard.js";
import { MotorSound } from "./audio/sound.js";
import { Annotations, Tour } from "./ui/annotations.js";
import { PresetDiff } from "./ui/presetDiff.js";

// Multi-tenant room: ?room=name isolates your benches from everyone else's.
//
// Deployed, every visitor needs their own room, or two people opening the
// same link would drive the same motor and fight over it. So when no room
// is given we mint one and put it in the URL, which also makes the URL
// shareable (send it to someone and they watch your bench) and stable
// across refreshes.
//
// Locally we stay on "main", so the server's autosave still restores the
// bench you left running.
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "::1", ""];

function resolveRoom() {
  const asked = new URLSearchParams(location.search).get("room");
  if (asked) return asked;
  if (LOCAL_HOSTS.includes(location.hostname)) return "main";
  const minted = "r" + Math.random().toString(36).slice(2, 10);
  const url = new URL(location.href);
  url.searchParams.set("room", minted);
  window.history.replaceState(null, "", url);
  return minted;
}

const ROOM = resolveRoom();
const roomQS = ROOM === "main" ? (p) => p
  : (p) => p + (p.includes("?") ? "&" : "?") + "room=" + encodeURIComponent(ROOM);

const socket = new SimSocket();
socket.room = ROOM;
const indicators = new Indicators();
const dashboard = new Dashboard(document.getElementById("gauges"));
const charts = new Charts(document.getElementById("charts"));
const scene = new MotorScene(document.getElementById("scene-canvas"));
const sound = new MotorSound(document.getElementById("btn-mute"));
const annotations = new Annotations(document.getElementById("annotations"));
new Tour(document.getElementById("btn-tour"));
const panels = new ControlPanels(socket, {
  onCompareSelectionChange: () => refreshCompare(),
  onShare: () => shareSession(),
  onSaveSession: () => saveSession(),
  onLoadSession: (file) => loadSessionFile(file),
  onStepTest: () => runStepTest(),
  onAutoTune: () => runAutoTune(),
  onReplay: (name) => startReplay(name),
  onReport: () => generateReport(),
  onCommand: (msg, bench) => noteHistory(msg, bench),
});
panels.roomQS = roomQS;

if (ROOM !== "main") {
  const chip = document.createElement("span");
  chip.className = "chip good";
  chip.title = "Private room. This URL is yours; share it to let someone watch.";
  chip.textContent = `room: ${ROOM}`;
  document.querySelector(".chips").prepend(chip);
}

// step the speed setpoint +25% and measure the classic response metrics
async function runStepTest() {
  const ctl = lastFrames[activeBench]?.ctl;
  const c = ctl?.controller;
  if (!c || c.mode !== "speed")
    return "Step test needs the controller in speed mode.";
  if (!ctl.running)
    return "Start the motor first.";
  const from = c.setpoint;
  const to = Math.round(Math.max(200, from * 1.25));
  socket.send({ type: "set_controller", mode: "speed", kp: c.kp, ki: c.ki,
                kd: c.kd, setpoint: to, bench: activeBench });
  const t0 = lastFrames[activeBench].t;
  await new Promise(r => setTimeout(r, 3000));

  const buf = charts.bufs[activeBench];
  let i0 = buf.t.findIndex(t => t >= t0);
  if (i0 < 0) return "No telemetry captured.";
  const ts = buf.t.slice(i0);
  const rpm = buf.rpm.slice(i0);
  if (rpm.length < 10) return "Not enough samples — is the sim paused?";

  const stepSize = Math.max(1, Math.abs(to - from));
  const peak = Math.max(...rpm);
  const final = rpm.slice(-12).reduce((a, v) => a + v, 0) / Math.min(12, rpm.length);
  const overshoot = Math.max(0, (peak - to) / stepSize * 100);
  const band = 0.02 * stepSize;
  let settle = null;
  for (let i = rpm.length - 1; i >= 0; i--) {
    if (Math.abs(rpm[i] - to) > band) {
      settle = i < rpm.length - 1 ? ts[i + 1] - t0 : null;
      break;
    }
    if (i === 0) settle = 0;
  }
  const sse = Math.abs(final - to);
  return `Step ${from.toFixed(0)} → ${to} RPM: overshoot ${overshoot.toFixed(1)}%, ` +
         `settling ${settle != null ? settle.toFixed(2) + " s (2% band)" : "> 3 s"}, ` +
         `steady-state error ${sse.toFixed(1)} RPM.`;
}

// ------------------------------------------------------------- auto-tune

// Identify the plant with an open-loop voltage step (gain K and time
// constant tau of the first-order speed response), then compute PI gains
// with the IMC rule: Kp = tau/(K*lambda), Ki = Kp/tau.
async function runAutoTune() {
  const params = stateCache[activeBench]?.params;
  if (!params) return "No bench state yet.";
  const bench = activeBench;
  const vmax = params.max_voltage ?? 24;
  socket.send({ type: "set_controller", mode: "off", bench });
  socket.send({ type: "set_pwm", enabled: false, bench });
  socket.send({ type: "set_running", on: false, bench });
  socket.send({ type: "reset", bench });
  await new Promise(r => setTimeout(r, 500));
  const vStep = 0.6 * vmax;
  socket.send({ type: "set_voltage", value: vStep, bench });
  socket.send({ type: "set_running", on: true, bench });
  await new Promise(r => setTimeout(r, 2800));

  const buf = charts.bufs[bench];
  const rpm = buf.rpm, ts = buf.t;
  if (rpm.length < 20) return "Not enough samples — is the sim paused?";
  const final = rpm.slice(-10).reduce((a, v) => a + v, 0) / 10;
  if (Math.abs(final) < 10)
    return "The motor didn't move — check the drive settings.";
  const K = Math.abs(final) / vStep;               // rpm per volt
  // measure tau from the step ONSET (first motion), not from buffer start
  let i0 = 0;
  for (let i = 0; i < rpm.length; i++) {
    if (Math.abs(rpm[i]) > 0.02 * Math.abs(final)) { i0 = Math.max(0, i - 1); break; }
  }
  let tau = null;
  for (let i = i0; i < rpm.length; i++) {
    if (Math.abs(rpm[i]) >= 0.632 * Math.abs(final)) {
      tau = ts[i] - ts[i0];
      break;
    }
  }
  tau = Math.max(0.02, tau ?? 0.1);
  const lambda = Math.max(tau / 2, 0.05);          // IMC aggressiveness
  const kp = tau / (K * lambda);
  const ki = kp / tau;
  const setpoint = Math.round(Math.abs(final) * 0.8);
  socket.send({ type: "set_controller", mode: "speed", kp, ki, kd: 0,
                setpoint, bench });
  return `Plant: K = ${K.toFixed(2)} RPM/V, τ = ${tau.toFixed(3)} s → ` +
         `Kp = ${kp.toFixed(4)}, Ki = ${ki.toFixed(3)} (IMC, λ = ${lambda.toFixed(2)} s). ` +
         `Speed mode engaged at ${setpoint} RPM.`;
}

// ------------------------------------------------------------ undo / redo

// Snapshot-based history per bench: every state-changing command schedules
// a snapshot once things settle; Ctrl+Z / Ctrl+Y walk the stack and replay
// it through apply_state.
const HISTORY_TYPES = new Set([
  "set_voltage", "set_motor", "set_params", "set_load", "set_limits",
  "set_battery", "set_pwm", "set_controller", "set_commutation",
  "set_step_rate", "set_supply_frequency", "set_brake",
]);
const history = { A: { stack: [], idx: -1 }, B: { stack: [], idx: -1 } };
const histTimers = { A: 0, B: 0 };
let applyingHistory = false;

function pushHistory(bench) {
  if (applyingHistory) return;
  const snap = benchSnapshot(bench);
  if (!snap) return;
  const s = JSON.stringify(snap);
  const h = history[bench];
  if (h.stack[h.idx] === s) return;
  h.stack.splice(h.idx + 1);        // editing forks: drop the redo tail
  h.stack.push(s);
  if (h.stack.length > 60) h.stack.shift();
  h.idx = h.stack.length - 1;
}

function noteHistory(msg, bench) {
  if (applyingHistory || !HISTORY_TYPES.has(msg.type)) return;
  if (history[bench].idx < 0) pushHistory(bench);   // baseline before 1st edit
  clearTimeout(histTimers[bench]);
  histTimers[bench] = setTimeout(() => pushHistory(bench), 600);
}

function applyHistory(snapText) {
  applyingHistory = true;
  socket.send({ type: "apply_state", state: JSON.parse(snapText),
                bench: activeBench });
  setTimeout(() => { applyingHistory = false; }, 800);
}

function undo() {
  const h = history[activeBench];
  pushHistory(activeBench);          // make sure "now" is on the stack
  if (h.idx <= 0) { note("nothing to undo"); return; }
  h.idx--;
  applyHistory(h.stack[h.idx]);
  note(`undo (${h.idx}/${h.stack.length - 1})`);
}

function redo() {
  const h = history[activeBench];
  if (h.idx >= h.stack.length - 1) { note("nothing to redo"); return; }
  h.idx++;
  applyHistory(h.stack[h.idx]);
  note(`redo (${h.idx}/${h.stack.length - 1})`);
}

// ----------------------------------------------------------------- replay

// Replay a recorded run through the 3D scene, gauges and warning lights,
// with a scrubber. Live telemetry for that bench is suppressed while the
// replay owns the visuals; charts keep streaming live underneath.
let replay = null;

function stopReplay() {
  if (!replay) return;
  clearInterval(replay.timer);
  replay.bar.remove();
  replay = null;
  note("replay closed — back to live");
}

async function startReplay(name) {
  let frames;
  try {
    const res = await fetch(roomQS(`/api/runs/${encodeURIComponent(name)}/data`));
    frames = (await res.json()).frames;
  } catch { frames = null; }
  if (!frames || frames.length < 2 || !frames[0].ctl) {
    note("⚠ that run has no full telemetry (imported logs can't replay)", 5000);
    return;
  }
  stopReplay();

  const bar = document.createElement("div");
  bar.className = "replay-bar";
  const btnPlay = document.createElement("button");
  btnPlay.textContent = "⏸";
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = 0;
  slider.max = frames.length - 1;
  slider.value = 0;
  const label = document.createElement("span");
  label.className = "replay-label";
  const btnClose = document.createElement("button");
  btnClose.textContent = "×";
  btnClose.title = "Close replay";
  bar.append(btnPlay, slider, label, btnClose);
  document.getElementById("scene-panel").appendChild(bar);

  replay = {
    name, frames, bar, slider, label, btnPlay,
    bench: frames[0].bench || "A",
    i: 0, playing: true, timer: 0,
  };
  const t0 = frames[0].t;
  const show = () => {
    const f = replay.frames[replay.i];
    scene.update(f);
    if (replay.bench === activeBench) {
      dashboard.update(f);
      indicators.update(f);
    }
    slider.value = replay.i;
    label.textContent =
      `${name} · ${(f.t - t0).toFixed(2)}s / ${(frames[frames.length - 1].t - t0).toFixed(1)}s`;
    hudRpm.textContent = `REPLAY · ${Math.round(Math.abs(f.rpm))} RPM`;
  };
  btnPlay.addEventListener("click", () => {
    replay.playing = !replay.playing;
    if (replay.playing && replay.i >= frames.length - 1) replay.i = 0;
    btnPlay.textContent = replay.playing ? "⏸" : "▶";
  });
  slider.addEventListener("input", () => {
    replay.playing = false;
    btnPlay.textContent = "▶";
    replay.i = +slider.value;
    show();
  });
  btnClose.addEventListener("click", stopReplay);
  replay.timer = setInterval(() => {
    if (!replay || !replay.playing) return;
    replay.i++;
    if (replay.i >= frames.length - 1) {
      replay.i = frames.length - 1;
      replay.playing = false;
      btnPlay.textContent = "▶";
    }
    show();
  }, 1000 / 60);
  show();
  note(`replaying “${name}” — the scene shows the recording, not live`);
}

// ----------------------------------------------------------------- report

function generateReport() {
  const state = stateCache[activeBench] || {};
  const f = lastFrames[activeBench];
  const sceneImg = scene.snapshot();
  const cells = charts.plots.map((p, i) => {
    let img = "";
    try { img = p.ctx.canvas.toDataURL("image/png"); } catch { }
    return { img, stats: charts.statEls[i]?.textContent || "" };
  });
  const esc = (s) => String(s).replace(/[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const paramRows = Object.entries(state.params || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  const ctl = f?.ctl;
  const html = `<!doctype html><html><head><meta charset="utf-8">
<title>Motor bench report — ${esc(new Date().toLocaleString())}</title>
<style>
 body{font-family:"Segoe UI",system-ui,sans-serif;color:#1f2430;max-width:880px;margin:24px auto;padding:0 16px}
 h1{font-size:22px} h2{font-size:15px;margin-top:28px;border-bottom:1px solid #e3e6ea;padding-bottom:4px}
 img{max-width:100%;border:1px solid #e3e6ea;border-radius:8px}
 table{border-collapse:collapse;font-size:13px} td{border:1px solid #e3e6ea;padding:4px 10px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 .stats{font-size:11px;color:#6b7280;font-variant-numeric:tabular-nums}
 .meta{color:#6b7280;font-size:13px}
 @media print { body{margin:8px auto} }
</style></head><body>
<h1>Motor Test Bench report</h1>
<p class="meta">${esc(new Date().toLocaleString())} · bench ${esc(activeBench)}
 · ${esc(ctl?.preset || "no preset")} · ${esc(state.motor_type || "?")} motor
 · ${esc(ctl?.backend || "?")} engine</p>
<p class="meta">${f ? esc(`${Math.round(Math.abs(f.rpm))} RPM · ${f.current} A · ` +
  `${f.p_in} W in / ${f.p_out} W out · ${(f.efficiency * 100).toFixed(0)}% efficient · ` +
  `winding ${f.temperature} °C / housing ${f.housing_temp} °C`) : ""}</p>
<h2>Bench</h2><img src="${sceneImg}" alt="3D scene">
<h2>Telemetry</h2><div class="grid">${cells.map(c =>
  `<div><img src="${c.img}" alt="chart"><div class="stats">${esc(c.stats)}</div></div>`).join("")}</div>
<h2>Motor parameters</h2><table>${paramRows}</table>
<p class="meta">Generated by the Motor Test Bench — print this page to get a PDF.</p>
</body></html>`;
  const blob = new Blob([html], { type: "text/html" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `motor-report-${activeBench}-${Date.now()}.html`;
  a.click();
  URL.revokeObjectURL(a.href);
  note("report downloaded — open it and print to PDF if needed");
}
initKeyboard(panels, { onUndo: () => undo(), onRedo: () => redo() });
new PresetDiff(document.getElementById("btn-diff"), () => knownPresets);

const themeBtn = document.getElementById("btn-theme");
function syncThemeBtn() { themeBtn.textContent = getTheme() === "dark" ? "Light" : "Dark"; }
themeBtn.addEventListener("click", () => {
  setTheme(getTheme() === "dark" ? "light" : "dark");
  syncThemeBtn();
});
syncThemeBtn();

const hudRpm = document.getElementById("hud-rpm");
const hudNote = document.getElementById("hud-note");
const presetSelect = document.getElementById("preset-select");
const compareCheck = document.getElementById("compare-mode");
const dualCheck = document.getElementById("dual-mode");
const benchGroup = document.getElementById("bench-group");

let activeBench = "A";
const stateCache = { A: null, B: null };   // full states per bench
const lastFrames = { A: null, B: null };   // latest telemetry per bench

// ------------------------------------------------------------------ benches

function applyBenchState(bench) {
  const state = stateCache[bench];
  if (!state) return;
  panels.syncState(state);
  dashboard.rescale(state.params, state.ctl);
  selectPreset(state.ctl.preset);
}

benchGroup.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  if (!btn || btn.dataset.bench === activeBench) return;
  activeBench = btn.dataset.bench;
  benchGroup.querySelectorAll("button").forEach(b =>
    b.classList.toggle("active", b.dataset.bench === activeBench));
  panels.setBench(activeBench);
  charts.setActiveBench(activeBench);
  applyBenchState(activeBench);
  const f = lastFrames[activeBench];
  if (f) { dashboard.update(f); indicators.update(f); panels.syncCtl(f.ctl); }
});

dualCheck.addEventListener("change", () => {
  scene.setDual(dualCheck.checked);
  if (!compareCheck.checked) charts.setDual(dualCheck.checked);
});

// ------------------------------------------------- share / session persist

function benchSnapshot(bench) {
  const state = stateCache[bench];
  const ctl = lastFrames[bench]?.ctl || state?.ctl;
  if (!state || !ctl) return null;
  return {
    name: `session bench ${bench}`,
    motor_type: ctl.motor_type,
    params: state.params,
    load: ctl.load,
    limits: { current_limit: ctl.limit_a, enabled: ctl.limit_enabled },
    thermal: { ambient_c: ctl.ambient_c, overheat_c: ctl.overheat_c,
               resistance_feedback: ctl.thermal_feedback },
    drive: { voltage: ctl.throttle_v },
    extras: {
      pwm: ctl.pwm,
      commutation: ctl.commutation,
      step_rate: ctl.step_rate,
      supply_hz: ctl.supply_hz,
      brake_mode: ctl.brake_mode,
      controller: ctl.controller,
    },
  };
}

function sessionPayload() {
  return {
    v: 1,
    A: benchSnapshot("A"),
    B: benchSnapshot("B"),
    ui: {
      bench: activeBench,
      dual: dualCheck.checked,
      window: charts.windowS,
      theme: getTheme(),
    },
  };
}

function applySession(payload) {
  if (!payload || typeof payload !== "object") return;
  for (const bench of ["A", "B"]) {
    if (payload[bench])
      socket.send({ type: "apply_state", state: payload[bench], bench });
  }
  const ui = payload.ui || {};
  if (ui.theme && ui.theme !== getTheme()) { setTheme(ui.theme); syncThemeBtn(); }
  if (typeof ui.dual === "boolean" && ui.dual !== dualCheck.checked) dualCheck.click();
  if (ui.window) {
    const btn = winGroup.querySelector(`button[data-win="${ui.window}"]`);
    if (btn) btn.click();
  }
  if (ui.bench && ui.bench !== activeBench) {
    const btn = benchGroup.querySelector(`button[data-bench="${ui.bench}"]`);
    if (btn) btn.click();
  }
  note("session state applied");
}

async function shareSession() {
  const enc = btoa(unescape(encodeURIComponent(JSON.stringify(sessionPayload()))));
  const url = `${location.origin}${location.pathname}#s=${enc}`;
  try {
    await navigator.clipboard.writeText(url);
    note("share link copied to clipboard");
  } catch {
    prompt("Copy this link:", url);   // clipboard blocked: show it instead
  }
}

function saveSession() {
  const blob = new Blob([JSON.stringify(sessionPayload(), null, 2)],
                        { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "motor-bench-session.json";
  a.click();
  URL.revokeObjectURL(a.href);
  note("session saved");
}

function loadSessionFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try { applySession(JSON.parse(reader.result)); }
    catch { note("⚠ not a valid session file", 5000); }
  };
  reader.readAsText(file);
}

// a #s=... hash means someone shared this exact setup — apply after hello
let pendingShared = null;
if (location.hash.startsWith("#s=")) {
  try {
    pendingShared = JSON.parse(decodeURIComponent(escape(atob(location.hash.slice(3)))));
  } catch { /* malformed hash: ignore */ }
  window.history.replaceState(null, "", location.pathname);
}

// ------------------------------------------------------------------ presets

let knownPresets = [];

function selectPreset(selected) {
  const match = knownPresets.find(p => p.id === selected || p.name === selected);
  presetSelect.value = match ? match.id : "";
}

function fillPresets(presets, selected) {
  knownPresets = presets;
  presetSelect.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "— preset —";
  presetSelect.appendChild(blank);
  for (const p of presets) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = (p.name || p.id) + (p.source === "user" ? " (saved)" : "");
    presetSelect.appendChild(opt);
  }
  selectPreset(selected);
}

presetSelect.addEventListener("change", () => {
  if (presetSelect.value)
    socket.send({ type: "load_preset", name: presetSelect.value, bench: activeBench });
});

async function refreshPresets(selected) {
  try {
    const res = await fetch("/api/presets");
    fillPresets(await res.json(), selected ?? presetSelect.value);
  } catch { /* offline; the next hello refreshes it */ }
}

// ------------------------------------------------------------- compare mode

async function refreshRuns() {
  try {
    const res = await fetch(roomQS("/api/runs"));
    panels.setRuns(await res.json());
    refreshCompare();
  } catch { /* ignore */ }
}

async function refreshCompare() {
  if (!compareCheck.checked) return;
  const names = panels.compareSelection();
  const runs = [];
  for (const name of names) {
    const imported = panels.getImportedRun(name);   // client-side log
    if (imported) { runs.push(imported); continue; }
    try {
      const res = await fetch(roomQS(`/api/runs/${encodeURIComponent(name)}/data`));
      if (res.ok) runs.push(await res.json());
    } catch { /* skip missing run */ }
  }
  charts.setCompare(true, runs);
}

compareCheck.addEventListener("change", () => {
  if (compareCheck.checked) {
    charts.setDual(false);
    refreshCompare();
  } else {
    charts.setCompare(false);
    charts.setDual(dualCheck.checked);
  }
});

// time-window buttons
const winGroup = document.getElementById("time-window-group");
winGroup.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  if (!btn) return;
  winGroup.querySelectorAll("button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  charts.setWindow(parseFloat(btn.dataset.win));
});

// ------------------------------------------------------------------- notes

let noteTimer = 0;
function note(text, ms = 3500) {
  hudNote.textContent = text;
  clearTimeout(noteTimer);
  if (ms) noteTimer = setTimeout(() => { hudNote.textContent = ""; }, ms);
}

// ---------------------------------------------------------------- socket in

socket.on("status", ({ connected }) => {
  indicators.setConnected(connected);
  if (!connected) note("connection lost — retrying…", 0);
  else note("");
});

socket.on("hello", (msg) => {
  for (const [bench, state] of Object.entries(msg.benches || {}))
    stateCache[bench] = state;
  fillPresets(msg.presets, stateCache[activeBench]?.ctl.preset);
  panels.setScenarios(msg.scenarios || []);
  panels.setRuns(msg.runs || []);
  applyBenchState(activeBench);
  if (pendingShared) {          // a shared #s= link brought us here
    applySession(pendingShared);
    pendingShared = null;
  }
  // undo baseline: capture each bench's arrival state once frames flow
  setTimeout(() => { pushHistory("A"); pushHistory("B"); }, 1200);
});

socket.on("telemetry", (frame) => {
  const bench = frame.bench || "A";
  lastFrames[bench] = frame;
  charts.push(frame);
  if (replay && bench === replay.bench) {
    // a replay owns this bench's visuals; keep controls/charts live
    if (bench === activeBench) {
      panels.lastT = frame.t;
      panels.syncCtl(frame.ctl);
    }
    return;
  }
  scene.update(frame);
  if (bench === activeBench) {
    dashboard.update(frame);
    indicators.update(frame);
    sound.update(frame);
    panels.lastT = frame.t;      // macro-recorder timestamps
    panels.syncCtl(frame.ctl);
    annotations.update(frame);
  }
  if (dualCheck.checked) {
    const a = lastFrames.A, b = lastFrames.B;
    hudRpm.textContent =
      `A ${a ? Math.round(Math.abs(a.rpm)) : 0} · ` +
      `B ${b ? Math.round(Math.abs(b.rpm)) : 0} RPM`;
  } else if (bench === activeBench) {
    hudRpm.textContent = `${Math.round(Math.abs(frame.rpm))} RPM`;
  }
});

socket.on("event", (msg) => {
  const bench = msg.bench || "A";
  switch (msg.event) {
    case "fault_triggered":
      charts.addMarker(bench, msg.t, msg.kind);
      break;
    case "setpoint_changed":
      charts.addMarker(bench, msg.t, `setpoint ${msg.value}`);
      break;
    case "preset_loaded":
      if (msg.state) {
        stateCache[msg.state.bench || bench] = msg.state;
        if ((msg.state.bench || bench) === activeBench) applyBenchState(activeBench);
      }
      charts.addMarker(bench, lastFrames[bench]?.t ?? 0, "preset");
      note(`preset loaded on bench ${bench}: ${msg.name}`);
      break;
    case "preset_saved":
      refreshPresets();
      note(`preset saved: ${msg.name}`);
      break;
    case "record_started":
      note(`recording “${msg.name}” (bench ${bench})`);
      refreshRuns();
      break;
    case "record_stopped":
      note(`saved run “${msg.name}”`);
      refreshRuns();
      break;
    case "scenario_started":
      if (bench === activeBench)
        panels.setScenarioStatus(`Running “${msg.name}” (${msg.steps} steps)…`);
      break;
    case "scenario_step":
      charts.addMarker(bench, lastFrames[bench]?.t ?? 0, msg.label);
      if (bench === activeBench)
        panels.setScenarioStatus(
          `“${msg.name}” — step ${msg.index}/${msg.total}: ${msg.label} @ ${msg.t}s`);
      break;
    case "scenario_finished":
      if (bench === activeBench) panels.setScenarioStatus(`“${msg.name}” finished.`);
      break;
    case "scenario_stopped":
      if (bench === activeBench) panels.setScenarioStatus(`“${msg.name}” stopped.`);
      break;
    case "numeric_fault":
    case "error":
      note(`⚠ ${msg.message}`, 6000);
      break;
  }
});

socket.connect();

// the browser may restore checkbox state across a reload without firing
// change events — bring the scene/charts in line with what it shows
if (dualCheck.checked) { scene.setDual(true); charts.setDual(true); }

// gauge ranges depend on motor params, which only travel in state (not in
// telemetry) — refresh them occasionally so live parameter edits re-scale
setInterval(async () => {
  if (document.hidden) return;
  try {
    const res = await fetch(roomQS("/api/state"));
    const benches = await res.json();
    for (const [bench, state] of Object.entries(benches))
      stateCache[bench] = state;
    const state = stateCache[activeBench];
    if (state) dashboard.rescale(state.params, state.ctl);
  } catch { /* offline */ }
}, 5000);

// live hardware bridge: poll status while connected so the badge stays
// honest and the compare overlay keeps refreshing with new samples
setInterval(async () => {
  if (document.hidden || !panels.hwConnected) return;
  try {
    const res = await fetch("/api/hardware");
    const status = await res.json();
    panels.setHardwareStatus(status);
    if (compareCheck.checked && panels.compareSelection().includes("hardware-live"))
      refreshCompare();
  } catch { /* offline */ }
}, 3000);

// -------------------------------------------------------------- render loop

let last = performance.now();
function loop(now) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  scene.tick(dt);
  dashboard.draw();
  charts.draw();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
