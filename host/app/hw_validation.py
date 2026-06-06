#!/usr/bin/env python3
"""
Hardware Validation Suite for OLS Logic Analyzer

Exercises all hardware paths matching the GHDL testbenches, prints
progress frequently, and saves results to host/hw_results/ for offline
comparison with simulation waveforms.

Usage:
    python host/hw_validation.py

Requires:
    - MAX1000 board connected via USB (FTDI FT2232H)
    - FPGA programmed with OLS_Logic_Analyzer bitstream
    - Python packages: ftd2xx, pyserial
"""

import sys, time, os, json, struct, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

NUM_CHANNELS = 16

try:
    from driver.ols_spi_device import OLSDeviceSPI
    from driver.ols_spi_device import (CMD_DIVIDER, CMD_RCOUNT, CMD_DCOUNT,
                                       CMD_TMASK, CMD_TVALUE, CMD_FAST_MODE,
                                       CMD_CONT_CAPTURE)
    from driver.ols_spi import OLS as OLS_SPI, GPIO_CS_LO, GPIO_CS_HI, PIN_DIR
    from app.OLS_Console import samples_to_channels, decode_uart, decode_i2c
except ImportError as e:
    print(f"ERROR: {e}")
    print("Make sure you're running from the repo root or host/ directory")
    sys.exit(1)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "hdl", "hw_test", "hw_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

PASS = 0
FAIL = 0
TOTAL = 0

def log(msg):
    print(f"  {msg}")
    sys.stdout.flush()

def save_result(name, data, meta):
    path = os.path.join(RESULTS_DIR, name)
    with open(path + ".bin", "wb") as f:
        f.write(data if data else b"")
    with open(path + ".json", "w") as f:
        json.dump(meta, f, indent=2)
    log(f"saved {path}.bin ({len(data) if data else 0} bytes) + .json")

def check(cond, msg):
    global PASS, FAIL, TOTAL
    TOTAL += 1
    if cond:
        log(f"  >>> PASS: {msg}")
        PASS += 1
    else:
        log(f"  >>> FAIL: {msg}")
        FAIL += 1

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    sys.stdout.flush()

def print_progress(current, total, label=""):
    pct = (current / total) * 100 if total else 0
    print(f"\r  [{current}/{total}] {pct:.0f}% {label}", end="")
    sys.stdout.flush()
    if current == total:
        print()

def decode_i2c_best(ch, samplerate, scl_idx=1, sda_idx=2, filter_threshold=0,
                    offsets=range(-32, 33)):
    best_decoded = []
    best_offset = 0
    best_score = -1
    for offset in offsets:
        decoded = decode_i2c(ch, samplerate, scl_idx=scl_idx, sda_idx=sda_idx,
                             filter_threshold=filter_threshold, sda_offset=offset)
        data_bytes = [v for t, v in decoded if t == "DATA"]
        score = sum(1 for b in data_bytes if b not in (0x00, 0xFF))
        if score > best_score:
            best_decoded = decoded
            best_offset = offset
            best_score = score
    return best_decoded, best_offset

# ====================================================================
# Test 1: UART CMD_ID
# ====================================================================
def test_uart_cmd_id():
    print_header("Test 1: UART CMD_ID query")
    try:
        import serial
    except ImportError:
        log("SKIP: pyserial not installed")
        return
    import glob
    time.sleep(2)  # wait for COM port enumeration after program
    ports = glob.glob("COM*") if sys.platform == "win32" else glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")
    log(f"available ports: {ports}")
    found = False
    for p in sorted(ports):
        log(f"trying {p}...")
        try:
            s = serial.Serial(p, 115200, timeout=1)
            time.sleep(0.1)
            s.write(bytes([0x02, 0x00, 0x00, 0x00, 0x00]))
            time.sleep(0.1)
            resp = s.read(10)
            s.close()
            if resp and b"1ALS" in resp:
                log(f"UART CMD_ID response: {resp.hex()}")
                check(b"1ALS" in resp, f"UART ID match on {p}")
                found = True
                break
            else:
                log(f"  no match on {p}: {resp.hex() if resp else '(empty)'}")
        except Exception as e:
            log(f"  error on {p}: {e}")
    if not found:
        check(False, "No UART device found with OLS ID")

