#include "motorsim/DCMotor.h"

#include <algorithm>
#include <cmath>

namespace motorsim {

namespace {
constexpr double kTwoPi = 6.283185307179586476925286766559;

double clamp(double v, double lo, double hi) {
    return std::max(lo, std::min(hi, v));
}

double sign(double v) {
    if (v > 0.0) return 1.0;
    if (v < 0.0) return -1.0;
    return 0.0;
}
} // namespace

DCMotor::DCMotor(const MotorParams& params) : params_(params) {
    reset();
}

void DCMotor::reset() {
    em_ = ElectroMechState{0.0, 0.0};
    state_ = MotorState{};
}

DCMotor::Derivative DCMotor::derivative(const ElectroMechState& s, double voltage, double loadTorque) const {
    const double didt = (voltage - s.current * params_.resistance - params_.backEmfConstant * s.omega)
                         / params_.inductance;

    const double frictionTorque = params_.viscousFriction * s.omega + params_.staticFriction * sign(s.omega);
    const double domegadt = (params_.torqueConstant * s.current - frictionTorque - loadTorque) / params_.inertia;

    return Derivative{didt, domegadt};
}

const MotorState& DCMotor::step(double dt, double voltageCommand, double loadTorque) {
    const double voltage = clamp(voltageCommand, -params_.maxVoltage, params_.maxVoltage);

    // Classic 4th-order Runge-Kutta over the 2-state (current, omega) system.
    const ElectroMechState s0 = em_;

    const Derivative k1 = derivative(s0, voltage, loadTorque);

    const ElectroMechState s1{s0.current + 0.5 * dt * k1.didt, s0.omega + 0.5 * dt * k1.domegadt};
    const Derivative k2 = derivative(s1, voltage, loadTorque);

    const ElectroMechState s2{s0.current + 0.5 * dt * k2.didt, s0.omega + 0.5 * dt * k2.domegadt};
    const Derivative k3 = derivative(s2, voltage, loadTorque);

    const ElectroMechState s3{s0.current + dt * k3.didt, s0.omega + dt * k3.domegadt};
    const Derivative k4 = derivative(s3, voltage, loadTorque);

    em_.current = s0.current + (dt / 6.0) * (k1.didt + 2.0 * k2.didt + 2.0 * k3.didt + k4.didt);
    em_.omega = s0.omega + (dt / 6.0) * (k1.domegadt + 2.0 * k2.domegadt + 2.0 * k3.domegadt + k4.domegadt);

    state_.time += dt;
    state_.voltage = voltage;
    state_.current = em_.current;
    state_.omega = em_.omega;
    state_.rpm = em_.omega * 60.0 / kTwoPi;
    state_.torque = params_.torqueConstant * em_.current;
    state_.loadTorque = loadTorque;
    state_.electricalAngleDeg = 0.0; // not meaningful for a brushed DC motor

    return state_;
}

} // namespace motorsim
