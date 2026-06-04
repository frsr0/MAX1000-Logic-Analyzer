"""
I2C accelerometer (LIS3DH) capture verification.

Tests I2C read of WHO_AM_I register across multiple sample rates and
capture modes: BRAM (fast), SDRAM (deep), and rolling (continuous).
Prints progress per-test so it never appears stalled.
"""
import sys, time, threading
sys.path.insert(0, '.')
from ols_spi_device import OLSDeviceSPI


I2C_ADDRS = [0x18, 0x19]

SAMPLE_RATES_FAST = [200_000, 400_000, 1_000_000, 4_000_000]
SAMPLE_RATES_DEEP = [200_000, 1_000_000, 4_000_000, 8_000_000, 12_000_000, 24_000_000]

NSAMPLES_FAST = 1024
NSAMPLES_DEEP = 50000

I2C_SPEED = 100000
TX_PIN = 2
SCL_PIN = 1
REG_WHO_AM_I = 0x0F
WHO_AM_I_VALUE = 0x33


def samples_to_channels(data, num_ch=8, stride=4):
    """Convert raw capture bytes to per-channel lists."""
    ns = len(data) // stride
    ch = [[] for _ in range(num_ch)]
    for i in range(ns):
        byte = data[i * stride]
        for c in range(num_ch):
            ch[c].append((byte >> c) & 1)
    return ch, ns


def decode_i2c(ch, samplerate, scl_idx=2, sda_idx=3):
    """Simple I2C decoder. Returns list of (type, value)."""
    scl = ch[scl_idx]
    sda = ch[sda_idx]
    result = []
    i = 0
    while i < len(scl) - 20:
        if scl[i] == 1 and sda[i] == 1 and i + 1 < len(scl) and scl[i + 1] == 1 and sda[i + 1] == 0:
            result.append(("START", None))
            for _ in range(20):
                byte = 0
                for b in range(8):
                    while i < len(scl) - 1 and not (scl[i] == 0 and scl[i + 1] == 1):
                        i += 1
                    i += 1
                    if i >= len(scl):
                        break
                    byte = (byte << 1) | sda[i]
                while i < len(scl) - 1 and not (scl[i] == 0 and scl[i + 1] == 1):
                    i += 1
                result.append(("DATA", byte))
                if i + 2 < len(scl) and scl[i] == 1 and sda[i - 1] == 0 and sda[i] == 1:
                    result.append(("STOP", None))
                    break
            break
        i += 1
    return result


def decode_and_verify(samples, rate_hz):
    if len(samples) < 16:
        return False, []
    ch, ns = samples_to_channels(samples)
    decoded = decode_i2c(ch, rate_hz, scl_idx=SCL_PIN, sda_idx=TX_PIN)
    found = any(typ == "DATA" and val == WHO_AM_I_VALUE for typ, val in decoded)
    return found, decoded


def find_i2c_address(dev):
    for addr in I2C_ADDRS:
        print(f"  Trying address 0x{addr:02X}...", end=" ")
        sys.stdout.flush()
        dev.reset()
        time.sleep(0.02)
        dev.spi.flush()
        samples = dev.i2c_capture_with_gen(
            rate_hz=400_000, nsamples=2000, i2c_speed=I2C_SPEED,
            dev_addr=addr, reg_addr=REG_WHO_AM_I, read_len=1,
            tx_pin=TX_PIN, scl_pin=SCL_PIN, fast_mode=True)
        ok, decoded = decode_and_verify(samples, 400_000)
        if ok:
            print(f"found 0x{addr:02X}")
            return addr
        print("no response")
    return None


