"""Unit tests for the mechanical load models.

Each load law is checked at known operating points (the plan asks for a
steady-state check of every law), plus the reflected-inertia bookkeeping
for wheel/flywheel and the protocol-facing validation in make_load().
"""
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_server.loads import (
    ConstantLoad, FanLoad, FlywheelLoad, PumpLoad, ViscousLoad, WheelLoad,
    available_loads, make_load,
)


def test_none_load_is_inert():
    load = make_load("none")
    assert load.torque(500.0) == 0.0
    assert load.extra_inertia() == 0.0


def test_constant_load_brakes_both_directions_and_never_drives():
    load = ConstantLoad(torque=0.05)
    # magnitude is speed-independent, but it always opposes the rotation
    assert load.torque(100.0) == pytest.approx(0.05)
    assert load.torque(-100.0) == pytest.approx(-0.05)
    # a brake applies nothing to a shaft at rest -- it must never spin one up
    assert load.torque(0.0) == 0.0
    # only the magnitude is meaningful: a brake is not a driving load
    assert ConstantLoad(torque=-0.02).torque(50.0) == pytest.approx(0.02)


def test_coulomb_loads_are_regularized_through_zero():
    """No sign-flip chatter at standstill: torque ramps smoothly through 0."""
    for load in (ConstantLoad(torque=0.05), PumpLoad(static_torque=0.01, coefficient=0.0)):
        full = load.torque(100.0)
        # inside the band the opposing torque scales with speed...
        assert load.torque(0.25) == pytest.approx(full * 0.5)
        assert load.torque(-0.25) == pytest.approx(-full * 0.5)
        # ...and is continuous across zero (no jump between adjacent speeds)
        assert abs(load.torque(1e-9) - load.torque(-1e-9)) < 1e-6
        # outside the band it's the full magnitude
        assert load.torque(5.0) == pytest.approx(full)


def test_viscous_load_is_linear_in_speed():
    load = ViscousLoad(coefficient=1e-3)
    assert load.torque(100.0) == pytest.approx(0.1)
    assert load.torque(-100.0) == pytest.approx(-0.1)  # opposes reverse too
    assert load.torque(200.0) == pytest.approx(2 * load.torque(100.0))


def test_fan_load_is_quadratic_and_opposes_rotation():
    load = FanLoad(coefficient=2e-7)
    assert load.torque(100.0) == pytest.approx(2e-7 * 100.0**2)
    assert load.torque(200.0) == pytest.approx(4 * load.torque(100.0))
    assert load.torque(-100.0) == pytest.approx(-load.torque(100.0))
    assert load.torque(0.0) == 0.0


def test_pump_load_static_head_plus_quadratic():
    load = PumpLoad(static_torque=0.01, coefficient=1e-6)
    assert load.torque(100.0) == pytest.approx(0.01 + 1e-6 * 100.0**2)
    assert load.torque(-100.0) == pytest.approx(-(0.01 + 1e-6 * 100.0**2))
    assert load.torque(0.0) == 0.0  # a stationary pump applies no torque


def test_wheel_load_reflected_through_gear_ratio():
    load = WheelLoad(mass=20.0, radius=0.15, gear_ratio=0.2,
                     rolling_coeff=0.015, drag_area=0.4, wheel_inertia=0.05)
    n, r = 0.2, 0.15
    rolling = 0.015 * 20.0 * 9.81 * r
    omega = 100.0
    v = n * omega * r
    drag = 0.5 * 1.225 * 0.4 * v * v * r
    assert load.torque(omega) == pytest.approx(n * (rolling + drag))
    assert load.extra_inertia() == pytest.approx(n * n * (0.05 + 20.0 * r * r))


def test_flywheel_load_inertia_dominates_drag():
    load = FlywheelLoad(mass=2.0, radius=0.08, bearing_drag=1e-5)
    assert load.extra_inertia() == pytest.approx(0.5 * 2.0 * 0.08**2)
    assert load.torque(100.0) == pytest.approx(1e-3)  # tiny bearing drag only


def test_make_load_validates_kind_and_params():
    assert set(available_loads()) == {
        "none", "constant", "viscous", "fan", "pump", "wheel", "flywheel"}
    with pytest.raises(ValueError):
        make_load("antigravity")
    with pytest.raises(ValueError):
        make_load("fan", {"coefficient": float("nan")})
    with pytest.raises(ValueError):
        make_load("fan", {"not_a_param": 1.0})
    fan = make_load("fan", {"coefficient": 3e-7})
    assert fan.kind == "fan" and fan.describe() == {"coefficient": 3e-7}