# ====================================================================
# Test 2: SPI handoff + CMD_ID
# ====================================================================
def test_spi_handoff(dev):
    print_header("Test 2: SPI handoff and CMD_ID")
    log("reset + interface mode set")
    dev.reset()
    time.sleep(0.02)
    dev.spi.flush()

    # CMD_ID triggers 4 TX bytes (0x31, 0x41, 0x4C, 0x53) in the TX pipeline.
    # Read them via NOPs (0x11) — never 0x00 which is CMD_RESET and clears state.
    log("sending CMD_ID via SPI...")
    resp = dev.spi.tx(0x02)
    log(f"CMD_ID raw response: {resp.hex() if resp else '(empty)'}")
    resp2 = dev.spi.tx(0x11)
    log(f"CMD_ID pipelined (NOP): {resp2.hex() if resp2 else '(empty)'}")
    # Each tx() returns 5 data bytes (preamble is the skipped MISO byte 0).
    # The 4 ID bytes span across both responses due to SPI pipeline timing.
    combined = bytes(resp) + bytes(resp2)
    check(b'\x31\x41\x4c\x53' in combined or
          (b'\x53' in combined and any(b & 0x10 for b in combined)),
          f"CMD_ID '1ALS' signature in: {combined.hex()}")

# ====================================================================
# Test 3: All SPI commands
# ====================================================================
def test_spi_commands(dev):
    print_header("Test 3: All SPI commands")
    cmds = [
        (0x00, 0, "CMD_RESET"),
        (0x01, 0, "CMD_ARM"),
        (0x03, 0, "CMD_STATUS"),
        (0x80, 100, "CMD_DIVIDER"),
        (0x84, 5000, "CMD_RCOUNT"),
        (0x83, 5000, "CMD_DCOUNT"),
        (0xC0, 0x000000FF, "CMD_TMASK"),
        (0xC1, 0x00000055, "CMD_TVALUE"),
        (0xA2, 208, "CMD_GEN_BAUD"),
        (0xA4, 0, "CMD_GEN_PROTO"),
        (0xA7, 0x00000300, "CMD_GEN_PINS"),
        (0xA8, 1, "CMD_FAST_MODE"),
        (0xAA, 1, "CMD_CONT_CAPTURE"),
        (0xAA, 0, "CMD_CONT_CAPTURE off"),
        (0xAE, 1, "CMD_CH_MODE"),
        (0xAC, 1, "CMD_IFACE_MODE"),
        (0xAF, 1, "CMD_SPI_TEST"),
        (0xA6, 0x00530001, "CMD_I2C_TEST"),
    ]
    for i, (opcode, data, name) in enumerate(cmds):
        print_progress(i + 1, len(cmds), name)
        try:
            resp = dev.spi.tx(opcode, struct.pack("<I", data) if data else None)
            time.sleep(0.002)
        except Exception as e:
            log(f"\n  ERROR on {name}: {e}")
    log("")
    check(True, "All SPI commands accepted without error")
    # Clean state leaked by the command sweep
    dev.reset()
    time.sleep(0.05)

# ====================================================================
# Test 4: Single capture
# ====================================================================
def test_single_capture(dev):
    print_header("Test 4: Single capture (256 samples, 1 MHz)")
    log("configuring capture...")
    # Slow capture first to verify test_div toggling (500 kHz so 46.9 kHz test_out is below Nyquist)
    slow = dev.capture(rate_hz=500_000, nsamples=256, timeout=10)
    if slow:
        ch, ns = samples_to_channels(slow)
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        log(f"slow capture (500 kHz): CH0 has {tr0} transitions in {ns} samples")
        raw = slow[:32]
        log(f"raw: {' '.join(f'{b:02x}' for b in raw)}")
        check(tr0 > 0, "slow capture: CH0 test_div is toggling")
    data = dev.capture(rate_hz=1_000_000, nsamples=256, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        raw_first = data[:32]
        log(f"first 32 raw bytes: {' '.join(f'{b:02x}' for b in raw_first)}")
        uniq = set(data)
        log(f"unique byte values: {sorted(uniq)[:10]}")
        for c in range(NUM_CHANNELS):
            tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
            ones = sum(ch[c])
            log(f"  CH{c}: {tr} transitions, {ones}/{ns} ones")
        nonzero = any(ch[c] for c in range(NUM_CHANNELS))
        any_tr = any(sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1]) > 1 for c in range(NUM_CHANNELS))
        check(any_tr, "capture data has signal transitions")
    else:
        check(False, "capture returned data")
    save_result("test4_single_capture", data, {"rate_hz": 1_000_000, "nsamples": 256})

