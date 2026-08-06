// Standalone C++ demo / smoke test for the motor simulation engine.
//
// This has NO dependency on Python or pybind11 -- it links directly against
// the motorsim engine library and is useful for (a) quickly sanity-checking
// the physics while developing the engine, and (b) as a minimal example of
// using the engine straight from C++ if a Python control layer isn't
// wanted for some use case.
//
// Build (from the project root):
//   cmake -S . -B build -DMOTORSIM_BUILD_DEMO=ON
//   cmake --build build --target motorsim_demo
//   ./build/demo/motorsim_demo
#include <cstdio>
#include <vector>

#include "motorsim/BLDCMotor.h"
#include "motorsim/DCMotor.h"
#include "motorsim/Simulator.h"

using namespace motorsim;

namespace {

void printHeader(const char* title) {
    std::printf("\n=== %s ===\n", title);
    std::printf("%10s %10s %10s %10s %10s\n", "t(s)", "V", "I(A)", "RPM", "T(N*m)");
}

void printRow(const MotorState& s) {
    std::printf("%10.4f %10.2f %10.3f %10.1f %10.4f\n", s.time, s.voltage, s.current, s.rpm, s.torque);
}

void runDcDemo() {
    MotorParams p;
    p.resistance = 1.2;        // Ohm
    p.inductance = 0.0006;     // H
    p.torqueConstant = 0.06;   // N*m/A
    p.backEmfConstant = 0.06;  // V*s/rad
    p.inertia = 0.00035;       // kg*m^2
    p.viscousFriction = 0.00008;
    p.staticFriction = 0.002;
    p.maxVoltage = 12.0;

    DCMotor motor(p);
    Simulator sim(motor);

    const double dt = 5e-5; // 50 us
    // Step 1: spin up under 12V with a light load, for 0.3s.
    auto spinUp = sim.runConstant(12.0, 0.01, 0.3, dt);
    // Step 2: apply a heavier load for another 0.2s at the same voltage.
    auto loaded = sim.runConstant(12.0, 0.05, 0.2, dt);

    printHeader("DC Motor: spin-up (12V, light load) -> loaded (12V, heavy load)");
    for (std::size_t i = 0; i < spinUp.size(); i += spinUp.size() / 10) printRow(spinUp[i]);
    std::printf("--- load step applied ---\n");
    for (std::size_t i = 0; i < loaded.size(); i += loaded.size() / 10) printRow(loaded[i]);
}

void runBldcDemo() {
    BLDCParams p;
    p.resistance = 0.8;        // Ohm (line-to-line, two windings in series)
    p.inductance = 0.0004;     // H
    p.torqueConstant = 0.045;
    p.backEmfConstant = 0.045;
    p.inertia = 0.00025;
    p.viscousFriction = 0.00006;
    p.staticFriction = 0.001;
    p.maxVoltage = 24.0;
    p.polePairs = 7;
    p.rippleDepth = 0.08;

    BLDCMotor motor(p);
    Simulator sim(motor);

    const double dt = 5e-5;
    auto run = sim.runConstant(18.0, 0.02, 0.3, dt);

    printHeader("BLDC Motor: 18V bus, moderate load (7 pole pairs, 8% ripple)");
    for (std::size_t i = 0; i < run.size(); i += run.size() / 15) printRow(run[i]);
}

} // namespace

int main() {
    runDcDemo();
    runBldcDemo();
    std::printf("\nDemo complete.\n");
    return 0;
}
