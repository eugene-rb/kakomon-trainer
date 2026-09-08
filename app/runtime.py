"""Locations that differ between source and PyInstaller builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO


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


def log_path() -> Path:
    """Destination for stdout/stderr of the windowed (console-less) build."""
    return config_dir() / "logs" / "app.log"


def _open_fallback_stream() -> IO[str] | None:
    """Return a writable text stream, or ``None`` if even the null device fails."""
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        try:
            return open(os.devnull, "w", encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - 書き込み先が一切ない環境
            return None


def bind_missing_std_streams() -> None:
    """GUI ビルド（``console=False``）は stdout/stderr を持たない。

    uvicorn のログ設定は ``sys.stdout.isatty()`` を呼ぶため、None のままだと
    起動時に ``Unable to configure formatter 'default'`` で落ちる。実体のある
    ストリームへ差し替えて、ログもファイルに残す。インポート時に呼ばれるので
    この関数は決して例外を投げない。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = _open_fallback_stream()
    if stream is None:
        return
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
