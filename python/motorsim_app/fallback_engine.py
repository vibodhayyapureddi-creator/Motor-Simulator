"""Pure-Python mirror of the C++ motorsim engine.

This exists ONLY as a convenience/testing fallback for when the compiled
`motorsim_py` pybind11 extension hasn't been built yet (e.g. no C++
toolchain set up, or no network access to fetch pybind11). It implements
the exact same equations as engine/src/DCMotor.cpp and
engine/src/BLDCMotor.cpp with the same RK4 integrator, so results match
the C++ engine to floating point precision for the same inputs.

The C++ engine (engine/) is the canonical, "real" simulation core meant to
run behind the interface -- this module is not a replacement for it, and
engine_bridge.py prefers the compiled extension whenever it's available.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sign(v: float) -> float:
    if v > 0.0:
        return 1.0
    if v < 0.0:
        return -1.0
    return 0.0


@dataclass
class MotorParams:
    resistance: float = 1.0
    inductance: float = 0.001
    torque_constant: float = 0.05
    back_emf_constant: float = 0.05
    inertia: float = 0.0005
    viscous_friction: float = 0.0002
    static_friction: float = 0.0
    max_voltage: float = 24.0


@dataclass
class BLDCParams(MotorParams):
    pole_pairs: int = 7
    ripple_depth: float = 0.05


@dataclass
class MotorState:
    time: float = 0.0
    voltage: float = 0.0
    current: float = 0.0
    omega: float = 0.0
    rpm: float = 0.0
    torque: float = 0.0
    load_torque: float = 0.0
    electrical_angle_deg: float = 0.0


class DCMotor:
    def __init__(self, params: MotorParams):
        self.params = params
        self._i = 0.0
        self._omega = 0.0
        self.state = MotorState()

    def reset(self) -> None:
        self._i = 0.0
        self._omega = 0.0
        self.state = MotorState()

    def _derivative(self, i: float, omega: float, voltage: float, load_torque: float):
        p = self.params
        didt = (voltage - i * p.resistance - p.back_emf_constant * omega) / p.inductance
        friction_torque = p.viscous_friction * omega + p.static_friction * _sign(omega)
        domegadt = (p.torque_constant * i - friction_torque - load_torque) / p.inertia
        return didt, domegadt

    def step(self, dt: float, voltage_command: float, load_torque: float) -> MotorState:
        p = self.params
        voltage = _clamp(voltage_command, -p.max_voltage, p.max_voltage)

        i0, w0 = self._i, self._omega
        k1 = self._derivative(i0, w0, voltage, load_torque)
        k2 = self._derivative(i0 + 0.5 * dt * k1[0], w0 + 0.5 * dt * k1[1], voltage, load_torque)
        k3 = self._derivative(i0 + 0.5 * dt * k2[0], w0 + 0.5 * dt * k2[1], voltage, load_torque)
        k4 = self._derivative(i0 + dt * k3[0], w0 + dt * k3[1], voltage, load_torque)

        self._i = i0 + (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        self._omega = w0 + (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])

        s = self.state
        s.time += dt
        s.voltage = voltage
        s.current = self._i
        s.omega = self._omega
        s.rpm = self._omega * 60.0 / (2.0 * math.pi)
        s.torque = p.torque_constant * self._i
        s.load_torque = load_torque
        s.electrical_angle_deg = 0.0
        return s


class BLDCMotor:
    def __init__(self, params: BLDCParams):
        self.params = params
        self._i = 0.0
        self._omega = 0.0
        self._theta_m = 0.0
        self._sector = 0
        self.state = MotorState()

    def reset(self) -> None:
        self._i = 0.0
        self._omega = 0.0
        self._theta_m = 0.0
        self._sector = 0
        self.state = MotorState()

    def commutation_sector(self) -> int:
        return self._sector

    @staticmethod
    def _wrap(angle: float) -> float:
        a = math.fmod(angle, 2.0 * math.pi)
        if a < 0.0:
            a += 2.0 * math.pi
        return a

    def _coupling_shape(self, electrical_angle: float) -> float:
        depth = _clamp(self.params.ripple_depth, 0.0, 1.0)
        if depth <= 0.0:
            return 1.0
        return 1.0 - depth * 0.5 * (1.0 - math.cos(6.0 * electrical_angle))

    def _derivative(self, i: float, omega: float, voltage: float, load_torque: float, shape: float):
        p = self.params
        ke = p.back_emf_constant * shape
        kt = p.torque_constant * shape
        didt = (voltage - i * p.resistance - ke * omega) / p.inductance
        friction_torque = p.viscous_friction * omega + p.static_friction * _sign(omega)
        domegadt = (kt * i - friction_torque - load_torque) / p.inertia
        return didt, domegadt

    def step(self, dt: float, voltage_command: float, load_torque: float) -> MotorState:
        p = self.params
        voltage = _clamp(voltage_command, -p.max_voltage, p.max_voltage)

        electrical_angle = self._wrap(self._theta_m * p.pole_pairs)
        shape = self._coupling_shape(electrical_angle)
        self._sector = int(electrical_angle / (2.0 * math.pi / 6.0)) % 6

        i0, w0 = self._i, self._omega
        k1 = self._derivative(i0, w0, voltage, load_torque, shape)
        k2 = self._derivative(i0 + 0.5 * dt * k1[0], w0 + 0.5 * dt * k1[1], voltage, load_torque, shape)
        k3 = self._derivative(i0 + 0.5 * dt * k2[0], w0 + 0.5 * dt * k2[1], voltage, load_torque, shape)
        k4 = self._derivative(i0 + dt * k3[0], w0 + dt * k3[1], voltage, load_torque, shape)

        self._i = i0 + (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        self._omega = w0 + (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        self._theta_m = self._wrap(self._theta_m + self._omega * dt)

        s = self.state
        s.time += dt
        s.voltage = voltage
        s.current = self._i
        s.omega = self._omega
        s.rpm = self._omega * 60.0 / (2.0 * math.pi)
        s.torque = p.torque_constant * shape * self._i
        s.load_torque = load_torque
        s.electrical_angle_deg = electrical_angle * 180.0 / math.pi
        return s


@dataclass
class StepperParams(MotorParams):
    holding_torque: float = 0.4       # N*m at standstill, energized
    step_angle_deg: float = 1.8       # full-step angle
    rated_current: float = 1.5        # constant-current drive per phase
    pullout_corner: float = 600.0     # steps/s where available torque halves-ish


class StepperMotor:
    """Hybrid stepper, electromagnetically simplified but honest.

    The drive advances a commanded angle at the requested step rate; the
    rotor is pulled toward it by a sinusoidal torque-angle curve
    (tau = -tau_avail * sin(Nr * (theta - theta_cmd)), Nr = rotor teeth =
    90deg-electrical per full step). Available torque follows the classic
    pull-out curve tau_h / sqrt(1 + (rate/corner)^2). If the lag exceeds
    two full steps the rotor slides over torque humps - genuine step loss
    (`slipping` flags it). De-energized, the shaft freewheels on friction
    (detent torque neglected). Python-only motor type (no C++ port yet);
    voltage acts as the energize input, current is the constant-current
    drive magnitude.
    """

    motor_kind = "stepper"

    def __init__(self, params: StepperParams):
        self.params = params
        self._theta = 0.0        # rotor mechanical angle (rad, unwrapped)
        self._cmd_theta = 0.0    # commanded angle (rad, unwrapped)
        self._omega = 0.0
        self._rate = 0.0         # commanded steps/s (signed)
        self._lag_base = 0.0     # lag offset accumulated by past slips
        self._slip_hold = 0.0    # seconds the slip flag stays lit
        self.lost_steps = 0
        self.slipping = False
        self.state = MotorState()

    def reset(self) -> None:
        self.__init__(self.params)

    def set_step_rate(self, steps_per_s: float) -> None:
        self._rate = steps_per_s

    def commutation_sector(self) -> int:
        return -1

    @property
    def _step_rad(self) -> float:
        return math.radians(self.params.step_angle_deg)

    def _tau_avail(self) -> float:
        f = abs(self._rate)
        return self.params.holding_torque / math.sqrt(
            1.0 + (f / max(1e-9, self.params.pullout_corner)) ** 2)

    def _derivative(self, theta: float, omega: float, energized: bool,
                    cmd: float, load_torque: float):
        p = self.params
        n_r = (math.pi / 2.0) / self._step_rad   # rotor teeth
        tau_e = 0.0
        if energized:
            tau_e = -self._tau_avail() * math.sin(n_r * (theta - cmd))
            # electromagnetic damping (winding losses acting on the slip
            # velocity) - without it the spring-mass rotor rings almost
            # undamped and can never pull in from rest; zeta ~ 0.25
            k = self.params.holding_torque * n_r
            c_em = 0.5 * math.sqrt(max(1e-12, k * p.inertia))
            tau_e -= c_em * (omega - self._rate * self._step_rad)
        friction = p.viscous_friction * omega + p.static_friction * _sign(omega)
        return omega, (tau_e - friction - load_torque) / p.inertia

    def step(self, dt: float, voltage_command: float, load_torque: float) -> MotorState:
        p = self.params
        energized = abs(voltage_command) > 0.5
        if energized:
            self._cmd_theta += self._rate * self._step_rad * dt
        cmd = self._cmd_theta

        t0, w0 = self._theta, self._omega
        k1 = self._derivative(t0, w0, energized, cmd, load_torque)
        k2 = self._derivative(t0 + 0.5 * dt * k1[0], w0 + 0.5 * dt * k1[1],
                              energized, cmd, load_torque)
        k3 = self._derivative(t0 + 0.5 * dt * k2[0], w0 + 0.5 * dt * k2[1],
                              energized, cmd, load_torque)
        k4 = self._derivative(t0 + dt * k3[0], w0 + dt * k3[1],
                              energized, cmd, load_torque)
        self._theta = t0 + (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        self._omega = w0 + (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])

        # slip detection: a slip event is the rotor sliding one full
        # electrical period (4 full steps) over the torque humps. Past
        # slips shift the lag baseline permanently, so compare against it;
        # the flag stays lit briefly so intermittent slipping reads out.
        lag = self._theta - self._cmd_theta
        period = 4.0 * self._step_rad
        n_slipped = round((lag - self._lag_base) / period)
        if energized and n_slipped != 0:
            self.lost_steps += 4 * abs(n_slipped)
            self._lag_base += n_slipped * period
            self._slip_hold = 0.3
        self._slip_hold = max(0.0, self._slip_hold - dt)
        self.slipping = energized and self._slip_hold > 0.0
        n_r = (math.pi / 2.0) / self._step_rad

        s = self.state
        s.time += dt
        s.voltage = voltage_command if energized else 0.0
        s.current = p.rated_current if energized else 0.0
        s.omega = self._omega
        s.rpm = self._omega * 60.0 / (2.0 * math.pi)
        s.torque = (-self._tau_avail() * math.sin(n_r * lag)) if energized else 0.0
        s.load_torque = load_torque
        # mechanical angle (deg, wrapped) so the viewer can show detents
        s.electrical_angle_deg = math.degrees(self._theta) % 360.0
        return s


@dataclass
class InductionParams(MotorParams):
    pole_pairs: int = 2
    breakdown_torque: float = 12.0    # N*m peak of the torque-slip curve
    breakdown_slip: float = 0.2
    magnetizing_current: float = 3.0  # no-load (flux) current, A
    rated_frequency: float = 60.0     # Hz at rated voltage (V/Hz base)


class InductionMotor:
    """Squirrel-cage induction motor via the Kloss torque-slip curve.

    The stator field rotates at omega_s = 2*pi*f/p; the rotor develops
    torque only while it slips relative to the field:

        s = (omega_s - omega) / omega_s
        tau = 2*tau_b / (s/s_b + s_b/s) * flux^2,  flux = (V/f)/(V_r/f_r)

    Negative slip (rotor above synchronous) generates - torque reverses.
    Drive is AC magnitude (voltage command) + supply frequency
    (set_supply_frequency); direction comes from the frequency's sign.
    Python-only motor type (no C++ port yet). Reported current is a
    display estimate: magnetizing current plus a torque-producing part.
    """

    motor_kind = "induction"

    def __init__(self, params: InductionParams):
        self.params = params
        self._omega = 0.0
        self._field_theta = 0.0
        self._hz = 0.0
        self.slipping = False
        self.state = MotorState()

    def reset(self) -> None:
        self.__init__(self.params)

    def set_supply_frequency(self, hz: float) -> None:
        self._hz = hz

    def commutation_sector(self) -> int:
        return -1

    def _torque(self, omega: float, voltage: float) -> float:
        p = self.params
        if abs(self._hz) < 0.01 or voltage <= 0.01:
            return 0.0
        omega_s = 2.0 * math.pi * self._hz / max(1, p.pole_pairs)
        slip = (omega_s - omega) / omega_s
        if abs(slip) < 1e-9:
            return 0.0
        # constant V/Hz keeps flux (and the curve) constant; boost clamped
        vhz = (voltage / abs(self._hz)) / (p.max_voltage / p.rated_frequency)
        flux2 = min(1.5, vhz) ** 2
        kloss = 2.0 * p.breakdown_torque / (slip / p.breakdown_slip
                                            + p.breakdown_slip / slip)
        # torque acts along the field direction: positive slip motors,
        # negative slip (rotor above synchronous) generates
        return _sign(omega_s) * kloss * flux2

    def _derivative(self, omega: float, voltage: float, load_torque: float):
        p = self.params
        tau = self._torque(omega, voltage)
        friction = p.viscous_friction * omega + p.static_friction * _sign(omega)
        return (tau - friction - load_torque) / p.inertia

    def step(self, dt: float, voltage_command: float, load_torque: float) -> MotorState:
        p = self.params
        v = _clamp(abs(voltage_command), 0.0, p.max_voltage)

        w0 = self._omega
        k1 = self._derivative(w0, v, load_torque)
        k2 = self._derivative(w0 + 0.5 * dt * k1, v, load_torque)
        k3 = self._derivative(w0 + 0.5 * dt * k2, v, load_torque)
        k4 = self._derivative(w0 + dt * k3, v, load_torque)
        self._omega = w0 + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)

        omega_s = 2.0 * math.pi * self._hz / max(1, p.pole_pairs)
        self._field_theta = (self._field_theta + omega_s * dt) % (2.0 * math.pi)
        tau = self._torque(self._omega, v)
        slip = 0.0 if abs(omega_s) < 1e-9 else (omega_s - self._omega) / omega_s
        self.slipping = v > 1.0 and abs(self._hz) > 2.0 and slip > 0.9

        s = self.state
        s.time += dt
        s.voltage = v
        i_torque = abs(tau) / max(1e-9, p.breakdown_torque) * 2.0 * p.magnetizing_current
        s.current = math.hypot(p.magnetizing_current if v > 0.01 else 0.0, i_torque)
        s.omega = self._omega
        s.rpm = self._omega * 60.0 / (2.0 * math.pi)
        s.torque = tau
        s.load_torque = load_torque
        # field angle: lets the viewer show the stator field leading the rotor
        s.electrical_angle_deg = math.degrees(self._field_theta)
        return s


class Simulator:
    def __init__(self, motor):
        self._motor = motor

    def run(self, voltage_profile: List[float], load_profile: List[float], dt: float) -> List[MotorState]:
        if len(voltage_profile) != len(load_profile):
            raise ValueError("voltage_profile and load_profile must be the same length")
        log = []
        for v, load in zip(voltage_profile, load_profile):
            # Copy the state out since the C++ backend also returns
            # independent snapshots per step (via pybind11 value semantics
            # on read), not a live-updating reference the caller keeps.
            state = self._motor.step(dt, v, load)
            log.append(MotorState(**vars(state)))
        return log

    def run_constant(self, voltage: float, load_torque: float, duration: float, dt: float) -> List[MotorState]:
        # round rather than truncate: floating-point division (e.g.
        # 0.3 / 5e-5) can land a hair under the intended integer step
        # count, which would otherwise silently drop the last step.
        steps = max(0, round(duration / dt))
        return self.run([voltage] * steps, [load_torque] * steps, dt)
