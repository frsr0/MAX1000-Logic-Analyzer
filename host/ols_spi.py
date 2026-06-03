"""
OLS Logic Analyzer - SPI Host Library
Fast capture via BRAM, 30 MHz SPI, zero-waste preamble, rolling/continuous mode.
"""
import ftd2xx as ft
import time

CMD_RESET           = 0x00
CMD_ARM             = 0x01
CMD_ARM2            = 0x02
CMD_SPI_STATUS      = 0x03
CMD_LEGACY_STATUS   = 0x04
CMD_SET_SAMPLE_CNT  = 0x84
CMD_SET_DIVIDER     = 0x80
CMD_SET_TRIGGER_MASK = 0xC0
CMD_SET_TRIGGER_VAL = 0xC1
CMD_FAST_MODE       = 0xA8
CMD_CONTINUOUS      = 0xAA
CMD_CH_MODE         = 0xAE

# Generator command constants (shared with UART backend)
CMD_GEN_LOAD   = 0xA0
CMD_GEN_STRT   = 0xA1
CMD_GEN_BAUD   = 0xA2
CMD_GEN_BLK    = 0xA3
CMD_GEN_PROTO  = 0xA4
CMD_GEN_PINS   = 0xA6
CMD_I2C_TEST   = 0xA7

class OLS:
    def __init__(self, channel=1, speed_hz=30000000):
        self.dev = None
        self.channel = channel
        self.speed_hz = speed_hz

    def open(self):
        # Auto-detect SPI channel: scan FTDI devices for the non-JTAG one
        n = ft.createDeviceInfoList()
        idx = self.channel
        for i in range(n):
            try:
                t = ft.open(i)
                info = t.getDeviceInfo()
                t.close()
                desc = info.get('description', b'').decode()
                # Pick the Blaster B / SPI channel (not JTAG Blaster A)
                if 'B' in desc or 'SPI' in desc or 'Serial' in desc:
                    idx = i
                    break
                # Fallback: second device is usually SPI
                if i == 1:
                    idx = i
            except:
                pass
        d = ft.open(idx)
        d.setBitMode(0xff, 0x00); time.sleep(0.05)
        d.setBitMode(0xff, 0x02); time.sleep(0.05)
        d.write(b'\xaa'); time.sleep(0.02)
        d.write(b'\xab'); time.sleep(0.02)
        d.purge()
        d.write(b'\x8a\x00\x00')
        d.write(b'\x85\x00\x00')
        d.write(b'\x86\x00\x00')
        d.write(b'\x9e\x00\x00')
        if self.speed_hz > 0:
            div = max(0, int(60000000 / (self.speed_hz * 2) - 1))
            d.write(bytes([0x86, div & 0xFF, (div >> 8) & 0xFF]))
        d.write(b'\x80\x08\x0b')
        d.purge()
        self.dev = d

    def close(self):
        if self.dev:
            self.dev.close()
            self.dev = None

    def tx(self, cmd, data=b'\x11\x11\x11\x11'):
        """5-byte SPI transaction. Returns [preamble, b0, b1, b2, b3]"""
        self.dev.write(b'\x80\x00\x0b')
        self.dev.write(bytes([0x31, 0x04, 0x00]))
        self.dev.write(bytes([cmd]) + data)
        self.dev.write(b'\x80\x08\x0b')
        time.sleep(0.001)
        return self.dev.read(5)

    def bulk_write(self, data):
        """Send arbitrary-length bytes via SPI (CS held low for entire transfer).
        
        Uses MPSSE combined write+read (0x31) so the FTDI clocks in the
        slave's reply simultaneously; return data is discarded.
        Length limit: 65536 bytes per call.
        """
        if not data or not self.dev:
            return
        n = len(data)
        lo = (n - 1) & 0xFF
        hi = ((n - 1) >> 8) & 0xFF
        self.dev.write(b'\x80\x00\x0b')
        self.dev.write(bytes([0x31, lo, hi]))
        self.dev.write(data)
        self.dev.write(b'\x80\x08\x0b')
        time.sleep(0.002)
        try:
            q = self.dev.getQueueStatus()
            if q:
                self.dev.read(q)
        except:
            pass

    def flush(self):
        """Discard stale FIFO data"""
        time.sleep(0.005)
        try:
            q = self.dev.getQueueStatus()
            if q: self.dev.read(q)
        except: pass

    def reset(self):
        self.tx(CMD_RESET)
        self.flush()

    def arm(self):
        self.tx(CMD_ARM)
        self.flush()

    def set_sample_count(self, n):
        lo = n & 0xFF; hi = (n >> 8) & 0xFF
        self.tx(CMD_SET_SAMPLE_CNT, bytes([lo, hi, 0, 0]))

    def set_divider(self, n):
        lo = n & 0xFF; hi = (n >> 8) & 0xFF; ext = (n >> 16) & 0xFF
        self.tx(CMD_SET_DIVIDER, bytes([lo, hi, ext, 0]))

    def set_trigger_mask(self, m):
        self.tx(CMD_SET_TRIGGER_MASK, bytes([(m >> (8*i)) & 0xFF for i in range(4)]))

    def set_trigger_value(self, v):
        self.tx(CMD_SET_TRIGGER_VAL, bytes([(v >> (8*i)) & 0xFF for i in range(4)]))

    def set_fast_mode(self, enable=True):
        self.tx(CMD_FAST_MODE, bytes([0, 1 if enable else 0, 0, 0]))

    def set_continuous(self, enable=True):
        self.tx(CMD_CONTINUOUS, bytes([0, 1 if enable else 0, 0, 0]))

    def set_ch_mode(self, mode_4ch=False):
        """False=8ch/500k, True=4ch/4M"""
        self.tx(CMD_CH_MODE, bytes([0, 1 if mode_4ch else 0, 0, 0]))

    def chained_read(self, nbytes):
        """Read nbytes of data via chained ARM+read. Returns data bytes (no preamble)."""
        total = nbytes + 1
        self.dev.write(b'\x80\x00\x0b')
        self.dev.write(bytes([0x31, (total - 1) & 0xFF, (total - 1) >> 8]))
        self.dev.write(b'\x11' * total)
        self.dev.write(b'\x80\x08\x0b')
        time.sleep(0.005)
        raw = self.dev.read(total)
        return raw[1:] if len(raw) > 1 else b''

    def capture_single(self, nsamples=256, divider=1):
        """Configure, arm, wait, read single-shot capture data."""
        self.reset()
        self.arm()
        self.set_sample_count(nsamples)
        self.set_divider(divider)
        self.set_trigger_mask(0xFF)
        self.set_trigger_value(0xFF)
        self.set_fast_mode(True)
        self.arm()
        # Wait for capture to complete
        time.sleep(0.01)
        return self.chained_read(nsamples * 4)

    def capture_rolling(self, nsamples=256, divider=1):
        """Configure and read rolling/continuous capture data."""
        self.reset()
        self.arm()
        self.set_sample_count(nsamples)
        self.set_divider(divider)
        self.set_trigger_mask(0xFF)
        self.set_trigger_value(0xFF)
        self.set_fast_mode(True)
        self.set_continuous(True)
        # Continuous mode auto-arms; data should be available immediately
        return self.chained_read(nsamples * 4)


if __name__ == '__main__':
    ols = OLS()
    ols.open()
    print("OLS @ 30 MHz SPI")

    # Single-shot test
    data = ols.capture_single(64, 1)
    if data:
        gaps = sum(1 for i in range(0, len(data)-3, 4) if all(b==0 for b in data[i:i+4]))
        uniq = len(set(data))
        print(f"Single-shot: {len(data)} bytes, gaps={gaps}, uniq={uniq}")
    else:
        print("Single-shot failed")

    # Rolling test
    data = ols.capture_rolling(64, 1)
    if data:
        gaps = sum(1 for i in range(0, len(data)-3, 4) if all(b==0 for b in data[i:i+4]))
        uniq = len(set(data))
        print(f"Rolling:     {len(data)} bytes, gaps={gaps}, uniq={uniq}")
    else:
        print("Rolling failed")

    ols.close()
