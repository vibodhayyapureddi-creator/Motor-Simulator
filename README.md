# Motor Simulation Program

An electric motor simulator. The physics runs in C++. You can use it two ways:
an interactive 3D test bench in the browser, or a command line tool for scripted
runs.

## Run it

Double-click `start_app.bat`, or:

```
cd python
python -m motorsim_server --open-browser
```

Then open http://127.0.0.1:8765/

Nothing to install. The server only uses the Python standard library, and if the
C++ engine isn't built it falls back to a Python version with the same equations.

## Command line

```
cd python
python -m motorsim_app.cli --config configs/dc_motor_basic.json
```

The config lists the motor, its parameters, and a series of
`(duration, voltage, load_torque)` segments. You get a CSV, plus a plot if
matplotlib is installed. Examples are in `examples/sample_output/`.

## Features

Four motor types: brushed DC, BLDC, stepper, and AC induction. Drive them with a
throttle or PWM, and edit any parameter while they run.

Attach loads to the shaft (fan, pump, wheel, flywheel, brake) and watch them spin
in 3D. Break things with voltage sag, rotor jam, current limits, and overheating.

Also included: a PID controller with auto-tune, a battery model, two benches for
side-by-side comparison, recording and CSV export, an FFT view, and scripted
scenarios.

With `pip install pyserial` it can read telemetry from real hardware over a
serial port and overlay it on the simulation.

## Physics

Both core models use SI units and RK4 integration.

DC motor:

```
L*di/dt = V - i*R - Ke*w
J*dw/dt = Kt*i - B*w - T_static*sign(w) - T_load
```

The BLDC model is six-step commutation. Two of three phases conduct at a time, so
the equations reduce to the same form. Pass resistance and inductance as
line-to-line values. Torque ripple is approximated with a multiplier on the Ke/Kt
coupling. Set `ripple_depth` to 0 and it matches the DC model exactly.

These are simple models on purpose. To add a motor type, implement
`step(dt, voltage, loadTorque) -> MotorState` on `MotorBase`.

## Build the C++ engine

Needs CMake 3.15+ and a C++17 compiler.

```
cmake -S . -B build -DMOTORSIM_BUILD_DEMO=ON
cmake --build build --config Release
```

For the Python extension:

```
pip install pybind11
cmake -S . -B build -DMOTORSIM_BUILD_PYTHON_BINDINGS=ON
cmake --build build --config Release
```

That puts `motorsim_py.pyd` or `.so` in `python/`. On Windows,
`setup_windows.bat` installs the prerequisites and builds everything.

If you move the project folder, delete `build/` and run CMake again.

## Layout

```
engine/     C++ core
bindings/   pybind11 module
python/     CLI, web server, simulation logic
web/        Front end, plain ES modules, no build step
tests/      Test suite
docs/       Protocol and design notes
```

## Tests

```
cd python
pip install pytest
pytest ../tests/python
```

## Security

This is a local tool. It listens on 127.0.0.1 and has no login.

Because a web page you have open can still reach localhost, the server checks the
Origin and Host headers on every request. That stops other sites from connecting
to it. Details are in `SECURITY.md`.

Don't run it with `--host 0.0.0.0` on an untrusted network. There's no
authentication, so anyone who can reach the port can control it.

## More

`docs/PROTOCOL.md` for the wire format, `CONTRIBUTING.md` to contribute,
`THIRD_PARTY.md` for bundled dependencies.

MIT licensed, see `LICENSE`.
