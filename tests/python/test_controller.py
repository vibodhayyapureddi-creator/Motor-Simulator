"""Tests for closed-loop PID control (plan phase 7)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.controller import PIDController
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


# ----------------------------------------------------------------- unit

def test_output_clamped_to_ceiling():
    c = PIDController(mode="speed", kp=1000.0, setpoint=1e6, out_max=24.0)
    assert c.update(0.001, 0.0) == 24.0
    c.setpoint = -1e6
    assert c.update(0.001, 0.0) == 0.0    # speed mode never drives negative


def test_position_mode_drives_both_directions():
    c = PIDController(mode="position", kp=100.0, setpoint=-10.0, out_max=24.0)
    assert c.update(0.001, 0.0) == -24.0


def test_integral_antiwindup_is_bounded():
    c = PIDController(mode="speed", kp=0.0, ki=2.0, setpoint=1000.0, out_max=24.0)
    for _ in range(100000):
        c.update(0.01, 0.0)               # error never closes
    assert c._integral == pytest.approx(24.0 / 2.0)   # clamped at out_max/ki
    assert c.output == 24.0
    # once the error reverses, the integral can unwind immediately
    c.setpoint = 0.0
    for _ in range(100):
        c.update(0.01, 1000.0)
    assert c.output < 24.0


def test_derivative_needs_history():
    c = PIDController(mode="speed", kp=0.0, kd=1.0, setpoint=10.0)
    first = c.update(0.001, 0.0)
    assert first == 0.0                    # no previous error yet
    second = c.update(0.001, 5.0)          # error fell 10 -> 5
    assert second == 0.0                   # negative derivative clamps to 0


# --------------------------------------------------------------- session

def test_speed_setpoint_converges():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_controller", "mode": "speed",
                      "kp": 0.02, "ki": 2.0, "kd": 0.0, "setpoint": 2000.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    for _ in range(20):
        advance(s, 0.1)
    assert s.motor.state.rpm == pytest.approx(2000.0, rel=0.03)
    frame = s._make_frame()
    assert frame["setpoint_rpm"] == 2000.0
    assert frame["ctl"]["controller"]["mode"] == "speed"


def test_integral_removes_steady_state_error_under_load():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 0.02}})
    s.handle_command({"type": "set_controller", "mode": "speed",
                      "kp": 0.02, "ki": 4.0, "setpoint": 1500.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    for _ in range(30):
        advance(s, 0.1)
    # with Ki, the loaded motor still hits the setpoint (P-only would sag)
    assert s.motor.state.rpm == pytest.approx(1500.0, rel=0.03)


def test_controller_off_returns_to_open_loop():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_controller", "mode": "speed",
                      "kp": 0.02, "ki": 2.0, "setpoint": 1000.0})
    drain(s)
    assert s.controller is not None
    s.handle_command({"type": "set_controller", "mode": "off"})
    drain(s)
    assert s.controller is None
    assert s._make_frame()["ctl"]["controller"] is None


def test_setpoint_change_emits_marker_event():
    s = SimulationSession(Recorder())
    events = []
    s.add_listener(events.append)
    s.handle_command({"type": "set_controller", "mode": "speed",
                      "kp": 0.02, "setpoint": 800.0})
    drain(s)
    kinds = [e.get("event") for e in events]
    assert "setpoint_changed" in kinds
    ev = next(e for e in events if e.get("event") == "setpoint_changed")
    assert ev["value"] == 800.0 and ev["bench"] == "A" and "t" in ev


def test_controller_validation():
    s = SimulationSession(Recorder())
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_controller", "mode": "bang-bang"})
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_controller", "mode": "speed",
                          "kp": float("inf")})


def test_fault_events_carry_time_and_bench():
    """Phase 2: fault commands emit chart-marker events."""
    s = SimulationSession(Recorder(), "B")
    events = []
    s.add_listener(events.append)
    s.handle_command({"type": "fault", "kind": "sag", "depth": 0.5,
                      "duration": 1.0})
    s.handle_command({"type": "fault", "kind": "jam", "on": True})
    s.handle_command({"type": "fault", "kind": "clear"})
    drain(s)
    marks = [e for e in events if e.get("event") == "fault_triggered"]
    assert [m["kind"] for m in marks] == ["sag", "jam", "clear"]
    assert all(m["bench"] == "B" and "t" in m for m in marks)
