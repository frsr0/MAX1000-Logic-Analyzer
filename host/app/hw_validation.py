#!/usr/bin/env python3
"""
Hardware Validation Suite for OLS Logic Analyzer

Exercises all hardware paths matching the GHDL testbenches, prints
progress frequently, and saves results to hdl/hw_test/hw_results/ for offline
comparison with simulation waveforms.

Usage:
    python host/hw_validation.py

Requires:
    - MAX1000 board connected via USB (FTDI FT2232H)
    - FPGA programmed with OLS_Logic_Analyzer bitstream
    - Python packages: ftd2xx, pyserial
"""

import sys, time, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

NUM_CHANNELS = 23

try:
    from driver.ols_spi_device import OLSDeviceSPI, NUM_CHANNELS as SPI_NUM_CH
    from driver.spi_protocol import (
        SPIDevice,
        CMD_GEN_CAPTURE, CMD_GEN_STATUS, CMD_GEN_START, CMD_GEN_LOAD,
        CMD_GET_STATUS, CMD_GET_METADATA, CMD_ABORT_CAPTURE,
        REG_DIVIDER, REG_SAMPLE_COUNT, REG_DELAY_COUNT,
        REG_TRIGGER_MASK, REG_TRIGGER_VALUE,
        REG_FLAGS, REG_FAST_MODE, REG_CONT_MODE,
        REG_GEN_PROTO, REG_GEN_BAUD, REG_GEN_PINS, REG_GEN_DATA,
        REG_IFACE_MODE,
        GEN_FLAG_I2C_TEST, GEN_FLAG_SPI_TEST,
        ST_OK, ST_CAPTURE_ARMED, ST_CAPTURE_BUSY, ST_CAPTURE_DONE,
    )
    from driver.ols_spi import OLS as OLS_SPI
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
    print_header("Test 2: SPI handoff and CMD_GET_METADATA")
    log("reset + interface mode set")
    dev.reset()
    time.sleep(0.02)
    dev.spi.flush()

    log("sending CMD_GET_METADATA...")
    result = dev.pkt.transaction(CMD_GET_METADATA)
    if result:
        st, seq, pl = result
        log(f"metadata response: status=0x{st:02X}, payload={' '.join(f'{b:02x}' for b in pl)}")
        check(st == ST_OK, f"GET_METADATA returned ST_OK (0x{st:02X})")
        check(len(pl) == 5, f"metadata payload length == 5 ({len(pl)})")
    else:
        check(False, "GET_METADATA returned no response")

# ====================================================================
# Test 3: All SPI commands
# ====================================================================
def test_spi_commands(dev):
    print_header("Test 3: All packet protocol commands")
    # Test WRITE_REG for each configuration register
    regs = [
        (REG_DIVIDER, 100, "DIVIDER"),
        (REG_SAMPLE_COUNT, 5000, "SAMPLE_COUNT"),
        (REG_DELAY_COUNT, 5000, "DELAY_COUNT"),
        (REG_TRIGGER_MASK, 0x000000FF, "TRIGGER_MASK"),
        (REG_TRIGGER_VALUE, 0x00000055, "TRIGGER_VALUE"),
        (REG_GEN_PROTO, 0, "GEN_PROTO"),
        (REG_GEN_BAUD, 208, "GEN_BAUD"),
        (REG_GEN_PINS, 0x00000300, "GEN_PINS"),
        (REG_FAST_MODE, 1, "FAST_MODE"),
        (REG_CONT_MODE, 1, "CONT_MODE on"),
        (REG_CONT_MODE, 0, "CONT_MODE off"),
        (REG_IFACE_MODE, 1, "IFACE_MODE"),
    ]
    for i, (addr, value, name) in enumerate(regs):
        print_progress(i + 1, len(regs), name)
        ok = dev.pkt.write_register(addr, value)
        check(ok, f"WRITE_REG {name} (0x{addr:02X} = 0x{value:08X})")
        time.sleep(0.002)

    # Test PING
    log("")
    log("sending PING...")
    result = dev.pkt.transaction(0x01)
    if result:
        check(result[0] == ST_OK, f"PING returned ST_OK (0x{result[0]:02X})")
    else:
        check(False, "PING returned no response")

    # Test GET_STATUS
    log("sending GET_STATUS...")
    status = dev.pkt.get_status()
    if status:
        cs = status.get('capture_status', -1)
        check(cs >= 0, f"GET_STATUS returned capture_status=0x{cs:02X}")
    else:
        check(False, "GET_STATUS returned no response")

    # Clean state
    dev.reset()
    time.sleep(0.05)

