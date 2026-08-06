#include "motorsim/BLDCMotor.h"

#include <algorithm>
#include <cmath>

namespace motorsim {

namespace {
constexpr double kTwoPi = 6.283185307179586476925286766559;
constexpr double kPi = 3.141592653589793238462643383279;

double clamp(double v, double lo, double hi) {
    return std::max(lo, std::min(hi, v));
}

double wrapTwoPi(double angle) {
    double a = std::fmod(angle, kTwoPi);
    if (a < 0.0) a += kTwoPi;
    return a;
}
} // namespace

BLDCMotor::BLDCMotor(const BLDCParams& params) : params_(params) {
    reset();
}

void BLDCMotor::reset() {
    em_ = ElectroMechState{0.0, 0.0};
    mechanicalAngle_ = 0.0;
    sector_ = 0;
    state_ = MotorState{};
}

double BLDCMotor::couplingShape(double electricalAngleRad) const {
    // Oscillates between (1 - rippleDepth) and 1, six times per electrical
    // revolution -- approximates the torque/back-EMF ripple produced by
    // idealized six-step trapezoidal commutation. rippleDepth = 0 collapses
    // this to a constant 1.0 (equivalent to DCMotor's coupling).
    const double depth = clamp(params_.rippleDepth, 0.0, 1.0);
    if (depth <= 0.0) return 1.0;
    return 1.0 - depth * 0.5 * (1.0 - std::cos(6.0 * electricalAngleRad));
}

BLDCMotor::Derivative BLDCMotor::derivative(const ElectroMechState& s, double voltage, double loadTorque,
                                             double shape) const {
    const double ke = params_.backEmfConstant * shape;
    const double kt = params_.torqueConstant * shape;

    const double didt = (voltage - s.current * params_.resistance - ke * s.omega) / params_.inductance;

    const double frictionTorque = params_.viscousFriction * s.omega
                                   + params_.staticFriction * (s.omega > 0 ? 1.0 : (s.omega < 0 ? -1.0 : 0.0));
    const double domegadt = (kt * s.current - frictionTorque - loadTorque) / params_.inertia;

    return Derivative{didt, domegadt};
}

const MotorState& BLDCMotor::step(double dt, double voltageCommand, double loadTorque) {
    const double voltage = clamp(voltageCommand, -params_.maxVoltage, params_.maxVoltage);

    // Electrical angle held constant (zero-order hold) over this step, same
    // simplification used for voltage/load -- valid for small dt.
    const double electricalAngle = wrapTwoPi(mechanicalAngle_ * params_.polePairs);
    const double shape = couplingShape(electricalAngle);
    sector_ = static_cast<int>(electricalAngle / (kTwoPi / 6.0)) % 6;

    const ElectroMechState s0 = em_;

    const Derivative k1 = derivative(s0, voltage, loadTorque, shape);

    const ElectroMechState s1{s0.current + 0.5 * dt * k1.didt, s0.omega + 0.5 * dt * k1.domegadt};
    const Derivative k2 = derivative(s1, voltage, loadTorque, shape);

    const ElectroMechState s2{s0.current + 0.5 * dt * k2.didt, s0.omega + 0.5 * dt * k2.domegadt};
    const Derivative k3 = derivative(s2, voltage, loadTorque, shape);

    const ElectroMechState s3{s0.current + dt * k3.didt, s0.omega + dt * k3.domegadt};
    const Derivative k4 = derivative(s3, voltage, loadTorque, shape);

    em_.current = s0.current + (dt / 6.0) * (k1.didt + 2.0 * k2.didt + 2.0 * k3.didt + k4.didt);
    em_.omega = s0.omega + (dt / 6.0) * (k1.domegadt + 2.0 * k2.domegadt + 2.0 * k3.domegadt + k4.domegadt);

    mechanicalAngle_ = wrapTwoPi(mechanicalAngle_ + em_.omega * dt);

    state_.time += dt;
    state_.voltage = voltage;
    state_.current = em_.current;
    state_.omega = em_.omega;
    state_.rpm = em_.omega * 60.0 / kTwoPi;
    state_.torque = params_.torqueConstant * shape * em_.current;
    state_.loadTorque = loadTorque;
    state_.electricalAngleDeg = electricalAngle * 180.0 / kPi;

    return state_;
}

} // namespace motorsim
