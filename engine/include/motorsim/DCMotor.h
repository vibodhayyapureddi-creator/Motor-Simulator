#pragma once

#include "MotorBase.h"

namespace motorsim {

// A brushed / generic DC motor model.
//
// Physics (standard armature-controlled DC motor, lumped-parameter model):
//
//   Electrical:  L * di/dt = V - i*R - Ke*omega
//   Mechanical:  J * domega/dt = Kt*i - B*omega - Tstatic*sign(omega) - Tload
//
// Integrated with a fixed-step RK4 integrator, treating the applied voltage
// and load torque as constant (zero-order hold) over each dt -- accurate as
// long as dt is small relative to the electrical/mechanical time constants
// (a few hundred microseconds to a millisecond is a safe default for
// typical small-motor parameters).
class DCMotor : public MotorBase {
public:
    explicit DCMotor(const MotorParams& params);

    const MotorState& step(double dt, double voltageCommand, double loadTorque) override;
    void reset() override;
    const MotorState& state() const override { return state_; }

    const MotorParams& params() const { return params_; }

private:
    struct ElectroMechState {
        double current;
        double omega;
    };
    struct Derivative {
        double didt;
        double domegadt;
    };

    Derivative derivative(const ElectroMechState& s, double voltage, double loadTorque) const;

    MotorParams params_;
    ElectroMechState em_{0.0, 0.0};
    MotorState state_{};
};

} // namespace motorsim
