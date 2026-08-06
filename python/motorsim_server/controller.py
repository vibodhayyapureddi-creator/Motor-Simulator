"""PIDController: closed-loop speed / torque / position control.

The controller runs at a realistic control rate (~1 kHz, enforced by the
session, not here) and produces a commanded drive voltage. Anti-windup is
a clamped integral (the integral term alone can never demand more than
the output ceiling), matching the reject-don't-misbehave ethos of
environment.py's CurrentLimiter.

Modes:
  speed    - setpoint in RPM (magnitude; direction stays a user input)
  torque   - setpoint in N*m (magnitude)
  position - setpoint in revolutions (signed; output drives either way)
"""
from __future__ import annotations

from dataclasses import dataclass, field

MODES = ("off", "speed", "torque", "position")


@dataclass
class PIDController:
    mode: str = "off"
    kp: float = 0.01
    ki: float = 0.0
    kd: float = 0.0
    setpoint: float = 0.0
    out_max: float = 24.0        # drive voltage ceiling
    _integral: float = field(default=0.0)
    _prev_err: float = field(default=0.0)
    _primed: bool = field(default=False)
    output: float = field(default=0.0)

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_err = 0.0
        self._primed = False
        self.output = 0.0

    def update(self, dt: float, measurement: float) -> float:
        err = self.setpoint - measurement
        if self.ki > 0.0:
            self._integral += err * dt
            lim = self.out_max / self.ki
            self._integral = max(-lim, min(lim, self._integral))
        # derivative on error; first sample has no history
        d = 0.0
        if self._primed and dt > 0.0:
            d = (err - self._prev_err) / dt
        self._prev_err = err
        self._primed = True

        out = self.kp * err + self.ki * self._integral + self.kd * d
        if self.mode == "position":
            # position error is signed and must drive both directions
            self.output = max(-self.out_max, min(self.out_max, out))
        else:
            self.output = max(0.0, min(self.out_max, out))
        return self.output

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "kp": self.kp, "ki": self.ki, "kd": self.kd,
            "setpoint": self.setpoint,
            "output": round(self.output, 3),
        }
