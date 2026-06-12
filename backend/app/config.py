"""Backend configuration."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST_DIR = REPO_ROOT / "host"          # existing, known-working host code
DATA_DIR = Path(os.environ.get("MSA_DATA_DIR", REPO_ROOT / "data"))
SESSION_DIR = DATA_DIR / "sessions"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

HOST = os.environ.get("MSA_HOST", "0.0.0.0")
PORT = int(os.environ.get("MSA_PORT", "8000"))

APP_NAME = "MAX1000 Mixed-Signal Analyser"
APP_VERSION = "2.0.0"

# Raw windows larger than this are served from the LOD pyramid instead.
MAX_RAW_POINTS = 8192
# LOD pyramid: bin sizes are LOD_BASE * LOD_FACTOR**level.
LOD_BASE = 16
LOD_FACTOR = 4

# Default capture limits (mirrors existing hardware: 1M samples SDRAM, 1024 BRAM)
MAX_SAMPLES = 1_000_000
BRAM_SAMPLES = 1024

SESSION_DIR.mkdir(parents=True, exist_ok=True)
