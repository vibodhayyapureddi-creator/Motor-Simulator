"""Tests for the v0.4 infrastructure: two-zone thermal, snapshot round
trips, autosave, rooms, the batch runner, and the hardware-line parser."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.environment import ThermalModel
from motorsim_server.hardware_bridge import parse_line
from motorsim_server.recording import Recorder
from motorsim_server.session import SimulationSession


def drain(session):
    while session._commands:
        session._commands.popleft()()


# ------------------------------------------------------------ two-zone heat

def test_winding_runs_hotter_than_housing_under_load():
    th = ThermalModel(ambient_c=25.0, thermal_resistance=8.0,
                      thermal_capacitance=12.0)
    for _ in range(20000):          # 100 s at 5 ms
        th.step(5e-3, 2.0, 1.5)
    assert th.temperature_c > th.housing_c > 25.0


def test_two_zone_equilibrium_matches_series_resistance():
    th = ThermalModel(ambient_c=25.0, thermal_resistance=8.0,
                      thermal_capacitance=12.0)
    power = 2.0 * 2.0 * 1.5
    for _ in range(400_000):        # 2000 s: both nodes fully settled
        th.step(5e-3, 2.0, 1.5)
    assert th.temperature_c == pytest.approx(25.0 + power * 8.0, rel=1e-3)
    assert th.housing_c == pytest.approx(25.0 + power * 8.0 * 0.65, rel=1e-3)


def test_housing_temp_in_telemetry():
    s = SimulationSession(Recorder())
    frame = s._make_frame()
    assert "housing_temp" in frame
    assert frame["housing_temp"] == pytest.approx(frame["temperature"], abs=0.1)


# ------------------------------------------------------- snapshot round trip

def test_snapshot_round_trips_through_apply_preset():
    a = SimulationSession(Recorder())
    a.handle_command({"type": "set_motor", "motor_type": "bldc",
                      "params": {"resistance": 0.5, "pole_pairs": 9}})
    a.handle_command({"type": "set_load", "kind": "fan",
                      "params": {"coefficient": 3e-7}})
    a.handle_command({"type": "set_limits", "current_limit": 17.0})
    a.handle_command({"type": "set_battery", "enabled": True,
                      "capacity_ah": 1.2, "nominal_voltage": 11.1})
    a.handle_command({"type": "set_pwm", "enabled": True, "duty": 0.7,
                      "frequency": 300})
    a.handle_command({"type": "set_controller", "mode": "speed",
                      "kp": 0.02, "ki": 1.5, "setpoint": 900.0})
    a.handle_command({"type": "set_commutation", "mode": "foc"})
    drain(a)
    snap = a.snapshot()

    b = SimulationSession(Recorder())
    b.apply_preset(json.loads(json.dumps(snap)))   # via JSON like autosave
    drain(b)
    assert b.motor.motor_type == "bldc"
    assert b.motor.params["resistance"] == 0.5
    assert b.load.kind == "fan"
    assert b.limiter.limit_a == 17.0
    assert b.battery is not None and b.battery.capacity_ah == 1.2
    assert b.pwm_enabled and b.pwm_duty == pytest.approx(0.7)
    assert b.controller is not None and b.controller.setpoint == 900.0
    assert b.commutation == "foc"
    assert b.motor.params["ripple_depth"] == 0.0


# ------------------------------------------------------------------- rooms

def test_rooms_are_isolated_and_lazy(tmp_path):
    from motorsim_server.app import MotorSimServer
    from motorsim_server.presets_service import PresetService
    server = MotorSimServer(("127.0.0.1", 0), PresetService(), restore=False)
    try:
        main = server.get_room("main")
        alice = server.get_room("alice")
        assert main is not alice
        assert alice.recorder is not main.recorder
        alice.sessions["A"].handle_command({"type": "set_voltage", "value": 9})
        while alice.sessions["A"]._commands:
            alice.sessions["A"]._commands.popleft()()
        assert alice.sessions["A"].throttle_v == 9
        assert main.sessions["A"].throttle_v == 0
        # bad names sanitize to something safe
        assert server.get_room("../../etc").name == "etc"
        assert server.get_room("") .name == "main"
    finally:
        server.server_close()


def test_autosave_and_restore(tmp_path):
    from motorsim_server.app import MotorSimServer
    from motorsim_server.presets_service import PresetService
    server = MotorSimServer(("127.0.0.1", 0), PresetService(), restore=False)
    server.autosave_path = tmp_path / "autosave.json"
    try:
        s = server.get_room("main").sessions["A"]
        s.handle_command({"type": "set_voltage", "value": 7.5})
        s.handle_command({"type": "set_load", "kind": "flywheel",
                          "params": {"mass": 1.0}})
        while s._commands:
            s._commands.popleft()()
        server._autosave()
        assert server.autosave_path.exists()
    finally:
        server.server_close()

    server2 = MotorSimServer(("127.0.0.1", 0), PresetService(), restore=False)
    server2.autosave_path = tmp_path / "autosave.json"
    try:
        room = server2.get_room("main")
        server2._restore(room)
        s2 = room.sessions["A"]
        while s2._commands:
            s2._commands.popleft()()
        assert s2.throttle_v == 7.5
        assert s2.load.kind == "flywheel"
    finally:
        server2.server_close()


# ---------------------------------------------------------------- hardware

def test_parse_line_json_and_kv():
    assert parse_line('{"rpm": 1200, "current": 0.8}') == {
        "rpm": 1200.0, "current": 0.8}
    assert parse_line("rpm=950.5,voltage=11.7") == {
        "rpm": 950.5, "voltage": 11.7}
    assert parse_line('{"rpm": 100, "malware": 1}') == {"rpm": 100.0}
    assert parse_line("") is None
    assert parse_line("garbage") is None
    assert parse_line('{"rpm": "not a number"}') is None
    assert parse_line("[1,2,3]") is None


# ------------------------------------------------------------------- batch

def test_batch_headless_run_and_summary(tmp_path):
    from motorsim_server.batch import run_headless, summarize, write_csv
    preset = {"motor_type": "dc", "params": {"max_voltage": 12.0},
              "drive": {"voltage": 12.0}}
    scenario = {"name": "t", "steps": [
        {"t": 0.0, "do": {"type": "set_running", "on": True}},
        {"t": 0.5, "do": {"type": "set_load", "kind": "constant",
                          "params": {"torque": 0.02}}},
    ]}
    frames = run_headless(preset, scenario, duration=1.5)
    assert len(frames) == pytest.approx(1.5 / (1 / 60), abs=2)
    assert frames[-1]["rpm"] > 500                 # it ran
    assert frames[-1]["load_torque"] == pytest.approx(0.02, abs=1e-3)
    summary = summarize(frames)
    assert summary["rpm"]["max"] >= summary["rpm"]["final"] > 0
    out = tmp_path / "run.csv"
    write_csv(frames, out)
    header = out.read_text().splitlines()[0]
    assert header.startswith("t,voltage,current")
