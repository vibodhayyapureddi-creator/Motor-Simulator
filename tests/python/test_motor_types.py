"""Tests for the phase-9 motor types: stepper, induction, and the
idealized FOC commutation mode for the BLDC."""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_app.fallback_engine import (
    InductionMotor, InductionParams, StepperMotor, StepperParams,
)
from motorsim_server.recording import Recorder
from motorsim_server.session import SimulationSession


def drain(session):
    while session._commands:
        session._commands.popleft()()


def advance(session, sim_seconds):
    remaining = sim_seconds
    while remaining > 1e-9:
        stepped = session._advance(remaining)
        if stepped <= 0.0:
            break
        remaining -= stepped


# ---------------------------------------------------------------- stepper

def test_stepper_holds_position_when_energized():
    m = StepperMotor(StepperParams())
    m.set_step_rate(0.0)
    for _ in range(5000):
        m.step(2e-4, 12.0, 0.01)     # light load against holding torque
    assert abs(m.state.omega) < 1.0
    assert abs(m._theta) < math.radians(1.8)   # held within one step
    assert not m.slipping


def test_stepper_follows_step_rate():
    m = StepperMotor(StepperParams())
    rate = 400.0                      # steps/s -> 400*1.8deg/s = 2 rev/s
    m.set_step_rate(rate)
    for _ in range(10000):            # 2 s
        m.step(2e-4, 12.0, 0.0)
    expected_omega = rate * math.radians(1.8)
    assert m.state.omega == pytest.approx(expected_omega, rel=0.05)
    assert not m.slipping


def test_stepper_loses_steps_when_overloaded():
    m = StepperMotor(StepperParams(holding_torque=0.4))
    m.set_step_rate(200.0)
    slipped = False
    for _ in range(10000):
        m.step(2e-4, 12.0, 1.0)       # load far beyond holding torque
        slipped = slipped or m.slipping
    assert slipped
    # the rotor cannot keep up with the commanded angle
    assert m.state.omega < 200.0 * math.radians(1.8) * 0.5


def test_stepper_freewheels_deenergized():
    m = StepperMotor(StepperParams())
    m._omega = 10.0
    m.step(2e-4, 0.0, 0.0)            # no drive voltage
    assert m.state.current == 0.0 and m.state.torque == 0.0


# -------------------------------------------------------------- induction

def test_induction_no_torque_at_synchronous_speed():
    p = InductionParams()
    m = InductionMotor(p)
    m.set_supply_frequency(60.0)
    omega_s = 2 * math.pi * 60.0 / p.pole_pairs
    assert m._torque(omega_s, 230.0) == pytest.approx(0.0, abs=1e-9)


def test_induction_torque_peaks_at_breakdown_slip():
    p = InductionParams(breakdown_torque=12.0, breakdown_slip=0.2)
    m = InductionMotor(p)
    m.set_supply_frequency(60.0)
    omega_s = 2 * math.pi * 60.0 / p.pole_pairs
    tau_at = lambda s: m._torque(omega_s * (1 - s), p.max_voltage)
    assert tau_at(0.2) == pytest.approx(12.0, rel=1e-6)   # Kloss peak
    assert tau_at(0.05) < 12.0 and tau_at(0.6) < 12.0
    assert tau_at(-0.2) == pytest.approx(-12.0, rel=1e-6)  # generating


def test_induction_settles_just_below_synchronous():
    m = InductionMotor(InductionParams())
    m.set_supply_frequency(60.0)
    for _ in range(30000):            # 6 s at fallback substep
        m.step(2e-4, 230.0, 1.0)      # light load
    omega_s = 2 * math.pi * 60.0 / m.params.pole_pairs
    assert 0.9 * omega_s < m.state.omega < omega_s
    assert not m.slipping


def test_induction_session_drive():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_motor", "motor_type": "induction",
                      "params": {"max_voltage": 230.0, "breakdown_torque": 12.0,
                                 "pole_pairs": 2}})
    s.handle_command({"type": "set_supply_frequency", "hz": 60.0})
    s.handle_command({"type": "set_voltage", "value": 230.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    advance(s, 3.0)
    frame = s._make_frame()
    assert frame["rpm"] > 1500                    # near 1800 sync for p=2
    assert frame["ctl"]["backend"] == "python-fallback"
    assert frame["ctl"]["supply_hz"] == 60.0


def test_stepper_session_drive_and_stall_flag():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_motor", "motor_type": "stepper", "params": {}})
    s.handle_command({"type": "set_step_rate", "rate": 300.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    advance(s, 1.0)
    frame = s._make_frame()
    expected_rpm = 300.0 * 1.8 / 360.0 * 60.0     # 90 RPM
    assert frame["rpm"] == pytest.approx(expected_rpm, rel=0.1)
    assert not frame["flags"]["stall"]
    # now jam it with an impossible load: step-loss must flag as stall
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 2.0}})
    drain(s)
    advance(s, 1.0)
    assert s._make_frame()["flags"]["stall"]


# -------------------------------------------------------------------- foc

def test_foc_removes_commutation_ripple():
    def ripple_ratio(commutation):
        s = SimulationSession(Recorder())
        s.handle_command({"type": "set_motor", "motor_type": "bldc",
                          "params": {"ripple_depth": 0.2}})
        drain(s)
        if commutation == "foc":
            s.handle_command({"type": "set_commutation", "mode": "foc"})
        s.handle_command({"type": "set_voltage", "value": 12.0})
        s.handle_command({"type": "set_load", "kind": "constant",
                          "params": {"torque": 0.01}})
        s.handle_command({"type": "set_running", "on": True})
        drain(s)
        advance(s, 0.8)               # settle
        s._make_frame()               # clear frame stats
        advance(s, 0.2)
        frame = s._make_frame()
        return frame["current_peak"] / max(1e-9, frame["current_rms"])

    assert ripple_ratio("six_step") > ripple_ratio("foc") + 0.02
    # FOC telemetry: sector view is off
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_motor", "motor_type": "bldc", "params": {}})
    drain(s)
    s.handle_command({"type": "set_commutation", "mode": "foc"})
    drain(s)
    assert s._make_frame()["sector"] == -1
    assert s._make_frame()["ctl"]["commutation"] == "foc"
    # switching back restores the stashed ripple depth
    s.handle_command({"type": "set_commutation", "mode": "six_step"})
    drain(s)
    assert s.motor.params["ripple_depth"] > 0


def test_type_only_params_rejected_on_wrong_motor():
    s = SimulationSession(Recorder())
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_params",
                          "params": {"holding_torque": 0.5}})   # dc motor
    s.handle_command({"type": "set_motor", "motor_type": "stepper", "params": {}})
    drain(s)
    s.handle_command({"type": "set_params", "params": {"holding_torque": 0.5}})
    drain(s)
    assert s.motor.params["holding_torque"] == 0.5