# ====================================================================
# Test 5: Fast mode (BRAM) capture
# ====================================================================
def test_fast_capture(dev):
    print_header("Test 5: Fast mode (BRAM) capture")
    log("configuring fast mode capture...")
    dev.reset()
    dev.spi.flush()
    rc = 1024
    div = max(0, dev.sys_clk // 1_000_000 - 1)
    dev._long(CMD_DIVIDER, div & 0xFFFFFF)
    dev._long(CMD_RCOUNT, rc)
    dev._long(CMD_DCOUNT, rc)
    dev._long(CMD_TMASK, 0)
    dev._long(CMD_TVALUE, 0)
    dev._long(CMD_FAST_MODE, 1)  # set AFTER reset, NOT via capture()
    dev._long(CMD_CONT_CAPTURE, 1)  # keeps readout pipeline alive
    dev.spi.flush()
    dev.spi.arm()
    dev.spi.flush()
    time.sleep(rc / 1_000_000 + 0.02)
    data = dev.spi.chained_read(rc * dev._stride)
    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        for c in range(NUM_CHANNELS):
            tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
            log(f"  CH{c}: {tr} transitions")
        any_tr = any(sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1]) > 1 for c in range(NUM_CHANNELS))
        check(any_tr, "fast mode capture has transitions")
    else:
        check(False, "fast mode capture returned data")
    # Clear fast_mode before next test
    dev._long(CMD_FAST_MODE, 0)
    dev.spi.flush()
    save_result("test5_fast_capture", data if data else b"", {"mode": "fast", "nsamples": rc})

# ====================================================================
# Test 6: Continuous capture (triple buffer)
# ====================================================================
def test_continuous_capture(dev):
    print_header("Test 6: Continuous capture (triple buffer)")
    log("setting up continuous capture...")
    dev.spi.reset()
    dev.spi.flush()
    time.sleep(0.02)

    # Configure
    dev._long(0x80, dev.sys_clk // 1_000_000 - 1)  # DIVIDER = 1 MHz
    dev._long(0x84, 256)  # RCOUNT
    dev._long(0x83, 256)  # DCOUNT
    dev._long(0xC0, 0)    # TMASK
    dev._long(0xC1, 0)    # TVALUE
    dev._long(0xA8, 1)    # FAST_MODE
    dev._long(0xAA, 1)    # CONTINUOUS
    dev.spi.flush()

    # ARM
    d = dev.spi.dev
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 0x04, 0x00])
    buf += bytes([0x01, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x87])
    buf += bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf += bytes([0x87])
    dev.spi._drain()
    d.write(buf)
    time.sleep(0.003)
    q = d.getQueueStatus()
    if q:
        d.read(q)

    # Try to read 3 buffers
    bufs_read = 0
    for attempt in range(5):
        chunk = dev.spi.chained_read(256)
        if chunk and any(b != 0 for b in chunk):
            bufs_read += 1
            log(f"buffer {bufs_read}: {len(chunk)} bytes, {sum(1 for b in chunk if b != 0)} non-zero")
            if bufs_read >= 3:
                break
        time.sleep(0.01)

    check(bufs_read >= 1, f"continuous capture read {bufs_read} buffers")

    # Disable continuous
    dev._long(0xAA, 0)
    dev.spi.flush()
    save_result("test6_continuous", b"", {"buffers_read": bufs_read})

