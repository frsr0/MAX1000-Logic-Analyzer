#!/usr/bin/env python3
"""OLS MPSSE SPI host driver via FT2232H Channel B."""
import time, struct, ftd2xx as ft

PIN_DIR     = 0x3B   # SCK/B0, MOSI/B1, CS/B3 out; MISO/B2 in
GPIO_INIT   = 0x08   # CS high, SCK low, MOSI low
GPIO_CS_LO  = 0x00
GPIO_CS_HI  = 0x08

SYS_CLK     = 48_000_000
CMD_RESET   = 0x00; CMD_ARM     = 0x01; CMD_ID      = 0x02; CMD_METADATA = 0x04
CMD_XON     = 0x11; CMD_XOFF    = 0x13
CMD_DIVIDER = 0x80; CMD_FLAGS   = 0x82; CMD_DCOUNT  = 0x83; CMD_RCOUNT  = 0x84
CMD_SET_IFACE = 0xAB

class OLS_SPI_MPSSE:
    def __init__(self, channel=1, spi_hz=12_000_000):
        self.d = ft.open(channel)
        self.d.setBitMode(0xFF, 0)
        time.sleep(0.05)
        self.d.setBitMode(0xFF, 2)
        time.sleep(0.1)
        self.d.purge()

        self.d.write(bytes([0x4B, 0x01]))
        time.sleep(0.01)
        self.d.write(bytes([0x85]))
        time.sleep(0.01)
        self.d.write(bytes([0x94, 0x00]))
        time.sleep(0.01)
        div = max(0, 60_000_000 // (2 * spi_hz) - 1)
        self.d.write(bytes([0x86, div & 0xFF, (div >> 8) & 0xFF]))
        time.sleep(0.01)

        self._gpio(GPIO_INIT)

    def _gpio(self, val):
        self.d.write(bytes([0x80, val, PIN_DIR]))

    def _sync_wait(self, n):
        while self.d.getQueueStatus() < n:
            time.sleep(0.00005)

    def xfer(self, data, read_len=None):
        """Full-duplex SPI. Returns read_len bytes (default len(data))."""
        if read_len is None:
            read_len = len(data)
        total = max(len(data), read_len)
        self._gpio(GPIO_CS_LO)
        self.d.write(bytes([0x11, (total - 1) & 0xFF, ((total - 1) >> 8) & 0xFF]))
        if read_len > len(data):
            data = data + bytes([0x00] * (read_len - len(data)))
        self.d.write(data)
        self.d.write(bytes([0x87]))
        self._sync_wait(total)
        resp = bytes(self.d.read(total))
        self._gpio(GPIO_CS_HI)
        return resp[:read_len]

    def cmd_id(self):
        r = self.xfer(bytes([CMD_ID, 0x00, 0x00, 0x00, 0x00]))
        return r[1:5]

    def metadata(self):
        """CMD_METADATA returns 18 bytes."""
        r = self.xfer(bytes([CMD_METADATA]), read_len=18)
        return r

    def short_cmd(self, cmd):
        self.xfer(bytes([cmd, 0x00, 0x00, 0x00, 0x00]))

    def long_cmd(self, cmd, val):
        self.xfer(bytes([cmd]) + struct.pack('<I', val))

    def reset(self):
        for _ in range(5):
            self.short_cmd(CMD_RESET)
        time.sleep(0.05)

    def capture_simple(self, samples=100, rate_hz=1_000_000):
        self.reset()
        div = max(0, int(SYS_CLK / rate_hz) - 1)
        self.short_cmd(CMD_XON)
        self.long_cmd(CMD_DIVIDER, div)
        self.long_cmd(CMD_RCOUNT, samples)
        self.long_cmd(CMD_DCOUNT, samples)
        self.long_cmd(CMD_FLAGS, 0)
        self.short_cmd(CMD_XOFF)
        self.short_cmd(CMD_ARM)
        time.sleep(max(0.01, samples / rate_hz * 1.5))

        need = samples * 4
        data = bytearray()
        while len(data) < need:
            chunk = need - len(data)
            if chunk > 4096:
                chunk = 4096
            resp = self.xfer(bytes([0x00] * chunk))
            data.extend(resp)

        return bytes(data)

    def close(self):
        self.d.setBitMode(0xFF, 0)
        self.d.close()
