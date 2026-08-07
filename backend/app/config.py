"""Backend configuration."""
import os
import sys
from pathlib import Path

def _runtime_root() -> Path:
    """Return the read-only application root for source and frozen builds.

    PyInstaller extracts one-file applications to ``sys._MEIPASS``. Keeping
    this lookup here means the backend can serve bundled frontend assets and
    load the bundled host driver without making the Electron launcher know
    about PyInstaller internals.
    """
    configured = os.environ.get("MSA_APP_ROOT")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _runtime_root()
HOST_DIR = Path(os.environ.get("MSA_HOST_DIR", REPO_ROOT / "host"))
DATA_DIR = Path(os.environ.get("MSA_DATA_DIR", REPO_ROOT / "data"))
SESSION_DIR = DATA_DIR / "sessions"
FRONTEND_DIST = Path(os.environ.get(
    "MSA_FRONTEND_DIST", REPO_ROOT / "frontend" / "dist"))

HOST = os.environ.get("MSA_HOST", "0.0.0.0")
PORT = int(os.environ.get("MSA_PORT", "8000"))

APP_NAME = "MAX1000 Mixed-Signal Analyser"
APP_VERSION = "3.0.0"

# Raw windows larger than this are served from the LOD pyramid instead.
MAX_RAW_POINTS = 8192
# LOD pyramid: bin sizes are LOD_BASE * LOD_FACTOR**level.
LOD_BASE = 16
LOD_FACTOR = 4

# Default capture limits (bitstream exposes the full 4 Mi 16-bit SDRAM words, 1024 BRAM)
MAX_SAMPLES = 4_194_304
BRAM_SAMPLES = 1024

SESSION_DIR.mkdir(parents=True, exist_ok=True)
