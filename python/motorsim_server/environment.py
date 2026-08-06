"""The "environment" around the motor: heat, limits, faults.

Plan sections 5.2/5.3 + 16: in v0.1 these ride on top of the C++ ODE core
in the Python layer. The core electromechanical integration stays in the
engine; this module adds:

- ThermalModel: lumped winding temperature from I^2*R heating,
  C*dT/dt = I^2*R - (T - T_ambient)/R_th, integrated explicitly at the
  (fast) sub-step rate. Optionally feeds back into winding resistance via
  copper's temperature coefficient.
- CurrentLimiter: controller-style current cap. When the (filtered)
  current magnitude exceeds the limit, the applied voltage folds back
  proportionally, exactly how a real drive protects itself.
- Supply: bus voltage with a user-triggerable sag (drop to a fraction of
  nominal for a duration, with a ramped recovery).
- StallDetector: flags the load-exceeds-capability condition (shaft near
  standstill while significant voltage is applied and current is high).

All state advances in simulation time, not wall time, so slow-motion and
pause behave correctly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# Copper's resistance temperature coefficient, 1/K (around 20 C).
COPPER_ALPHA = 0.00393


@dataclass
class ThermalModel:
    """Two-node lumped thermal model: winding -> housing -> ambient.

    The winding is the small mass sitting right on the copper losses; the
    housing is the big mass that sheds heat to ambient. The two total
    parameters keep their original meaning (series resistance / summed
    capacitance), so presets and the steady state T_w = T_amb + P*R_total
    are unchanged - but transients now show the winding running hotter
    than the housing, which the 3D view renders as separate zone glows.
    """

    ambient_c: float = 25.0
    thermal_resistance: float = 8.0     # K/W  total winding -> ambient
    thermal_capacitance: float = 12.0   # J/K  total winding + housing mass
    overheat_c: float = 120.0           # warning threshold (winding)
    resistance_feedback: bool = False   # raise R with temperature (derating)
    temperature_c: float = field(default=25.0)   # winding node
    housing_c: float = field(default=25.0)       # housing node

    _SPLIT_R = 0.35   # share of R_total between winding and housing
    _SPLIT_C = 0.25   # share of C_total that is the winding mass

    def __post_init__(self):
        self.temperature_c = self.ambient_c
        self.housing_c = self.ambient_c

    def reset(self) -> None:
        self.temperature_c = self.ambient_c
        self.housing_c = self.ambient_c

    def step(self, dt: float, current: float, resistance: float) -> float:
        """Advance by dt seconds of sim time; returns the winding temp."""
        r_wh = self.thermal_resistance * self._SPLIT_R
        r_ha = self.thermal_resistance * (1.0 - self._SPLIT_R)
        c_w = self.thermal_capacitance * self._SPLIT_C
        c_h = self.thermal_capacitance * (1.0 - self._SPLIT_C)
        power = current * current * resistance
        q_wh = (self.temperature_c - self.housing_c) / r_wh
        q_ha = (self.housing_c - self.ambient_c) / r_ha
        self.temperature_c += dt * (power - q_wh) / c_w
        self.housing_c += dt * (q_wh - q_ha) / c_h
        return self.temperature_c

    def hot_resistance(self, cold_resistance: float) -> float:
        """Winding resistance at the current temperature (if feedback is on)."""
        if not self.resistance_feedback:
            return cold_resistance
        return cold_resistance * (1.0 + COPPER_ALPHA * (self.temperature_c - 20.0))

    @property
    def overheated(self) -> bool:
        return self.temperature_c >= self.overheat_c


@dataclass
class CurrentLimiter:
    """Proportional voltage fold-back above a current ceiling.

    Real controllers cap current by reducing the effective drive voltage.
    Each sub-step the commanded voltage is reduced by gain * overshoot,
    never crossing zero (the limiter throttles, it doesn't reverse).
    """

    limit_a: float = 30.0      # current ceiling (A); <= 0 disables
    gain: float = 2.0          # volts removed per amp of overshoot
    enabled: bool = True
    active: bool = field(default=False)

    def apply(self, voltage_cmd: float, current: float) -> float:
        self.active = False
        if not self.enabled or self.limit_a <= 0.0:
            return voltage_cmd
        overshoot = abs(current) - self.limit_a
        if overshoot <= 0.0:
            return voltage_cmd
        self.active = True
        correction = self.gain * overshoot
        if voltage_cmd >= 0.0:
            return max(0.0, voltage_cmd - correction)
        return min(0.0, voltage_cmd + correction)


@dataclass
class Supply:
    """Bus voltage source with a triggerable sag profile."""

    nominal_fraction: float = 1.0     # steady-state output as fraction of command
    _sag_depth: float = field(default=0.0)     # fraction removed while sagging
    _sag_remaining: float = field(default=0.0) # sim-seconds of sag left
    _recovery: float = field(default=0.15)     # sim-seconds to ramp back up
    _recovering: float = field(default=0.0)

    def trigger_sag(self, depth: float = 0.5, duration: float = 1.0) -> None:
        self._sag_depth = min(1.0, max(0.0, depth))
        self._sag_remaining = max(0.0, duration)
        self._recovering = 0.0

    def clear(self) -> None:
        self._sag_depth = 0.0
        self._sag_remaining = 0.0
        self._recovering = 0.0

    def step(self, dt: float) -> None:
        if self._sag_remaining > 0.0:
            self._sag_remaining -= dt
            if self._sag_remaining <= 0.0:
                self._recovering = self._recovery
        elif self._recovering > 0.0:
            self._recovering -= dt
            if self._recovering <= 0.0:
                self._sag_depth = 0.0

    @property
    def sagging(self) -> bool:
        return self._sag_remaining > 0.0 or self._recovering > 0.0

    def factor(self) -> float:
        """Multiplier on the commanded voltage right now."""
        if self._sag_remaining > 0.0:
            return self.nominal_fraction * (1.0 - self._sag_depth)
        if self._recovering > 0.0 and self._recovery > 0.0:
            # linear ramp from sagged back to nominal
            progress = 1.0 - (self._recovering / self._recovery)
            return self.nominal_fraction * (1.0 - self._sag_depth * (1.0 - progress))
        return self.nominal_fraction


@dataclass
class StallDetector:
    """Stall = commanded hard, barely turning, pulling heavy current.

    The condition must hold continuously for `hold_time` sim-seconds so the
    normal start-up instant (also zero speed + high current) doesn't flag.
    """

    omega_threshold: float = 2.0      # rad/s counted as "not turning"
    voltage_threshold: float = 1.0    # V of drive that should produce motion
    current_factor: float = 0.6       # fraction of V/R locked-rotor current
    hold_time: float = 0.25           # s of continuous condition before flagging
    _held: float = field(default=0.0)
    stalled: bool = field(default=False)

    def reset(self) -> None:
        self._held = 0.0
        self.stalled = False

    def step(self, dt: float, omega: float, voltage: float, current: float,
             resistance: float) -> bool:
        locked_rotor = abs(voltage) / max(resistance, 1e-9)
        condition = (
            abs(omega) < self.omega_threshold
            and abs(voltage) > self.voltage_threshold
            and abs(current) > self.current_factor * locked_rotor
        )
        if condition:
            self._held += dt
        else:
            self._held = 0.0
        self.stalled = self._held >= self.hold_time
        return self.stalled


def finite(*values: float) -> bool:
    """Numerical blow-up guard: all values sane?"""
    return all(math.isfinite(v) for v in values)
