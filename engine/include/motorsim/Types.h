#pragma once
//
// Types.h
//
// Shared value types for the motor simulation engine.
//
// Unit convention (SI throughout):
//   voltage      volts (V)
//   current      amperes (A)
//   resistance   ohms (Ohm)
//   inductance   henries (H)
//   torque       newton-meters (N*m)
//   inertia      kg*m^2
//   speed        radians/second (rad/s)   [rpm is derived for display only]
//   angle        radians
//   time         seconds (s)
//

namespace motorsim {

// Parameters common to every motor model. Concrete motor types (DCMotor,
// BLDCMotor, ...) extend this with whatever extra parameters their physics
// need (e.g. pole pairs for BLDC commutation).
struct MotorParams {
    double resistance = 1.0;        // winding resistance, R (Ohm)
    double inductance = 0.001;      // winding inductance, L (H)
    double torqueConstant = 0.05;   // Kt (N*m / A)
    double backEmfConstant = 0.05;  // Ke (V*s / rad) -- numerically equal to
                                     // Kt in consistent SI units for an ideal
                                     // machine; kept separate so non-ideal
                                     // motors can be modeled later.
    double inertia = 0.0005;        // rotor (+ reflected load) inertia, J (kg*m^2)
    double viscousFriction = 0.0002;// viscous friction coefficient, B (N*m*s/rad)
    double staticFriction = 0.0;    // Coulomb / static friction torque (N*m),
                                     // opposes motion, applied as a step at
                                     // omega ~ 0.
    double maxVoltage = 24.0;       // supply / bus voltage limit (V), used to
                                     // clamp commanded voltage.
};

// Extra parameters specific to a BLDC (brushless DC) motor. The BLDC engine
// reuses MotorParams for the electrical/mechanical constants and adds the
// commutation-related parameters here. See BLDCMotor.h for the simplified
// six-step trapezoidal commutation model this drives.
struct BLDCParams : public MotorParams {
    int polePairs = 7;              // number of magnetic pole PAIRS
    double rippleDepth = 0.05;      // 0..1, how much the six-step commutation
                                     // modulates torque/back-EMF coupling
                                     // away from unity during sector
                                     // transitions (approximates trapezoidal
                                     // torque ripple). 0 = perfectly smooth
                                     // (behaves like an ideal DC motor).
};

// A single sample of motor telemetry, produced after every step().
struct MotorState {
    double time = 0.0;          // simulation time (s)
    double voltage = 0.0;       // applied terminal/bus voltage this step (V)
    double current = 0.0;       // winding / bus current (A)
    double omega = 0.0;         // mechanical angular velocity (rad/s)
    double rpm = 0.0;           // omega converted to RPM, for convenience
    double torque = 0.0;        // electromagnetic torque produced (N*m)
    double loadTorque = 0.0;    // external load torque applied this step (N*m)
    double electricalAngleDeg = 0.0; // commutation angle (BLDC only; 0 for DC)
};

} // namespace motorsim
