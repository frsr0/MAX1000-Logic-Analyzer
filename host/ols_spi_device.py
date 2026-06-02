"""
SPI-based OLS device backend — drop-in replacement for UART OLSDevice.
Wraps ols_spi.py with the same interface the GUI expects.
"""
import time
import struct
import threading
from ols_spi import OLS as OLS_SPI

# Reuse the same command constants from ols_spi
CMD_RESET       = 0x00
CMD_ARM         = 0x01
CMD_ARM2        = 0x02
CMD_SPI_STATUS  = 0x03
CMD_METADATA    = 0x04
CMD_XON         = 0x11
CMD_XOFF        = 0x13

class OLSDeviceSPI:
    """SPI backend device — implements the same capture/rolling interface as OLSDevice."""

    def __init__(self, sys_clk_hz=48000000):
        self.sys_clk = sys_clk_hz
        self._stride = 4
        self._raw_flags = 0
        self.spi = None

    def open(self):
        self.spi = OLS_SPI(speed_hz=30000000)
        self.spi.open()

    def close(self):
        if self.spi:
            self.spi.close()
            self.spi = None

    def reset(self):
        if not self.spi:
            return
        self.spi.reset()
        time.sleep(0.02)
        self.spi.flush()

    def get_metadata(self):
        """Return 50-byte metadata block (same format as UART backend)."""
        if not self.spi:
            return b''
        # CMD_METADATA (0x04) in SPI mode returns the OLS version string
        r = self.spi.tx(CMD_METADATA)
        # Pad to 50 bytes like the UART backend
        return bytes(r) + b'\x00' * (50 - len(r))

    def raw_mode(self, enable=True):
        if enable:
            self._stride = 1
        else:
            self._stride = 4

    def fast_mode(self, enable=True):
        if self.spi:
            self.spi.set_fast_mode(enable)

    def rolling_capture(self, rate_hz, chunk_nsamp, buffer_nsamp,
                         stop_evt, progress_cb=None, gen_data=None, gen_baud=115200,
                         gen_tx_pin=3, full_out=None, use_continuous=True):
        """Generator: continuous rolling capture via SPI.

        Yields (buf, samples_so_far, total_samples) chunks for live waveform.
        """
        if not self.spi:
            return

        self.spi.reset()
        self.spi.flush()

        # Configure
        div = max(0, int(self.sys_clk / rate_hz) - 1)
        self.spi.set_divider(div)
        self.spi.set_sample_count(buffer_nsamp)
        self.spi.set_trigger_mask(0)
        self.spi.set_trigger_value(0)
        self.spi.set_fast_mode(True)
        self.spi.set_continuous(True)
        self.spi.flush()

        buf = b''
        seq = 0
        stride = self._stride
        yield_granule = 1024 * stride
        max_bytes = buffer_nsamp * stride

        while not stop_evt.is_set():
            try:
                # Read a chunk of data
                need = chunk_nsamp * stride
                chunk = self.spi.chained_read(need)
                if not chunk:
                    time.sleep(0.001)
                    continue
                if len(chunk) < 4:
                    time.sleep(0.001)
                    continue
                if full_out is not None:
                    full_out.extend(chunk)
                buf += chunk
                if len(buf) > max_bytes:
                    buf = buf[-max_bytes:]
                seq += len(chunk) // stride
                if progress_cb:
                    progress_cb(buf, seq, buffer_nsamp)
                yield buf, seq, buffer_nsamp
            except Exception:
                break


def find_spi_device():
    """Check if SPI device (FTDI Channel B) is available."""
    try:
        import ftd2xx as ft
        n = ft.createDeviceInfoList()
        for i in range(n):
            try:
                t = ft.open(i)
                info = t.getDeviceInfo()
                t.close()
                desc = info.get('description', b'').decode()
                if 'B' in desc or 'SPI' in desc:
                    return True
                if i == 1:  # second device is usually SPI
                    return True
            except:
                pass
        return False
    except:
        return False
