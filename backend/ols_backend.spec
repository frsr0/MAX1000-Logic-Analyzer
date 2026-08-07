"""PyInstaller spec for the backend bundled into the Windows desktop app."""
from importlib.util import find_spec
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
HOST = ROOT / "host"
FRONTEND_DIST = ROOT / "frontend" / "dist"

# collect_submodules runs in an isolated interpreter, so make the source
# packages visible there as well as in Analysis.pathex below.
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(HOST))

hiddenimports = []
for package in ("app", "driver", "uvicorn"):
    try:
        hiddenimports.extend(collect_submodules(package))
    except (ImportError, ModuleNotFoundError):
        pass

# ftd2xx is optional for mock mode. Include it when the build environment has
# it installed so the same executable can use real hardware after the FTDI
# D2XX driver is installed on Windows.
if find_spec("ftd2xx"):
    hiddenimports.extend(["ftd2xx", "ftd2xx._ftd2xx"])

datas = [
    (str(HOST), "host"),
]
if FRONTEND_DIST.exists():
    datas.append((str(FRONTEND_DIST), "frontend/dist"))
if (ROOT / "data" / "mil").exists():
    datas.append((str(ROOT / "data" / "mil"), "data/mil"))

a = Analysis(
    [str(BACKEND / "run.py")],
    pathex=[str(BACKEND), str(ROOT), str(HOST)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ols-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
