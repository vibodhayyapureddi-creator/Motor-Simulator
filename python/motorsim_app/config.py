"""Scenario configuration loading for the motor simulation control layer.

A scenario file is plain JSON describing: which motor model to use, its
physical parameters, the simulation step size, and a list of time-ordered
"segments" (constant voltage + load torque held for a duration) that make
up a scripted/batch run. See python/configs/*.json for examples.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class MotorParamsConfig:
    resistance: float
    inductance: float
    torque_constant: float
    back_emf_constant: float
    inertia: float
    viscous_friction: float = 0.0
    static_friction: float = 0.0
    max_voltage: float = 24.0
    # BLDC-only fields; ignored for motor_type == "dc".
    pole_pairs: Optional[int] = None
    ripple_depth: Optional[float] = None


@dataclass
class SegmentConfig:
    duration: float       # seconds
    voltage: float        # volts, held constant for this segment
    load_torque: float    # N*m, held constant for this segment


@dataclass
class OutputConfig:
    csv: Optional[str] = None
    plot: Optional[str] = None


@dataclass
class ScenarioConfig:
    name: str
    motor_type: str                 # "dc" or "bldc"
    params: MotorParamsConfig
    dt: float
    segments: List[SegmentConfig] = field(default_factory=list)
    output: OutputConfig = field(default_factory=OutputConfig)

    @property
    def total_duration(self) -> float:
        return sum(seg.duration for seg in self.segments)


def load_config(path: str | Path) -> ScenarioConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    motor_type = raw["motor_type"].lower().strip()
    if motor_type not in ("dc", "bldc"):
        raise ValueError(f"Unknown motor_type '{motor_type}' in {path} (expected 'dc' or 'bldc')")

    params_raw = raw["params"]
    params = MotorParamsConfig(
        resistance=params_raw["resistance"],
        inductance=params_raw["inductance"],
        torque_constant=params_raw["torque_constant"],
        back_emf_constant=params_raw["back_emf_constant"],
        inertia=params_raw["inertia"],
        viscous_friction=params_raw.get("viscous_friction", 0.0),
        static_friction=params_raw.get("static_friction", 0.0),
        max_voltage=params_raw.get("max_voltage", 24.0),
        pole_pairs=params_raw.get("pole_pairs"),
        ripple_depth=params_raw.get("ripple_depth"),
    )

    if motor_type == "bldc" and params.pole_pairs is None:
        raise ValueError(f"{path}: motor_type 'bldc' requires params.pole_pairs")

    segments = [
        SegmentConfig(duration=seg["duration"], voltage=seg["voltage"], load_torque=seg["load_torque"])
        for seg in raw.get("segments", [])
    ]
    if not segments:
        raise ValueError(f"{path}: scenario must define at least one segment")

    output_raw = raw.get("output", {})
    output = OutputConfig(csv=output_raw.get("csv"), plot=output_raw.get("plot"))

    return ScenarioConfig(
        name=raw.get("name", path.stem),
        motor_type=motor_type,
        params=params,
        dt=raw["dt"],
        segments=segments,
        output=output,
    )
