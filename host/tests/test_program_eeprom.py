"""Behavioral tests for the FT2232H EEPROM programming utility."""

import runpy
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import program_eeprom


def _successful_driver():
    ee = SimpleNamespace(Version=4)
    device = MagicMock()
    device.handle = object()
    device.getDeviceInfo.return_value = {
        "description": b"Dual RS232-HS B",
        "serial": b"SERIAL-B",
        "type": 6,
        "id": 0x04036010,
    }
    device.eeRead.return_value = ee
    driver = types.ModuleType("ftd2xx")
    driver.listDevices = MagicMock(return_value=2)
    driver.open = MagicMock(return_value=device)
    driver.call_ft = MagicMock()
    native = types.ModuleType("ftd2xx._ftd2xx")
    native.FT_WriteEE = object()
    native.DWORD = lambda value: value
    native.WORD = lambda value: value
    return driver, native, device, ee


def _install_driver(monkeypatch, driver, native):
    monkeypatch.setitem(sys.modules, "ftd2xx", driver)
    monkeypatch.setitem(sys.modules, "ftd2xx._ftd2xx", native)
    monkeypatch.setattr(program_eeprom, "ft", driver)
    monkeypatch.setattr(program_eeprom.time, "sleep", lambda _: None)
    monkeypatch.setattr(program_eeprom.random, "randint", lambda lo, hi: 0x1234)


def test_programs_all_eeprom_fields_signatures_and_cycles_port(monkeypatch, capsys):
    driver, native, device, ee = _successful_driver()
    _install_driver(monkeypatch, driver, native)

    program_eeprom.main()

    assert ee.Manufacturer == b"OLS Project"
    assert ee.Description == b"OLS Logic Analyzer MPSSE"
    assert ee.SerialNumber == b"OLS_1234"
    assert ee.AIsVCP7 == 1 and ee.BIsVCP7 == 0
    assert ee.MaxPower == 500 and ee.BLDriveCurrent == 4
    device.eeProgram.assert_called_once_with(ee)
    assert [call.args[-1] for call in driver.call_ft.call_args_list] == [
        0x696C,
        0x746E,
        0x0004,
    ]
    device.cyclePort.assert_called_once()
    assert "EEPROM programmed successfully" in capsys.readouterr().out


def test_wait_loop_tolerates_probe_exception_then_finds_device(monkeypatch):
    driver, native, device, _ = _successful_driver()
    driver.listDevices.side_effect = [OSError("enumerating"), 1, 2]
    _install_driver(monkeypatch, driver, native)
    program_eeprom.main()
    assert driver.listDevices.call_count == 3


def test_missing_device_exits_after_bounded_wait(monkeypatch, capsys):
    driver, native, _, _ = _successful_driver()
    driver.listDevices.return_value = 0
    _install_driver(monkeypatch, driver, native)
    with pytest.raises(SystemExit) as exc:
        program_eeprom.main()
    assert exc.value.code == 1
    assert driver.listDevices.call_count == 30
    assert "Device not found" in capsys.readouterr().out


def test_eeprom_program_failure_is_reported(monkeypatch, capsys):
    driver, native, device, _ = _successful_driver()
    device.eeProgram.side_effect = OSError("write protected")
    _install_driver(monkeypatch, driver, native)
    with pytest.raises(SystemExit) as exc:
        program_eeprom.main()
    assert exc.value.code == 1
    assert "write protected" in capsys.readouterr().out


def test_signature_failure_is_reported(monkeypatch, capsys):
    driver, native, _, _ = _successful_driver()
    driver.call_ft.side_effect = OSError("signature write failed")
    _install_driver(monkeypatch, driver, native)
    with pytest.raises(SystemExit) as exc:
        program_eeprom.main()
    assert exc.value.code == 1
    assert "signature write failed" in capsys.readouterr().out


def test_module_entrypoint_runs_main(monkeypatch):
    driver, native, device, _ = _successful_driver()
    monkeypatch.setitem(sys.modules, "ftd2xx", driver)
    monkeypatch.setitem(sys.modules, "ftd2xx._ftd2xx", native)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("random.randint", lambda lo, hi: 1)
    runpy.run_module("app.program_eeprom", run_name="__main__")
    device.eeProgram.assert_called_once()
