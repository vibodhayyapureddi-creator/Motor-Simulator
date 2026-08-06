#pragma once

#include "MotorBase.h"

namespace motorsim {

// A simplified brushless DC (BLDC) motor model using idealized six-step
// (trapezoidal) commutation.
//
// Simplifying assumptions (documented explicitly -- this is a v1 core
// engine, not a full three-phase EMT simulation):
//   - At any instant exactly two of the three phases conduct (standard
//     120-degree six-step drive), so the motor is modeled as a single
//     lumped electrical loop, structurally identical to the DCMotor
//     equations, EXCEPT the resistance/inductance the caller supplies via
//     MotorParams should represent the *line-to-line* (two windings in
//     series) values, since that's the circuit that's actually conducting.
//   - Commutation is assumed ideal (perfectly aligned with rotor position,
//     zero commutation delay), so the drive electronics keep torque
//     unidirectional for a given commanded voltage sign -- there is no
//     sign flipping of Ke/Kt needed, unlike raw per-phase currents.
//   - The trapezoidal nature of real back-EMF / six-step commutation is
//     approximated with a smooth ripple multiplier on the Ke/Kt coupling,
//     oscillating at 6x the electrical frequency (the classic signature of
//     six-step drives) with depth controlled by BLDCParams::rippleDepth.
//     Setting rippleDepth = 0 makes this mathematically identical to
//     DCMotor.
//
// This is enough to produce realistic-looking RPM/torque/current curves
// including commutation ripple, while keeping the engine simple. A future
// version could replace this with a full three-phase current model if
// more fidelity is needed -- MotorBase is the extension point for that.
class BLDCMotor : public MotorBase {
public:
    explicit BLDCMotor(const BLDCParams& params);

    const MotorState& step(double dt, double voltageCommand, double loadTorque) override;
    void reset() override;
    const MotorState& state() const override { return state_; }

    const BLDCParams& params() const { return params_; }

    // Current commutation sector, 0..5 (six 60-electrical-degree sectors
    // per electrical revolution). Exposed mainly for visualization/debugging.
    int commutationSector() const { return sector_; }

private:
    struct ElectroMechState {
        double current;
        double omega;
    };
    struct Derivative {
        double didt;
        double domegadt;
    };

    // Ripple multiplier (0..1 relative to full coupling) as a function of
    // electrical angle (radians), oscillating 6x per electrical revolution.
    double couplingShape(double electricalAngleRad) const;

    Derivative derivative(const ElectroMechState& s, double voltage, double loadTorque, double shape) const;

    BLDCParams params_;
    ElectroMechState em_{0.0, 0.0};
    double mechanicalAngle_ = 0.0; // rad, wrapped to [0, 2*pi)
    int sector_ = 0;
    MotorState state_{};
};

} // namespace motorsim
