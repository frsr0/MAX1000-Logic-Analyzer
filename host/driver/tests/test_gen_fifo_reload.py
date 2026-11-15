"""The generator FIFO appends: a reload must start from an empty FIFO.

Signal_Gen.vhd only clears the 256-byte FIFO on CMD_GEN_STOP / abort and drops
what does not fit, so loading a second waveform while a repeating one is stored
plays [old][head of new] as one loop. For the 200-byte debug wave that makes a
1,024-symbol loop that ends 24 symbols into a half period: one run 24 symbols
(24 us) too long per loop, at any sample rate.
"""
from unittest.mock import MagicMock

from driver.ols_spi_device import OLSDeviceSPI
from driver.spi_protocol import CMD_GEN_STOP


def _device():
    dev = OLSDeviceSPI()
    dev.spi = MagicMock()
    dev.pkt = MagicMock()
    dev.sys_clk = 100_000_000
    return dev


def _order(dev):
    """Names of the calls on the packet layer in order."""
    names = []
    for call in dev.pkt.mock_calls:
        if call[0] == "transaction" and call[1] and call[1][0] == CMD_GEN_STOP:
            names.append("STOP")
        elif call[0] == "load_gen_data":
            names.append("LOAD")
    return names


def test_send_raw_symbols_clears_the_fifo_before_loading():
    dev = _device()
    dev.start_gen = MagicMock()
    dev.send_raw_symbols([1, 0] * 10, symbol_rate=1_000_000, tx_pin=0,
                         scl_pin=25, repeat=True)
    assert _order(dev) == ["STOP", "LOAD"]
    dev.start_gen.assert_called_once()


def test_debug_wave_reload_never_appends_to_a_playing_pattern():
    dev = _device()
    dev.start_gen = MagicMock()
    dev.set_bitbang_pwm(True, 10_000.0)
    dev.set_bitbang_pwm(True)                      # what manage_debug_source does
    assert _order(dev) == ["STOP", "LOAD", "STOP", "LOAD"]
