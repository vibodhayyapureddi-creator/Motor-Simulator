# Motor Simulator

Developed by Vibodh Ayyapureddi.

Live at [motorsimulator.app](https://motorsimulator.app)

An electric motor test bench that runs in a browser. You pick a motor, set a
voltage, put a load on the shaft, and watch the current, torque, speed and
winding temperature respond.

The physics is a C++ engine stepping at 20 kHz. The browser doesn't calculate
anything, it just sends inputs and draws the telemetry that comes back.

## Motors

Four types are implemented: brushed DC, BLDC (six-step or idealised FOC), a
stepper, and an AC induction motor on a V/Hz drive.

They don't behave identically. The stepper detents and drops steps past
pull-out. The induction motor slips under load. The BLDC has torque ripple at
six times the electrical frequency, which you can see on the charts if you slow
time down.

There's also a PWM mode where you set duty cycle and switching frequency instead
of a plain voltage, and the current ripple that produces is visible.

Motor parameters can be edited while the motor is running. Change R or Kt or J
mid-spin and it keeps going with the new values rather than resetting.

## Loads

Six load models, each drawn as a 3D object coupled to the shaft:

- Fan or propeller, torque proportional to speed squared
- Pump, static head plus a quadratic term
- Wheel, with reflected inertia, rolling resistance and aero drag through a gear
  ratio
- Flywheel, mostly inertia and very little drag
- Constant or viscous brake

The wheel and flywheel add real rotating inertia. Engaging one conserves angular
momentum, so the motor visibly slows the instant it's attached, then recovers.

## Faults and limits

You can sag the supply voltage, jam the rotor, or drive the motor past its
current limit and watch the controller fold back its output voltage to protect
itself.

There's a lumped thermal model too. The winding heats from I²R losses and cools
toward ambient, and if you turn on resistance feedback, copper's temperature
coefficient raises R as it gets hot and the motor derates.

Short explanations pop up when these happen, so it's reasonably clear what
you're looking at.

## Control

A PID controller for speed, torque or position, with gains you can change while
it runs. There's a step test that reports overshoot, settling time and
steady-state error, and an auto-tune that identifies the plant from an open-loop
voltage step and works out PI gains using the IMC rule.

## Battery

Optional. Capacity, internal resistance, a discharge curve and state of charge.
With regenerative braking enabled, energy goes back into the pack rather than
being burned off.

## Two benches

A and B are separate simulations that run at the same time. You can show both
motors side by side in 3D and overlay their traces on the same charts, which is
useful for comparing two motors, or the same motor before and after a change.

## Instruments

Gauges for speed, current, torque, winding temperature, voltage and load. Click
any of them and it shows the equation behind it with the current numbers
substituted in.

The charts scroll over a selectable window and show min, max, mean and standard
deviation per channel. Any channel can be switched to an FFT view. Above the
gauges there's electrical power in, mechanical power out, losses and efficiency.

Time can be slowed to 0.02x or single-stepped, which you need for the inrush
spike and commutation ripple since both are over in milliseconds.

The 3D motor glows as the winding heats, throws sparks on overcurrent, and
lights up the active commutation sector so the electrical angle is visible.

## Recording and scripting

Runs can be named, recorded, overlaid, scrubbed through, and exported as CSV.

Scenarios are timed sequences of commands in JSON, editable in the app. Four
ship with it: spin-up to stall, sag ride-through, thermal derating, and PWM
ripple in slow motion. There's a macro recorder that turns whatever you just did
by hand into a scenario.

Each visitor gets their own simulation on a private URL. Sharing that URL lets
someone else watch the same bench.

## Real hardware

If you `pip install pyserial`, the app can read telemetry from a real motor over
a serial port and overlay it on the simulation. There's an Arduino sketch in
`tools/hil_arduino_example/` showing the expected line format.

## Presets

Seven motors with parameters taken from real spec sheets: a 12 V hobby
gearmotor, an 18 V cordless drill motor, a 3S drone BLDC, a 120 mm PC fan, a
36 V e-bike hub motor, a NEMA 17 stepper, and a 1 hp induction motor. You can
save your own.

## Running it locally

Double-click `start_app.bat`, or:

```
cd python
python -m motorsim_server --open-browser
```

Then open http://127.0.0.1:8765/

There's nothing to install. The server only uses the Python standard library,
and if the C++ extension isn't built it falls back to a Python implementation of
the same equations.

## Command line

For scripted runs:

```
cd python
python -m motorsim_app.cli --config configs/dc_motor_basic.json
```

The config file lists the motor, its parameters, and a series of
`(duration, voltage, load_torque)` segments. It writes a CSV of every sample,
and a plot if matplotlib is installed. There are examples in
`examples/sample_output/`.

## The physics

SI units, RK4 integration.

DC motor:

```
L*di/dt = V - i*R - Ke*w
J*dw/dt = Kt*i - B*w - T_static*sign(w) - T_load
```

The BLDC model is six-step commutation. Two of three phases conduct at once, so
it reduces to the same equations, which means resistance and inductance should
be given as line-to-line values. The torque ripple is approximated with a
multiplier on the Ke/Kt coupling rather than simulated properly. Set
`ripple_depth` to 0 and it's identical to the DC model.

These are simple lumped-parameter models and the headers say so. They produce
sensible curves without a full three-phase simulation. If you want to add
another motor type, implement `step(dt, voltage, loadTorque) -> MotorState` on
`MotorBase` and the rest of the stack works unchanged.

The load laws, thermal model and fault logic currently live in the Python layer
wrapped around the C++ core. The electromechanical integration is in C++.

## Building the C++ engine

CMake 3.15 or later and a C++17 compiler.

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

That drops `motorsim_py.pyd` or `.so` into `python/`. On Windows,
`setup_windows.bat` installs the prerequisites and builds it for you.

If you move the project folder, delete `build/` and re-run CMake. The build tree
stores absolute paths.

## Layout

```
engine/     C++ core, no dependencies
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

143 tests covering config parsing, the physics of each motor type and load law,
thermal equilibrium, current limiting, stall detection, sag recovery, command
validation, live parameter edits, the recorder, and a set of security tests that
drive a real server over a socket.

## Security

It's a local tool by default: bound to 127.0.0.1, no login.

A web page you have open can still make requests to localhost, though, so the
server checks the Origin and Host headers on every request. That blocks other
sites from talking to it and stops DNS rebinding. `SECURITY.md` has the details.

Don't run it with `--host 0.0.0.0` on a network you don't trust. There's no
authentication, so anyone who can reach the port can drive it.

## Other files

`docs/PROTOCOL.md` for the WebSocket and REST format, `CONTRIBUTING.md`,
`THIRD_PARTY.md` for the bundled dependencies.

MIT licensed, copyright Vibodh Ayyapureddi.
