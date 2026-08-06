"""Optional live hardware-in-the-loop bridge (serial telemetry reader).

Streams telemetry lines from a microcontroller/ESC over a COM port and
keeps them as a rolling run named "hardware-live", which the front-end
overlays via the existing compare-mode charts - the real motor next to
its simulated twin.

The server itself stays standard-library only: pyserial is imported
lazily and the bridge simply reports "pyserial not installed" if it's
missing. Wire format, one line per sample (see
tools/hil_arduino_example/hil_arduino_example.ino):

    JSON object:   {"rpm": 1200, "current": 0.8, "voltage": 11.7}
    or key=value:  rpm=1200,current=0.8,voltage=11.7

A "t" field (seconds) is optional; if absent, samples are stamped with
the bridge's own elapsed clock. Unknown keys are ignored.
"""
from __future__ import annotations

import json
import threading
import time
from typing import List, Optional

_KNOWN_FIELDS = ("t", "rpm", "omega", "current", "current_peak", "torque",
                 "load_torque", "voltage", "temperature", "p_in", "p_out",
                 "efficiency")
_MAX_FRAMES = 36000   # ~10 min at 60 Hz


def parse_line(line: str) -> Optional[dict]:
    """One telemetry line -> partial frame dict, or None if unusable."""
    line = line.strip()
    if not line:
        return None
    frame = {}
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        items = data.items()
    else:
        items = []
        for part in line.split(","):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            items.append((key.strip(), value.strip()))
    for key, value in items:
        key = str(key).lower()
        if key not in _KNOWN_FIELDS:
            continue
        try:
            frame[key] = float(value)
        except (TypeError, ValueError):
            continue
    return frame or None


class HardwareBridge:
    """Reads telemetry lines off a serial port on a daemon thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._serial = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._t0 = 0.0
        self.frames: List[dict] = []
        self.port: Optional[str] = None
        self.error: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._serial is not None

    def connect(self, port: str, baud: int = 115200) -> None:
        try:
            import serial   # optional dependency, imported lazily
        except ImportError:
            raise ValueError(
                "pyserial is not installed - run: pip install pyserial")
        self.disconnect()
        try:
            self._serial = serial.Serial(port, baud, timeout=1.0)
        except Exception as exc:
            self._serial = None
            raise ValueError(f"could not open {port}: {exc}")
        self.port = port
        self.error = None
        self._t0 = time.monotonic()
        with self._lock:
            self.frames = []
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, daemon=True,
                                        name="hardware-bridge")
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.port = None

    def _reader(self) -> None:
        ser = self._serial
        while not self._stop.is_set() and ser is not None:
            try:
                raw = ser.readline()
            except Exception as exc:
                self.error = str(exc)
                break
            if not raw:
                continue
            frame = parse_line(raw.decode("utf-8", errors="replace"))
            if frame is None:
                continue
            frame.setdefault("t", round(time.monotonic() - self._t0, 4))
            with self._lock:
                self.frames.append(frame)
                if len(self.frames) > _MAX_FRAMES:
                    del self.frames[:len(self.frames) - _MAX_FRAMES]

    def get_frames(self) -> List[dict]:
        with self._lock:
            return list(self.frames)

    def status(self) -> dict:
        with self._lock:
            n = len(self.frames)
            latest = self.frames[-1] if self.frames else None
        return {"connected": self.connected, "port": self.port,
                "frames": n, "latest": latest, "error": self.error}

    def list_ports(self) -> list:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        return [{"port": p.device, "description": p.description}
                for p in list_ports.comports()]
