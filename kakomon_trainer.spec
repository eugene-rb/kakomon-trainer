# PyInstaller specification for the one-file Windows application.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
datas = [(str(ROOT / "app" / "static"), "app/static"), (str(ROOT / "app" / "prompts"), "app/prompts")]
datas += collect_data_files("qrcode")

a = Analysis([str(ROOT / "app" / "main.py")], pathex=[str(ROOT)], datas=datas, hiddenimports=["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto"],)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [], name="KakomonTrainer", console=False, onefile=True, icon=str(ROOT / "desktop" / "KakomonTrainer" / "Assets" / "app.ico"), version=str(ROOT / "packaging" / "version_info.txt"))
