"""LiveMotor: a motor instance that tolerates being edited while running.

Plan section 5.4 wants live parameter changes, motor-type switches, and
resets while running; section 16 constrains v0.1 to the *existing* engine
API. Those collide on one point: the compiled C++ motor takes its
parameters at construction and exposes them read-only, and its integrator
state (current, omega) cannot be injected from Python.

Resolution: the pure-Python fallback engine implements the exact same
equations with the same RK4 integrator (validated to match the C++ output
to floating point - see fallback_engine.py), and *its* state and params are
freely mutable. So:

- A session starts on the preferred backend (C++ if built).
- The first live edit (parameter slider, thermal resistance feedback, or a
  load that changes effective inertia) hot-swaps to a fallback motor seeded
  from the last engine state - continuity is seamless, physics identical.
- A reset or motor-type switch starts a fresh motor, back on the preferred
  backend.

Telemetry reports which backend is live so nothing is hidden. When the
engine later grows native setters, this wrapper shrinks
to a thin pass-through without the swap.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from motorsim_app import engine_bridge, fallback_engine

# Parameter bounds (guardrails): {name: (min, max)}.
_PARAM_BOUNDS: Dict[str, tuple] = {
    "resistance": (1e-3, 1e3),
    "inductance": (1e-6, 10.0),
    "torque_constant": (1e-4, 50.0),
    "back_emf_constant": (1e-4, 50.0),
    "inertia": (1e-7, 100.0),
    "viscous_friction": (0.0, 10.0),
    "static_friction": (0.0, 50.0),
    "max_voltage": (0.1, 1000.0),
    "pole_pairs": (1, 50),
    "ripple_depth": (0.0, 1.0),
    # stepper (Python-only motor type)
    "holding_torque": (1e-4, 100.0),
    "step_angle_deg": (0.45, 15.0),
    "rated_current": (0.01, 100.0),
    "pullout_corner": (10.0, 100000.0),
    # induction (Python-only motor type)
    "breakdown_torque": (1e-3, 1000.0),
    "breakdown_slip": (0.01, 0.9),
    "magnetizing_current": (0.0, 100.0),
    "rated_frequency": (10.0, 400.0),
}

# parameters that only exist on certain motor types
_TYPE_ONLY: Dict[str, tuple] = {
    "pole_pairs": ("bldc", "induction"),
    "ripple_depth": ("bldc",),
    "holding_torque": ("stepper",),
    "step_angle_deg": ("stepper",),
    "rated_current": ("stepper",),
    "pullout_corner": ("stepper",),
    "breakdown_torque": ("induction",),
    "breakdown_slip": ("induction",),
    "magnetizing_current": ("induction",),
    "rated_frequency": ("induction",),
}

MOTOR_TYPES = ("dc", "bldc", "stepper", "induction")
_FALLBACK_ONLY_TYPES = ("stepper", "induction")   # no C++ port yet

DEFAULT_PARAMS: Dict[str, float] = {
    "resistance": 1.0,
    "inductance": 0.001,
    "torque_constant": 0.05,
    "back_emf_constant": 0.05,
    "inertia": 0.0005,
    "viscous_friction": 0.0002,
    "static_friction": 0.0,
    "max_voltage": 24.0,
    "pole_pairs": 7,
    "ripple_depth": 0.05,
    "holding_torque": 0.4,
    "step_angle_deg": 1.8,
    "rated_current": 1.5,
    "pullout_corner": 600.0,
    "breakdown_torque": 12.0,
    "breakdown_slip": 0.2,
    "magnetizing_current": 3.0,
    "rated_frequency": 60.0,
}


def validate_params(partial: Dict[str, Any]) -> Dict[str, float]:
    """Clamp-free validation: reject rather than silently alter bad input."""
    clean: Dict[str, float] = {}
    for key, value in partial.items():
        if key not in _PARAM_BOUNDS:
            raise ValueError(f"unknown motor parameter '{key}'")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"parameter '{key}' must be a finite number")
        lo, hi = _PARAM_BOUNDS[key]
        if not (lo <= value <= hi):
            raise ValueError(f"parameter '{key}'={value} outside [{lo}, {hi}]")
        clean[key] = int(value) if key == "pole_pairs" else float(value)
    return clean


class LiveMotor:
    def __init__(self, motor_type: str = "dc", params: Optional[Dict[str, float]] = None):
        self.motor_type = "dc"
        self.params: Dict[str, float] = dict(DEFAULT_PARAMS)
        self.extra_inertia = 0.0  # reflected inertia from the attached load
        self._motor = None
        self._on_fallback = False
        self.set_motor(motor_type, params or {})

    # ------------------------------------------------------------------ build

    def _build_params(self, backend) -> Any:
        if self.motor_type == "bldc":
            p = backend.BLDCParams()
            p.pole_pairs = int(self.params["pole_pairs"])
            p.ripple_depth = self.params["ripple_depth"]
        elif self.motor_type == "stepper":
            p = backend.StepperParams()
            p.holding_torque = self.params["holding_torque"]
            p.step_angle_deg = self.params["step_angle_deg"]
            p.rated_current = self.params["rated_current"]
            p.pullout_corner = self.params["pullout_corner"]
        elif self.motor_type == "induction":
            p = backend.InductionParams()
            p.pole_pairs = int(self.params["pole_pairs"])
            p.breakdown_torque = self.params["breakdown_torque"]
            p.breakdown_slip = self.params["breakdown_slip"]
            p.magnetizing_current = self.params["magnetizing_current"]
            p.rated_frequency = self.params["rated_frequency"]
        else:
            p = backend.MotorParams()
        p.resistance = self.params["resistance"]
        p.inductance = self.params["inductance"]
        p.torque_constant = self.params["torque_constant"]
        p.back_emf_constant = self.params["back_emf_constant"]
        p.inertia = self.params["inertia"] + self.extra_inertia
        p.viscous_friction = self.params["viscous_friction"]
        p.static_friction = self.params["static_friction"]
        p.max_voltage = self.params["max_voltage"]
        return p

    def _build_motor(self, backend) -> Any:
        p = self._build_params(backend)
        if self.motor_type == "bldc":
            return backend.BLDCMotor(p)
        if self.motor_type == "stepper":
            return backend.StepperMotor(p)
        if self.motor_type == "induction":
            return backend.InductionMotor(p)
        return backend.DCMotor(p)

    # ---------------------------------------------------------------- control

    def set_motor(self, motor_type: str, params: Dict[str, Any]) -> None:
        """Fresh motor of the given type: full reset, preferred backend."""
        motor_type = (motor_type or "dc").lower().strip()
        if motor_type not in MOTOR_TYPES:
            raise ValueError(f"unknown motor_type '{motor_type}'")
        clean = validate_params(params)
        self.motor_type = motor_type
        self.params.update(clean)
        # stepper/induction exist only in the Python layer (plan phase 9)
        self._on_fallback = (motor_type in _FALLBACK_ONLY_TYPES
                             or not engine_bridge.is_using_cpp_backend())
        self._motor = self._build_motor(
            fallback_engine if self._on_fallback else engine_bridge
        )

    def reset(self) -> None:
        """Back to standstill on the preferred backend, params kept."""
        self.set_motor(self.motor_type, {})

    def set_params(self, partial: Dict[str, Any]) -> None:
        """Apply a live parameter edit without losing the running state."""
        clean = validate_params(partial)
        for key, types in _TYPE_ONLY.items():
            if key in clean and self.motor_type not in types:
                raise ValueError(
                    f"'{key}' only applies to {' / '.join(types)} motors")
        if not clean:
            return
        self._ensure_mutable()
        self.params.update(clean)
        self._apply_params_in_place()

    def set_extra_inertia(self, extra: float) -> None:
        """Change reflected load inertia while running (wheel / flywheel).

        Engaging a coupling onto a spinning shaft conserves angular
        momentum: the shaft slows by J_old / J_new. That drop (and the
        recovery from it) is real physics and reads great on screen.
        """
        if not math.isfinite(extra) or extra < 0.0:
            raise ValueError("extra inertia must be a finite value >= 0")
        if abs(extra - self.extra_inertia) < 1e-15:
            return
        self._ensure_mutable()
        j_old = self.params["inertia"] + self.extra_inertia
        j_new = self.params["inertia"] + extra
        self.extra_inertia = extra
        self._apply_params_in_place()
        self._motor._omega *= j_old / j_new
        self._motor.state.omega = self._motor._omega
        self._motor.state.rpm = self._motor._omega * 60.0 / (2.0 * math.pi)

    # ----------------------------------------------------------------- moving

    def step(self, dt: float, voltage: float, load_torque: float):
        return self._motor.step(dt, voltage, load_torque)

    @property
    def state(self):
        s = self._motor.state
        return s() if callable(s) else s  # C++ exposes state(); fallback, .state

    @property
    def backend_name(self) -> str:
        return "python-fallback" if self._on_fallback else "cpp"

    @property
    def commutation_sector(self) -> int:
        if self.motor_type == "bldc":
            return self._motor.commutation_sector()
        return -1

    @property
    def slipping(self) -> bool:
        """Stepper step-loss / induction excessive-slip flag."""
        return bool(getattr(self._motor, "slipping", False))

    def set_step_rate(self, steps_per_s: float) -> None:
        if self.motor_type == "stepper":
            self._motor.set_step_rate(steps_per_s)

    def set_supply_frequency(self, hz: float) -> None:
        if self.motor_type == "induction":
            self._motor.set_supply_frequency(hz)

    def effective_inertia(self) -> float:
        return self.params["inertia"] + self.extra_inertia

    # ------------------------------------------------------------ backend swap

    def _ensure_mutable(self) -> None:
        """Make sure the live motor's params/state can be edited in place."""
        if self._on_fallback:
            return
        old = self.state
        motor = self._build_motor(fallback_engine)
        motor._i = old.current
        motor._omega = old.omega
        motor.state.time = old.time
        motor.state.voltage = old.voltage
        motor.state.current = old.current
        motor.state.omega = old.omega
        motor.state.rpm = old.rpm
        motor.state.torque = old.torque
        motor.state.load_torque = old.load_torque
        motor.state.electrical_angle_deg = old.electrical_angle_deg
        if self.motor_type == "bldc":
            elec = math.radians(old.electrical_angle_deg)
            motor._theta_m = elec / max(1, int(self.params["pole_pairs"]))
        self._motor = motor
        self._on_fallback = True

    def _apply_params_in_place(self) -> None:
        p = self._motor.params
        p.resistance = self.params["resistance"]
        p.inductance = self.params["inductance"]
        p.torque_constant = self.params["torque_constant"]
        p.back_emf_constant = self.params["back_emf_constant"]
        p.inertia = self.params["inertia"] + self.extra_inertia
        p.viscous_friction = self.params["viscous_friction"]
        p.static_friction = self.params["static_friction"]
        p.max_voltage = self.params["max_voltage"]
        if self.motor_type == "bldc":
            p.pole_pairs = int(self.params["pole_pairs"])
            p.ripple_depth = self.params["ripple_depth"]
        elif self.motor_type == "stepper":
            p.holding_torque = self.params["holding_torque"]
            p.step_angle_deg = self.params["step_angle_deg"]
            p.rated_current = self.params["rated_current"]
            p.pullout_corner = self.params["pullout_corner"]
        elif self.motor_type == "induction":
            p.pole_pairs = int(self.params["pole_pairs"])
            p.breakdown_torque = self.params["breakdown_torque"]
            p.breakdown_slip = self.params["breakdown_slip"]
            p.magnetizing_current = self.params["magnetizing_current"]
            p.rated_frequency = self.params["rated_frequency"]
