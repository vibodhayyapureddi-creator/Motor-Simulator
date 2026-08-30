"""Tests for SimulationSession command handling, the recorder, and the
LiveMotor hot-swap.

The session's real-time thread is never started here; commands are pushed
through handle_command() and drained manually, and _advance() is called
directly with known sim-time budgets, so the tests are deterministic.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.livemotor import LiveMotor, validate_params
from motorsim_server.recording import Recorder
from motorsim_server.session import SimulationSession


def drain(session):
    while session._commands:
        session._commands.popleft()()


def make_session():
    return SimulationSession(Recorder())


# ------------------------------------------------------------- validation

def test_unknown_command_rejected():
    s = make_session()
    with pytest.raises(ValueError):
        s.handle_command({"type": "self_destruct"})


def test_bad_values_rejected_before_enqueue():
    s = make_session()
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_voltage", "value": float("nan")})
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_params", "params": {"resistance": -5}})
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_load", "kind": "warp_drive"})
    with pytest.raises(ValueError):
        s.handle_command({"type": "fault", "kind": "gremlins"})
    assert not s._commands  # nothing slipped through


def test_validate_params_bounds():
    assert validate_params({"resistance": 2.0}) == {"resistance": 2.0}
    assert validate_params({"pole_pairs": 7.0}) == {"pole_pairs": 7}  # int-ified
    with pytest.raises(ValueError):
        validate_params({"resistance": 1e9})
    with pytest.raises(ValueError):
        validate_params({"made_up": 1.0})


# ---------------------------------------------------------------- commands

def test_drive_commands_apply_at_tick_boundary():
    s = make_session()
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    s.handle_command({"type": "set_direction", "value": -1})
    assert s.throttle_v == 0.0 and not s.running   # not yet applied
    drain(s)
    assert s.throttle_v == 12.0 and s.running and s.direction == -1


def test_time_commands():
    s = make_session()
    s.handle_command({"type": "time", "action": "pause"})
    s.handle_command({"type": "time", "scale": 0.1})
    drain(s)
    assert s.paused and s.time_scale == 0.1
    s.handle_command({"type": "time", "action": "step", "step_s": 0.002})
    drain(s)
    assert s._pending_step_s == pytest.approx(0.002)


def test_motor_spins_up_and_telemetry_frame_shape():
    s = make_session()
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    s._advance(0.2)
    frame = s._make_frame()
    assert frame["type"] == "telemetry"
    assert frame["rpm"] > 100
    for key in ("t", "current", "current_peak", "torque", "voltage",
                "temperature", "load_torque", "flags", "ctl"):
        assert key in frame
    assert set(frame["flags"]) == {"overcurrent", "overheat", "stall",
                                   "sag", "numeric", "regen"}


def test_idle_motor_with_brake_load_stays_at_rest():
    """A stopped motor must not spin itself up. Regression: a constant load
    applied its torque in a fixed direction regardless of speed, so at rest
    it drove the shaft backwards until the coast voltage (Ke*omega) hit the
    max_voltage clamp -- parking an idle motor near full no-load speed."""
    s = make_session()
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 0.004}})
    s.handle_command({"type": "set_voltage", "value": 12.0})
    drain(s)                      # note: never set_running -- the motor is idle
    for _ in range(10):
        s._advance(0.2)
    assert abs(s._make_frame()["rpm"]) < 1.0


def test_coasting_motor_decelerates_to_rest_under_brake_load():
    """Spun up, then stopped: the brake must bring it monotonically down to
    rest and hold it there -- never reversing it through zero and running
    away backwards, which is what the fixed-direction load used to do."""
    s = make_session()
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 0.004}})
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    s._advance(0.2)
    rpm = s._make_frame()["rpm"]
    assert rpm > 100                              # spun up forward

    s.handle_command({"type": "set_running", "on": False})
    drain(s)
    # _advance() caps itself on wall-clock per call, so step until settled
    # rather than assuming a fixed call count covers the coast-down.
    for _ in range(200):
        s._advance(0.2)
        prev, rpm = rpm, s._make_frame()["rpm"]
        assert rpm <= prev + 1e-6                 # only ever slowing down
        assert rpm > -1.0                         # and never driven backwards
        if abs(rpm) < 1.0:
            break
    assert abs(rpm) < 1.0                         # reached rest and stayed


def test_stall_flag_emerges_under_rotor_jam():
    s = make_session()
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    s.handle_command({"type": "fault", "kind": "jam", "on": True})
    drain(s)
    s._advance(0.2)   # > twice the detector's 0.25 s hold time in chunks
    s._advance(0.2)
    frame = s._make_frame()
    assert frame["flags"]["stall"]
    assert abs(frame["rpm"]) < 60  # jam holds it near standstill
    s.handle_command({"type": "fault", "kind": "clear"})
    drain(s)
    assert not s.jammed


def test_load_command_swaps_load_and_inertia():
    s = make_session()
    s.handle_command({"type": "set_load", "kind": "flywheel",
                      "params": {"mass": 2.0, "radius": 0.1}})
    drain(s)
    assert s.load.kind == "flywheel"
    assert s.motor.extra_inertia == pytest.approx(0.5 * 2.0 * 0.01)


def test_live_param_edit_preserves_running_state():
    s = make_session()
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    s._advance(0.2)
    omega_before = s.motor.state.omega
    assert omega_before > 0
    s.handle_command({"type": "set_params", "params": {"resistance": 2.0}})
    drain(s)
    assert s.motor.params["resistance"] == 2.0
    assert s.motor.state.omega == pytest.approx(omega_before)  # no reset
    assert s.motor.backend_name == "python-fallback"  # hot-swapped mutable


def test_engaging_inertia_conserves_angular_momentum():
    motor = LiveMotor("dc", {})
    for _ in range(2000):
        motor.step(1e-4, 12.0, 0.0)
    omega_before = motor.state.omega
    j_before = motor.effective_inertia()
    motor.set_extra_inertia(0.01)
    expected = omega_before * j_before / (j_before + 0.01)
    assert motor.state.omega == pytest.approx(expected, rel=1e-9)


def test_numeric_blowup_autopauses():
    s = make_session()
    # an absurd-but-in-bounds parameter set that diverges: huge R with tiny L
    # is fine, so instead force divergence via direct state injection
    s.handle_command({"type": "set_voltage", "value": 12.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    s.motor._ensure_mutable()
    s.motor._motor._i = float("nan")
    s._advance(0.01)
    assert s.numeric_fault and s.paused
    frame = s._make_frame()
    assert frame["flags"]["numeric"]


# ---------------------------------------------------------------- recorder

def test_recorder_round_trip_and_csv():
    rec = Recorder()
    name = rec.start("Test Run")
    assert rec.recording == name
    for i in range(5):
        rec.append({"t": i * 0.016, "voltage": 12, "current": 1.5, "rpm": 100 * i,
                    "omega": 10 * i, "torque": 0.05, "load_torque": 0.01,
                    "current_peak": 2.0, "elec_angle": 0, "sector": -1,
                    "temperature": 30.0,
                    "flags": {"overcurrent": False, "overheat": False,
                              "stall": False, "sag": False}})
    rec.stop()
    assert rec.recording is None
    runs = rec.list_runs()
    assert len(runs) == 1 and runs[0]["frames"] == 5 and runs[0]["complete"]
    csv_bytes = rec.export_csv(name)
    lines = csv_bytes.decode().strip().splitlines()
    assert len(lines) == 6                      # header + 5 rows
    assert lines[0].startswith("t,voltage,current")
    assert rec.get_frames(name)[2]["rpm"] == 200
    assert rec.delete(name) and rec.export_csv(name) is None


def test_recorder_names_deduplicate():
    rec = Recorder()
    a = rec.start("run")
    rec.stop()
    b = rec.start("run")
    rec.stop()
    assert a == "run" and b == "run-2"


# ------------------------------------------------------- idle CPU behaviour

def test_unwatched_bench_does_not_step():
    """An unwatched bench must not burn CPU simulating into the void.

    The loop is greedy by design (it chases wall-clock time), so two idle
    benches can saturate a small container and starve the web server that
    serves the page. Regression: a deployed instance spent all its CPU
    stepping two benches nobody was connected to.
    """
    s = make_session()
    assert not s._watched()          # no listeners, not recording


def test_bench_is_watched_when_a_client_listens():
    s = make_session()
    sink = lambda _msg: None
    s.add_listener(sink)
    assert s._watched()
    s.remove_listener(sink)
    assert not s._watched()


def test_recording_keeps_an_unwatched_bench_alive():
    """A run recorded from a script must not stop when the tab closes."""
    rec = Recorder()
    s = SimulationSession(rec)
    assert not s._watched()
    rec.start("headless")
    assert s._watched()
    rec.stop()
    assert not s._watched()


def test_cpu_budget_is_bounded():
    """The knob must stay in a sane range whatever the environment says."""
    from motorsim_server.session import CPU_BUDGET
    assert 0.02 <= CPU_BUDGET <= 0.9
