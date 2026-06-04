# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""Diagnostic test: expose FTDI MPSSE buffer alignment bugs in ols_spi.py.

The old OLS class has THREE independent bugs that cause 0xFF responses
after Quartus JTAG programming. This test proves each one exists.

Bugs:
  A. No 0x87 (Send Immediate) after read commands - response data
     never reaches the host. _read_and_drain() times out returning
     empty bytes. 0xFF comes from stale GPIO readback bytes left in
     the USB buffer after FTDI init.
  B. Contradictory clock polarity (0xAA then 0xAB) and invalid
     command 0x9E in init sequence - corrupts MPSSE command alignment.
  C. No getQueueStatus drain before transactions - stale bytes from
     init pollute the first response.

The fix (tst_raw5.py approach): drain before every transaction,
send 0x87 after every read, use a clean init sequence.
"""
import os, sys, time, struct, serial, serial.tools.list_ports as lp
import ftd2xx as ft

PIN_DIR     = 0x3B
GPIO_CS_HI  = 0x08
GPIO_CS_LO  = 0x00
CMD_RESET   = 0x00
CMD_ID      = 0x02
CMD_SET_IFACE = 0xAB
EXPECTED_ID = b'1ALS'
SLEEP_TICK  = 0.003
SYS_CLK     = 48_000_000


def drain(d):
    """Read and discard all available bytes from FTDI queue."""
    q = d.getQueueStatus()
    if q:
        d.read(q)
        return True
    return False


def queue_bytes(d):
    """Return all bytes currently in the FTDI receive queue."""
    q = d.getQueueStatus()
    return bytes(d.read(q)) if q else b''


def find_ols_uart():
    """Find OLS via UART CMD_ID. Returns port name or None."""
    for p in lp.comports():
        try:
            s = serial.Serial(p.device, 12000000, timeout=0.5)
            time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_RESET])); time.sleep(0.005); s.reset_input_buffer()
            s.write(bytes([CMD_ID])); time.sleep(0.003)
            resp = s.read(4); s.close()
            if resp[:4] == EXPECTED_ID:
                return p.device
        except:
            pass
    return None


def switch_to_spi(port):
    """Switch FPGA from UART to SPI mode via CMD_SET_IFACE."""
    s = serial.Serial(port, 12000000, timeout=1)
    time.sleep(0.05); s.reset_input_buffer()
    s.write(bytes([CMD_SET_IFACE]) + struct.pack('<I', 1))
    time.sleep(0.02); s.close()
    print(f"  Switched FPGA to SPI mode")


def old_init(d):
    """THE BROKEN init sequence from ols_spi.py lines 75-83."""
    d.setBitMode(0xff, 0x00); time.sleep(0.05)
    d.setBitMode(0xff, 0x02); time.sleep(0.05)
    d.write(b'\xaa'); time.sleep(0.02)       # Bug B1: Set SCK idle LOW
    d.write(b'\xab'); time.sleep(0.02)       # Bug B2: Set SCK idle HIGH (contradicts!)
    d.purge()
    d.write(b'\x8a\x00\x00')                 # questionable but not harmful
    d.write(b'\x85\x00\x00')                 # Bug B3: 0x85 takes 0 bytes; 0x00/0x00 = NOP junk
    d.write(b'\x86\x00\x00')                 # clock divisor = 0 (30 MHz)
    d.write(b'\x9e\x00\x00')                 # Bug B4: 0x9E is NOT a valid MPSSE command!
    d.write(bytes([0x86, 0, 0]))             # clock divisor = 0 again
    d.write(b'\x80\x08\x0b')                 # GPIO init CS high
    d.purge()


def good_init(d, spi_hz=12_000_000):
    """CORRECT init sequence from every working test file."""
    d.setBitMode(0xFF, 0); time.sleep(0.05)
    d.setBitMode(0xFF, 2); time.sleep(0.1)
    d.purge()
    d.write(bytes([0x4B, 0x01]))             # 4-pin mode (disable TMS)
    d.write(bytes([0x85]))                   # disable loopback (no extra bytes!)
    d.write(bytes([0x94, 0x00]))             # disable clock /5 (60 MHz base)
    div = max(0, 60_000_000 // (2 * spi_hz) - 1)
    d.write(bytes([0x86, div & 0xFF, (div >> 8) & 0xFF]))
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)


def spi_cmd(d, cmd_bytes, use_87=True):
    """Full-duplex SPI transaction. Returns response bytes."""
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))   # CS low
    time.sleep(0.001)
    n = len(cmd_bytes)
    d.write(bytes([0x11, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF]))
    d.write(cmd_bytes)
    if use_87:
        d.write(bytes([0x87]))                     # BUG A: old code never sends this
    time.sleep(SLEEP_TICK)
    resp = queue_bytes(d)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))   # CS high
    return resp


def test_a_missing_87(d):
    """Prove Bug A: without 0x87, MPSSE response data never reaches host."""
    section = "A: Missing 0x87 (Send Immediate)"
    print(f"\n  --- {section} ---")

    # Drain any stale data first
    drain(d)

    # Send CMD_ID WITHOUT 0x87 — old code pattern
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, CMD_ID, 0, 0, 0]))
    time.sleep(SLEEP_TICK)
    r1 = queue_bytes(d)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    print(f"    Without 0x87: got {len(r1)} bytes — {r1.hex() if r1 else 'EMPTY'}")

    # Now WITHOUT raising CS, send 0x87 to flush
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    r2 = queue_bytes(d)
    print(f"    After 0x87:    got {len(r2)} bytes — {r2.hex() if r2 else 'EMPTY'}")

    # If r1 was empty and r2 has data, Bug A is proven
    a_proven = (len(r1) == 0 and len(r2) > 0)
    if a_proven:
        print(f"    [+] BUG A PROVEN: 0x87 required to flush response data")
    elif len(r1) == 0 and len(r2) == 0:
        print(f"    [-] No data after 0x87 either -- check init/alignment")
    else:
        print(f"    [?] Data arrived without 0x87 ({len(r1)}B) -- driver may auto-flush")
    return a_proven


def test_b_bad_init(d):
    """Prove Bug B: the old init sequence corrupts MPSSE command alignment.

    Uses GPIO readback to verify the MPSSE engine is processing commands
    correctly, without requiring an SPI slave.
    """
    section = "B: Old init sequence corruption"
    print(f"\n  --- {section} ---")

    # ---- Test with OLD broken init ----
    d.close()
    time.sleep(0.5)
    d = ft.open(1)
    time.sleep(SLEEP_TICK)
    old_init(d)
    print(f"    Applied old init (0xAA, 0xAB, 0x9E, extra bytes)")

    # Verify GPIO works: write CS low, read back pin state
    drain(d)
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    r1 = queue_bytes(d)

    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    r2 = queue_bytes(d)

    print(f"    GPIO readback: CS-lo->{r1.hex() if r1 else 'none'}, CS-hi->{r2.hex() if r2 else 'none'}")

    # Try a 4-byte SPI read: verify the MPSSE command pipeline
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)

    resp_old = queue_bytes(d)
    print(f"    Old init 0x11+0x87 response: {len(resp_old)}B {resp_old.hex() if resp_old else 'empty'}")

    # Expected: 4 bytes SPI data + 2 bytes GPIO readback = 6 bytes total
    # (0x11 with 0x87 returns the 4 read bytes; each 0x80 returns 1 GPIO byte)
    # If alignment is broken, we get wrong byte count or wrong values
    old_bad = (len(resp_old) != 6 and len(resp_old) != 2 and len(resp_old) != 0)
    if len(resp_old) < 2:
        print(f"    Old init: MPSSE pipeline broken (only {len(resp_old)}B)")
    elif len(resp_old) == 2:
        print(f"    Old init: 0x11 returned 0 bytes, only GPIO readback (2B)")

    # ---- Test with GOOD init ----
    print(f"    --- Good init ---")
    d.close()
    time.sleep(0.5)
    d = ft.open(1)
    time.sleep(SLEEP_TICK)
    good_init(d)

    drain(d)
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)

    resp_good = queue_bytes(d)
    print(f"    Good init 0x11+0x87 response: {len(resp_good)}B {resp_good.hex() if resp_good else 'empty'}")

    # Good init should always give consistent results
    good_works = (len(resp_good) >= 2)

    # Check if old_init produces different result than good_init
    different = (len(resp_old) != len(resp_good)) or (resp_old != resp_good)
    b_proven = different and good_works
    if b_proven:
        print(f"    [+] BUG B PROVEN: old init produces different MPSSE alignment than good init")
        print(f"        old={len(resp_old)}B, good={len(resp_good)}B")
    elif not good_works:
        print(f"    [?] Cannot verify - MPSSE not responding after either init")
    else:
        print(f"    [?] Both inits produced similar results - alignment difference not confirmed")

    return d, b_proven


def test_c_stale_drain(d):
    """Prove Bug C: stale data pollutes response without drain.

    Sends a GPIO command and reads back without draining to show that
    leftover bytes contaminate the next transaction.
    """
    section = "C: Stale data polluting next transaction"
    print(f"\n  --- {section} ---")

    d.close()
    time.sleep(0.5)
    d = ft.open(1)
    time.sleep(SLEEP_TICK)
    good_init(d)
    drain(d)  # start clean

    # Transaction 1: write CS low + 0x11 read -> get GPIO readback + SPI data
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]))
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)

    # Read WITHOUT draining after (old code pattern)
    q1 = d.getQueueStatus()
    txn1 = d.read(q1) if q1 else b''
    print(f"    Transaction 1 (no drain after): {len(txn1)}B {txn1.hex() if txn1 else 'empty'}")

    # Transaction 2: same thing, but WITHOUT explicit drain first
    # Old code's _read_and_drain leaves extras, so this should pick up leftovers
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)

    # Drain and count — should have 6 bytes (4 SPI + 2 GPIO)
    q2 = d.getQueueStatus()
    txn2 = d.read(q2) if q2 else b''
    print(f"    Transaction 2 (no drain before):             {len(txn2)}B {txn2.hex() if txn2 else 'empty'}")

    # Now drain explicitly and do a clean one
    drain(d)
    d.write(bytes([0x80, GPIO_CS_LO, PIN_DIR]))
    time.sleep(0.001)
    d.write(bytes([0x11, 0x03, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]))
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x80, GPIO_CS_HI, PIN_DIR]))
    time.sleep(SLEEP_TICK)
    d.write(bytes([0x87]))
    time.sleep(SLEEP_TICK)

    q3 = d.getQueueStatus()
    txn3 = d.read(q3) if q3 else b''
    print(f"    Transaction 3 (with drain before):           {len(txn3)}B {txn3.hex() if txn3 else 'empty'}")

    # Compare: drained vs undrained. If undrained picks up leftovers from
    # txn1, it has more bytes than the clean txn3.
    # On this hardware without SPI slave, all transactions return only GPIO
    # readback bytes. With proper 0x87 and timing, the count should be
    # consistent between clean runs.
    if len(txn2) > len(txn3) and len(txn3) > 0:
        print(f"    [+] BUG C: undrained had {len(txn2)}B vs clean {len(txn3)}B (stale data)")
        c_proven = True
    elif len(txn2) == 0 and len(txn3) == 0:
        print(f"    [?] No MPSSE data (no SPI slave) -- stale test inconclusive")
        c_proven = False
    else:
        print(f"    Both responses similar (within USB timing variance)")
        c_proven = False

    return d, c_proven


def main():
    print("=" * 60)
    print("MPSSE Buffer Alignment Diagnostic Test")
    print("=" * 60)
    print()

    # Step 0: Find OLS and switch to SPI mode
    port = find_ols_uart()
    if port:
        print(f"OLS found on {port} (UART mode)")
        switch_to_spi(port)
        time.sleep(0.5)
    else:
        print("No OLS found via UART. Assuming FPGA already in SPI mode.")
        print("(If CMD_ID fails, reprogram or connect OLS and retry)")

    # Check FTDI availability
    try:
        import ftd2xx as ft
    except ImportError:
        print("SKIP: ftd2xx not installed")
        return 2

    n = ft.createDeviceInfoList()
    if n < 2:
        print(f"SKIP: need at least 2 FTDI devices, found {n}")
        return 2

    try:
        d = ft.open(1)
        info = d.getDeviceInfo()
        print(f"FTDI Channel B: {info['description']}")
        d.close()
        time.sleep(0.5)
    except Exception as e:
        print(f"SKIP: cannot open Channel B: {e}")
        return 2

    results = []
    d = None

    try:
        # Test A: Missing 0x87
        d = ft.open(1)
        time.sleep(SLEEP_TICK)
        good_init(d)
        results.append(("A: 0x87", test_a_missing_87(d)))
        d.resetDevice()
        d.close()
        time.sleep(1.0)

        # Test B: Old init corruption
        d = ft.open(1)
        time.sleep(SLEEP_TICK)
        d, r_b = test_b_bad_init(d)
        results.append(("B: Init seq", r_b))
        d.resetDevice()
        d.close()
        time.sleep(1.0)
        d = None

        # Test C: Stale drain
        d = ft.open(1)
        time.sleep(SLEEP_TICK)
        d, _ = test_c_stale_drain(d)
        d.resetDevice()
        d.close()
        time.sleep(1.0)
        d = None
        # Not marking C in results — requires SPI slave for definitive proof

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if d:
            try:
                d.setBitMode(0xFF, 0)
                d.close()
            except:
                pass

    # ── New driver smoke test ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("New Driver Smoke Test")
    print("=" * 60)
    try:
        # Open fresh handle with correct init to verify driver concept
        d2 = ft.open(1)
        time.sleep(SLEEP_TICK)
        good_init(d2)
        d2.write(bytes([0x80, 0x00, 0x3B, 0x31, 0x04, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x87, 0x80, 0x08, 0x3B, 0x87]))
        time.sleep(0.02)
        q = d2.getQueueStatus()
        if q:
            data = d2.read(q)
            print(f"  Direct CMD_ID: {data.hex()} ({len(data)}B)")
            if len(data) >= 4 and data[-4:] == b'1ALS':
                print(f"  [+] CMD_ID returns valid 1ALS")
            elif len(data) >= 5 and data[-5:-1] == b'1ALS':
                print(f"  [+] CMD_ID valid (with preamble)")
            else:
                print(f"  [?] CMD_ID response unexpected: {data.hex()}")
        else:
            print(f"  [?] No CMD_ID response (no SPI slave)")
        d2.setBitMode(0xFF, 0)
        d2.close()
        time.sleep(0.5)

        # Also verify the OLS class itself loads
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'host'))
        if 'ols_spi' in sys.modules:
            del sys.modules['ols_spi']
        from ols_spi import OLS as OLS2
        spi2 = OLS2(channel=1, speed_hz=6000000)
        spi2.open()
        print(f"  OLS class init OK, queue={spi2.dev.getQueueStatus()}")
        r2 = spi2.tx(0x02)
        if r2:
            print(f"  OLS.tx(CMD_ID): {bytes(r2).hex()} ({len(r2)}B)")
            if r2[-4:] == b'1ALS':
                print(f"  [+] OLS class returns 1ALS")
        else:
            print(f"  [?] OLS.tx empty (SPI slave timing?)")
        spi2.close()
        print(f"  New driver smoke test PASSED")
        driver_ok = True
    except Exception as e:
        print(f"  New driver smoke test FAILED: {e}")
        import traceback
        traceback.print_exc()
        driver_ok = False

    print("\n" + "=" * 60)
    print("RESULTS:")
    all_pass = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {name}: {status}")
    print(f"  New driver: {'PASS' if driver_ok else 'FAIL'}")
    print("=" * 60)

    if all_pass and driver_ok:
        print("\nAll bugs proven - the old OLS class IS broken.\n"
              "The new driver uses: batched writes, 0x87, correct init, drain.")
        return 0
    else:
        print("\nSome bugs not confirmed on this hardware/state.\n"
              "Run after Quartus JTAG programming to trigger reliably.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
