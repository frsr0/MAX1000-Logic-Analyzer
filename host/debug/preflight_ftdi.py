"""Quick FTDI preflight for OLS hardware selection.

Prints each enumerated FTDI slot, whether it can be opened, and the metadata
needed to spot the real MPSSE channel before running longer captures.
"""
from __future__ import annotations

import argparse

import ftd2xx as ft


def _decode(value):
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def inspect_device(index: int) -> dict:
    info = {}
    opened = False
    handle = None
    try:
        handle = ft.open(index)
        opened = True
        info = handle.getDeviceInfo()
        try:
            bitmode = handle.getBitMode()
        except Exception as exc:  # pragma: no cover - hardware-only branch
            bitmode = f"ERR:{exc}"
        try:
            com_port = handle.getComPortNumber()
        except Exception as exc:  # pragma: no cover - hardware-only branch
            com_port = f"ERR:{exc}"
    except Exception as exc:
        return {
            "index": index,
            "opened": False,
            "error": str(exc),
        }
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    return {
        "index": index,
        "opened": opened,
        "description": _decode(info.get("description", b"")),
        "serial": _decode(info.get("serial", b"")),
        "id": info.get("id"),
        "type": info.get("type"),
        "flags": info.get("flags"),
        "com_port": com_port,
        "bitmode": bitmode,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="FTDI hardware preflight")
    parser.add_argument("--max-devices", type=int, default=8,
                        help="Maximum number of FTDI slots to probe")
    args = parser.parse_args()

    count = ft.createDeviceInfoList()
    print(f"enumerated={count}")
    if count == 0:
        return 1

    for index in range(min(count, args.max_devices)):
        row = inspect_device(index)
        if row["opened"]:
            print(
                f"[{index}] OPEN desc={row['description']!r} "
                f"serial={row['serial']!r} com={row['com_port']} "
                f"bitmode={row['bitmode']} id={row['id']} "
                f"flags={row['flags']} type={row['type']}"
            )
        else:
            print(f"[{index}] FAIL error={row['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
