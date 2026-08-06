# Interactive Motor Simulator — Wire Protocol

*The fixed contract between the browser front-end (`web/`) and the Python
server (`python/motorsim_server/`). Plan section 8; this is the Phase 0
deliverable both sides are built against.*

Transport: JSON text messages over a WebSocket at `GET /ws`. Ordinary HTTP
serves the page, static assets, and the REST endpoints listed at the end.
All numbers are plain JSON numbers (SI units unless stated); all commands
are objects with a `type` field.

**Benches.** The server runs two independent simulations, benches `"A"`
and `"B"`. Every command may carry a `bench` field (default `"A"`); every
telemetry frame and bench-scoped event carries the `bench` it belongs to.

**Rooms (multi-tenant).** `?room=<name>` on the page URL, the WebSocket,
and every `/api/state` / `/api/runs*` request selects an isolated bench
pair + run library. Rooms are created lazily and garbage-collected after
10 idle minutes; the default room `main` always exists and is what the
plain URL uses. The `main` room's bench states are autosaved to
`state/autosave.json` every ~20 s and restored on server start (disable
with `--no-restore`).

**Hardware bridge.** `GET/POST /api/hardware` controls the optional live
serial telemetry bridge (`{action: "connect"|"disconnect", port, baud}`;
requires `pip install pyserial`). While connected, samples appear as the
`hardware-live` run in `/api/runs`, for compare-mode overlay against the
simulation. Wire format: one JSON object or `key=value,...` line per
sample (see `tools/hil_arduino_example/`).

**Headless batch runs.** `python -m motorsim_server.batch` runs presets ×
scenarios without the browser or real-time pacing and writes CSV +
summaries (`--list`, `--all`, `--preset`, `--scenario`, `--duration`,
`--out`).

## 1. Server → client messages

### 1.1 `hello` — sent once, immediately after connect

```json
{
  "type": "hello",
  "benches": { "A": { ...state... }, "B": { ...state... } },
  "presets": [ ... ],       // preset list, same shape as GET /api/presets
  "scenarios": [ ... ],     // built-in scripts, same as GET /api/scenarios
  "runs":    [ ... ]        // recorded-run summaries, same as GET /api/runs
}
```

Each bench state (also returned per bench by `GET /api/state`):

```json
{
  "bench": "A",
  "motor_type": "dc" | "bldc",
  "params": {                       // full motor parameter set
    "resistance": 1.0,              // ohm
    "inductance": 0.001,            // H
    "torque_constant": 0.05,        // N·m/A
    "back_emf_constant": 0.05,      // V·s/rad
    "inertia": 0.0005,              // kg·m² (rotor only, without load)
    "viscous_friction": 0.0002,     // N·m·s/rad
    "static_friction": 0.0,         // N·m
    "max_voltage": 24.0,            // V
    "pole_pairs": 7,                // BLDC only
    "ripple_depth": 0.05            // BLDC only, 0..1
  },
  "ctl": { ... },                   // control state, shape below (1.2)
  "time_scales": [1.0, 0.25, 0.1, 0.02]
}
```

### 1.2 `telemetry` — ~60 frames per second while connected

```json
{
  "type": "telemetry",
  "bench": "A",
  "t": 1.234567,            // sim time, s
  "rpm": 3120.5,
  "omega": 326.8,           // rad/s (signed)
  "current": 1.42,          // A (latest instantaneous)
  "current_peak": 6.10,     // max |i| over the frame window (catches inrush/ripple)
  "current_rms": 1.55,      // RMS i over the frame window
  "torque": 0.071,          // N·m produced by the motor
  "voltage": 12.0,          // V actually applied (after sag / limiter)
  "temperature": 41.2,      // winding °C (the hot node)
  "housing_temp": 33.6,     // housing °C (two-zone thermal model)
  "load_torque": 0.05,      // N·m opposing, from the attached load
  "p_in": 17.0,             // mean electrical power over the frame, W
  "p_out": 14.8,            // mean mechanical power (τ·ω), W
  "efficiency": 0.87,       // p_out / p_in while driving, else 0
  "battery": {              // null when no battery installed
    "soc": 0.93, "voltage": 11.8, "capacity_ah": 1.5,
    "internal_resistance": 0.03, "nominal_voltage": 11.1,
    "energy_recovered_wh": 0.12 },
  "setpoint_rpm": 2000,     // active speed-mode setpoint, else null
  "position_rev": 12.5,     // integrated shaft position (for position mode)
  "elec_angle": 231.4,      // electrical angle, deg (BLDC)
  "sector": 3,              // commutation sector 0..5, or -1 for DC
  "flags": {
    "overcurrent": false,   // limiter folding back (or peak above the cap)
    "overheat": false,      // temperature >= overheat threshold
    "stall": false,         // stall detector latched (or rotor jam fault on)
    "sag": false,           // supply currently sagging / recovering
    "numeric": false        // sim diverged and auto-paused
  },
  "ctl": {                  // echoed control state so late joiners stay in sync
    "running": true,
    "brake": false,
    "direction": 1,             // 1 | -1
    "throttle_v": 12.0,
    "time_scale": 1.0,
    "paused": false,
    "motor_type": "dc",
    "backend": "cpp" | "python-fallback",
    "load": { "kind": "fan", "params": { "coefficient": 2e-7 } },
    "limit_a": 30.0,
    "limit_enabled": true,
    "ambient_c": 25.0,
    "overheat_c": 120.0,
    "thermal_feedback": false,
    "recording": null,          // run name if THIS bench is recording
    "rt_factor": 1.0,           // achieved sim-rate / requested rate (≤ 1)
    "preset": "builtin:hobby_gearmotor_12v",
    "jammed": false,
    "pwm": { "enabled": false, "duty": 0.5, "frequency": 500 },
    "scenario": null,           // running scenario name, or null
    "brake_mode": "short",      // "regen" only when opted in
    "battery_enabled": false,
    "controller": null,         // or {mode, kp, ki, kd, setpoint, output}
    "step_rate": 200,           // stepper steps/s
    "supply_hz": 60,            // induction AC frequency
    "commutation": "six_step"   // bldc: six_step | foc
  }
}
```