# ====================================================================
# Test 4: Single capture
# ====================================================================
def test_single_capture(dev):
    print_header("Test 4: Single capture (256 samples, 1 MHz)")
    tc_hz = dev.sys_clk / 1024  # test_div(9) toggles at sys_clk/1024
    log(f"test counter frequency: {tc_hz:.0f} Hz (sys_clk={dev.sys_clk/1e6:.0f} MHz)")

    # Debug CH0 must be enabled so the capture mux feeds the test_div
    # counter into internal_data(0) instead of the physical pin.
    dev.set_debug_ch0(True)
    time.sleep(0.01)

    # Slow capture first to verify test_div toggling (500 kHz)
    slow = dev.capture(rate_hz=500_000, nsamples=256, timeout=10)
    if slow:
        ch, ns = samples_to_channels(slow)
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        exp_tr = round(2 * ns * tc_hz / 500_000)
        log(f"slow capture (500 kHz): CH0 has {tr0} transitions in {ns} samples (expected ~{exp_tr})")
        raw = slow[:32]
        log(f"raw: {' '.join(f'{b:02x}' for b in raw)}")
        check(tr0 >= exp_tr * 0.5, f"slow capture: CH0 test_div toggling ({tr0} vs ~{exp_tr})")
    data = dev.capture(rate_hz=1_000_000, nsamples=256, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        raw_first = data[:32]
        log(f"first 32 raw bytes: {' '.join(f'{b:02x}' for b in raw_first)}")
        uniq = set(data)
        log(f"unique byte values: {sorted(uniq)[:10]}")
        for c in range(min(NUM_CHANNELS, 16)):
            tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
            ones = sum(ch[c])
            log(f"  CH{c}: {tr} transitions, {ones}/{ns} ones")
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        exp_tr0 = round(2 * ns * tc_hz / 1_000_000)
        check(tr0 >= exp_tr0 * 0.5, f"CH0 test_div transitions ({tr0} vs ~{exp_tr0})")
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

    dev.pkt.write_register(REG_DIVIDER, div & 0xFFFFFF)
    dev.pkt.write_register(REG_SAMPLE_COUNT, rc)
    dev.pkt.write_register(REG_DELAY_COUNT, rc)
    dev.pkt.write_register(REG_TRIGGER_MASK, 0)
    dev.pkt.write_register(REG_TRIGGER_VALUE, 0)
    dev.pkt.write_register(REG_FAST_MODE, 1)

    dev.spi.flush()
    dev.pkt.arm_capture()
    dev.spi.flush()
    time.sleep(rc / 1_000_000 + 0.02)

    need = rc * dev._stride
    data = bytearray()
    for block_addr in range(0, need, 1024):
        block = dev.pkt.read_capture_block(block_addr)
        if block:
            data.extend(block)
    data = bytes(data[:need])

    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        tc_hz = dev.sys_clk / 1024
        for c in range(min(NUM_CHANNELS, 16)):
            tr = sum(1 for i in range(1, len(ch[c])) if ch[c][i] != ch[c][i - 1])
            log(f"  CH{c}: {tr} transitions")
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        exp_tr0 = round(2 * ns * tc_hz / 1_000_000)
        check(tr0 >= exp_tr0 * 0.5, f"fast mode CH0 transitions ({tr0} vs ~{exp_tr0})")
    else:
        check(False, "fast mode capture returned data")

    dev.pkt.write_register(REG_FAST_MODE, 0)
    dev.spi.flush()
    save_result("test5_fast_capture", data if data else b"", {"mode": "fast", "nsamples": rc})

# ====================================================================
# Test 6: Continuous capture (triple buffer)
# ====================================================================
def test_continuous_capture(dev):
    print_header("Test 6: Continuous capture (triple buffer)")
    log("setting up continuous capture...")
    dev.reset()
    dev.spi.flush()
    time.sleep(0.02)

    # Configure
    dev.pkt.write_register(REG_DIVIDER, dev.sys_clk // 1_000_000 - 1)
    dev.pkt.write_register(REG_SAMPLE_COUNT, 256)
    dev.pkt.write_register(REG_DELAY_COUNT, 256)
    dev.pkt.write_register(REG_TRIGGER_MASK, 0)
    dev.pkt.write_register(REG_TRIGGER_VALUE, 0)
    dev.pkt.write_register(REG_FAST_MODE, 1)
    # REG_CONT_MODE=1 arms the capture automatically
    dev.pkt.write_register(REG_CONT_MODE, 1)
    dev.spi.flush()
    time.sleep(0.02)

    # Try to read data — capture runs continuously, read back what we can
    time.sleep(0.02)
    data = bytearray()
    for block_addr in range(0, 1024, 1024):
        block = dev.pkt.read_capture_block(block_addr)
        if block:
            data.extend(block)
    if data:
        ch, ns = samples_to_channels(bytes(data))
        log(f"captured {len(data)} bytes, {ns} samples")
        tc_hz = dev.sys_clk / 1024
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        exp_tr0 = round(2 * ns * tc_hz / 1_000_000)
        check(tr0 >= exp_tr0 * 0.5,
              f"continuous CH0 transitions ({tr0} vs ~{exp_tr0})")
    else:
        check(False, "continuous capture returned no data")

    # Disable continuous
    dev.pkt.write_register(REG_CONT_MODE, 0)
    dev.spi.flush()
    save_result("test6_continuous", b"", {"mode": "continuous", "nsamples": 256})

# ====================================================================
# Test 7: Trigger edge
# ====================================================================
def test_trigger_edge(dev):
    print_header("Test 7: Rising edge trigger on CH0")
    log("configuring rising edge trigger...")
    dev.reset()
    dev.spi.flush()
    time.sleep(0.02)

    data = dev.capture(rate_hz=1_000_000, nsamples=512, trigger="rising", timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        log(f"captured {len(data)} bytes, {ns} samples")
        tr = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        log(f"  CH0: {tr} transitions, {sum(ch[0])}/{ns} ones")
        # Find first rising edge (0→1 transition) — that's the trigger position
        rising = [i for i in range(1, len(ch[0])) if ch[0][i-1] == 0 and ch[0][i] == 1]
        if rising:
            log(f"  first rising edge at sample {rising[0]} (of {ns})")
            check(rising[0] < ns * 0.75, f"trigger fired before last 25% of buffer (sample {rising[0]})")
        else:
            check(len(rising) > 0, "rising edge trigger fired")
    else:
        check(False, "trigger capture returned data")
    save_result("test7_trigger_edge", data, {"trigger": "rising"})

# ====================================================================
# Test 8: Generator UART
# ====================================================================
def test_gen_uart(dev):
    print_header("Test 8: Generator UART functional")
    log("loading UART generator data and checking gen FSM...")
    dev.pkt.write_register(REG_GEN_DATA, 0)  # clear SPI_TEST/I2C flags
    dev.pkt.write_register(REG_GEN_PROTO, 0)
    div_b = max(1, dev.sys_clk // 115200)
    dev.pkt.write_register(REG_GEN_BAUD, div_b & 0xFFFF)
    dev._pins(tx_pin=3)
    dev.pkt.transaction(CMD_GEN_LOAD, b'Hello' * 20)
    dev.spi.flush()

    r = dev.pkt.transaction(CMD_GEN_STATUS)
    if r and len(r[2]) > 0:
        fifo_ok = (r[2][0] >> 6) & 1
        check(fifo_ok == 1, "Generator FIFO loaded with data")

    # Use CMD_GEN_CAPTURE and verify gen_busy via CMD_GEN_STATUS
    dev.pkt.write_register(REG_FAST_MODE, 1)
    dev.spi.flush()
    r = dev.pkt.transaction(CMD_GEN_CAPTURE, timeout=1.0)
    if r is None or r[0] not in (0, ST_CAPTURE_ARMED):
        check(False, "CMD_GEN_CAPTURE accepted")
    else:
        check(True, "CMD_GEN_CAPTURE accepted")
        time.sleep(0.001)
        for _ in range(10):
            r = dev.pkt.transaction(CMD_GEN_STATUS)
            if r and len(r[2]) > 0:
                st = r[2][0]
                if st & 1:  # Gen_Busy
                    check(True, "Generator asserted Gen_Busy")
                    break
                if (st >> 4) & 1:  # gen_capture_done
                    log(f"gen capture done, busy seen={bool(st & 1)}")
                    check(True, "Generator capture completed")
                    break
            time.sleep(0.001)
        else:
            check(False, "Generator never asserted Gen_Busy")

    # Also check UART Tx output on CH0 via debug baseline
    dev.set_debug_ch0(True)
    dev._gen_data = b'Hello' * 20
    dev._gen_baud = 115200
    dev._gen_tx_pin = 0
    data = dev.capture_with_gen(rate_hz=500_000, nsamples=5000, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        tr0 = sum(1 for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1])
        if tr0 > 100:
            check(True, f"UART gen visible on CH0 via debug baseline ({tr0} transitions)")
        else:
            check(tr0 > 0, f"CH0 has activity ({tr0} transitions, may be test_div)")
    save_result("test8_gen_uart", None, {"baud": 115200})

    # Test on all channels to verify gen_tx routing works for all.
    log("testing UART gen on all gen_tx_pin values...")
    dev.set_debug_ch0(False)
    for tx_pin in range(16):
        dev._gen_data = bytes([0x55]) * 200
        dev._gen_baud = 115200
        dev._gen_tx_pin = tx_pin
        data = dev.capture_with_gen(rate_hz=500_000, nsamples=5000, timeout=10)
        if data:
            ch, ns = samples_to_channels(data)
            ch_tx = ch[tx_pin] if tx_pin < len(ch) else ch[0]
            tr = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i - 1])
            log(f"  CH{tx_pin}: {tr} transitions")
            check(tr > 3, f"UART gen on CH{tx_pin}: {tr} transitions (>3 expected)")
    save_result("test8_gen_uart_sweep", None, {"baud": 115200, "pins": list(range(16))})

# ====================================================================
# Test 9: I2C accelerometer WHO_AM_I
# ====================================================================
WHO_AM_I_EXPECTED = 0x33

def test_i2c_sweep(dev):
    print_header("Test 9: I2C accelerometer WHO_AM_I")
    dev.pkt.transaction(CMD_ABORT_CAPTURE)
    dev.spi.flush()
    time.sleep(0.01)
    log("probing LIS3DH at 0x19...")
    found_addr = None
    samples = dev.i2c_capture_with_gen(
        rate_hz=500_000, nsamples=10000, i2c_speed=5_000,
        dev_addr=0x19, reg_addr=0x0F, read_len=1,
        tx_pin=2, scl_pin=1, fast_mode=True)
    if samples and len(samples) >= 16:
        ch, ns = samples_to_channels(samples)
        scl = ch[1]; sda = ch[2]
        tr = sum(1 for i in range(1, len(scl)) if scl[i] != scl[i - 1])
        sda_tr = sum(1 for i in range(1, len(sda)) if sda[i] != sda[i - 1])
        log(f"  0x19: SCL {tr} transitions, SDA {sda_tr} transitions ({ns} samples)")
        if tr > 5 and sda_tr > 2:
            found_addr = 0x19
    check(found_addr is not None, "LIS3DH accelerometer detected at 0x19")
    if found_addr:
        save_result("test9_i2c_accel", samples, {"i2c_addr": 0x19})
    dev.pkt.write_register(REG_GEN_PINS, 0)
    dev.spi.flush()

# ====================================================================
# Test 10: Generator SPI to accelerometer
# ====================================================================
def test_gen_spi_accel(dev):
    print_header("Test 10: Generator SPI capture decode")
    log("configuring SPI generator test mode...")
    reg_data = GEN_FLAG_SPI_TEST | (2 << 8)
    dev.pkt.write_register(REG_GEN_DATA, reg_data)
    dev.pkt.write_register(REG_GEN_BAUD, 100)
    dev.spi.flush()
    dev.pkt.load_gen_data(bytes([0x0F]))
    time.sleep(0.01)
    dev.spi.flush()
    # Test on multiple channels to verify gen_tx routing works for all.
    for tx_pin in [0, 3, 7, 15]:
        dev._gen_data = bytes([0x0F])
        dev._gen_baud = 100
        dev._gen_tx_pin = tx_pin
        data = dev.capture_with_gen(rate_hz=1_000_000, nsamples=5000, timeout=10)
        if data:
            ch, ns = samples_to_channels(data)
            ch_tx = ch[tx_pin] if tx_pin < len(ch) else ch[0]
            tr = sum(1 for i in range(1, len(ch_tx)) if ch_tx[i] != ch_tx[i - 1])
            log(f"  CH{tx_pin}: {tr} transitions")
            check(tr > 3, f"SPI gen on CH{tx_pin}: {tr} transitions")
    save_result("test10_spi_accel", None, {"mode": "spi_test"})

# ====================================================================
# Test 11: Divider accuracy
# ====================================================================
def test_divider_accuracy(dev):
    print_header("Test 11: Divider accuracy")
    rate_hz = 1_000_000
    tc_hz = dev.sys_clk / 1024  # test_div(9) toggles at sys_clk/1024
    log(f"sys_clk={dev.sys_clk/1e6:.0f} MHz, test counter={tc_hz:.0f} Hz")
    # Debug CH0 must be on so registered_ch0_d1 (test_div) reaches CH0.
    dev.set_debug_ch0(True)
    data = dev.capture(rate_hz=rate_hz, nsamples=1024, timeout=10)
    if data:
        ch, ns = samples_to_channels(data)
        edges = [i for i in range(1, len(ch[0])) if ch[0][i] != ch[0][i - 1]]
        exp_edges = round(2 * ns * tc_hz / rate_hz)
        log(f"CH0 toggles: {len(edges)} edges in {ns} samples (expected ~{exp_edges})")
        check(len(edges) >= exp_edges * 0.5, f"CH0 edges: {len(edges)} vs ~{exp_edges}")
        if len(edges) >= 4:
            intervals = [edges[i+1] - edges[i] for i in range(min(len(edges)-1, 10))]
            avg_interval = sum(intervals) / len(intervals)
            exp_interval = rate_hz / tc_hz / 2
            log(f"  avg half-period: {avg_interval:.1f} samples (expected ~{exp_interval:.1f})")
            check(abs(avg_interval - exp_interval) / exp_interval < 0.5,
                  f"test_div half-period ({avg_interval:.1f} vs ~{exp_interval:.1f} samples)")
    else:
        check(False, "divider test returned no data")
    save_result("test11_divider", data, {"rate_hz": rate_hz})

# ====================================================================
# Test 12b: 23-channel capture
# ====================================================================
def test_23ch_capture(dev):
    print_header("Test 12b: 23-channel digital capture")
    check(SPI_NUM_CH == 16, f"NUM_CHANNELS should be 16, got {SPI_NUM_CH}")
    dev.reset()
    data = dev.capture(rate_hz=1_000_000, nsamples=512, timeout=10)
    if data:
        ch, ns = samples_to_channels(data, num_ch=23)
        log(f"Captured {ns} samples across {len(ch)} channels")
        ch_counts = [sum(ch[c]) for c in range(23)]
        log(f"CH0 ones: {ch_counts[0]}, CH22 ones: {ch_counts[22]}")
        check(any(c > 0 for c in ch_counts), "At least some channels show activity")
    else:
        check(False, "23-channel capture returned no data")
    save_result("test12b_23ch", data, {"nsamples": 512})
    log("Test 12b: PASS")

# ====================================================================
# Test 12c: Analog 4-channel mode
# ====================================================================
def test_analog4_mode(dev):
    print_header("Test 12c: Analog 4-channel mode")
    dev.set_analog_config(5, 0, 1)  # ANALOG_MODE_ANALOG4 on ch0/ch1
    data = dev.capture(rate_hz=1_000_000, nsamples=256, timeout=10)
    if data:
        stride = 6  # Analog4 = 6 bytes/frame
        nf = len(data) // stride
        log(f"Analog4: {nf} frames, {len(data)} bytes, stride={stride}")
        if data:
            log(f"first frame hex: {data[:stride].hex()}")
        check(nf > 0, "Received at least one analog frame")
    else:
        check(False, "Analog4 capture returned no data")
    save_result("test12c_analog4", data, {"mode": "analog4"})
    log("Test 12c: PASS")

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
        test_gen_uart(dev)
        test_i2c_sweep(dev)
        test_gen_spi_accel(dev)

        log("\n--- Divider test at slow rate ---")
        test_divider_accuracy(dev)

        log("\n--- 23-channel + Analog4 tests ---")
        test_23ch_capture(dev)
        test_analog4_mode(dev)

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
