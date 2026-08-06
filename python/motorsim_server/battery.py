"""BatteryModel: a finite supply instead of the ideal infinite bus.

Same dataclass style as environment.py's Supply/ThermalModel. State of
charge is tracked by coulomb counting; the open-circuit voltage follows a
simple piecewise-linear discharge curve (as a fraction of nominal), and
the terminal voltage sags under load through the internal resistance:

    V_term = OCV(soc) - I * R_internal

Positive current discharges, negative current charges (regenerative
braking). Charging current is limited and stops at full charge; the
energy actually banked is integrated in watt-hours for display.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# OCV as a fraction of nominal voltage vs. state of charge - the familiar
# lithium-ish shape: steep knee at the bottom, plateau, rise at the top.
_OCV_CURVE = (
    (0.00, 0.78),
    (0.05, 0.88),
    (0.20, 0.95),
    (0.80, 1.02),
    (1.00, 1.08),
)


@dataclass
class BatteryModel:
    capacity_ah: float = 2.0
    internal_resistance: float = 0.05   # ohm
    nominal_voltage: float = 12.0
    regen_limit_a: float = 10.0         # max charging current accepted
    soc: float = field(default=1.0)     # 0..1
    energy_recovered_wh: float = field(default=0.0)

    def ocv(self) -> float:
        """Open-circuit voltage at the current state of charge."""
        s = min(1.0, max(0.0, self.soc))
        for (s0, f0), (s1, f1) in zip(_OCV_CURVE, _OCV_CURVE[1:]):
            if s <= s1:
                r = 0.0 if s1 == s0 else (s - s0) / (s1 - s0)
                return (f0 + r * (f1 - f0)) * self.nominal_voltage
        return _OCV_CURVE[-1][1] * self.nominal_voltage

    def terminal_voltage(self, current: float = 0.0) -> float:
        """Voltage at the terminals while sourcing `current` amps."""
        if self.soc <= 0.0:
            return 0.0   # empty pack: protection cutoff
        return max(0.0, self.ocv() - current * self.internal_resistance)

    def step(self, dt: float, current: float) -> None:
        """Advance by dt sim-seconds at the given battery current.

        Positive discharges. Charging (negative) is clamped to the regen
        limit and refused entirely at full charge.
        """
        if current < 0.0:
            if self.soc >= 1.0:
                return
            current = max(current, -self.regen_limit_a)
            self.energy_recovered_wh += -current * self.terminal_voltage() * dt / 3600.0
        self.soc -= current * dt / 3600.0 / max(1e-9, self.capacity_ah)
        self.soc = min(1.0, max(0.0, self.soc))

    def describe(self) -> dict:
        return {
            "soc": round(self.soc, 4),
            "voltage": round(self.terminal_voltage(), 2),
            "capacity_ah": self.capacity_ah,
            "internal_resistance": self.internal_resistance,
            "nominal_voltage": self.nominal_voltage,
            "energy_recovered_wh": round(self.energy_recovered_wh, 3),
        }
