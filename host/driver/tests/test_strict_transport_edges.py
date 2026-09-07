"""Transport boundary tests for chunking, drain, and script entrypoints."""
from __future__ import annotations

import runpy
import sys
from unittest.mock import MagicMock, Mock

import pytest

from driver.ols_spi_mpsse import OLS_SPI_MPSSE
from driver.ols_spi_pyftdi import SpiController


def test_mpsse_simple_capture_chunks_reads_above_4096(monkeypatch):
    import driver.ols_spi_mpsse as module
    analyser = OLS_SPI_MPSSE.__new__(OLS_SPI_MPSSE)
    analyser.reset = Mock()
    analyser.short_cmd = Mock()
    analyser.long_cmd = Mock()
    analyser.xfer = Mock(side_effect=lambda payload: b"\x55" * len(payload))
    monkeypatch.setattr(module.time, "sleep", Mock())
    result = analyser.capture_simple(samples=1_025, rate_hz=1_000_000)
    assert len(result) == 4_100
    assert [len(call.args[0]) for call in analyser.xfer.call_args_list] == [4_096, 4]


def test_pyftdi_configure_drains_stale_rx_bytes(monkeypatch):
    import driver.ols_spi_pyftdi as module
    device = MagicMock()
    device.getQueueStatus.side_effect = [3, 0]
    module.ft.open.return_value = device
    controller = SpiController(channel=2)
    assert controller.configure() is controller
    module.ft.open.assert_called_with(2)
    device.read.assert_called_once_with(3)


def test_pyftdi_module_entrypoint_runs_probe_and_reports_failure(monkeypatch, capsys):
    import driver.ols_spi_pyftdi as module
    device = MagicMock()
    device.getQueueStatus.return_value = 0
    module.ft.open.return_value = device
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("driver.ols_spi_pyftdi", run_name="__main__")
    output = capsys.readouterr().out
    assert "CMD_ID:" in output and "FAIL" in output
