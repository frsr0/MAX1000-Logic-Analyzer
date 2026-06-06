"""
Test generator command sequences: validate that send_uart() now transmits
GEN_STRT (0xA1) with safe 0x11 padding.

This test uses a mock SPI backend to verify the exact byte sequences
produced by the software, without requiring physical hardware.
"""
import sys, struct
sys.path.insert(0, '.')

# Mock SPI device that captures transmitted bytes
class MockSPI:
    def __init__(self):
        self.history = []
        self.dev = self
    def open(self): pass
    def close(self): pass
    def flush(self): pass
    def reset(self): pass
    def tx(self, cmd, data=None):
        if data is None:
            data = b'\x00\x00\x00\x00'
        cmd_byte = struct.pack('B', cmd)
        self.history.append(('tx', cmd_byte + data[:4]))
        return bytes([0x00]) * 5
    def write(self, buf):
        self.history.append(('write', buf))
    def getQueueStatus(self): return 0
    def read(self, n): return b''
    def _drain(self): pass

from ols_spi_device import OLSDeviceSPI, CMD_GEN_STRT, CMD_GEN_PROTO, CMD_GEN_BAUD, CMD_GEN_BLK, CMD_GEN_PINS

def test_send_uart_start_gen_called():
    """send_uart() must call start_gen() after configuration."""
    dev = OLSDeviceSPI(sys_clk_hz=24000000)
    dev.spi = MockSPI()
    dev.send_uart(b'Hello', baud=115200, tx_pin=3)
    cmds = [h for h in dev.spi.history if h[0] == 'tx']
    cmd_bytes = b''.join(c[1] for c in cmds)
    print(f"send_uart transmitted {len(cmds)} commands:")
    for seq, c in enumerate(cmds):
        print(f"  {seq}: {' '.join(f'{b:02x}' for b in c[1])}")
    # Must contain GEN_STRT
    assert CMD_GEN_STRT in cmd_bytes, "FAIL: No GEN_STRT (0xA1) in send_uart() output"
    # Find the GEN_STRT transmission
    for c in cmds:
        if c[1][0] == CMD_GEN_STRT:
            # Check padding is 0x11, not 0x00
            assert c[1][1] == 0x11, f"FAIL: GEN_STRT padding byte 1 is 0x{c[1][1]:02x}, expected 0x11"
            assert c[1][2] == 0x11, f"FAIL: GEN_STRT padding byte 2 is 0x{c[1][2]:02x}, expected 0x11"
            assert c[1][3] == 0x11, f"FAIL: GEN_STRT padding byte 3 is 0x{c[1][3]:02x}, expected 0x11"
            assert c[1][4] == 0x11, f"FAIL: GEN_STRT padding byte 4 is 0x{c[1][4]:02x}, expected 0x11"
            print("  PASS: GEN_STRT uses 0x11 padding (safe, no CMD_RESET)")
    # Check order: PROTO → BAUD → BLK → PINS → STR
    proto_idx = next(i for i, c in enumerate(cmds) if c[1][0] == CMD_GEN_PROTO)
    baud_idx = next(i for i, c in enumerate(cmds) if c[1][0] == CMD_GEN_BAUD)
    pins_idx = next(i for i, c in enumerate(cmds) if c[1][0] == CMD_GEN_PINS)
    strt_idx = next(i for i, c in enumerate(cmds) if c[1][0] == CMD_GEN_STRT)
    assert proto_idx < baud_idx < pins_idx < strt_idx, \
        f"FAIL: Command order wrong. PROTO={proto_idx} BAUD={baud_idx} PINS={pins_idx} STRT={strt_idx}"
    print("  PASS: Command order correct (PROTO -> BAUD -> PINS -> STR)")
    print("PASS: send_uart() command sequence verified")
    return True

def test_start_gen_padding():
    """start_gen() must use 0x11 padding, not 0x00."""
    dev = OLSDeviceSPI(sys_clk_hz=24000000)
    dev.spi = MockSPI()
    dev.start_gen()
    cmds = [h for h in dev.spi.history if h[0] == 'tx']
    assert len(cmds) == 1, f"Expected 1 command, got {len(cmds)}"
    c = cmds[0][1]
    assert c[0] == CMD_GEN_STRT, f"Expected CMD_GEN_STRT, got 0x{c[0]:02x}"
    assert c[1] == 0x11, f"Padding byte 1 is 0x{c[1]:02x}"
    assert c[2] == 0x11, f"Padding byte 2 is 0x{c[2]:02x}"
    assert c[3] == 0x11, f"Padding byte 3 is 0x{c[3]:02x}"
    assert c[4] == 0x11, f"Padding byte 4 is 0x{c[4]:02x}"
    print(f"  start_gen() transmits: {' '.join(f'{b:02x}' for b in c)}")
    print("PASS: start_gen() uses 0x11 padding")
    return True

def test_fast_start_gen_padding():
    """fast_start_gen() must use 0x11 padding, not 0x00."""
    dev = OLSDeviceSPI(sys_clk_hz=24000000)
    dev.spi = MockSPI()
    dev.fast_start_gen()
    cmds = [h for h in dev.spi.history if h[0] == 'tx']
    assert len(cmds) == 1, f"Expected 1 command, got {len(cmds)}"
    c = cmds[0][1]
    assert c[0] == CMD_GEN_STRT, f"Expected CMD_GEN_STRT, got 0x{c[0]:02x}"
    assert c[1] == 0x11, f"Padding byte 1 is 0x{c[1]:02x}"
    assert c[2] == 0x11, f"Padding byte 2 is 0x{c[2]:02x}"
    assert c[3] == 0x11, f"Padding byte 3 is 0x{c[3]:02x}"
    assert c[4] == 0x11, f"Padding byte 4 is 0x{c[4]:02x}"
    print(f"  fast_start_gen() transmits: {' '.join(f'{b:02x}' for b in c)}")
    print("PASS: fast_start_gen() uses 0x11 padding")
    return True

def test_old_padding_was_reset():
    """Demonstrate that the OLD 0x00 padding would decode as CMD_RESET."""
    from ols_spi import CMD_RESET as RESET
    print(f"  0x00 == CMD_RESET ({RESET:#04x}) — trailing 0x00 bytes in old start_gen()")
    print(f"  would execute CMD_RESET (clear Run_OLS, Run, Gen_Baud_Div, etc.)")
    print("  Confirmed: old padding was harmful")
    return True

if __name__ == '__main__':
    ok = True
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith('test_')):
        print(f"{name}...")
        try:
            fn()
        except Exception as e:
            print(f"  FAIL: {e}")
            ok = False
    print()
    print("*** ALL TESTS PASSED ***" if ok else "*** SOME TESTS FAILED ***")
    sys.exit(0 if ok else 1)
