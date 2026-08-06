"""Smoke tests for the pure-Python fallback engine's physics.

These don't require the compiled C++ extension, so they run anywhere, and
they pin down basic sanity properties of the model (monotonic spin-up
under constant voltage/light load, current decaying as back-EMF builds,
BLDC ripple magnitude bounded, etc.) so a future change to the equations
can't silently break the physics without a test noticing.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_app.fallback_engine import BLDCMotor, BLDCParams, DCMotor, MotorParams, Simulator


def test_dc_motor_spins_up_and_current_decays():
    p = MotorParams(resistance=1.2, inductance=0.0006, torque_constant=0.06, back_emf_constant=0.06,
                     inertia=0.00035, viscous_friction=0.00008, static_friction=0.002, max_voltage=12.0)
    motor = DCMotor(p)
    sim = Simulator(motor)
    states = sim.run_constant(12.0, 0.01, 0.3, 5e-5)

    assert len(states) == 6000
    # Speed should climb monotonically (no load steps in this run) and stay
    # within a sane bound (no-load speed ~ V/Ke).
    rpms = [s.rpm for s in states]
    assert all(b >= a - 1e-6 for a, b in zip(rpms, rpms[1:])), "RPM should be non-decreasing under constant drive"
    assert rpms[-1] > 1000

    # Current should spike near start-up then decay as back-EMF builds.
    currents = [s.current for s in states]
    assert currents[0] < currents[10]  # still rising in the first few steps
    assert currents[-1] < max(currents) * 0.5


def test_bldc_motor_produces_positive_torque_with_ripple():
    p = BLDCParams(resistance=0.8, inductance=0.0004, torque_constant=0.045, back_emf_constant=0.045,
                    inertia=0.00025, viscous_friction=0.00006, static_friction=0.001, max_voltage=24.0,
                    pole_pairs=7, ripple_depth=0.08)
    motor = BLDCMotor(p)
    sim = Simulator(motor)
    states = sim.run_constant(18.0, 0.02, 0.2, 5e-5)

    torques = [s.torque for s in states[-1000:]]  # settled portion
    assert all(t > 0 for t in torques), "torque should stay positive under a forward-driving voltage"
    # Ripple should be present but bounded relative to mean torque.
    mean_t = sum(torques) / len(torques)
    spread = max(torques) - min(torques)
    assert 0 < spread < mean_t, "expect some commutation ripple, but not wildly larger than the mean torque"


def test_zero_ripple_bldc_matches_dc_style_smoothness():
    # rippleDepth = 0 should behave like an (approximately) smooth motor --
    # i.e. no 6x-per-electrical-revolution oscillation. Run long enough to
    # be close to steady state, then look at a short trailing window (a
    # handful of electrical cycles) so residual settling drift doesn't
    # swamp the (near-zero) ripple signal we're actually checking for.
    p = BLDCParams(resistance=0.8, inductance=0.0004, torque_constant=0.045, back_emf_constant=0.045,
                    inertia=0.00025, viscous_friction=0.00006, static_friction=0.0, max_voltage=24.0,
                    pole_pairs=7, ripple_depth=0.0)
    motor = BLDCMotor(p)
    sim = Simulator(motor)
    states = sim.run_constant(18.0, 0.02, 1.0, 5e-5)
    torques = [s.torque for s in states[-20:]]
    spread = max(torques) - min(torques)
    mean_t = sum(torques) / len(torques)
    assert spread < 0.005 * mean_t
