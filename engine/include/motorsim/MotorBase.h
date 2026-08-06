#pragma once

#include "Types.h"

namespace motorsim {

// Abstract interface every motor model implements. This is the boundary the
// control layer (Python, or the standalone C++ demo) talks to: it knows
// nothing about the electrical/mechanical equations underneath, only that it
// can push an applied voltage + a load torque forward by dt seconds and read
// back telemetry.
//
// This is also the extension point for new motor types: to add a stepper
// motor, induction motor, etc. later, implement this interface and it will
// work with the existing bindings/CLI/plotting code unchanged.
class MotorBase {
public:
    virtual ~MotorBase() = default;

    // Advance the simulation by dt seconds, given:
    //   voltageCommand - commanded terminal/bus voltage (V), clamped
    //                     internally to +-maxVoltage.
    //   loadTorque     - external mechanical load torque opposing rotation
    //                     (N*m). Positive opposes positive-direction spin.
    // Returns the resulting MotorState (also retrievable via state()).
    virtual const MotorState& step(double dt, double voltageCommand, double loadTorque) = 0;

    // Reset the motor to rest (zero current, zero speed, t=0).
    virtual void reset() = 0;

    // Most recent telemetry sample (same as the return value of the last step()).
    virtual const MotorState& state() const = 0;
};

} // namespace motorsim
