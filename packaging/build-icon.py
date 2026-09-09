"""Regenerate every icon asset from packaging/icon.svg.

Run after editing icon.svg:

    python packaging/build-icon.py

Requires ImageMagick (with the RSVG delegate) on PATH and Pillow.
Outputs are committed to the repo:

    desktop/KakomonTrainer/Assets/app.ico   WPF exe + window icon
    app/static/favicon.ico                  browser tab icon
    app/static/icon.svg                     scalable favicon (copied from source)
    app/static/icon-192.png                 PWA / Android
    app/static/icon-512.png                 PWA / large
    app/static/apple-touch-icon.png         iOS home screen (180px)
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "packaging" / "icon.svg"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
FAVICON_SIZES = [16, 24, 32, 48]
PNG_TARGETS = {
    ROOT / "app" / "static" / "icon-192.png": 192,
    ROOT / "app" / "static" / "icon-512.png": 512,
    ROOT / "app" / "static" / "apple-touch-icon.png": 180,
}


def render(size: int, dst: Path) -> Path:
    subprocess.run(
        ["magick", "-background", "none", "-density", "512", str(SRC),
         "-resize", f"{size}x{size}", str(dst)],
        check=True,
    )
    return dst


def build_ico(sizes: list[int], dst: Path, tmp: Path) -> None:
    frames = [Image.open(render(s, tmp / f"f{s}.png")).convert("RGBA") for s in sizes]
    frames[-1].save(dst, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=frames[:-1])


def main() -> None:
    if not shutil.which("magick"):
        raise SystemExit("ImageMagick 'magick' not found on PATH.")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        build_ico(ICO_SIZES, ROOT / "desktop" / "KakomonTrainer" / "Assets" / "app.ico", tmp)
        build_ico(FAVICON_SIZES, ROOT / "app" / "static" / "favicon.ico", tmp)
        for dst, size in PNG_TARGETS.items():
            render(size, dst)
    shutil.copyfile(SRC, ROOT / "app" / "static" / "icon.svg")
    print("Regenerated icon assets from", SRC.name)


if __name__ == "__main__":
    main()
