"""
motorsim_app - the Python control / interface layer for the motor
simulation program.

This package owns everything the C++ engine does NOT: reading scenario
configuration, driving the simulation loop with a chosen input profile,
collecting telemetry, and presenting results (CSV export, plots, and later
a live interactive dashboard). It talks to the simulation core only through
`engine_bridge`, which hides whether the compiled C++/pybind11 engine
(`motorsim_py`) is available or the pure-Python fallback engine is being
used instead.
"""
