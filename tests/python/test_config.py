"""Smoke tests for scenario config loading. Run with: pytest tests/python"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from motorsim_app.config import load_config

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "python" / "configs"


def test_load_dc_config():
    cfg = load_config(CONFIGS_DIR / "dc_motor_basic.json")
    assert cfg.motor_type == "dc"
    assert cfg.params.resistance == 1.2
    assert len(cfg.segments) == 3
    assert cfg.total_duration == 0.3 + 0.3 + 0.2


def test_load_bldc_config():
    cfg = load_config(CONFIGS_DIR / "bldc_motor_basic.json")
    assert cfg.motor_type == "bldc"
    assert cfg.params.pole_pairs == 7
    assert cfg.params.ripple_depth == 0.08
    assert len(cfg.segments) == 2
