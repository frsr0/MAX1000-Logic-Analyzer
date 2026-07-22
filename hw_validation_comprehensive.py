#!/usr/bin/env python3
"""Compatibility wrapper for the current hardware validation suite.

The old root-level comprehensive validator predated the newer host/app
hardware API and had drifted into stale method names. Keep the entry point
around, but delegate straight to the maintained suite so callers always get
the current hardware checks.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOST_DIR = ROOT / "host"
if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))

from app.hw_validation import main as hw_validation_main  # noqa: E402


def main() -> int:
    os.chdir(HOST_DIR)
    return hw_validation_main()


if __name__ == "__main__":
    raise SystemExit(main())