`flags` also includes `regen` (regenerative braking actively charging).
For stepper/induction motors, `stall` reflects the model's own physics
(step loss / excessive slip) instead of the DC locked-rotor heuristic,
and `elec_angle` carries the stepper's true mechanical angle / the
induction machine's rotating-field angle.

### 1.3 `event` — occasional notifications

```json
{ "type": "event", "event": "record_started",  "name": "run-1", "bench": "A" }
{ "type": "event", "event": "record_stopped",  "name": "run-1", "bench": "A" }
{ "type": "event", "event": "preset_loaded",   "name": "...", "state": { ... } }
{ "type": "event", "event": "scenario_started",  "name": "...", "steps": 9, "bench": "A" }
{ "type": "event", "event": "scenario_step",     "name": "...", "index": 3, "total": 9,
                   "label": "set_load", "t": 2.5, "bench": "A" }
{ "type": "event", "event": "scenario_finished", "name": "...", "bench": "A" }
{ "type": "event", "event": "scenario_stopped",  "name": "...", "bench": "A" }
{ "type": "event", "event": "numeric_fault",   "message": "..." }
{ "type": "event", "event": "error",           "message": "..." }
```

### 1.4 `pong` — reply to a client `ping`.

## 2. Client → server commands

Every command is validated synchronously; invalid input produces an
`event/error` back to *that* client and changes nothing. Valid commands are
applied at the next simulation tick boundary (plan section 4). An optional
`bench: "A"|"B"` field (default `"A"`) picks the target simulation.

| Command | Fields | Notes |
| --- | --- | --- |
| `set_voltage` | `value`: 0..1000 (V) | Throttle setpoint; clamped to the motor's `max_voltage` when applied. |
| `set_running` | `on`: bool | Master start/stop. Stopped + no brake = coast (zero-current). |
| `set_brake` | `on`: bool | Dynamic brake (shorted winding) while not running. |
| `set_direction` | `value`: 1 \| -1 | Sign of the drive voltage. |
| `set_motor` | `motor_type`: "dc"\|"bldc", `params`: {..} | Fresh motor of that type (full reset, preferred backend). |
| `set_params` | `params`: {..} | Live edit of any subset of motor params, state preserved. |
| `set_load` | `kind`, `params`: {..} | See load table below. |
| `fault` | `kind`: "sag" (`depth` 0..1, `duration` s) \| "jam" (`on`) \| "clear" | `clear` also resets stall/numeric latches. |
| `set_limits` | any of `current_limit` (A), `limit_enabled`, `ambient_c`, `overheat_c`, `thermal_feedback` | Limiter + thermal environment settings. |
| `time` | `action`: "pause"\|"play"\|"step" (`step_s`) — or `scale`: 0..4 | `play` also clears a numeric fault pause. |
| `set_pwm` | any of `enabled`, `duty` (0..1), `frequency` (20..2000 Hz) | PWM chopper drive: the full bus (`max_voltage`) switched at duty × frequency instead of the throttle voltage. |
| `scenario` | `action`: "start" (`name`, `steps`) \| "stop" | `steps` = `[{ "t": sim-seconds, "do": <any command above> }, ...]`; all steps validate before anything runs; times are simulation time. |
| `set_battery` | `enabled`; when true: `capacity_ah`, `internal_resistance`, `nominal_voltage`, `regen_limit`, `soc` | Installs a finite pack as the bus (fresh instance each apply); `enabled:false` returns to the ideal rail. |
| `set_brake` | `on`, optional `mode`: "short" (default) \| "regen" | Regen is opt-in and needs a battery; it only charges while back-EMF exceeds the bus voltage. |
| `set_controller` | `mode`: "off"\|"speed"\|"torque"\|"position", `kp`, `ki`, `kd`, `setpoint` | Closed-loop PID at ~1 kHz; owns the drive voltage (or PWM duty) while active. Setpoint units: RPM / N·m / revolutions. Emits `setpoint_changed` events. |
| `set_step_rate` | `rate`: 0..100000 steps/s | Stepper drive speed (direction still from `set_direction`). |
| `set_supply_frequency` | `hz`: 0..400 | Induction-motor AC supply frequency; `set_voltage` sets the AC magnitude (V/Hz drive). |
| `set_commutation` | `mode`: "six_step" \| "foc" | BLDC only. FOC is the idealized sinusoidal drive: no six-step torque ripple, continuous field view, `sector` reports −1. |
| `apply_state` | `state`: preset-shaped object | Applies a full state blob (share links / session restore); same validation as presets. |

