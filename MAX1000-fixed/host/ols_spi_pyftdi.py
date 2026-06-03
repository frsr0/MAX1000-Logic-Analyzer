"""pyftdi-compatible OLS SPI driver using ftd2xx bitbang.

Provides the same SpiController API as pyftdi.spi.SpiController,
but uses ftd2xx bitbang underneath since the Arrow USB Programmer2
custom firmware blocks MPSSE mode on Channel B.

Usage:
    from ols_spi_pyftdi import SpiController
    ctrl = SpiController()
    spi = ctrl.get_port(cs_count=1, freq=1000)
    spi.write(bytes([0x02, 0x00, 0x00, 0x00, 0x00]))
    resp = spi.read(5)
"""
import time, ftd2xx as ft

PIN_SCK  = 1 << 0
PIN_MOSI = 1 << 1
PIN_MISO = 1 << 2
PIN_CSn  = 1 << 3
WRITE_MASK = 0b11111011


class SpiPort:
    """Single SPI port, mirrors pyftdi.spi.SpiPort API."""

    def __init__(self, dev, cs_pin, delay):
        self._dev = dev
        self._cs_pin = cs_pin
        self._delay = delay

    def _flush(self):
        while True:
            q = self._dev.getQueueStatus()
            if not q:
                break
            self._dev.read(q)

    def _wr(self, val):
        self._dev.purge()
        self._dev.write(bytes([val]))
        time.sleep(self._delay)
        q = self._dev.getQueueStatus()
        return self._dev.read(q)[-1] if q else 0

    def _cs_high(self):
        self._wr(self._cs_pin)

    def _cs_low(self):
        self._wr(0x00)
        self._flush()

    def write(self, data):
        """SPI write (MOSI), discard MISO."""
        self._cs_low()
        for byte_val in data:
            for bit in range(8):
                mosi_bit = (byte_val >> (7 - bit)) & 1
                self._wr(PIN_SCK | (mosi_bit << 1))
                self._wr(mosi_bit << 1)
        self._cs_high()

    def read(self, readlen):
        """SPI read (MISO), send 0x00 on MOSI."""
        self._cs_low()
        rx = []
        for _ in range(readlen):
            rx_byte = 0
            for bit in range(8):
                self._wr(PIN_SCK)
                rd = self._wr(0x00)
                miso = (rd >> 2) & 1
                rx_byte = (rx_byte << 1) | miso
            rx.append(rx_byte)
        self._cs_high()
        return bytes(rx)

    def exchange(self, data, readlen=0):
        """Full-duplex exchange."""
        self._cs_low()
        rx = []
        tx_len = max(len(data), readlen)
        for i in range(tx_len):
            tx_byte = data[i] if i < len(data) else 0x00
            rx_byte = 0
            for bit in range(8):
                mosi_bit = (tx_byte >> (7 - bit)) & 1
                s = self._wr(PIN_SCK | (mosi_bit << 1))
                miso = (s >> 2) & 1
                rx_byte = (rx_byte << 1) | miso
                self._wr(mosi_bit << 1)
            rx.append(rx_byte)
        self._cs_high()
        return bytes(rx)


class SpiController:
    """pyftdi-compatible SPI controller for Arrow USB Programmer2.

    Uses ftd2xx bitbang since the custom Arrow firmware blocks MPSSE.
    """

    def __init__(self, channel=1):
        self._channel = channel
        self._dev = None

    def configure(self, url=''):
        """Open and configure the FTDI device for SPI bitbang."""
        self._dev = ft.open(self._channel)
        self._dev.setBitMode(0xFF, 0)
        time.sleep(0.05)
        self._dev.setBitMode(WRITE_MASK, 1)
        time.sleep(0.1)
        self._dev.setLatencyTimer(1)
        self._dev.setBaudRate(1000000)
        # Init CS high
        self._dev.purge()
        self._dev.write(bytes([PIN_CSn]))
        time.sleep(0.005)
        while True:
            q = self._dev.getQueueStatus()
            if not q:
                break
            self._dev.read(q)
        return self

    def get_port(self, cs_count=1, freq=1000):
        """Return a SpiPort for the given CS line."""
        delay = 1.0 / (2 * max(freq, 100))
        delay = min(max(delay, 0.0008), 0.01)
        return SpiPort(self._dev, PIN_CSn, delay)

    def close(self):
        if self._dev:
            self._dev.setBitMode(0xFF, 0)
            self._dev.close()
            self._dev = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


if __name__ == '__main__':
    with SpiController() as ctrl:
        ctrl.configure()
        spi = ctrl.get_port(freq=500)
        resp = spi.exchange(bytes([0x02, 0x00, 0x00, 0x00, 0x00]))
        print(f'CMD_ID: {resp[1:5]}')
        print('PASS' if resp[1:5] == b'1ALS' else 'FAIL')
