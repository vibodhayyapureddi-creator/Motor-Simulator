"""Headless batch runner: presets x scenarios -> CSV + summary, no browser.

Runs the exact same SimulationSession the interactive app uses, but
synchronously (no real-time pacing, no web server) - a 20-second scenario
finishes in well under a second. Useful for overnight sweeps, regression
checks against the physics, and generating data offline.

Usage:
  python -m motorsim_server.batch --list
  python -m motorsim_server.batch --preset builtin:hobby_gearmotor_12v \
      --scenario "Spin-up, overload, stall" --duration 15 --out out\\run.csv
  python -m motorsim_server.batch --all --duration 15 --out-dir out\\batch
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Optional

from .app import _load_scenarios
from .presets_service import PresetService
from .recording import CSV_FIELDS, Recorder
from .session import SimulationSession

FRAME_DT = 1.0 / 60.0   # same telemetry cadence as the live app


def run_headless(preset: Optional[dict], scenario: Optional[dict],
                 duration: float) -> List[dict]:
    """Advance one bench for `duration` sim-seconds; return telemetry frames."""
    session = SimulationSession(Recorder(), "A")
    if preset is not None:
        session.apply_preset(preset)
    if scenario is not None:
        session.handle_command({"type": "scenario", "action": "start",
                                "name": scenario.get("name", "scenario"),
                                "steps": scenario.get("steps", [])})
    else:
        # no script: just run at the preset's drive voltage
        session.handle_command({"type": "set_running", "on": True})

    frames: List[dict] = []
    sim_t = 0.0
    while sim_t < duration:
        while session._commands:                    # tick boundary
            session._commands.popleft()()
        if session._scenario is not None:           # fire due script steps
            while (session._scenario is not None
                   and session._scenario_idx < len(session._scenario)
                   and session._scenario[session._scenario_idx][0]
                       <= session._scenario_elapsed):
                _, _, closure = session._scenario[session._scenario_idx]
                session._scenario_idx += 1
                closure()
            if (session._scenario is not None
                    and session._scenario_idx >= len(session._scenario)):
                session._scenario = None
        remaining = FRAME_DT
        while remaining > 1e-9:                     # defeat the RT deadline cap
            stepped = session._advance(remaining)
            if stepped <= 0.0:
                break
            remaining -= stepped
            if session._scenario is not None:
                session._scenario_elapsed += stepped
        sim_t += FRAME_DT
        frames.append(dict(session._make_frame()))
    return frames


def summarize(frames: List[dict]) -> dict:
    if not frames:
        return {}
    fields = ("rpm", "current", "torque", "temperature", "p_in", "efficiency")
    out = {}
    for f in fields:
        vals = [fr[f] for fr in frames if isinstance(fr.get(f), (int, float))]
        if vals:
            out[f] = {"final": vals[-1], "max": max(vals), "min": min(vals)}
    flags = {}
    for fr in frames:
        for key, val in (fr.get("flags") or {}).items():
            if val:
                flags[key] = flags.get(key, 0) + 1
    out["flag_frames"] = flags
    return out


def write_csv(frames: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_FIELDS)
        for f in frames:
            flags = f.get("flags", {})
            writer.writerow([
                f.get("t", ""), f.get("voltage", ""), f.get("current", ""),
                f.get("current_peak", ""), f.get("omega", ""), f.get("rpm", ""),
                f.get("torque", ""), f.get("load_torque", ""),
                f.get("elec_angle", ""), f.get("sector", ""),
                f.get("temperature", ""),
                f.get("p_in", ""), f.get("p_out", ""), f.get("efficiency", ""),
                int(bool(flags.get("overcurrent"))), int(bool(flags.get("overheat"))),
                int(bool(flags.get("stall"))), int(bool(flags.get("sag"))),
            ])


def _fmt_summary(name: str, summary: dict) -> str:
    parts = [name]
    rpm = summary.get("rpm", {})
    cur = summary.get("current", {})
    tmp = summary.get("temperature", {})
    parts.append(f"rpm final {rpm.get('final', 0):.0f} (max {rpm.get('max', 0):.0f})")
    parts.append(f"I max {cur.get('max', 0):.2f} A")
    parts.append(f"T max {tmp.get('max', 0):.1f} C")
    flags = summary.get("flag_frames", {})
    if flags:
        parts.append("flags: " + ", ".join(f"{k}x{v}" for k, v in flags.items()))
    return " | ".join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Headless batch runner for the motor test bench")
    parser.add_argument("--preset", default="",
                        help="Preset id or name (default: bare DC motor).")
    parser.add_argument("--scenario", default="",
                        help="Built-in scenario name (default: constant drive).")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--out", default="", help="CSV output path.")
    parser.add_argument("--out-dir", default="output/batch",
                        help="Directory for --all outputs.")
    parser.add_argument("--all", action="store_true",
                        help="Run every built-in scenario and print a summary table.")
    parser.add_argument("--list", action="store_true",
                        help="List available presets and scenarios.")
    args = parser.parse_args(argv)

    presets = PresetService()
    scenarios = _load_scenarios()

    if args.list:
        print("Presets:")
        for p in presets.list():
            print(f"  {p['id']}  ({p['name']})")
        print("Scenarios:")
        for s in scenarios:
            print(f"  {s['name']}")
        return 0

    preset = presets.get(args.preset) if args.preset else None
    if args.preset and preset is None:
        print(f"no preset '{args.preset}'", file=sys.stderr)
        return 2

    if args.all:
        out_dir = Path(args.out_dir)
        for sc in scenarios:
            frames = run_headless(preset, sc, args.duration)
            safe = "".join(c if c.isalnum() else "_" for c in sc["name"])[:40]
            write_csv(frames, out_dir / f"{safe}.csv")
            print(_fmt_summary(sc["name"], summarize(frames)))
        print(f"CSV files in {out_dir}")
        return 0

    scenario = None
    if args.scenario:
        scenario = next((s for s in scenarios
                         if s["name"].lower() == args.scenario.lower()), None)
        if scenario is None:
            print(f"no scenario '{args.scenario}'", file=sys.stderr)
            return 2

    frames = run_headless(preset, scenario, args.duration)
    print(_fmt_summary(args.scenario or "constant drive", summarize(frames)))
    if args.out:
        write_csv(frames, Path(args.out))
        print(f"wrote {args.out} ({len(frames)} frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
