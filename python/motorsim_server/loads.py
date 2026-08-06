"""Mechanical load models for the interactive simulator.

Plan section 5.1 / 16: in v0.1 the load laws are computed in the Python
server layer, evaluated every engine sub-step from the current speed, and
fed to the existing engine API as the per-step scalar load torque. Later
phases can push these same laws down into C++ unchanged.

Every model maps angular velocity (rad/s) -> opposing torque (N*m). These
are all *loads*: they resist whatever motion exists and never drive a
stationary shaft. Models that physically add rotating inertia (wheel,
flywheel) also report an ``extra_inertia`` that the session adds to the
motor's rotor inertia.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

# Coulomb-type loads are discontinuous at standstill: a raw sign(omega)
# flips the torque every sub-step as the shaft crosses zero, buzzing it in
# a limit cycle around 0. Ramp the opposing torque linearly across a narrow
# speed band instead (standard regularized-Coulomb treatment), so it passes
# smoothly through zero and behaves as a strong damper at rest. The band is
# ~5 RPM -- far below anything visible, so faster motion is unaffected.
_STICTION_BAND = 0.5  # rad/s


def _oppose(omega: float, magnitude: float) -> float:
    """A torque of |magnitude| directed against the current rotation."""
    m = abs(magnitude)
    if omega > _STICTION_BAND:
        return m
    if omega < -_STICTION_BAND:
        return -m
    return m * (omega / _STICTION_BAND)


class LoadModel:
    """Base: no load. Subclasses override torque() and maybe extra_inertia()."""

    kind = "none"

    def torque(self, omega: float) -> float:
        return 0.0

    def extra_inertia(self) -> float:
        return 0.0

    def describe(self) -> Dict[str, float]:
        return {}


class ConstantLoad(LoadModel):
    """A brake: speed-independent torque magnitude, always opposing motion.

    Only the magnitude is meaningful -- a brake resists rotation whichever
    way the shaft turns, and applies nothing to a shaft at rest. (Modeling
    an overhauling/gravity load that *drives* the shaft would be a distinct
    load kind, not a negative brake.)
    """

    kind = "constant"

    def __init__(self, torque: float = 0.01):
        self.constant = abs(float(torque))

    def torque(self, omega: float) -> float:
        return _oppose(omega, self.constant)

    def describe(self):
        return {"torque": self.constant}


class ViscousLoad(LoadModel):
    """T = c * omega (linear in speed), e.g. fluid shear, eddy brake."""

    kind = "viscous"

    def __init__(self, coefficient: float = 1e-4):
        self.coefficient = abs(float(coefficient))

    def torque(self, omega: float) -> float:
        return self.coefficient * omega

    def describe(self):
        return {"coefficient": self.coefficient}


class FanLoad(LoadModel):
    """T = k * omega^2, opposing rotation - the classic aerodynamic load."""

    kind = "fan"

    def __init__(self, coefficient: float = 2e-7):
        self.coefficient = abs(float(coefficient))

    def torque(self, omega: float) -> float:
        return _oppose(omega, self.coefficient * omega * omega)

    def describe(self):
        return {"coefficient": self.coefficient}


class PumpLoad(LoadModel):
    """T = a + b * omega^2: static head plus quadratic flow losses.

    The static-head part only engages while rotating (a stationary pump
    doesn't apply torque to the shaft).
    """

    kind = "pump"

    def __init__(self, static_torque: float = 0.01, coefficient: float = 2e-7):
        self.static_torque = abs(float(static_torque))
        self.coefficient = abs(float(coefficient))

    def torque(self, omega: float) -> float:
        return _oppose(omega, self.static_torque + self.coefficient * omega * omega)

    def describe(self):
        return {"static_torque": self.static_torque, "coefficient": self.coefficient}


class WheelLoad(LoadModel):
    """A driven wheel/vehicle reflected through a gear ratio.

    Reflected to the motor shaft (gear ratio n = wheel revs per motor rev,
    n < 1 for reduction):
      inertia:            J_reflected = n^2 * (J_wheel + m * r^2)
      rolling resistance: T = n * (Crr * m * g * r)
      aero drag:          T = n * (0.5 * rho * Cd * A * v^2) * r,  v = n*omega*r
    """

    kind = "wheel"

    def __init__(
        self,
        mass: float = 20.0,          # vehicle mass carried by this wheel (kg)
        radius: float = 0.15,        # wheel radius (m)
        gear_ratio: float = 0.2,     # wheel revs per motor rev
        rolling_coeff: float = 0.015,
        drag_area: float = 0.4,      # Cd * A (m^2)
        wheel_inertia: float = 0.05, # bare wheel inertia (kg*m^2)
    ):
        self.mass = abs(float(mass))
        self.radius = abs(float(radius))
        self.gear_ratio = max(1e-4, abs(float(gear_ratio)))
        self.rolling_coeff = abs(float(rolling_coeff))
        self.drag_area = abs(float(drag_area))
        self.wheel_inertia = abs(float(wheel_inertia))

    def torque(self, omega: float) -> float:
        n, r = self.gear_ratio, self.radius
        rolling = self.rolling_coeff * self.mass * 9.81 * r
        v = n * omega * r  # vehicle speed, m/s
        drag = 0.5 * 1.225 * self.drag_area * v * v * r
        return _oppose(omega, n * (rolling + drag))

    def extra_inertia(self) -> float:
        n, r = self.gear_ratio, self.radius
        return n * n * (self.wheel_inertia + self.mass * r * r)

    def describe(self):
        return {
            "mass": self.mass, "radius": self.radius, "gear_ratio": self.gear_ratio,
            "rolling_coeff": self.rolling_coeff, "drag_area": self.drag_area,
            "wheel_inertia": self.wheel_inertia,
        }


class FlywheelLoad(LoadModel):
    """A large inertia disk with only tiny bearing drag.

    Dramatic slow spin-up and long coast-down. Inertia of a uniform disk:
    J = 1/2 * m * r^2.
    """

    kind = "flywheel"

    def __init__(self, mass: float = 2.0, radius: float = 0.08, bearing_drag: float = 1e-5):
        self.mass = abs(float(mass))
        self.radius = abs(float(radius))
        self.bearing_drag = abs(float(bearing_drag))

    def torque(self, omega: float) -> float:
        return self.bearing_drag * omega

    def extra_inertia(self) -> float:
        return 0.5 * self.mass * self.radius * self.radius

    def describe(self):
        return {"mass": self.mass, "radius": self.radius, "bearing_drag": self.bearing_drag}


_FACTORIES: Dict[str, Callable[..., LoadModel]] = {
    "none": LoadModel,
    "constant": ConstantLoad,
    "viscous": ViscousLoad,
    "fan": FanLoad,
    "pump": PumpLoad,
    "wheel": WheelLoad,
    "flywheel": FlywheelLoad,
}


def available_loads() -> List[str]:
    return list(_FACTORIES.keys())


def make_load(kind: str, params: Dict[str, float] | None = None) -> LoadModel:
    """Build a load model from a protocol message. Unknown keys are rejected."""
    kind = (kind or "none").lower().strip()
    factory = _FACTORIES.get(kind)
    if factory is None:
        raise ValueError(f"unknown load kind '{kind}' (expected one of {available_loads()})")
    params = dict(params or {})
    for key, value in params.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"load parameter '{key}' must be a finite number")
    try:
        return factory(**params)
    except TypeError as exc:
        raise ValueError(f"bad parameters for load '{kind}': {exc}") from None
