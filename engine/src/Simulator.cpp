#include "motorsim/Simulator.h"

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace motorsim {

std::vector<MotorState> Simulator::run(const std::vector<double>& voltageProfile,
                                        const std::vector<double>& loadProfile,
                                        double dt) {
    if (voltageProfile.size() != loadProfile.size()) {
        throw std::invalid_argument("Simulator::run: voltageProfile and loadProfile must be the same length");
    }
    if (dt <= 0.0) {
        throw std::invalid_argument("Simulator::run: dt must be positive");
    }

    std::vector<MotorState> log;
    log.reserve(voltageProfile.size());

    for (std::size_t i = 0; i < voltageProfile.size(); ++i) {
        log.push_back(motor_.step(dt, voltageProfile[i], loadProfile[i]));
    }
    return log;
}

std::vector<MotorState> Simulator::runConstant(double voltage, double loadTorque, double duration, double dt) {
    if (dt <= 0.0) {
        throw std::invalid_argument("Simulator::runConstant: dt must be positive");
    }
    // round rather than truncate: floating-point division (e.g. 0.3 / 5e-5)
    // can land a hair under the intended integer step count, which would
    // otherwise silently drop the last step.
    const std::size_t steps = static_cast<std::size_t>(std::max(0.0, std::round(duration / dt)));
    const std::vector<double> voltageProfile(steps, voltage);
    const std::vector<double> loadProfile(steps, loadTorque);
    return run(voltageProfile, loadProfile, dt);
}

} // namespace motorsim