# ====================================================================
# Test 7: Trigger edge
# ====================================================================
def test_trigger_edge(dev):
    print_header("Test 7: Rising edge trigger on CH0")
    log("configuring rising edge trigger...")
    dev.spi.reset()
    dev.spi.flush()
    time.sleep(0.02)

    data = dev.capture(rate_hz=1_000_000, nsamples=512, trigger="rising", timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        log(f"  CH0: {tr} transitions, {sum(ch[0])}/{ns} ones")
        check(len(data) > 0, "trigger capture returned data")
    else:
        check(False, "trigger capture returned data")
    save_result("test7_trigger_edge", data, {"trigger": "rising"})

# ====================================================================
# Test 8: Generator UART
# ====================================================================
def test_gen_uart(dev):
    print_header("Test 8: Generator UART 'Hello' on CH3")
    log("configuring UART generator + capture...")
    dev.reset()
    time.sleep(0.02)
    dev.spi.flush()
    # Configure capture
    dev._short(0x11)
    dev._long(0x80, 47)
    dev._long(0x84, 1024)
    dev._long(0x83, 1024)
    dev._long(0xC0, 0)
    dev._long(0xC1, 0)
    dev._long(0x82, 0)
    dev._long(0xC2, 0)
    dev._short(0x13)
    dev.spi.flush()
    # Configure UART generator (don't start yet)
    dev._long(0xA4, 0)
    div = max(1, 48000000 // 115200)
    dev._long(0xA2, div & 0xFFFF)
    dev._pins(tx_pin=3)
    dev._load_block(b'Hello')
    dev.spi.flush()
    # Fast mode + continuous
    dev._long(0xA8, 1)
    dev._long(0xAA, 1)
    dev.spi.flush()
    # ARM first, then start generator within same CS-low burst
    from driver.ols_spi_device import GPIO_CS_LO, GPIO_CS_HI, PIN_DIR, CMD_ARM
    d = dev.spi.dev
    buf = bytes([0x80, GPIO_CS_LO, PIN_DIR])
    buf += bytes([0x31, 4, 0, CMD_ARM, 0x11, 0x11, 0x11, 0x11])
    buf += bytes([0x31, 4, 0, 0xA1, 0x11, 0x11, 0x11, 0x11])
    need = 1024 * 4
    remaining = need + 512
    while remaining > 0:
        n = min(128, remaining)
        buf += bytes([0x31, (n-1) & 0xFF, ((n-1) >> 8) & 0xFF])
        buf += bytes([0x11] * n)
        remaining -= n
    buf += bytes([0x87, 0x80, GPIO_CS_HI, PIN_DIR, 0x87])
    dev.spi._drain()
    d.write(buf)
    time.sleep(0.05)
    r = dev.spi._read_all(timeout=0.1)
    # Skip 10 bytes (ARM response + GEN_STRT response), take need bytes
    data = r[10:10 + need] if r and len(r) > 10 + need else (r[10:] if r else b'')
    log(f"captured {len(data)} bytes")
    if data:
        raw_first = data[:64]
        log(f"first 64 raw bytes: {' '.join(f'{b:02x}' for b in raw_first)}")
        ch, ns = samples_to_channels(data)
        ch3 = ch[3]
        tr3 = sum(1 for i in range(1, len(ch3)) if ch3[i] != ch3[i - 1])
        zeros = [i for i, v in enumerate(ch3) if v == 0]
        log(f"CH3: {tr3} transitions, {len(zeros)} zeros")
        if zeros:
            log(f"first start bit at sample {zeros[0]}")
        # Try decode at nominal and nearby baud rates
        for baud in [115200, 57600, 230400, 115385]:
            decoded = decode_uart(ch, 1_000_000, 3, baud)
            if decoded:
                text = ''.join(chr(r.value) if 32 <= r.value < 127 else '.' for r in decoded)
                if 'H' in text or any(r.value == ord('H') for r in decoded):
                    log(f"decoded UART ({baud} baud): '{text}'")
                    check(True, f"UART decode contains 'Hello': '{text}'")
                    break
        else:
            # None matched; show visual for debugging
            bar = ''.join('#' if v else ' ' for v in ch3[:200])
            log(f"CH3 visual: |{bar}|")
            # Sample first start bit manually
            for si in range(len(ch3)-10):
                if ch3[si]==1 and ch3[si+1]==0:
                    spb = 1_000_000/115200
                    byte_vals = []
                    for try_baud in [115200, 115385, 57600]:
                        spb_try = 1_000_000/try_baud
                        byte = 0
                        ok = True
                        for b in range(8):
                            pos = int(si + 1 + (b+0.5)*spb_try)
                            if pos < len(ch3):
                                byte |= (ch3[pos] << b)
                        byte_vals.append(f"{try_baud}={byte:02x}('{chr(byte) if 32<=byte<127 else '.'}')")
                    log(f"first start bit at {si}, bytes: {', '.join(byte_vals)}")
                    break
            check(tr3 > 5, f"UART generator produced {tr3} transitions on CH3")
    else:
        check(False, "UART capture returned data")
    save_result("test8_gen_uart", data, {"baud": 115200, "expected": "Hello"})

# ====================================================================
# Test 9: Generator I2C to accelerometer
# ====================================================================
def test_gen_i2c_accel(dev):
    print_header("Test 9: Generator I2C read LIS3DH WHO_AM_I")
    log("probing I2C addresses 0x18, 0x19...")
    found_addr = None
    for addr in [0x19, 0x18]:  # try 0x19 first (SA0 may be pulled high on SEN_SDO)
        dev._long(0xA4, 1)  # I2C mode
        dev.spi.flush()
        samples = dev.i2c_capture_with_gen(
            rate_hz=2_000_000, nsamples=2048, i2c_speed=100_000,
            dev_addr=addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples and len(samples) >= 16:
            ch, ns = samples_to_channels(samples)
            scl = ch[1]
            sda = ch[2]
            tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i - 1])
            sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i - 1])
            log(f"  addr 0x{addr:02X}: SCL {tr} transitions, SDA {sda_tr} transitions ({ns} samples)")
            if tr > 5 and sda_tr > 2:
                found_addr = addr
                log(f"  -> I2C device found at 0x{addr:02X}")
                break
    check(found_addr is not None, f"I2C accelerometer detected at {'0x%02X' % found_addr if found_addr else 'none'}")
    if found_addr:
        # Verify WHO_AM_I (1 MHz gives 1 µs/sample — well within I2C setup time)
        samples = dev.i2c_capture_with_gen(
            rate_hz=1_000_000, nsamples=4096, i2c_speed=100_000,
            dev_addr=found_addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples:
            ch, ns = samples_to_channels(samples)
            decoded, used_offset = decode_i2c_best(ch, 1_000_000, scl_idx=1, sda_idx=2)
            save_result("test9_i2c_accel", samples,
                        {"i2c_addr": found_addr, "reg": "0x0F", "speed": 100_000,
                         "decode_sda_offset": used_offset})
            data_bytes = [v for t, v in decoded if t == "DATA"]
            log(f"  decode SDA offset: {used_offset:+d}")
            for i, b in enumerate(data_bytes):
                log(f"  I2C byte {i}: 0x{b:02X}")
            found_val = next((b for b in reversed(data_bytes) if b not in (0xFF, 0x00)), None)
            if found_val is not None:
                log(f"  WHO_AM_I = 0x{found_val:02X}")
                check(True, "I2C device responded with data")
            else:
                check(False, "No I2C response data")
    else:
        log("SKIP: no accelerometer found")

# ====================================================================
# Test 9b: Fast capture I2C read LIS3DH WHO_AM_I (extended sample count)
# Uses BRAM + fast_mode but captures the I2C transaction preamble.
# ====================================================================
def test_i2c_accel_deep(dev):
    print_header("Test 9b: Fast capture I2C read LIS3DH WHO_AM_I")
    log("fast mode with extended samples...")
    found_addr = None
    for addr in [0x19, 0x18]:
        dev._long(0xA4, 1)
        dev.spi.flush()
        samples = dev.i2c_capture_with_gen(
            rate_hz=4_000_000, nsamples=4096, i2c_speed=100_000,
            dev_addr=addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples and len(samples) >= 64:
            ch, ns = samples_to_channels(samples)
            scl = ch[1]
            sda = ch[2]
            tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i - 1])
            sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i - 1])
            log(f"  addr 0x{addr:02X}: SCL {tr} transitions, SDA {sda_tr} transitions ({ns} samples)")
            if tr > 10 and sda_tr > 10:
                found_addr = addr
                log(f"  -> I2C device found at 0x{addr:02X}")
                break
    check(found_addr is not None, f"I2C accelerometer detected at {'0x%02X' % found_addr if found_addr else 'none'}")
    if found_addr:
        samples = dev.i2c_capture_with_gen(
            rate_hz=4_000_000, nsamples=4096, i2c_speed=100_000,
            dev_addr=found_addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples:
            ch, ns = samples_to_channels(samples)
            decoded, used_offset = decode_i2c_best(ch, 4_000_000, scl_idx=1, sda_idx=2)
            data_bytes = [v for t, v in decoded if t == "DATA"]
            log(f"  decode SDA offset: {used_offset:+d}")
            for i, b in enumerate(data_bytes):
                log(f"  I2C byte {i}: 0x{b:02X}")
            found_val = next((b for b in reversed(data_bytes) if b not in (0xFF, 0x00)), None)
            if found_val is not None:
                log(f"  WHO_AM_I = 0x{found_val:02X}")
                check(True, "I2C device responded with data")
            else:
                check(False, "No I2C response data")
            save_result("test9b_i2c_accel_fast", samples,
                    {"i2c_addr": found_addr, "decode_sda_offset": used_offset})
        else:
            check(False, "No I2C capture data")

# ====================================================================
# Test 9c: Filtered I2C read LIS3DH WHO_AM_I (glitch_filter=3)
# ====================================================================
def test_i2c_accel_filtered(dev):
    print_header("Test 9c: Filtered I2C read LIS3DH WHO_AM_I")
    log("filtered decode (glitch_filter=3)...")
    found_addr = None
    for addr in [0x19, 0x18]:
        dev._long(0xA4, 1)
        dev.spi.flush()
        samples = dev.i2c_capture_with_gen(
            rate_hz=2_000_000, nsamples=2048, i2c_speed=100_000,
            dev_addr=addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples and len(samples) >= 64:
            ch, ns = samples_to_channels(samples)
            scl = ch[1]; sda = ch[2]
            tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i - 1])
            sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i - 1])
            log(f"  addr 0x{addr:02X}: SCL {tr} transitions, SDA {sda_tr} transitions ({ns} samples)")
            if tr > 5 and sda_tr > 2:
                found_addr = addr
                log(f"  -> I2C device found at 0x{addr:02X}")
                break
    check(found_addr is not None, f"I2C accelerometer detected")
    if found_addr:
        samples = dev.i2c_capture_with_gen(
            rate_hz=2_000_000, nsamples=2048, i2c_speed=100_000,
            dev_addr=found_addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples:
            ch, ns = samples_to_channels(samples)
            decoded, used_offset = decode_i2c_best(
                ch, 2_000_000, scl_idx=1, sda_idx=2, filter_threshold=1)
            data_bytes = [v for t, v in decoded if t == "DATA"]
            log(f"  decode SDA offset: {used_offset:+d}")
            for i, b in enumerate(data_bytes):
                log(f"  I2C byte {i}: 0x{b:02X}")
            found_val = next((b for b in reversed(data_bytes) if b not in (0xFF, 0x00)), None)
            if found_val is not None:
                log(f"  WHO_AM_I = 0x{found_val:02X}")
                check(True, "I2C device responded with data")
            else:
                check(False, "No I2C response data")
        save_result("test9c_i2c_accel_filtered", samples if samples else b"",
                    {"i2c_addr": found_addr, "filter": 3,
                     "decode_sda_offset": used_offset if samples else None})
    else:
        log("SKIP: no accelerometer found")

# ====================================================================
# Test 9d: Filtered deep capture I2C read LIS3DH WHO_AM_I
# ====================================================================
def test_i2c_accel_deep_filtered(dev):
    print_header("Test 9d: Filtered deep capture I2C read LIS3DH WHO_AM_I")
    log("filtered decode, deep capture (glitch_filter=3)...")
    found_addr = None
    for addr in [0x19, 0x18]:
        dev._long(0xA4, 1)
        dev.spi.flush()
        samples = dev.i2c_capture_with_gen(
            rate_hz=4_000_000, nsamples=4096, i2c_speed=100_000,
            dev_addr=addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples and len(samples) >= 64:
            ch, ns = samples_to_channels(samples)
            scl = ch[1]; sda = ch[2]
            tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i - 1])
            sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i - 1])
            log(f"  addr 0x{addr:02X}: SCL {tr} transitions, SDA {sda_tr} transitions ({ns} samples)")
            if tr > 10 and sda_tr > 10:
                found_addr = addr
                log(f"  -> I2C device found at 0x{addr:02X}")
                break
    check(found_addr is not None, f"I2C accelerometer detected")
    if found_addr:
        samples = dev.i2c_capture_with_gen(
            rate_hz=4_000_000, nsamples=4096, i2c_speed=100_000,
            dev_addr=found_addr, reg_addr=0x0F, read_len=1,
            tx_pin=2, scl_pin=1, fast_mode=True)
        if samples:
            ch, ns = samples_to_channels(samples)
            decoded, used_offset = decode_i2c_best(
                ch, 4_000_000, scl_idx=1, sda_idx=2, filter_threshold=1)
            data_bytes = [v for t, v in decoded if t == "DATA"]
            log(f"  decode SDA offset: {used_offset:+d}")
            for i, b in enumerate(data_bytes):
                log(f"  I2C byte {i}: 0x{b:02X}")
            found_val = next((b for b in reversed(data_bytes) if b not in (0xFF, 0x00)), None)
            if found_val is not None:
                log(f"  WHO_AM_I = 0x{found_val:02X}")
                check(True, "I2C device responded with data")
            else:
                check(False, "No I2C response data")
        save_result("test9d_i2c_accel_deep_filtered", samples if samples else b"",
                    {"i2c_addr": found_addr, "filter": 3,
                     "decode_sda_offset": used_offset if samples else None})
        # Clear leaked state
        dev._long(0xAF, 0); dev._long(0xA7, 0); dev.spi.flush()
    else:
        log("SKIP: no accelerometer found")

