// pybind11 bindings exposing the C++ motorsim engine to Python.
//
// This is the entire language boundary: everything above this file (the
// Python control layer in python/motorsim_app) only ever talks to the
// engine through the classes exposed here. The engine itself
// (engine/include, engine/src) has no knowledge that Python exists.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "motorsim/BLDCMotor.h"
#include "motorsim/DCMotor.h"
#include "motorsim/Simulator.h"
#include "motorsim/Types.h"

namespace py = pybind11;
using namespace motorsim;

PYBIND11_MODULE(motorsim_py, m) {
    m.doc() = "Python bindings for the motorsim C++ simulation engine";

    py::class_<MotorState>(m, "MotorState")
        .def(py::init<>())
        .def_readwrite("time", &MotorState::time)
        .def_readwrite("voltage", &MotorState::voltage)
        .def_readwrite("current", &MotorState::current)
        .def_readwrite("omega", &MotorState::omega)
        .def_readwrite("rpm", &MotorState::rpm)
        .def_readwrite("torque", &MotorState::torque)
        .def_readwrite("load_torque", &MotorState::loadTorque)
        .def_readwrite("electrical_angle_deg", &MotorState::electricalAngleDeg)
        .def("__repr__", [](const MotorState& s) {
            return "<MotorState t=" + std::to_string(s.time) + "s rpm=" + std::to_string(s.rpm)
                   + " current=" + std::to_string(s.current) + "A torque=" + std::to_string(s.torque) + "Nm>";
        });

    py::class_<MotorParams>(m, "MotorParams")
        .def(py::init<>())
        .def_readwrite("resistance", &MotorParams::resistance)
        .def_readwrite("inductance", &MotorParams::inductance)
        .def_readwrite("torque_constant", &MotorParams::torqueConstant)
        .def_readwrite("back_emf_constant", &MotorParams::backEmfConstant)
        .def_readwrite("inertia", &MotorParams::inertia)
        .def_readwrite("viscous_friction", &MotorParams::viscousFriction)
        .def_readwrite("static_friction", &MotorParams::staticFriction)
        .def_readwrite("max_voltage", &MotorParams::maxVoltage);

    py::class_<BLDCParams, MotorParams>(m, "BLDCParams")
        .def(py::init<>())
        .def_readwrite("pole_pairs", &BLDCParams::polePairs)
        .def_readwrite("ripple_depth", &BLDCParams::rippleDepth);

    py::class_<MotorBase>(m, "MotorBase");

    py::class_<DCMotor, MotorBase>(m, "DCMotor")
        .def(py::init<const MotorParams&>(), py::arg("params"))
        .def("step", &DCMotor::step, py::arg("dt"), py::arg("voltage_command"), py::arg("load_torque"),
             py::return_value_policy::reference_internal)
        .def("reset", &DCMotor::reset)
        .def("state", &DCMotor::state, py::return_value_policy::reference_internal)
        .def_property_readonly("params", &DCMotor::params);

    py::class_<BLDCMotor, MotorBase>(m, "BLDCMotor")
        .def(py::init<const BLDCParams&>(), py::arg("params"))
        .def("step", &BLDCMotor::step, py::arg("dt"), py::arg("voltage_command"), py::arg("load_torque"),
             py::return_value_policy::reference_internal)
        .def("reset", &BLDCMotor::reset)
        .def("state", &BLDCMotor::state, py::return_value_policy::reference_internal)
        .def("commutation_sector", &BLDCMotor::commutationSector)
        .def_property_readonly("params", &BLDCMotor::params);

    py::class_<Simulator>(m, "Simulator")
        .def(py::init<MotorBase&>(), py::arg("motor"), py::keep_alive<1, 2>())
        .def("run", &Simulator::run, py::arg("voltage_profile"), py::arg("load_profile"), py::arg("dt"))
        .def("run_constant", &Simulator::runConstant, py::arg("voltage"), py::arg("load_torque"),
             py::arg("duration"), py::arg("dt"));
}
