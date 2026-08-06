"""Selects which simulation backend the control layer talks to.

Prefers the compiled C++ engine (`motorsim_py`, built from bindings/ via
CMake) since that's the real, performant simulation core this program is
built around. Falls back to the pure-Python mirror in fallback_engine.py
if the extension hasn't been built yet, so the CLI/config/plotting layer
still works out of the box. See bindings/CMakeLists.txt and README.md for
how to build motorsim_py.
"""
from __future__ import annotations

BACKEND_NAME: str
DCMotor = None
BLDCMotor = None
MotorParams = None
BLDCParams = None
Simulator = None

try:
    import motorsim_py as _backend

    BACKEND_NAME = "cpp (motorsim_py)"
    DCMotor = _backend.DCMotor
    BLDCMotor = _backend.BLDCMotor
    MotorParams = _backend.MotorParams
    BLDCParams = _backend.BLDCParams
    Simulator = _backend.Simulator
except ImportError:
    from . import fallback_engine as _backend

    BACKEND_NAME = "python fallback (motorsim_py extension not built)"
    DCMotor = _backend.DCMotor
    BLDCMotor = _backend.BLDCMotor
    MotorParams = _backend.MotorParams
    BLDCParams = _backend.BLDCParams
    Simulator = _backend.Simulator


def is_using_cpp_backend() -> bool:
    return BACKEND_NAME.startswith("cpp")
