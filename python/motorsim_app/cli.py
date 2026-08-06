"""Batch/scripted simulation runner -- the CLI half of the control layer.

    python -m motorsim_app.cli --config configs/dc_motor_basic.json

Loads a scenario config (see config.py), builds the requested motor via the
active engine backend (engine_bridge.py -- C++ if built, Python fallback
otherwise), steps it through the configured voltage/load segments, and
writes the resulting telemetry to CSV and (optionally) a plot.

This module intentionally does none of the physics itself -- it only wires
configuration to the engine and the engine's output to files. That keeps
the "interface/control layer vs. simulation core" boundary clean, and
means the same building blocks (engine_bridge + config) can be reused by a
future interactive GUI that drives the engine step-by-step instead of from
a pre-scripted profile.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import engine_bridge
from .config import MotorParamsConfig, ScenarioConfig, load_config


def _build_params(motor_type: str, cfg: MotorParamsConfig):
    if motor_type == "dc":
        params = engine_bridge.MotorParams()
    else:
        params = engine_bridge.BLDCParams()
        params.pole_pairs = cfg.pole_pairs
        params.ripple_depth = cfg.ripple_depth if cfg.ripple_depth is not None else 0.05

    params.resistance = cfg.resistance
    params.inductance = cfg.inductance
    params.torque_constant = cfg.torque_constant
    params.back_emf_constant = cfg.back_emf_constant
    params.inertia = cfg.inertia
    params.viscous_friction = cfg.viscous_friction
    params.static_friction = cfg.static_friction
    params.max_voltage = cfg.max_voltage
    return params


def _build_motor(scenario: ScenarioConfig):
    params = _build_params(scenario.motor_type, scenario.params)
    if scenario.motor_type == "dc":
        return engine_bridge.DCMotor(params)
    return engine_bridge.BLDCMotor(params)


def run_scenario(scenario: ScenarioConfig):
    """Runs a scenario end-to-end and returns the list of MotorState samples."""
    motor = _build_motor(scenario)
    sim = engine_bridge.Simulator(motor)

    all_states = []
    for seg in scenario.segments:
        states = sim.run_constant(seg.voltage, seg.load_torque, seg.duration, scenario.dt)
        all_states.extend(states)
    return all_states


def write_csv(states, out_path: str) -> None:
    fieldnames = ["time", "voltage", "current", "omega", "rpm", "torque", "load_torque", "electrical_angle_deg"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames)
        for s in states:
            writer.writerow([getattr(s, name) for name in fieldnames])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run a motor simulation scenario (batch mode).")
    parser.add_argument("--config", required=True, help="Path to a scenario JSON config.")
    parser.add_argument("--no-plot", action="store_true", help="Skip plot generation even if configured.")
    parser.add_argument("--out-dir", default=None, help="Directory to write outputs to (default: config file's directory).")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    scenario = load_config(config_path)
    out_dir = Path(args.out_dir) if args.out_dir else config_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Scenario: {scenario.name}")
    print(f"Motor type: {scenario.motor_type.upper()}")
    print(f"Engine backend: {engine_bridge.BACKEND_NAME}")
    print(f"Segments: {len(scenario.segments)}  Total duration: {scenario.total_duration:.3f}s  dt: {scenario.dt}s")

    states = run_scenario(scenario)
    print(f"Simulated {len(states)} steps.")

    if scenario.output.csv:
        csv_path = out_dir / scenario.output.csv
        write_csv(states, str(csv_path))
        print(f"Wrote CSV: {csv_path}")

    if scenario.output.plot and not args.no_plot:
        try:
            from .plotting import plot_results
            plot_path = out_dir / scenario.output.plot
            plot_results(states, scenario.name, str(plot_path))
            print(f"Wrote plot: {plot_path}")
        except ImportError:
            print("matplotlib not installed -- skipping plot. (pip install matplotlib to enable)", file=sys.stderr)

    final = states[-1]
    print(f"Final state: {final.rpm:.1f} RPM, {final.current:.3f} A, {final.torque:.4f} N*m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
