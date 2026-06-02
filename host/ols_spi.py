"""
OLS Logic Analyzer - SPI Host Library
Fast capture via BRAM (default), 12 MHz SPI, zero-waste preamble.
"""
import ftd2xx as ft
import time

CMD_RESET           = 0x00
CMD_ARM             = 0x01
CMD_ID              = 0x02
CMD_SPI_STATUS      = 0x03
CMD_SET_SAMPLE_CNT  = 0x82
CMD_SET_DIVIDER     = 0xC0
CMD_SET_TRIGGER_MASK = 0x80
CMD_SET_TRIGGER_VAL = 0x81

class OLS:
    def __init__(self, channel=1, speed_hz=12000000):
        self.dev = None
        self.channel = channel
        self.speed_hz = speed_hz

    def open(self):
        d = ft.open(self.channel)
        d.setBitMode(0xff, 0x00)
        time.sleep(0.1)
        d.setBitMode(0xff, 0x02)
        time.sleep(0.1)
        d.write(b'\xaa'); time.sleep(0.05)
        d.write(b'\xab'); time.sleep(0.05)
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
            self.dev.write(b'\x80\x08\x0b')
            time.sleep(0.1)
            self.dev.close()
            self.dev = None

    def tx(self, cmd, data=b'\x11\x11\x11\x11'):
        """5-byte SPI transaction. Returns [preamble, r0, r1, r2, r3]"""
        d = self.dev
        d.write(b'\x80\x00\x0b')
        d.write(bytes([0x31, 0x04, 0x00]))
        d.write(bytes([cmd]) + data)
        d.write(b'\x80\x08\x0b')
        time.sleep(0.005)
        return d.read(5)

    def preamble(self, resp):
        return resp[0]

    def is_running(self, resp): return bool(resp[0] & 0x80)
    def is_armed(self, resp):   return bool(resp[0] & 0x40)
    def is_full(self, resp):    return bool(resp[0] & 0x20)

    def get_id(self):
        self.tx(CMD_ID)
        r = self.tx(CMD_ID)
        return bytes(r[1:5]).decode('ascii')

    def get_status(self):
        """0x03 SPI status: 4 bytes, no lingering handler state"""
        return self.tx(CMD_SPI_STATUS)

    def set_sample_count(self, n):
        self.tx(CMD_SET_SAMPLE_CNT, bytes([n & 0xFF, (n >> 8) & 0xFF, 0, 0]))

    def set_divider(self, n):
        self.tx(CMD_SET_DIVIDER, bytes([n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, 0]))

    def set_trigger_mask(self, m):
        self.tx(CMD_SET_TRIGGER_MASK, bytes([(m >> (8*i)) & 0xFF for i in range(4)]))

    def set_trigger_value(self, v):
        self.tx(CMD_SET_TRIGGER_VAL, bytes([(v >> (8*i)) & 0xFF for i in range(4)]))

    def arm(self):
        self.tx(CMD_ARM)

    def wait_full(self, timeout_ms=10000):
        t0 = time.time()
        while True:
            r = self.get_status()
            if self.is_full(r):
                return True
            if (time.time() - t0) * 1000 > timeout_ms:
                return False
            time.sleep(0.01)

    def read_data(self, nbytes):
        """Chained read: returns nbytes of captured data with minimal overhead"""
        d = self.dev
        total = nbytes + 1
        d.write(b'\x80\x00\x0b')
        d.write(bytes([0x31, (total - 1) & 0xFF, (total - 1) >> 8]))
        d.write(b'\x11' * total)
        d.write(b'\x80\x08\x0b')
        time.sleep(0.01)
        raw = d.read(total)
        return raw[1:] if len(raw) > 1 else b''

    def capture(self, nsamples=256, divider=0):
        """Full capture: configure, arm, wait, read data"""
        self.set_sample_count(nsamples)
        self.set_divider(divider)
        self.set_trigger_mask(0)
        self.set_trigger_value(0)
        self.arm()
        if not self.wait_full(timeout_ms=30000):
            return None
        # nsamples * 4 bytes each for 8-channel mode
        return self.read_data(nsamples * 4)


if __name__ == '__main__':
    ols = OLS()
    ols.open()
    print(f"OLS ID: {ols.get_id()}")
    data = ols.capture(32)
    if data:
        print(f"Captured {len(data)} bytes")
        print(f"Byte values: {sorted(set(data))}")
    else:
        print("Capture failed")
    ols.close()
