# Contributing

Bug reports, physics corrections, new motor models, and new load laws are all
welcome.

## Getting set up

There's no build step and no third-party Python packages needed:

```
cd python
python -m motorsim_server --open-browser
```

That runs on the bundled Python engine. To build the faster C++ core, see the
build section of the README. If you move the project folder, delete `build/` and
run CMake again, because the build tree stores absolute paths.

## Running the tests

```
cd python
pip install pytest
pytest ../tests/python
```

Everything should pass before you open a pull request. The suite runs without the
compiled extension, so you don't need a C++ toolchain to work on the Python or
web layers.

## The one architectural rule

Physics lives in the engine. Everything above it drives and presents.

The C++ core in `engine/` knows nothing about Python, files, or the browser. The
Python layer decides what to simulate and records the result. The browser draws
whatever telemetry it receives and never computes motor physics itself. Please
keep that separation. It's what lets the engine be reused and the front end be
replaced.

`MotorBase` is the extension point for new motor types. Implement
`step(dt, voltage, loadTorque) -> MotorState` and the CLI, server, and front end
keep working.

## Physics changes

Simulation code is easy to break in ways that still look plausible on a chart, so:

* Add a test that pins the behaviour at a known operating point: a steady state,
  an analytic limit, or a conservation law. `test_loads.py` and
  `test_environment.py` have examples.
* Say why a constant or model form is what it is. The existing models are
  deliberately simplified and documented as such. New ones should be equally
  clear about their assumptions.
* If a change alters existing results, say so instead of letting it ride as an
  incidental diff.

## Style

Match the surrounding code. It uses clear names, SI units throughout, and
comments that explain why rather than restate the code. No formatter is enforced.

## Security

Please don't file vulnerabilities as public issues. See `SECURITY.md`.

## License

Contributions are accepted under the MIT License that covers the project.
