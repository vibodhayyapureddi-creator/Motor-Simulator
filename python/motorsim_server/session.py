"""SimulationSession: the real-time loop at the heart of the app.

Plan section 4. A dedicated thread advances the motor in fixed sub-steps
until simulated time has caught up with elapsed wall-clock time multiplied
by the user's time scale (the classic fixed-timestep accumulator). Inputs
land in a command queue and are applied at tick boundaries; telemetry is
downsampled to ~60 frames/s, each carrying the latest values plus the
peak/RMS current over the frame window so downsampling can't hide the
ripple or the inrush spike.

Guardrails: parameter validation happens before a
command is enqueued, catch-up per tick is capped (falling behind lowers the
reported real-time factor instead of freezing the app), and a NaN/blow-up
check auto-pauses the sim with a "numeric" fault rather than crashing.
"""
from __future__ import annotations

import collections
import math
import os
import threading
import time
from typing import Callable, Dict, List, Optional

from .battery import BatteryModel
from .controller import MODES as PID_MODES, PIDController
from .environment import CurrentLimiter, StallDetector, Supply, ThermalModel, finite
from .livemotor import LiveMotor
from .loads import LoadModel, make_load
from .recording import Recorder

TICK_SECONDS = 1.0 / 60.0            # target telemetry / input cadence

# Fraction of each tick a bench may spend stepping the engine. The loop is
# otherwise greedy: it always tries to catch up to wall-clock time, so one
# bench happily eats ~90% of a core and two eat more than a small machine
# has. That starves the web server itself, and requests start failing
# before they ever reach the app. Lower it (MOTORSIM_CPU_BUDGET=0.15) to
# trade simulation speed for a responsive server; the real-time factor
# already shown in the UI is the honest readout of that trade.
CPU_BUDGET = max(0.02, min(0.9, float(os.environ.get("MOTORSIM_CPU_BUDGET", 0.75))))

# How often an unwatched bench wakes just to stay responsive to commands.
IDLE_TICK_SECONDS = 0.5
SUBSTEP_CPP = 5e-5                   # engine sub-step on the C++ backend (s)
SUBSTEP_FALLBACK = 2e-4              # coarser on pure Python; RK4 stays stable
MAX_CATCHUP_SIM_S = 0.25             # per-tick catch-up cap (sim seconds)
TIME_SCALES = (1.0, 0.25, 0.1, 0.02)

# Rotor-jam fault: cap on the braking torque (far beyond any preset motor).
JAM_TORQUE = 1000.0