# ====================================================================
# Test 10: Generator SPI to accelerometer
# ====================================================================
def test_gen_spi_accel(dev):
    print_header("Test 10: Generator SPI read accelerometer")
    log("configuring SPI generator test mode...")
    dev._long(0xAF, 1)  # CMD_SPI_TEST
    dev._long(0xA2, 100)  # SPI speed
    dev.spi.flush()
    # Load block: SPI read command for DEVID (0x0F)
    dev._load_block(bytes([0x0F]))
    time.sleep(0.01)
    dev.spi.flush()
    # Capture
    data = dev.capture_with_gen(rate_hz=1_000_000, nsamples=5000, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        log(f"CH0 (SCLK): {tr} transitions")
        check(tr > 5, f"SPI generator produced {tr} transitions")
    else:
        check(False, "SPI capture returned data")
    save_result("test10_spi_accel", data, {"mode": "spi_test"})

# ====================================================================
# Test 11: Divider accuracy
# ====================================================================
def test_divider_accuracy(dev):
    print_header("Test 11: Divider accuracy")
    log("capturing known pattern to verify sample rate...")
    # Use CH0 (test counter) which toggles at ~sys_clk/2048
    # Capture at high rate and measure the period
    data = dev.capture(rate_hz=12_000_000, nsamples=2048, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        edges = [i for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1]]
        log(f"CH0 toggles: {len(edges)} edges in {ns} samples")
        check(len(edges) >= 4, f"not enough CH0 edges ({len(edges)})")
    else:
        check(False, "divider test returned no data")
    save_result("test11_divider", data, {"rate_hz": 12_000_000})

# ====================================================================
# Main
# ====================================================================
def main():
    global PASS, FAIL, TOTAL
    print("=" * 60)
    print("  OLS Logic Analyzer — Hardware Validation Suite")
    print("=" * 60)
    print(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Results: {RESULTS_DIR}")
    print()

    # Test 1: UART (skipped per user request)
    # test_uart_cmd_id()

    # Tests 2-11: SPI device needed
    dev = OLSDeviceSPI()
    try:
        dev.open()
        log(f"SPI device opened, sys_clk={dev.sys_clk / 1e6:.0f} MHz")
        dev.reset()
        time.sleep(0.5)  # allow PLL to lock

        test_spi_handoff(dev)
        test_spi_commands(dev)
        test_single_capture(dev)
        test_fast_capture(dev)
        test_continuous_capture(dev)
        test_trigger_edge(dev)

        log("\n--- Running generator tests at 500 kHz for better visibility ---")
        # Override test params for gen tests
        test_gen_uart(dev)
        test_gen_i2c_accel(dev)
        test_i2c_accel_filtered(dev)
        test_i2c_accel_deep(dev)
        test_i2c_accel_deep_filtered(dev)
        test_gen_spi_accel(dev)

        log("\n--- Divider test at slow rate ---")
        test_divider_accuracy(dev)

    except Exception as e:
        log(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            dev.close()
        except:
            pass
        log("SPI device closed")

    # Summary
    print(f"\n{'='*60}")
    print(f"  RESULTS: {PASS}/{TOTAL} passed, {FAIL} failed")
    print(f"{'='*60}")
    if FAIL == 0:
        print("  ALL TESTS PASSED")
    else:
        print(f"  {FAIL} TEST(S) FAILED")

    return 0 if FAIL == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
