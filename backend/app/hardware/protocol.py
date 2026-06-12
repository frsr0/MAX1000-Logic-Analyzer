"""Bridge to the existing, known-working host driver package.

The real hardware protocol (CRC16-framed SPI packets, register map, command
opcodes) lives in host/driver/ and is NOT reimplemented here. This module
makes that package importable and re-exports the pieces the adapter needs.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import HOST_DIR

if str(HOST_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_DIR))


def import_host_driver():
    """Import the existing driver modules lazily — ftd2xx is only required
    when real hardware is actually used."""
    from driver import ols_spi_device, spi_protocol  # noqa: import from host/
    return ols_spi_device, spi_protocol


def import_host_decoders():
    """The legacy pure-function decoders (kept as reference implementation)."""
    from app import gui_decoders  # noqa: import from host/
    return gui_decoders
