"""Tests for the v0.2 features: power/efficiency telemetry, PWM drive,
scenario scripting, and multi-bench recording.

Same approach as test_session.py: no real-time thread; commands drained
manually and _advance() called with known sim-time budgets.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.recording import Recorder
from motorsim_server.session import SimulationSession


def drain(session):
    while session._commands:
        session._commands.popleft()()


def run_scenario_steps(session):
    """Execute the scenario-runner logic the _run loop performs."""
    if session._scenario is None:
        return
    while (session._scenario is not None
           and session._scenario_idx < len(session._scenario)
           and session._scenario[session._scenario_idx][0] <= session._scenario_elapsed):
        _, _, closure = session._scenario[session._scenario_idx]
        session._scenario_idx += 1
        closure()
    if session._scenario is not None and session._scenario_idx >= len(session._scenario):
        session._scenario = None


def advance(session, sim_seconds):
    """Step the full budget (the real loop's per-tick deadline bail would
    otherwise cut a long test advance short), firing scenario steps as the
    _run loop would."""
    remaining = sim_seconds
    while remaining > 1e-9:
        stepped = session._advance(remaining)
        if stepped <= 0.0:
            break  # less than one sub-step left (or paused/faulted)
        remaining -= stepped
        if session._scenario is not None:
            session._scenario_elapsed += stepped
        run_scenario_steps(session)


# ----------------------------------------------------------------- power

def test_power_fields_are_consistent():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 0.02}})
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    advance(s, 0.5)   # settle to steady state
    frame = s._make_frame()
    assert frame["p_in"] > 0 and frame["p_out"] > 0
    assert frame["p_out"] < frame["p_in"]          # losses are real
    assert 0.0 < frame["efficiency"] <= 1.0
    assert frame["efficiency"] == pytest.approx(
        frame["p_out"] / frame["p_in"], abs=0.02)  # frame rounding slack


def test_power_zero_at_rest():
    s = SimulationSession(Recorder())
    advance(s, 0.1)
    frame = s._make_frame()
    assert frame["p_in"] == pytest.approx(0.0, abs=1e-6)
    assert frame["efficiency"] == 0.0


# ------------------------------------------------------------------- pwm

def test_pwm_mean_voltage_sets_speed():
    """At duty D the mean drive is D*Vbus, so steady no-load speed should
    land near D * Vbus / Ke (well within ripple + friction slack)."""
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_pwm", "enabled": True, "duty": 0.5,
                      "frequency": 500})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    for _ in range(10):
        advance(s, 0.1)
    ke = s.motor.params["back_emf_constant"]
    vbus = s.motor.params["max_voltage"]
    expected = 0.5 * vbus / ke
    assert s.motor.state.omega == pytest.approx(expected, rel=0.15)


def test_pwm_produces_current_ripple():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "set_pwm", "enabled": True, "duty": 0.5,
                      "frequency": 200})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    for _ in range(6):
        advance(s, 0.1)
        s._make_frame()
    advance(s, 0.05)
    frame = s._make_frame()
    # chopped drive -> peak clearly above RMS (a DC drive would have ~equal)
    assert frame["current_peak"] > frame["current_rms"] * 1.2


def test_pwm_validation():
    s = SimulationSession(Recorder())
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_pwm"})
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_pwm", "duty": float("nan")})
    s.handle_command({"type": "set_pwm", "frequency": 999999})  # clamped
    drain(s)
    assert s.pwm_freq == 2000.0


# -------------------------------------------------------------- scenario

def test_scenario_runs_steps_on_sim_time():
    s = SimulationSession(Recorder())
    s.handle_command({"type": "scenario", "action": "start", "name": "demo",
                      "steps": [
        {"t": 0.0, "do": {"type": "set_voltage", "value": 6.0}},
        {"t": 0.1, "do": {"type": "set_running", "on": True}},
        {"t": 0.3, "do": {"type": "set_voltage", "value": 12.0}},
    ]})
    drain(s)
    assert s._scenario is not None
    advance(s, 0.05)          # past t=0 only
    assert s.throttle_v == 6.0 and not s.running
    advance(s, 0.1)           # past t=0.1
    assert s.running
    advance(s, 0.2)           # past t=0.3 -> finishes
    assert s.throttle_v == 12.0
    assert s._scenario is None


def test_scenario_survives_reset_step():
    """A reset zeroes motor sim time; scenario timing must not be stranded."""
    s = SimulationSession(Recorder())
    advance(s, 0.2)           # motor clock now at 0.2 s
    s.handle_command({"type": "scenario", "action": "start", "steps": [
        {"t": 0.0, "do": {"type": "reset"}},
        {"t": 0.1, "do": {"type": "set_voltage", "value": 9.0}},
    ]})
    drain(s)
    advance(s, 0.05)          # fires reset (motor time back to 0)
    assert s.motor.state.time < 0.1
    advance(s, 0.1)           # elapsed accumulates past 0.1 regardless
    assert s.throttle_v == 9.0
    assert s._scenario is None


def test_scenario_validation():
    s = SimulationSession(Recorder())
    with pytest.raises(ValueError):
        s.handle_command({"type": "scenario", "action": "start", "steps": []})
    with pytest.raises(ValueError):   # bad nested command rejected up front
        s.handle_command({"type": "scenario", "action": "start", "steps": [
            {"t": 0, "do": {"type": "warp_core_breach"}}]})
    with pytest.raises(ValueError):   # no scenarios inside scenarios
        s.handle_command({"type": "scenario", "action": "start", "steps": [
            {"t": 0, "do": {"type": "scenario", "action": "stop"}}]})
    assert not s._commands


def test_builtin_scenarios_compile():
    """Every shipped scenario file must validate against the live compiler."""
    from motorsim_server.app import _load_scenarios
    scenarios = _load_scenarios()
    assert len(scenarios) >= 3
    for sc in scenarios:
        s = SimulationSession(Recorder())
        s.handle_command({"type": "scenario", "action": "start",
                          "name": sc["name"], "steps": sc["steps"]})
        drain(s)
        assert s._scenario is not None, sc["name"]


# ---------------------------------------------------------------- benches

def test_recorder_filters_frames_by_bench():
    rec = Recorder()
    a = SimulationSession(rec, "A")
    b = SimulationSession(rec, "B")
    rec.start("only-a", "A")
    rec.append(a._make_frame())
    rec.append(b._make_frame())
    rec.append(a._make_frame())
    rec.stop()
    frames = rec.get_frames("only-a")
    assert len(frames) == 2
    assert all(f["bench"] == "A" for f in frames)
    assert rec.list_runs()[0]["bench"] == "A"


def test_recording_for_reports_own_bench_only():
    rec = Recorder()
    a = SimulationSession(rec, "A")
    b = SimulationSession(rec, "B")
    a.handle_command({"type": "record", "action": "start", "name": "x"})
    drain(a)
    assert rec.recording_for("A") == "x"
    assert rec.recording_for("B") is None
    assert a._control_state()["recording"] == "x"
    assert b._control_state()["recording"] is None


def test_frames_and_state_carry_bench():
    s = SimulationSession(Recorder(), "B")
    assert s._make_frame()["bench"] == "B"
    assert s.full_state()["bench"] == "B"
