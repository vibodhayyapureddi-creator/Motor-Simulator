"""Unit tests for the thermal / limit / fault environment (plan 5.2-5.3).

Pins down the behaviors the plan calls out explicitly: thermal equilibrium
at the analytic steady-state temperature, current-cap fold-back, stall
detection (including that a normal start-up does NOT flag), and the supply
sag profile with ramped recovery.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.environment import (
    COPPER_ALPHA, CurrentLimiter, StallDetector, Supply, ThermalModel, finite,
)


# ----------------------------------------------------------------- thermal

def test_thermal_equilibrium_matches_analytic_steady_state():
    th = ThermalModel(ambient_c=25.0, thermal_resistance=8.0,
                      thermal_capacitance=12.0)
    current, resistance = 2.0, 1.5
    # T_ss = T_amb + I^2 R * R_th
    expected = 25.0 + current**2 * resistance * 8.0
    for _ in range(200_000):  # 1000 s at 5 ms: >> the 96 s time constant
        th.step(5e-3, current, resistance)
    assert th.temperature_c == pytest.approx(expected, rel=1e-3)


def test_thermal_cools_back_to_ambient():
    th = ThermalModel(ambient_c=25.0)
    th.temperature_c = 90.0
    for _ in range(200_000):
        th.step(5e-3, 0.0, 1.0)
    assert th.temperature_c == pytest.approx(25.0, abs=0.1)


def test_overheat_flag_and_resistance_feedback():
    th = ThermalModel(overheat_c=100.0, resistance_feedback=True)
    th.temperature_c = 120.0
    assert th.overheated
    hot = th.hot_resistance(1.0)
    assert hot == pytest.approx(1.0 * (1.0 + COPPER_ALPHA * 100.0))
    th.resistance_feedback = False
    assert th.hot_resistance(1.0) == 1.0


# ----------------------------------------------------------------- limiter

def test_current_limiter_folds_back_proportionally():
    lim = CurrentLimiter(limit_a=10.0, gain=2.0)
    assert lim.apply(12.0, 5.0) == 12.0 and not lim.active
    # 5 A overshoot at gain 2 -> 10 V removed
    assert lim.apply(12.0, 15.0) == pytest.approx(2.0) and lim.active
    # fold-back never crosses zero (throttles, doesn't reverse)
    assert lim.apply(4.0, 25.0) == 0.0
    # symmetric for negative drive
    assert lim.apply(-12.0, -15.0) == pytest.approx(-2.0)


def test_current_limiter_disable_paths():
    lim = CurrentLimiter(limit_a=10.0, enabled=False)
    assert lim.apply(12.0, 50.0) == 12.0 and not lim.active
    lim = CurrentLimiter(limit_a=0.0, enabled=True)  # <= 0 disables
    assert lim.apply(12.0, 50.0) == 12.0 and not lim.active


# ------------------------------------------------------------------- stall

def test_stall_flags_only_after_hold_time():
    det = StallDetector(hold_time=0.25)
    # locked rotor: V=12, R=1 -> locked-rotor current 12 A; draw 10 A at rest
    for _ in range(24):  # 0.24 s: just under the hold time
        det.step(0.01, omega=0.1, voltage=12.0, current=10.0, resistance=1.0)
    assert not det.stalled
    det.step(0.01, omega=0.1, voltage=12.0, current=10.0, resistance=1.0)
    assert det.stalled
    det.reset()
    assert not det.stalled


def test_normal_startup_does_not_flag_stall():
    det = StallDetector(hold_time=0.25)
    # inrush lasts a few ms, then the shaft moves: condition never holds long
    for i in range(100):
        omega = 0.05 * i          # accelerating away from standstill
        current = 10.0 if i < 5 else 2.0
        det.step(0.01, omega, 12.0, current, 1.0)
        assert not det.stalled


# ------------------------------------------------------------------ supply

def test_supply_sag_and_ramped_recovery():
    sup = Supply()
    assert sup.factor() == 1.0 and not sup.sagging
    sup.trigger_sag(depth=0.4, duration=0.35)
    sup.step(0.1)
    assert sup.factor() == pytest.approx(0.6) and sup.sagging
    # run out the sag (0.35 s isn't a multiple of 0.1 -> no float edge);
    # recovery then ramps the factor back toward 1
    for _ in range(3):
        sup.step(0.1)           # sag depletes on the last step -> recovery arms
    sup.step(0.05)              # partway through the 0.15 s recovery ramp
    assert sup.sagging          # recovering
    mid = sup.factor()
    assert 0.6 < mid < 1.0
    for _ in range(10):
        sup.step(0.1)
    assert not sup.sagging and sup.factor() == 1.0
    # clear() kills an active sag immediately
    sup.trigger_sag(0.8, 10.0)
    sup.clear()
    assert sup.factor() == 1.0 and not sup.sagging


def test_finite_guard():
    assert finite(1.0, -2.5, 0.0)
    assert not finite(float("nan"))
    assert not finite(1.0, float("inf"))