class SimulationSession:
    def __init__(self, recorder: Optional[Recorder] = None, name: str = "A"):
        self.name = name          # bench id: telemetry routing + recording tag
        self.motor = LiveMotor("dc", {})
        self.load: LoadModel = make_load("none")
        self.thermal = ThermalModel()
        self.limiter = CurrentLimiter()
        self.supply = Supply()
        self.stall = StallDetector()
        self.recorder = recorder or Recorder()

        # user drive inputs
        self.throttle_v = 0.0
        self.running = False
        self.brake = False
        self.brake_mode = "short"   # "short" = dynamic brake, "regen" = into battery
        self.direction = 1

        # optional finite supply; None = ideal infinite bus
        self.battery: Optional[BatteryModel] = None
        self._regen_active = False

        # closed-loop control (plan phase 7); position integrates the shaft
        self.controller: Optional[PIDController] = None
        self._position_rev = 0.0
        self._ctrl_v = 0.0

        # motor-type-specific drive inputs (plan phase 9)
        self.step_rate = 200.0        # stepper: commanded steps/s
        self.supply_hz = 60.0         # induction: AC supply frequency
        self.commutation = "six_step" # bldc: six_step | foc (idealized)
        self._pre_foc_ripple: Optional[float] = None
        self.time_scale = 1.0
        self.paused = False
        self.preset_name: Optional[str] = None

        # PWM chopper drive (alternative to direct voltage): the bus is
        # switched at pwm_freq with the given duty, like a real controller.
        # Frequency is capped so a period spans >= ~10 engine sub-steps.
        self.pwm_enabled = False
        self.pwm_duty = 0.5
        self.pwm_freq = 500.0
        self._pwm_phase = 0.0

        # scripted scenario: steps compiled up front, fired on sim time.
        # Elapsed time is accumulated from stepped sim-seconds (not read off
        # the motor clock) so a reset step inside a script can't strand it.
        self._scenario: Optional[list] = None   # [(t, label, closure), ...]
        self._scenario_name = ""
        self._scenario_elapsed = 0.0
        self._scenario_idx = 0

        # thermal resistance feedback needs the user's cold R kept separate
        self._cold_resistance = self.motor.params["resistance"]

        self.numeric_fault = False
        self.jammed = False
        self._rt_factor = 1.0
        self._accumulator = 0.0
        self._pending_step_s = 0.0

        self._commands: "collections.deque[Callable[[], None]]" = collections.deque()
        self._listeners: List[Callable[[dict], None]] = []
        self._listeners_lock = threading.Lock()

        # per-frame stats (peak/RMS current, mean powers - honest under PWM)
        self._frame_i_peak = 0.0
        self._frame_i_sq_sum = 0.0
        self._frame_p_in_sum = 0.0
        self._frame_p_out_sum = 0.0
        self._frame_i_n = 0

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sim-loop", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def add_listener(self, fn: Callable[[dict], None]) -> None:
        with self._listeners_lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict], None]) -> None:
        with self._listeners_lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def _watched(self) -> bool:
        """Is this bench worth spending CPU on right now?

        With nobody connected there is no one to send telemetry to, so
        stepping the engine is pure waste. It is not free waste either:
        the loop is greedy by design, so two idle benches can saturate a
        small container and starve the web server that serves the page.
        Recording keeps a bench alive even with no viewers.
        """
        with self._listeners_lock:
            if self._listeners:
                return True
        return self.recorder.recording is not None

    def _emit(self, message: dict) -> None:
        with self._listeners_lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(message)
            except Exception:
                pass  # a dead client must never take down the sim loop

    # ------------------------------------------------------------- commands

    def handle_command(self, msg: dict) -> None:
        """Validate a protocol message and enqueue its effect.

        Runs on a WebSocket reader thread. Raises ValueError on bad input
        (the caller reports it to that client); valid commands apply at the
        next tick boundary.
        """
        closure = self._compile(msg)
        if closure is not None:
            self._commands.append(closure)

    def _compile(self, msg: dict):
        """Validate a command and return its apply-closure (or None).

        Shared by live commands and the scenario compiler, so scripted
        steps get exactly the same validation as interactive input.
        """
        if not isinstance(msg, dict):
            raise ValueError("commands must be JSON objects")
        kind = msg.get("type")
        if not isinstance(kind, str) or kind.startswith("_"):
            raise ValueError(f"unknown command type '{kind}'")
        handler = getattr(self, f"_cmd_{kind}", None)
        if handler is None:
            raise ValueError(f"unknown command type '{kind}'")
        return handler(msg)  # validation happens here, synchronously

    @staticmethod
    def _num(msg: dict, key: str, lo: float, hi: float, default=None) -> float:
        v = msg.get(key, default)
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v):
            raise ValueError(f"'{key}' must be a finite number")
        return min(hi, max(lo, float(v)))

    def _cmd_set_voltage(self, msg):
        v = self._num(msg, "value", 0.0, 1000.0)
        def apply(): self.throttle_v = v
        return apply

    def _cmd_set_running(self, msg):
        on = bool(msg.get("on"))
        def apply(): self.running = on
        return apply

    def _cmd_set_brake(self, msg):
        on = bool(msg.get("on"))
        mode = str(msg.get("mode") or self.brake_mode)
        if mode not in ("short", "regen"):
            raise ValueError("brake mode must be short|regen")
        def apply():
            self.brake = on
            self.brake_mode = mode
        return apply

    def _cmd_set_battery(self, msg):
        if "enabled" in msg and not msg["enabled"]:
            def apply(): self.battery = None
            return apply
        cap = self._num(msg, "capacity_ah", 0.05, 1000.0, 2.0)
        r_int = self._num(msg, "internal_resistance", 0.0, 10.0, 0.05)
        v_nom = self._num(msg, "nominal_voltage", 1.0, 1000.0,
                          self.motor.params["max_voltage"])
        soc = self._num(msg, "soc", 0.0, 1.0, 1.0)
        regen_a = self._num(msg, "regen_limit", 0.1, 1000.0, 10.0)
        def apply():
            self.battery = BatteryModel(
                capacity_ah=cap, internal_resistance=r_int,
                nominal_voltage=v_nom, regen_limit_a=regen_a, soc=soc)
        return apply

    def _cmd_set_direction(self, msg):
        d = 1 if self._num(msg, "value", -1, 1, 1) >= 0 else -1
        def apply(): self.direction = d
        return apply

    def _cmd_set_motor(self, msg):
        motor_type = msg.get("motor_type", self.motor.motor_type)
        params = msg.get("params") or {}
        # validate eagerly by building a throwaway (cheap) LiveMotor check
        from .livemotor import validate_params
        validate_params(params)
        def apply():
            self.motor.set_motor(motor_type, params)
            self._after_motor_rebuild()
        return apply

    def _cmd_set_params(self, msg):
        from .livemotor import _TYPE_ONLY, validate_params
        params = validate_params(msg.get("params") or {})
        for key, types in _TYPE_ONLY.items():   # reject eagerly, not at apply
            if key in params and self.motor.motor_type not in types:
                raise ValueError(
                    f"'{key}' only applies to {' / '.join(types)} motors")
        def apply():
            self.motor.set_params(params)
            if "resistance" in params:
                self._cold_resistance = params["resistance"]
        return apply

    def _cmd_set_load(self, msg):
        load = make_load(msg.get("kind", "none"), msg.get("params"))
        def apply():
            self.load = load
            self.motor.set_extra_inertia(load.extra_inertia())
        return apply

    def _cmd_fault(self, msg):
        kind = (msg.get("kind") or "").lower()
        if kind == "sag":
            depth = self._num(msg, "depth", 0.0, 1.0, 0.5)
            duration = self._num(msg, "duration", 0.05, 30.0, 1.0)
            def apply():
                self.supply.trigger_sag(depth, duration)
                self._fault_event("sag")
        elif kind == "jam":
            on = bool(msg.get("on", True))
            def apply():
                self.jammed = on
                self._fault_event("jam" if on else "unjam")
        elif kind == "clear":
            def apply():
                self.jammed = False
                self.supply.clear()
                self.stall.reset()
                self.numeric_fault = False
                self._fault_event("clear")
        else:
            raise ValueError(f"unknown fault kind '{kind}'")
        return apply

    def _fault_event(self, kind: str) -> None:
        """Chart-marker event: what happened and when (sim time)."""
        self._emit({"type": "event", "event": "fault_triggered", "kind": kind,
                    "bench": self.name, "t": round(self.motor.state.time, 3)})

    def _cmd_set_limits(self, msg):
        updates = {}
        if "current_limit" in msg:
            updates["limit"] = self._num(msg, "current_limit", 0.0, 10000.0)
        if "limit_enabled" in msg:
            updates["limit_enabled"] = bool(msg["limit_enabled"])
        if "ambient_c" in msg:
            updates["ambient"] = self._num(msg, "ambient_c", -40.0, 200.0)
        if "overheat_c" in msg:
            updates["overheat"] = self._num(msg, "overheat_c", 0.0, 400.0)
        if "thermal_feedback" in msg:
            updates["feedback"] = bool(msg["thermal_feedback"])
        def apply():
            if "limit" in updates: self.limiter.limit_a = updates["limit"]
            if "limit_enabled" in updates: self.limiter.enabled = updates["limit_enabled"]
            if "ambient" in updates: self.thermal.ambient_c = updates["ambient"]
            if "overheat" in updates: self.thermal.overheat_c = updates["overheat"]
            if "feedback" in updates:
                self.thermal.resistance_feedback = updates["feedback"]
                if not updates["feedback"]:
                    self.motor.set_params({"resistance": self._cold_resistance})
        return apply

    def _cmd_set_step_rate(self, msg):
        rate = self._num(msg, "rate", 0.0, 100000.0)
        def apply(): self.step_rate = rate
        return apply

    def _cmd_set_supply_frequency(self, msg):
        hz = self._num(msg, "hz", 0.0, 400.0)
        def apply(): self.supply_hz = hz
        return apply

    def _cmd_set_commutation(self, msg):
        mode = str(msg.get("mode", "six_step")).lower()
        if mode not in ("six_step", "foc"):
            raise ValueError("commutation must be six_step|foc")
        def apply():
            if self.motor.motor_type != "bldc":
                raise ValueError("commutation modes apply to BLDC motors")
            if mode == self.commutation:
                return
            if mode == "foc":
                # idealized FOC: sinusoidal commutation = no six-step
                # torque ripple; the ripple depth is stashed for return
                self._pre_foc_ripple = self.motor.params["ripple_depth"]
                self.motor.set_params({"ripple_depth": 0.0})
            else:
                self.motor.set_params({"ripple_depth":
                    self._pre_foc_ripple if self._pre_foc_ripple is not None else 0.05})
            self.commutation = mode
        return apply

    def _cmd_set_controller(self, msg):
        mode = str(msg.get("mode", "off")).lower()
        if mode not in PID_MODES:
            raise ValueError(f"controller mode must be one of {PID_MODES}")
        kp = self._num(msg, "kp", 0.0, 1e6, 0.01)
        ki = self._num(msg, "ki", 0.0, 1e6, 0.0)
        kd = self._num(msg, "kd", 0.0, 1e6, 0.0)
        setpoint = self._num(msg, "setpoint", -1e9, 1e9, 0.0)
        def apply():
            if mode == "off":
                self.controller = None
                return
            if self.controller is None or self.controller.mode != mode:
                self.controller = PIDController(mode=mode)
                self._position_rev = 0.0   # position control: here = zero
            c = self.controller
            c.kp, c.ki, c.kd = kp, ki, kd
            c.out_max = self.motor.params["max_voltage"]
            if c.setpoint != setpoint:
                c.setpoint = setpoint
                self._emit({"type": "event", "event": "setpoint_changed",
                            "bench": self.name, "value": setpoint,
                            "t": round(self.motor.state.time, 3)})
        return apply

    def _cmd_set_pwm(self, msg):
        updates = {}
        if "enabled" in msg:
            updates["enabled"] = bool(msg["enabled"])
        if "duty" in msg:
            updates["duty"] = self._num(msg, "duty", 0.0, 1.0)
        if "frequency" in msg:
            # cap so one period spans >= ~10 engine sub-steps (50 us each)
            updates["freq"] = self._num(msg, "frequency", 20.0, 2000.0)
        if not updates:
            raise ValueError("set_pwm needs enabled, duty and/or frequency")
        def apply():
            if "enabled" in updates:
                self.pwm_enabled = updates["enabled"]
                self._pwm_phase = 0.0
            if "duty" in updates: self.pwm_duty = updates["duty"]
            if "freq" in updates: self.pwm_freq = updates["freq"]
        return apply

    def _cmd_scenario(self, msg):
        action = msg.get("action")
        if action == "stop":
            def apply():
                if self._scenario is not None:
                    self._scenario = None
                    self._emit({"type": "event", "event": "scenario_stopped",
                                "name": self._scenario_name, "bench": self.name})
            return apply
        if action != "start":
            raise ValueError("scenario action must be start|stop")
        steps = msg.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("scenario needs a non-empty 'steps' list")
        if len(steps) > 200:
            raise ValueError("scenario too long (max 200 steps)")
        compiled = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"step {i} must be an object")
            t = self._num(step, "t", 0.0, 3600.0, 0.0)
            do = step.get("do")
            if not isinstance(do, dict) or do.get("type") == "scenario":
                raise ValueError(f"step {i} needs a 'do' command (not a scenario)")
            label = do.get("type", "?")
            compiled.append((t, label, self._compile(do)))
        compiled.sort(key=lambda s: s[0])
        name = str(msg.get("name") or "scenario")
        def apply():
            self._scenario = compiled
            self._scenario_name = name
            self._scenario_elapsed = 0.0
            self._scenario_idx = 0
            self._emit({"type": "event", "event": "scenario_started",
                        "name": name, "steps": len(compiled), "bench": self.name})
        return apply

    def _cmd_time(self, msg):
        action = msg.get("action")
        if action == "pause":
            def apply(): self.paused = True
        elif action == "play":
            def apply():
                self.paused = False
                self.numeric_fault = False
        elif action == "step":
            step_s = self._num(msg, "step_s", 1e-5, 0.1, 0.001)
            def apply(): self._pending_step_s += step_s
        elif "scale" in msg:
            scale = self._num(msg, "scale", 0.0, 4.0)
            def apply(): self.time_scale = scale
        else:
            raise ValueError("time command needs action or scale")
        return apply

    def _cmd_record(self, msg):
        action = msg.get("action")
        if action == "start":
            name = str(msg.get("name") or "")
            def apply():
                run = self.recorder.start(name, self.name)
                self._emit({"type": "event", "event": "record_started",
                            "name": run, "bench": self.name})
        elif action == "stop":
            def apply():
                run = self.recorder.stop()
                self._emit({"type": "event", "event": "record_stopped",
                            "name": run, "bench": self.name})
        else:
            raise ValueError("record action must be start|stop")
        return apply

    def _cmd_reset(self, msg):
        def apply():
            self.motor.reset()
            self._after_motor_rebuild()
        return apply

    def _after_motor_rebuild(self) -> None:
        """Shared cleanup after any full motor rebuild."""
        self.commutation = "six_step"
        self._pre_foc_ripple = None
        self.motor.set_extra_inertia(self.load.extra_inertia())
        self._cold_resistance = self.motor.params["resistance"]
        self.thermal.reset()
        self.stall.reset()
        self.supply.clear()
        self.jammed = False
        self.numeric_fault = False
        self._accumulator = 0.0

    def snapshot(self) -> dict:
        """Full state as a preset-shaped dict (autosave / share / restore).

        Round-trips through apply_preset(), including the drive extras
        that plain presets don't normally carry.
        """
        snap = {
            "name": self.preset_name or f"bench {self.name}",
            "motor_type": self.motor.motor_type,
            "params": dict(self.motor.params),
            "load": {"kind": self.load.kind, "params": self.load.describe()},
            "limits": {"current_limit": self.limiter.limit_a,
                       "enabled": self.limiter.enabled},
            "thermal": {
                "ambient_c": self.thermal.ambient_c,
                "overheat_c": self.thermal.overheat_c,
                "thermal_resistance": self.thermal.thermal_resistance,
                "thermal_capacitance": self.thermal.thermal_capacitance,
                "resistance_feedback": self.thermal.resistance_feedback,
            },
            "drive": {"voltage": self.throttle_v},
            "extras": {
                "pwm": {"enabled": self.pwm_enabled, "duty": self.pwm_duty,
                        "frequency": self.pwm_freq},
                "commutation": self.commutation,
                "step_rate": self.step_rate,
                "supply_hz": self.supply_hz,
                "brake_mode": self.brake_mode,
                "controller": (self.controller.describe()
                               if self.controller is not None else None),
            },
        }
        if self.battery is not None:
            b = self.battery
            snap["battery"] = {"capacity_ah": b.capacity_ah,
                               "internal_resistance": b.internal_resistance,
                               "nominal_voltage": b.nominal_voltage,
                               "regen_limit": b.regen_limit_a, "soc": b.soc}
        return snap

    def apply_preset(self, preset: dict) -> None:
        """Load a full scenario preset (motor + load + limits + thermal)."""
        motor_type = preset.get("motor_type", "dc")
        params = preset.get("params") or {}
        load_cfg = preset.get("load") or {"kind": "none"}
        load = make_load(load_cfg.get("kind", "none"), load_cfg.get("params"))
        limits = preset.get("limits") or {}
        thermal = preset.get("thermal") or {}
        drive = preset.get("drive") or {}
        battery_cfg = preset.get("battery")
        def apply():
            self.motor.set_motor(motor_type, params)
            self.load = load
            self._after_motor_rebuild()
            if "current_limit" in limits:
                self.limiter.limit_a = float(limits["current_limit"])
            self.limiter.enabled = bool(limits.get("enabled", True))
            if "ambient_c" in thermal:
                self.thermal.ambient_c = float(thermal["ambient_c"])
            if "overheat_c" in thermal:
                self.thermal.overheat_c = float(thermal["overheat_c"])
            if "thermal_resistance" in thermal:
                self.thermal.thermal_resistance = float(thermal["thermal_resistance"])
            if "thermal_capacitance" in thermal:
                self.thermal.thermal_capacitance = float(thermal["thermal_capacitance"])
            self.thermal.resistance_feedback = bool(thermal.get("resistance_feedback", False))
            self.thermal.reset()
            if battery_cfg:
                self.battery = BatteryModel(
                    capacity_ah=float(battery_cfg.get("capacity_ah", 2.0)),
                    internal_resistance=float(battery_cfg.get("internal_resistance", 0.05)),
                    nominal_voltage=float(battery_cfg.get(
                        "nominal_voltage", params.get("max_voltage", 12.0))),
                    regen_limit_a=float(battery_cfg.get("regen_limit", 10.0)),
                    soc=float(battery_cfg.get("soc", 1.0)))
            else:
                self.battery = None
            self.throttle_v = float(drive.get("voltage", self.throttle_v))
            # drive extras: carried by snapshots/share-links, not by plain
            # preset files - restores the full driving context
            extras = preset.get("extras") or {}
            pwm = extras.get("pwm") or {}
            if pwm:
                self.pwm_enabled = bool(pwm.get("enabled", False))
                self.pwm_duty = min(1.0, max(0.0, float(pwm.get("duty", 0.5))))
                self.pwm_freq = min(2000.0, max(20.0, float(pwm.get("frequency", 500.0))))
            if "step_rate" in extras:
                self.step_rate = min(100000.0, max(0.0, float(extras["step_rate"])))
            if "supply_hz" in extras:
                self.supply_hz = min(400.0, max(0.0, float(extras["supply_hz"])))
            if extras.get("brake_mode") in ("short", "regen"):
                self.brake_mode = extras["brake_mode"]
            if (extras.get("commutation") == "foc"
                    and self.motor.motor_type == "bldc"):
                self._pre_foc_ripple = self.motor.params["ripple_depth"]
                self.motor.set_params({"ripple_depth": 0.0})
                self.commutation = "foc"
            pid = extras.get("controller")
            if pid and pid.get("mode") in ("speed", "torque", "position"):
                self.controller = PIDController(
                    mode=pid["mode"],
                    kp=max(0.0, float(pid.get("kp", 0.01))),
                    ki=max(0.0, float(pid.get("ki", 0.0))),
                    kd=max(0.0, float(pid.get("kd", 0.0))),
                    setpoint=float(pid.get("setpoint", 0.0)),
                    out_max=self.motor.params["max_voltage"])
            self.preset_name = preset.get("name")
            self._emit({"type": "event", "event": "preset_loaded",
                        "name": self.preset_name, "state": self.full_state()})
        self._commands.append(apply)

    # ------------------------------------------------------------- main loop

    def _run(self) -> None:
        last = time.monotonic()
        rt_sim = 0.0     # sim seconds actually stepped (for rt factor)
        rt_wall = 0.0    # scaled wall seconds requested
        rt_report = last
        while not self._stop.is_set():
            now = time.monotonic()
            elapsed = min(now - last, 0.25)  # ignore huge stalls (debugger etc.)
            last = now

            while self._commands:
                try:
                    self._commands.popleft()()
                except Exception as exc:
                    self._emit({"type": "event", "event": "error", "message": str(exc)})

            # scripted scenario: fire steps whose sim time has come. Runs on
            # sim time, so pause/slow-motion pause/stretch the script too.
            if self._scenario is not None:
                elapsed_sim = self._scenario_elapsed
                while (self._scenario is not None
                       and self._scenario_idx < len(self._scenario)
                       and self._scenario[self._scenario_idx][0] <= elapsed_sim):
                    t, label, closure = self._scenario[self._scenario_idx]
                    self._scenario_idx += 1
                    try:
                        closure()
                    except Exception as exc:
                        self._emit({"type": "event", "event": "error",
                                    "message": f"scenario step '{label}': {exc}"})
                    self._emit({"type": "event", "event": "scenario_step",
                                "name": self._scenario_name, "bench": self.name,
                                "index": self._scenario_idx,
                                "total": len(self._scenario) if self._scenario else 0,
                                "label": label, "t": t})
                if (self._scenario is not None
                        and self._scenario_idx >= len(self._scenario)):
                    self._emit({"type": "event", "event": "scenario_finished",
                                "name": self._scenario_name, "bench": self.name})
                    self._scenario = None

            # Nobody watching: idle cheaply instead of simulating into the
            # void. Commands are still drained above, so a bench resumes
            # instantly when someone connects.
            if not self._watched():
                self._accumulator = 0.0
                self._stop.wait(IDLE_TICK_SECONDS)
                last = time.monotonic()
                continue

            budget = 0.0
            if not self.paused and not self.numeric_fault:
                budget = elapsed * self.time_scale
            if self._pending_step_s > 0.0:
                budget += self._pending_step_s
                self._pending_step_s = 0.0

            self._accumulator += budget
            requested = self._accumulator
            if self._accumulator > MAX_CATCHUP_SIM_S:
                self._accumulator = MAX_CATCHUP_SIM_S

            stepped = self._advance(self._accumulator)
            self._accumulator -= stepped
            if self._scenario is not None:
                self._scenario_elapsed += stepped

            rt_sim += stepped
            rt_wall += requested if requested > 0 else 0.0
            if now - rt_report >= 1.0:
                self._rt_factor = 1.0 if rt_wall <= 0 else min(1.0, rt_sim / rt_wall)
                rt_sim = rt_wall = 0.0
                rt_report = now

            frame = self._make_frame()
            self.recorder.append(frame)
            self._emit(frame)

            spent = time.monotonic() - now
            self._stop.wait(max(0.001, TICK_SECONDS - spent))

    def _advance(self, sim_seconds: float) -> float:
        """Run whole sub-steps totalling at most sim_seconds; returns time stepped."""
        dt = SUBSTEP_CPP if self.motor.backend_name == "cpp" else SUBSTEP_FALLBACK
        n = int(sim_seconds / dt)
        if n <= 0:
            return 0.0

        ke = self.motor.params["back_emf_constant"]
        ctrl_every = max(1, int(round(0.001 / dt)))   # ~1 kHz control rate
        deadline = time.monotonic() + CPU_BUDGET * TICK_SECONDS
        mtype = self.motor.motor_type
        # motor-type-specific drive inputs (constant across one tick)
        if mtype == "stepper":
            self.motor.set_step_rate(
                self.direction * self.step_rate if self.running else 0.0)
        elif mtype == "induction":
            self.motor.set_supply_frequency(self.direction * self.supply_hz)
        done = 0
        for k in range(n):
            state = self.motor.state
            omega, current = state.omega, state.current

            # closed-loop controller: overwrite the drive command at a
            # realistic control rate, not every electrical sub-step
            # (steppers are position-driven by construction: skip PID)
            if (self.controller is not None and self.running
                    and mtype != "stepper"
                    and (k % ctrl_every) == 0):
                c = self.controller
                if c.mode == "speed":
                    meas = abs(state.rpm)
                elif c.mode == "torque":
                    meas = abs(state.torque)
                else:
                    meas = self._position_rev
                out = c.update(dt * ctrl_every, meas)
                if c.mode == "position":
                    self._ctrl_v = out
                elif self.pwm_enabled:
                    self.pwm_duty = min(1.0, out / max(1e-9, c.out_max))
                else:
                    self.throttle_v = out

            # bus voltage available this sub-step: the battery's terminal
            # voltage (sagging under the load current) or the ideal rail,
            # either way scaled by any active supply sag
            self.supply.step(dt)
            if self.battery is not None:
                v_avail = self.battery.terminal_voltage(
                    abs(current) if self.running else 0.0) * self.supply.factor()
            else:
                v_avail = self.motor.params["max_voltage"] * self.supply.factor()

            # drive voltage: throttle when running, brake, or coast
            # (coast injects v = Ke*omega so winding current ~ 0 -> no torque)
            bus_connected = False
            regen_now = False
            if self.running:
                if (self.controller is not None
                        and self.controller.mode == "position"):
                    # position control drives either direction directly
                    v_cmd = max(-v_avail, min(v_avail, self._ctrl_v))
                    bus_connected = True
                elif self.pwm_enabled:
                    # chopper drive: full bus while phase < duty, else 0.
                    # Phase advances in sim time so slow-motion shows the
                    # switching (and the current ripple it causes).
                    self._pwm_phase += dt * self.pwm_freq
                    if self._pwm_phase >= 1.0:
                        self._pwm_phase -= int(self._pwm_phase)
                    on_phase = self._pwm_phase < self.pwm_duty
                    v_cmd = self.direction * (v_avail if on_phase else 0.0)
                    bus_connected = on_phase
                else:
                    v_cmd = self.direction * min(self.throttle_v, v_avail)
                    bus_connected = True
            elif self.brake:
                if (self.brake_mode == "regen" and self.battery is not None
                        and abs(ke * omega) > v_avail and v_avail > 0.0):
                    # back-EMF exceeds the bus: the motor generates and
                    # current flows into the pack until they meet
                    v_cmd = v_avail if omega >= 0.0 else -v_avail
                    bus_connected = True
                    regen_now = True
                else:
                    v_cmd = 0.0   # plain dynamic brake (winding shorted)
            else:
                v_cmd = ke * omega

            if mtype == "stepper":
                # steppers are current-driven: the bus just energizes the
                # phases (running or holding via brake); rate carries speed
                energize = self.running or self.brake
                v_cmd = v_avail if energize else 0.0
                bus_connected = energize
                regen_now = False
            else:
                v_cmd = self.limiter.apply(v_cmd, current)

            load_t = self.load.torque(omega)
            if self.jammed:
                # deadbeat brake: the torque that would null omega in ~one
                # sub-step (capped). A fixed huge opposing torque would
                # bang-bang the shaft into a +/-100 rad/s oscillation at
                # this dt; this holds it at a sub-RPM creep instead.
                j_eff = self.motor.effective_inertia()
                brake = 0.9 * j_eff * omega / dt
                load_t += max(-JAM_TORQUE, min(JAM_TORQUE, brake))

            state = self.motor.step(dt, v_cmd, load_t)

            # battery current: winding current referred to the bus polarity
            # (positive discharges the pack, negative charges it)
            if self.battery is not None and bus_connected:
                i_batt = state.current * (1.0 if v_cmd >= 0.0 else -1.0)
                self.battery.step(dt, i_batt)
                self._regen_active = regen_now and i_batt < -0.02
            elif self.battery is not None:
                self._regen_active = False

            self._position_rev += state.omega * dt / (2.0 * math.pi)

            # environment updates in sim time
            r_cold = self._cold_resistance
            self.thermal.step(dt, state.current, self.thermal.hot_resistance(r_cold))
            if mtype not in ("stepper", "induction"):
                # V/R locked-rotor heuristic only makes sense for DC-style
                # drives; the new types flag stall via their own physics
                self.stall.step(dt, state.omega, state.voltage, state.current,
                                self.thermal.hot_resistance(r_cold))

            ai = abs(state.current)
            if ai > self._frame_i_peak:
                self._frame_i_peak = ai
            self._frame_i_sq_sum += state.current * state.current
            self._frame_p_in_sum += state.voltage * state.current
            self._frame_p_out_sum += state.torque * state.omega
            self._frame_i_n += 1

            if not finite(state.current, state.omega):
                self.numeric_fault = True
                self.paused = True
                self._emit({"type": "event", "event": "numeric_fault",
                            "message": "simulation diverged (non-finite state); "
                                       "auto-paused - reset or fix parameters"})
                done = k + 1
                break

            done = k + 1
            # never let one tick starve the server: bail and catch up next tick
            if (k & 0x3F) == 0 and time.monotonic() > deadline:
                break

        # thermal -> resistance feedback, applied per tick, not per sub-step
        if self.thermal.resistance_feedback:
            hot = self.thermal.hot_resistance(self._cold_resistance)
            if abs(hot - self.motor.params["resistance"]) > 0.002 * self._cold_resistance:
                self.motor.set_params({"resistance": hot})

        return done * dt

    # ------------------------------------------------------------ telemetry

    def _make_frame(self) -> dict:
        state = self.motor.state
        n = max(1, self._frame_i_n)
        i_rms = math.sqrt(self._frame_i_sq_sum / n) if self._frame_i_n else abs(state.current)
        i_peak = self._frame_i_peak if self._frame_i_n else abs(state.current)
        if self._frame_i_n:
            p_in = self._frame_p_in_sum / n
            p_out = self._frame_p_out_sum / n
        else:
            p_in = state.voltage * state.current
            p_out = state.torque * state.omega
        self._frame_i_peak = 0.0
        self._frame_i_sq_sum = 0.0
        self._frame_p_in_sum = 0.0
        self._frame_p_out_sum = 0.0
        self._frame_i_n = 0

        # efficiency only means something while actually driving a load
        if p_in > 0.25 and p_out > 0.0:
            efficiency = min(1.0, p_out / p_in)
        else:
            efficiency = 0.0

        overcurrent = self.limiter.active or (
            self.limiter.limit_a > 0 and i_peak > self.limiter.limit_a)
        frame = {
            "type": "telemetry",
            "bench": self.name,
            "t": round(state.time, 6),
            "rpm": round(state.rpm, 2),
            "omega": round(state.omega, 4),
            "current": round(state.current, 4),
            "current_peak": round(i_peak, 4),
            "current_rms": round(i_rms, 4),
            "torque": round(state.torque, 5),
            "voltage": round(state.voltage, 3),
            "temperature": round(self.thermal.temperature_c, 2),
            "housing_temp": round(self.thermal.housing_c, 2),
            "load_torque": round(state.load_torque, 5),
            "p_in": round(p_in, 3),
            "p_out": round(p_out, 3),
            "efficiency": round(efficiency, 4),
            "battery": self.battery.describe() if self.battery else None,
            "setpoint_rpm": (self.controller.setpoint
                             if self.controller is not None
                             and self.controller.mode == "speed" else None),
            "position_rev": round(self._position_rev, 4),
            "elec_angle": round(state.electrical_angle_deg, 2),
            "sector": (self.motor.commutation_sector
                       if self.commutation != "foc" else -1),
            "flags": {
                "overcurrent": bool(overcurrent),
                "overheat": self.thermal.overheated,
                "stall": (self.motor.slipping
                          if self.motor.motor_type in ("stepper", "induction")
                          else self.stall.stalled) or self.jammed,
                "sag": self.supply.sagging,
                "numeric": self.numeric_fault,
                "regen": bool(self.battery and self._regen_active),
            },
            "ctl": self._control_state(),
        }
        return frame

    def _control_state(self) -> dict:
        return {
            "running": self.running,
            "brake": self.brake,
            "brake_mode": self.brake_mode,
            "direction": self.direction,
            "throttle_v": self.throttle_v,
            "time_scale": self.time_scale,
            "paused": self.paused,
            "motor_type": self.motor.motor_type,
            "backend": self.motor.backend_name,
            "load": {"kind": self.load.kind, "params": self.load.describe()},
            "limit_a": self.limiter.limit_a,
            "limit_enabled": self.limiter.enabled,
            "ambient_c": self.thermal.ambient_c,
            "overheat_c": self.thermal.overheat_c,
            "thermal_feedback": self.thermal.resistance_feedback,
            "recording": self.recorder.recording_for(self.name),
            "rt_factor": round(self._rt_factor, 3),
            "preset": self.preset_name,
            "jammed": self.jammed,
            "pwm": {"enabled": self.pwm_enabled, "duty": self.pwm_duty,
                    "frequency": self.pwm_freq},
            "battery_enabled": self.battery is not None,
            "controller": (self.controller.describe()
                           if self.controller is not None else None),
            "step_rate": self.step_rate,
            "supply_hz": self.supply_hz,
            "commutation": self.commutation,
            "scenario": self._scenario_name if self._scenario is not None else None,
        }

    def full_state(self) -> dict:
        """Everything a newly-connected client needs (the hello message)."""
        return {
            "bench": self.name,
            "motor_type": self.motor.motor_type,
            "params": dict(self.motor.params),
            "ctl": self._control_state(),
            "time_scales": list(TIME_SCALES),
        }
