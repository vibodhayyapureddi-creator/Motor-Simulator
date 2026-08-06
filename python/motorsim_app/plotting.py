"""Telemetry plotting for the control layer.

Kept separate from cli.py so a future interactive GUI can reuse the same
plotting logic (e.g. embedding these axes in a live-updating canvas)
instead of only being usable from the batch CLI.
"""
from __future__ import annotations

from typing import List, Sequence


def plot_results(states: Sequence, title: str, out_path: str) -> None:
    """Render RPM, torque, and current vs. time to a PNG file.

    `states` is any sequence of objects with .time, .rpm, .torque, .current,
    .voltage attributes (both the compiled MotorState and the fallback
    engine's MotorState satisfy this).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = [s.time for s in states]
    rpm = [s.rpm for s in states]
    torque = [s.torque for s in states]
    current = [s.current for s in states]
    voltage = [s.voltage for s in states]

    fig, axes = plt.subplots(4, 1, figsize=(9, 10), sharex=True)
    fig.suptitle(title)

    axes[0].plot(t, rpm, color="#2563eb")
    axes[0].set_ylabel("Speed (RPM)")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(t, torque, color="#16a34a")
    axes[1].set_ylabel("Torque (N*m)")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(t, current, color="#dc2626")
    axes[2].set_ylabel("Current (A)")
    axes[2].grid(True, alpha=0.3)

    axes[3].plot(t, voltage, color="#7c3aed")
    axes[3].set_ylabel("Voltage (V)")
    axes[3].set_xlabel("Time (s)")
    axes[3].grid(True, alpha=0.3)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
