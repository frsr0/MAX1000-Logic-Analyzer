"""Safe, optional serial-port and FTDI inspection helpers.

The MAX1000 USB device has two FTDI interfaces: Channel A is reserved for
JTAG/programming and Channel B is the analyser's MPSSE/SPI transport.  Neither
interface should be reprogrammed into a VCP merely to manufacture a COM port;
that would break the existing hardware paths.  Extra host COM ports require a
separate virtual-COM driver and protocol bridge, which this module reports as
an explicit capability rather than pretending an EEPROM write provides it.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value) if value is not None else ""


def list_serial_ports() -> dict:
    """Return OS-visible serial ports, without opening or changing them."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return {"available": False, "error": "pyserial is not installed", "ports": []}

    ports = []
    for info in list_ports.comports():
        ports.append({
            "device": _text(info.device),
            "description": _text(info.description),
            "manufacturer": _text(info.manufacturer),
            "product": _text(info.product),
            "serial_number": _text(info.serial_number),
            "vid": info.vid,
            "pid": info.pid,
            "location": _text(info.location),
            "interface": _text(info.interface),
            "hwid": _text(info.hwid),
        })
    return {"available": True, "ports": ports}


def _ftdi_indexes(ft: Any) -> list[int]:
    """Handle the two return shapes used by ftd2xx.listDevices()."""
    raw = ft.listDevices(0)
    if raw is None:
        return []
    if isinstance(raw, int):
        return list(range(max(0, raw)))
    return list(range(len(raw)))


def _ftdi_channel_hint(index: int, description: str) -> str:
    """Prefer explicit FTDI/board naming; do not assume global enumeration order."""
    text = description.upper()
    if any(token in text for token in ("CHANNEL B", " USB BLASTER B", "MPSSE", "SPI")):
        return "B/MPSSE"
    if any(token in text for token in ("CHANNEL A", " USB BLASTER A", "JTAG")):
        return "A/JTAG"
    return "unknown"


def list_ftdi_devices() -> dict:
    """Inspect FTDI endpoints through D2XX, without changing EEPROM state."""
    try:
        import ftd2xx as ft
    except ImportError:
        return {"available": False, "error": "ftd2xx is not installed", "devices": []}

    devices = []
    try:
        indexes = _ftdi_indexes(ft)
    except Exception as exc:
        return {"available": False, "error": f"FTDI enumeration failed: {exc}",
                "devices": []}

    for index in indexes:
        handle = None
        try:
            handle = ft.open(index)
            info = handle.getDeviceInfo() or {}
            try:
                com_port = handle.getComPortNumber()
            except Exception:
                com_port = None
            try:
                bit_mode = handle.getBitMode()
            except Exception:
                bit_mode = None
            devices.append({
                "index": index,
                "id": info.get("id"),
                "type": info.get("type"),
                "description": _text(info.get("description")),
                "serial_number": _text(info.get("serial")),
                "com_port": f"COM{com_port}" if com_port not in (None, 0, -1) else None,
                "bit_mode": bit_mode,
                "channel_hint": _ftdi_channel_hint(index, _text(info.get("description"))),
            })
        except Exception as exc:
            devices.append({"index": index, "error": str(exc),
                            "channel_hint": "unknown"})
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
    return {"available": True, "devices": devices}


def ftdi_interface_layout() -> dict:
    """Describe the board's fixed FTDI roles and COM-port limitation."""
    from .virtual_bridge import virtual_com_manager

    virtual = virtual_com_manager.status()
    return {
        "interfaces": [
            {
                "channel": "A",
                "role": "JTAG/programming",
                "transport": "FTDI D2XX/MPSSE JTAG",
                "com_port": False,
                "safe_to_repurpose": False,
            },
            {
                "channel": "B",
                "role": "MAX1000 analyser transport",
                "transport": "FTDI D2XX/MPSSE SPI",
                "com_port": False,
                "safe_to_repurpose": False,
            },
        ],
        "extra_hardware_com_ports": False,
        "virtual_com_ports": {
            "available": virtual["driver"]["available"],
            "driver": virtual["driver"],
            "tcp_bridge_available": True,
            "bridge": virtual,
            "reason": (
                "A host-only virtual-COM driver is optional. The app also exposes "
                "a localhost TCP software bridge using newline-delimited JSON; "
                "neither option changes the board's JTAG or MPSSE interfaces."
            ),
        },
    }
