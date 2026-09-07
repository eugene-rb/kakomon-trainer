"""Locations that differ between source and PyInstaller builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Directory containing read-only packaged application resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Per-user writable configuration and data directory."""
    if is_frozen():
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "KakomonTrainer"
    return resource_dir()


def default_data_root() -> Path:
    return config_dir() / "data" if is_frozen() else resource_dir() / "data"