def run_rate_test(dev, rate_hz, nsamples, fast_mode):
    cap_time = nsamples / rate_hz
    timeout = max(6, int(cap_time * 4) + 2)
    samples = dev.i2c_capture_with_gen(
        rate_hz=rate_hz, nsamples=nsamples, i2c_speed=I2C_SPEED,
        dev_addr=I2C_ADDRS[0], reg_addr=REG_WHO_AM_I, read_len=1,
        tx_pin=TX_PIN, scl_pin=SCL_PIN, fast_mode=fast_mode,
        timeout=timeout)
    if not samples:
        print("  FAIL (no data)")
        return False
    ok, decoded = decode_and_verify(samples, rate_hz)
    if ok:
        print(f"  PASS ({len(samples)//4} samples, {len(decoded)} decoded)")
        return True
    if decoded:
        tx = ' '.join(f"0x{v:02X}" if v is not None else str(t) for t, v in decoded)
        print(f"  DECODE ({tx})")
    else:
        print(f"  NO I2C ({len(samples)} raw bytes)")
    return False


def test_i2c_fast(dev, addr):
    print(f"\n--- BRAM Fast Capture @ {I2C_SPEED/1e3:.0f} kHz I2C ---")
    ok = 0
    for i, rate in enumerate(SAMPLE_RATES_FAST):
        label = f"[{i+1}/{len(SAMPLE_RATES_FAST)}]  {rate/1e6:.2f} MHz  {NSAMPLES_FAST} samples"
        print(f"  {label}")
        if run_rate_test(dev, rate, NSAMPLES_FAST, fast_mode=True):
            ok += 1
    print(f"  -> FAST: {ok}/{len(SAMPLE_RATES_FAST)} passed")
    return ok == len(SAMPLE_RATES_FAST)


def test_i2c_sdram(dev, addr):
    print(f"\n--- SDRAM Deep Capture @ {I2C_SPEED/1e3:.0f} kHz I2C ---")
    ok = 0
    for i, rate in enumerate(SAMPLE_RATES_DEEP):
        nsamp = max(NSAMPLES_DEEP, int(0.002 * rate))
        label = f"[{i+1}/{len(SAMPLE_RATES_DEEP)}]  {rate/1e6:.2f} MHz  {nsamp} samples"
        print(f"  {label}")
        if run_rate_test(dev, rate, nsamp, fast_mode=False):
            ok += 1
    print(f"  -> DEEP: {ok}/{len(SAMPLE_RATES_DEEP)} passed")
    return ok == len(SAMPLE_RATES_DEEP)


def test_i2c_rolling(dev, addr):
    print(f"\n--- Rolling Continuous Capture @ 1 MHz ---")
    stop_evt = threading.Event()
    seen = False
    nbufs = 0
    try:
        for buf, seq, bufsz in dev.i2c_rolling_capture(
                rate_hz=1_000_000, chunk_nsamp=2048, buffer_nsamp=8192,
                stop_evt=stop_evt, i2c_speed=I2C_SPEED,
                dev_addr=addr, reg_addr=REG_WHO_AM_I, read_len=1,
                tx_pin=TX_PIN, scl_pin=SCL_PIN):
            nbufs += 1
            ok, decoded = decode_and_verify(buf, 1_000_000)
            if ok:
                seen = True
            tx = ' '.join(f"0x{v:02X}" if v is not None else str(t) for t, v in decoded[:6]) if decoded else "(empty)"
            print(f"  Buffer {nbufs}: {len(buf)}B  WHO_AM_I={'OK' if ok else '--'}  [{tx}]")
            if nbufs >= 3:
                break
    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        stop_evt.set()
    print(f"  -> ROLLING: {'PASS' if seen else 'FAIL'} ({nbufs} buffers)")
    return seen


if __name__ == '__main__':
    dev = OLSDeviceSPI()
    try:
        dev.open()
        print(f"Device opened  sys_clk={dev.sys_clk/1e6:.0f} MHz")

        print("Scanning LIS3DH I2C address...")
        addr = find_i2c_address(dev)
        if addr is None:
            print("  FAIL: no LIS3DH detected at 0x18 or 0x19")
            print("  Continuing with 0x18...")
            addr = 0x18

        I2C_ADDRS[0] = addr

        all_ok = True
        all_ok &= test_i2c_fast(dev, addr)
        all_ok &= test_i2c_sdram(dev, addr)
        all_ok &= test_i2c_rolling(dev, addr)

        print(f"\n{'='*50}")
        print(f"OVERALL: {'ALL TESTS PASS' if all_ok else 'SOME FAILURES'}")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        dev.close()
        print("Device closed")
