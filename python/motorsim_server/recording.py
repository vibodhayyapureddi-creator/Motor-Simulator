"""Recording service: buffer telemetry into named runs, export CSV.

Plan section 6. Runs live in memory so the front-end can overlay them on
the charts (compare mode); CSV export extends the batch CLI's column
format (cli.write_csv) with the interactive-only channels (temperature,
fault flags).

Frames are the downsampled telemetry frames the session broadcasts
(~60 Hz), which is also the honest thing to export: it's exactly what the
user saw.
"""
from __future__ import annotations

import io
import csv
import re
import threading
import time
from typing import Dict, List, Optional

# Superset of the batch CLI's CSV columns, same order up front.
CSV_FIELDS = [
    "t", "voltage", "current", "current_peak", "omega", "rpm", "torque",
    "load_torque", "elec_angle", "sector", "temperature",
    "p_in", "p_out", "efficiency",
    "overcurrent", "overheat", "stall", "sag",
]

_MAX_FRAMES_PER_RUN = 120_000   # ~30 min at 60 Hz; guardrail, not a feature
_MAX_RUNS = 24


def _safe_name(name: str) -> str:
    name = (name or "").strip() or f"run-{int(time.time())}"
    return re.sub(r"[^A-Za-z0-9 _\-\.]", "_", name)[:64]


class Run:
    def __init__(self, name: str, bench: str = "A"):
        self.name = name
        self.bench = bench          # which bench's frames belong in this run
        self.created_at = time.time()
        self.frames: List[dict] = []
        self.complete = False

    def summary(self) -> dict:
        duration = 0.0
        if len(self.frames) >= 2:
            duration = self.frames[-1]["t"] - self.frames[0]["t"]
        return {
            "name": self.name,
            "bench": self.bench,
            "frames": len(self.frames),
            "duration": round(duration, 3),
            "complete": self.complete,
        }


class Recorder:
    """Owns all runs. Thread-safe: the sim loop appends while HTTP reads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: Dict[str, Run] = {}
        self._active: Optional[Run] = None

    # -------------------------------------------------------------- recording

    def start(self, name: str, bench: str = "A") -> str:
        with self._lock:
            name = _safe_name(name)
            base, n = name, 2
            while name in self._runs:
                name = f"{base}-{n}"
                n += 1
            if len(self._runs) >= _MAX_RUNS:
                oldest = min(self._runs.values(), key=lambda r: r.created_at)
                if oldest is self._active:
                    raise ValueError("run limit reached")
                del self._runs[oldest.name]
            run = Run(name, bench)
            self._runs[name] = run
            self._active = run
            return name

    def stop(self) -> Optional[str]:
        with self._lock:
            if self._active is None:
                return None
            self._active.complete = True
            name = self._active.name
            self._active = None
            return name

    def append(self, frame: dict) -> None:
        """Called from every bench's sim loop; only the recorded bench lands."""
        with self._lock:
            run = self._active
            if run is None or frame.get("bench", "A") != run.bench:
                return
            if len(run.frames) >= _MAX_FRAMES_PER_RUN:
                run.complete = True
                self._active = None
                return
            run.frames.append(frame)

    @property
    def recording(self) -> Optional[str]:
        with self._lock:
            return self._active.name if self._active else None

    def recording_for(self, bench: str) -> Optional[str]:
        """Active run name if (and only if) it records the given bench."""
        with self._lock:
            if self._active is not None and self._active.bench == bench:
                return self._active.name
            return None

    # ----------------------------------------------------------------- access

    def list_runs(self) -> List[dict]:
        with self._lock:
            return [r.summary() for r in
                    sorted(self._runs.values(), key=lambda r: r.created_at)]

    def get_frames(self, name: str, max_points: int = 4000) -> Optional[List[dict]]:
        """Frames for chart overlay, decimated to a sane point count."""
        with self._lock:
            run = self._runs.get(name)
            if run is None:
                return None
            frames = list(run.frames)
        if len(frames) > max_points:
            stride = len(frames) / max_points
            frames = [frames[int(i * stride)] for i in range(max_points)]
        return frames

    def delete(self, name: str) -> bool:
        with self._lock:
            run = self._runs.pop(name, None)
            if run is self._active:
                self._active = None
            return run is not None

    def export_csv(self, name: str) -> Optional[bytes]:
        with self._lock:
            run = self._runs.get(name)
            if run is None:
                return None
            frames = list(run.frames)
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
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
        return buf.getvalue().encode("utf-8")
