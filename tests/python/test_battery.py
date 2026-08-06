"""Tests for the battery model and regenerative braking (plan phase 5)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.battery import BatteryModel
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


# ------------------------------------------------------------- model unit

def test_coulomb_counting():
    b = BatteryModel(capacity_ah=1.0, soc=1.0)
    for _ in range(3600):          # 1 A for one hour empties a 1 Ah pack
        b.step(1.0, 1.0)
    assert b.soc == pytest.approx(0.0, abs=1e-9)
    b.step(1.0, 1.0)               # clamps at empty
    assert b.soc == 0.0
    assert b.terminal_voltage() == 0.0   # protection cutoff


def test_terminal_voltage_sags_with_internal_resistance():
    b = BatteryModel(capacity_ah=2.0, internal_resistance=0.1,
                     nominal_voltage=12.0, soc=1.0)
    open_circuit = b.terminal_voltage(0.0)
    assert open_circuit == pytest.approx(12.0 * 1.08)
    assert b.terminal_voltage(10.0) == pytest.approx(open_circuit - 1.0)


def test_ocv_falls_with_soc():
    b = BatteryModel(nominal_voltage=10.0)
    b.soc = 1.0
    full = b.ocv()
    b.soc = 0.5
    mid = b.ocv()
    b.soc = 0.02
    low = b.ocv()
    assert full > mid > low


def test_charging_respects_regen_limit_and_full_charge():
    b = BatteryModel(capacity_ah=1.0, soc=0.5, regen_limit_a=5.0)
    b.step(3600.0, -20.0)   # asks for 20 A charge; only 5 A accepted
    assert b.soc == pytest.approx(1.0)   # 5 Ah offered into 0.5 Ah headroom
    assert b.energy_recovered_wh > 0
    e = b.energy_recovered_wh
    b.step(10.0, -5.0)       # already full: refuses charge
    assert b.soc == 1.0 and b.energy_recovered_wh == e


# ---------------------------------------------------------- session level

def make_session():
    return SimulationSession(Recorder())


def test_battery_command_and_telemetry():
    s = make_session()
    s.handle_command({"type": "set_battery", "enabled": True,
                      "capacity_ah": 0.5, "internal_resistance": 0.2,
                      "nominal_voltage": 12.0})
    drain(s)
    assert s.battery is not None
    frame = s._make_frame()
    assert frame["battery"]["soc"] == 1.0
    assert frame["ctl"]["battery_enabled"] is True
    s.handle_command({"type": "set_battery", "enabled": False})
    drain(s)
    assert s.battery is None
    assert s._make_frame()["battery"] is None


def test_battery_drains_under_load():
    s = make_session()
    s.handle_command({"type": "set_battery", "enabled": True,
                      "capacity_ah": 0.05, "nominal_voltage": 24.0})
    s.handle_command({"type": "set_voltage", "value": 24.0})
    s.handle_command({"type": "set_load", "kind": "constant",
                      "params": {"torque": 0.05}})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    advance(s, 2.0)
    assert s.battery.soc < 1.0


def test_regen_braking_charges_only_above_bus_voltage():
    s = make_session()
    # spin up on the ideal bus first
    s.handle_command({"type": "set_voltage", "value": 24.0})
    s.handle_command({"type": "set_running", "on": True})
    drain(s)
    advance(s, 1.0)
    omega0 = s.motor.state.omega
    ke = s.motor.params["back_emf_constant"]
    assert ke * omega0 > 8.0   # plenty of back-EMF

    # install a half-charged low-voltage pack, then regen-brake into it
    s.handle_command({"type": "set_battery", "enabled": True,
                      "capacity_ah": 0.2, "nominal_voltage": 6.0, "soc": 0.5,
                      "regen_limit": 50.0})
    s.handle_command({"type": "set_running", "on": False})
    s.handle_command({"type": "set_brake", "on": True, "mode": "regen"})
    drain(s)
    advance(s, 0.3)
    assert s.battery.soc > 0.5
    assert s.battery.energy_recovered_wh > 0
    assert s.motor.state.omega < omega0          # it IS braking
    assert s._make_frame()["ctl"]["brake_mode"] == "regen"


def test_regen_falls_back_to_short_brake_below_bus():
    s = make_session()
    s.handle_command({"type": "set_battery", "enabled": True,
                      "capacity_ah": 1.0, "nominal_voltage": 48.0, "soc": 0.5})
    s.handle_command({"type": "set_brake", "on": True, "mode": "regen"})
    drain(s)
    advance(s, 0.2)   # motor at standstill: Ke*omega = 0 < bus
    assert s.battery.soc == pytest.approx(0.5)
    assert s.battery.energy_recovered_wh == 0.0


def test_brake_mode_validation_and_default():
    s = make_session()
    with pytest.raises(ValueError):
        s.handle_command({"type": "set_brake", "on": True, "mode": "warp"})
    s.handle_command({"type": "set_brake", "on": True})   # mode omitted
    drain(s)
    assert s.brake and s.brake_mode == "short"            # regen is opt-in


def test_apply_state_full_blob():
    """apply_state (share links / session restore) round-trips a full state."""
    s = make_session()
    s.apply_preset({
        "motor_type": "bldc",
        "params": {"resistance": 0.5, "max_voltage": 12.0},
        "load": {"kind": "fan", "params": {"coefficient": 1e-7}},
        "limits": {"current_limit": 9.0},
        "battery": {"capacity_ah": 1.5, "nominal_voltage": 11.1},
        "drive": {"voltage": 6.0},
    })
    drain(s)
    assert s.motor.motor_type == "bldc"
    assert s.load.kind == "fan"
    assert s.limiter.limit_a == 9.0
    assert s.battery is not None and s.battery.capacity_ah == 1.5
    assert s.throttle_v == 6.0
