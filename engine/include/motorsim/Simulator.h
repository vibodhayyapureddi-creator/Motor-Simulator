#pragma once

#include <vector>

#include "MotorBase.h"

namespace motorsim {

// Drives a MotorBase through a pre-sampled input profile and records the
// resulting telemetry. This is the "batch" runner used by the standalone
// C++ demo and by the Python CLI's scripted/profile-driven scenarios.
//
// It is intentionally NOT required for interactive/real-time use: a future
// live-dashboard control layer can simply call motor->step(dt, v, load)
// directly, once per UI tick, using MotorBase without going through
// Simulator at all. Simulator exists purely as a convenience for
// "run this whole scenario and give me the log" use cases.
class Simulator {
public:
    explicit Simulator(MotorBase& motor) : motor_(motor) {}

    // voltageProfile and loadProfile must be the same length; each entry is
    // held constant for one dt-second step (zero-order hold). Returns one
    // MotorState per step (i.e. the same length as the input profiles).
    std::vector<MotorState> run(const std::vector<double>& voltageProfile,
                                 const std::vector<double>& loadProfile,
                                 double dt);

    // Convenience overload: constant voltage and load applied for the full
    // duration, sampled at fixed dt.
    std::vector<MotorState> runConstant(double voltage, double loadTorque, double duration, double dt);

private:
    MotorBase& motor_;
};

} // namespace motorsim