Preset-shaped blobs (`apply_state`, autosave, share links) may carry an
`extras` block — `pwm`, `commutation`, `step_rate`, `supply_hz`,
`brake_mode`, `controller` — restoring the full driving context that
plain preset files don't include. `SimulationSession.snapshot()` emits
exactly this shape.

`set_motor` now accepts `motor_type` "dc" \| "bldc" \| "stepper" \|
"induction" — the latter two run on the Python engine layer only (no C++
port yet; the `backend` chip reports it honestly). Type-specific params:
stepper `holding_torque`, `step_angle_deg`, `rated_current`,
`pullout_corner`; induction `pole_pairs`, `breakdown_torque`,
`breakdown_slip`, `magnetizing_current`, `rated_frequency`.
| `record` | `action`: "start" (`name`) \| "stop" | Events confirm with the final (deduplicated) run name. |
| `reset` | — | Motor back to standstill; params, load config and limits kept. |
| `load_preset` | `name`: preset id or name | Applies a full scenario preset. |
| `ping` | — | Server replies `pong`. |

### Load kinds (`set_load`)

| kind | params (all optional, defaults in `loads.py`) | physics |
| --- | --- | --- |
| `none` | — | no load |
| `constant` | `torque` | fixed T (signed) |
| `viscous` | `coefficient` | T = c·ω |
| `fan` | `coefficient` | T = k·ω², opposing |
| `pump` | `static_torque`, `coefficient` | T = a + b·ω², opposing |
| `wheel` | `mass`, `radius`, `gear_ratio`, `rolling_coeff`, `drag_area`, `wheel_inertia` | rolling + aero drag, adds reflected inertia |
| `flywheel` | `mass`, `radius`, `bearing_drag` | big inertia, tiny drag |

## 3. Preset format

A preset (built-in file in `motorsim_server/presets/`, or user-saved via
`POST /api/presets`) extends the batch config's motor description:

```json
{
  "name": "Hobby gearmotor 12 V",
  "description": "Small brushed DC gearmotor",
  "motor_type": "dc",
  "params": { ...same keys as state.params... },
  "load":    { "kind": "constant", "params": { "torque": 0.02 } },
  "limits":  { "current_limit": 8.0, "enabled": true },
  "thermal": { "ambient_c": 25, "overheat_c": 120,
               "thermal_resistance": 8.0, "thermal_capacitance": 12.0,
               "resistance_feedback": false },
  "drive":   { "voltage": 12.0 }
}
```

The server adds `id` (`builtin:<file>` / `user:<slug>`) and `source` when
listing.

## 4. Importable log CSVs (hardware-in-the-loop, log flavor)

The front-end's "Import log (CSV)" button overlays a real measurement on
the compare charts without any server involvement. The file needs a
header row containing a `t` column (seconds) plus any of the telemetry
channel names — the canonical set is `CSV_FIELDS` in
`python/motorsim_server/recording.py` (`rpm`, `current`, `torque`,
`temperature`, …). A run exported from `/api/runs/{name}/csv` re-imports
losslessly, so sim-vs-real comparisons and sim-vs-sim round trips both
work.

## 5. REST endpoints

| Route | Method | Purpose |
| --- | --- | --- |
| `/` and `/<static>` | GET/HEAD | the app and everything under `web/` (incl. `assets/motor.glb`) |
| `/api/state` | GET | `{ "A": state, "B": state }` snapshots (hello's `benches`) |
| `/api/presets` | GET | preset library |
| `/api/presets` | POST | save a user preset (body = preset JSON) |
| `/api/scenarios` | GET | built-in scenario scripts `{name, description, steps}` |
| `/api/runs` | GET | recorded-run summaries `{name, bench, frames, duration, complete}` |
| `/api/runs/{name}/data` | GET | frames for chart overlay (decimated JSON) |
| `/api/runs/{name}/csv` | GET | CSV download (superset of the batch CLI's columns) |
| `/api/runs/{name}` | DELETE | delete a run |
