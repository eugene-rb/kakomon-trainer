"""GitHub Releases update client for the Windows installer build."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.runtime import is_frozen
from app.version import APP_SLUG, __version__

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


@dataclass(frozen=True)
class Release:
    version: str
    asset_url: str
    asset_name: str
    notes: str = ""


def _parts(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(value.strip())
    return tuple(map(int, match.groups())) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    candidate_parts, current_parts = _parts(candidate), _parts(current)
    return bool(candidate_parts and current_parts and candidate_parts > current_parts)


def latest_release(repository: str, timeout: float = 5.0) -> Release | None:
    """Fetch a newer matching installer from a public GitHub repository."""
    repository = repository.strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        return None
    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_SLUG}/{__version__}"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310: fixed GitHub API host
            payload: dict[str, Any] = json.load(response)
    except Exception:
        return None
    version = str(payload.get("tag_name", "")).lstrip("v")
    if not is_newer(version):
        return None
    suffix = "-Setup.exe"
    for asset in payload.get("assets", []):
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if name.endswith(suffix) and url.startswith("https://github.com/"):
            return Release(version, url, name, str(payload.get("body", "")))
    return None


def download_and_install(release: Release, shutdown: Callable[[], None]) -> None:
    """Download the signed installer, then close this process so Inno Setup can replace it."""
    if not is_frozen():
        raise RuntimeError("自動更新はインストール版でのみ利用できます。")
    target = Path(tempfile.gettempdir()) / release.asset_name
    request = Request(release.asset_url, headers={"User-Agent": f"{APP_SLUG}/{__version__}"})
    try:
        with urlopen(request, timeout=30) as response, target.open("wb") as output:  # nosec B310
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"更新ファイルをダウンロードできませんでした: {exc}") from exc

    # Delay just enough for the HTTP response to be returned before shutting down uvicorn.
    def run() -> None:
        time.sleep(1)
        subprocess.Popen([str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS"], close_fds=True)
        shutdown()

    threading.Thread(target=run, daemon=True).start()
