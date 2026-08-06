"""Preset library: built-in scenario presets + user-saved ones.

Built-ins ship in motorsim_server/presets/ (the starter set);
user saves land in presets/user/ so they never collide with shipped files.
A preset extends the batch scenario JSON's motor description with load,
limits, thermal, and drive fields (see docs/PROTOCOL.md).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

_BUILTIN_DIR = Path(__file__).parent / "presets"
_USER_DIR = _BUILTIN_DIR / "user"

# Saving is an unauthenticated local endpoint, so it gets explicit ceilings
# rather than trusting the caller: a bounded library of bounded files.
_MAX_USER_PRESETS = 200
_MAX_PRESET_BYTES = 64 * 1024


def _slug(name: str) -> str:
    """Filename-safe stem. Strips every separator, so it cannot traverse."""
    cleaned = re.sub(r"[^a-z0-9_\-]+", "_", (name or "").lower()).strip("_")[:48]
    return cleaned or "preset"


def _load_dir(directory: Path, source: str) -> List[dict]:
    presets = []
    if not directory.is_dir():
        return presets
    for path in sorted(directory.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("name", path.stem)
            data["id"] = f"{source}:{path.stem}"
            data["source"] = source
            presets.append(data)
        except (OSError, json.JSONDecodeError):
            continue  # a broken preset file shouldn't hide the others
    return presets


class PresetService:
    def list(self) -> List[dict]:
        return _load_dir(_BUILTIN_DIR, "builtin") + _load_dir(_USER_DIR, "user")

    def get(self, preset_id: str) -> Optional[dict]:
        for preset in self.list():
            if preset["id"] == preset_id or preset["name"] == preset_id:
                return preset
        return None

    def save(self, preset: Dict) -> dict:
        """Persist a user preset; returns the stored copy (with id)."""
        if not isinstance(preset, dict) or "params" not in preset:
            raise ValueError("preset must be an object with a 'params' field")
        name = str(preset.get("name") or "custom preset")[:120]
        slug = _slug(name)
        stored = {k: v for k, v in preset.items() if k not in ("id", "source")}
        stored["name"] = name

        try:
            body = json.dumps(stored, indent=2)
        except (TypeError, ValueError):
            raise ValueError("preset is not JSON-serialisable") from None
        if len(body.encode("utf-8")) > _MAX_PRESET_BYTES:
            raise ValueError(
                f"preset too large (limit {_MAX_PRESET_BYTES // 1024} KiB)")

        _USER_DIR.mkdir(parents=True, exist_ok=True)
        path = _USER_DIR / f"{slug}.json"
        # Overwriting an existing preset of the same name is fine; only a
        # genuinely new file counts against the library ceiling.
        if not path.exists():
            existing = sum(1 for _ in _USER_DIR.glob("*.json"))
            if existing >= _MAX_USER_PRESETS:
                raise ValueError(
                    f"preset library full ({_MAX_USER_PRESETS}); delete some first")

        with path.open("w", encoding="utf-8") as f:
            f.write(body)
        stored["id"] = f"user:{slug}"
        stored["source"] = "user"
        return stored
