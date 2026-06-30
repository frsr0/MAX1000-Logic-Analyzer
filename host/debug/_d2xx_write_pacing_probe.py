"""D2XX write-pacing diagnosis probe.

Goal: confirm WHERE the ~0.7 MB/s (~310 kS/s) ceiling comes from, by
separating two hypotheses that have opposite fixes:

  A. USB-write driver-bound  -> dev.write() itself caps near 0.7 MB/s
       regardless of what the bytes do (FIFO drains instantly).
       => genuinely a driver/firmware limit, unfixable in our code.

  B. SPI-clock / FIFO-drain bound -> dev.write() only blocks because the
       4 KB MPSSE TX FIFO drains at the SPI *clock* rate, and per-block
       round-trips (getQueueStatus polling) add latency.
       => fixable: raise SPI clock / batch larger reads.

Recall the read mechanic: to READ n bytes the MPSSE driver must WRITE n
0x11 NOP bytes (0x20 is avoided because it floats MOSI low = 0x00 =
CMD_RESET on the FPGA). So read throughput is bounded by write throughput
by construction. That is why "write pacing" gates the whole readout.

The four tests:

  A  raw USB write   : push a big buffer of fast-executing GPIO-set MPSSE
                       commands (0x80 v dir). The FIFO drains ~instantly,
                       so write() time == pure USB bulk-OUT throughput.
  B  SPI clock-out   : one big 0x11 block clocks n bytes out MOSI at the
                       configured SPI clock. FIFO drains at the WIRE rate.
  C  block read      : real read_capture_block() loop (1 KB blocks, each a
                       polled round-trip) -- what deep capture actually does.
  D  CS-held stream  : one big stream_read() -- minimal per-block overhead.

Interpretation:
  A ~0.7         -> hypothesis A: USB-write driver bound (user is right).
  A fast, B ~0.7 -> SPI clock not really 30 MHz / clocking overhead.
  A,B fast, C~0.7, D fast -> per-block round-trip overhead (code-fixable).
  A,B fast, D~0.7         -> read coupling (NOP-write+MISO) is the limit.

Usage: python host/debug/_d2xx_write_pacing_probe.py [spi_speed_hz]
       default spi_speed_hz = 30000000
"""
import os
import sys
import time

root = os.getcwd()
sys.path.insert(0, os.path.join(root, 'host'))
sys.path.insert(0, os.path.join(root, 'host', 'driver'))

from ols_spi import OLS as OLS_SPI, GPIO_CS_HI, GPIO_CS_LO, PIN_DIR
from ols_spi_device import OLSDeviceSPI


def _mbps(nbytes, secs):
    return (nbytes / 1e6) / secs if secs > 0 else float('inf')


def test_a_raw_usb_write(spi, nbytes=1 << 20, reps=5):
    """Raw USB bulk-OUT throughput: GPIO-set commands drain ~instantly."""
    # 0x80 v dir == set low-byte GPIO; executes in ~1 FTDI cycle, so the
    # FIFO never backs up and write() time is pure USB transfer time.
    triple = bytes([0x80, GPIO_CS_HI, PIN_DIR])
    buf = triple * (nbytes // 3)
    spi._drain()
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        spi.dev.write(buf)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return len(buf), best, _mbps(len(buf), best)


def test_b_spi_clock_out(spi, nbytes=1 << 16, reps=5):
    """Clock n bytes out MOSI at the SPI clock; FIFO drains at the WIRE rate.

    0x11 is WRITE-ONLY (no MISO capture), max length 65536 bytes. We time
    dev.write() alone: with a payload far larger than the 4 KB TX FIFO,
    write() must block until the wire has clocked the bytes out, so the
    elapsed time measures FIFO-drain-at-SPI-clock -- i.e. exactly the
    "write pacing" the original hypothesis blames.
    """
    n = min(nbytes, 65536)
    payload = bytes([0x11] * n)
    cmd = (bytes([0x80, GPIO_CS_LO, PIN_DIR])
           + bytes([0x11, (n - 1) & 0xFF, ((n - 1) >> 8) & 0xFF])
           + payload
           + bytes([0x87, 0x80, GPIO_CS_HI, PIN_DIR, 0x87]))
    spi._drain()
    best = None
    for _ in range(reps):
        spi._drain()
        t0 = time.perf_counter()
        spi.dev.write(cmd)   # write-only opcode: blocks until FIFO drains
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return n, best, _mbps(n, best)


def test_d_split(spi, nbytes=1 << 16, reps=3):
    """Decompose a CS-held read into write-half vs read-drain-half.

    Mirrors stream_read() but times dev.write(NOP block) separately from
    _read_n() draining MISO. This is the decisive test:
      write-half dominant  -> write/FIFO pacing (original hypothesis)
      read-half  dominant  -> MISO drain (getQueueStatus poll + USB IN) bound
    """
    n = min(nbytes, 65536)
    buf = (bytes([0x80, GPIO_CS_LO, PIN_DIR])
           + bytes([0x31, (n - 1) & 0xFF, ((n - 1) >> 8) & 0xFF])
           + bytes([0x11] * n)
           + bytes([0x87, 0x80, GPIO_CS_HI, PIN_DIR, 0x87]))
    best = None
    for _ in range(reps):
        spi._drain()
        t0 = time.perf_counter()
        spi.dev.write(buf)
        t1 = time.perf_counter()
        r = spi._read_n(n, timeout=3.0)
        t2 = time.perf_counter()
        w_dt, r_dt = t1 - t0, t2 - t1
        if best is None or (w_dt + r_dt) < best[0]:
            best = (w_dt + r_dt, w_dt, r_dt, len(r) if r else 0)
    tot, w_dt, r_dt, got = best
    return n, got, w_dt, r_dt, _mbps(n, w_dt), _mbps(got, r_dt)


def test_c_block_reads(dev, nblocks=64):
    """Real deep-capture readout: read_capture_block() in a tight loop.

    Arms and completes a real fixed-duration capture first so the SDRAM
    holds data, then times the block-read loop (1 KB blocks, each a polled
    round-trip through _transaction_raw).
    """
    dev.set_analog_config(0)
    nsamp = nblocks * 512 + 1024
    # 5 MHz capture of nsamp samples: completes fast, fills SDRAM.
    data = dev.capture(rate_hz=5_000_000, nsamples=nsamp, timeout=5)
    if not data:
        return 0, 0.0, 0.0, 0
    blk = 0
    got = 0
    dev.pkt.read_capture_block(0)  # prime: discard cold first read
    t0 = time.perf_counter()
    for i in range(nblocks):
        b = dev.pkt.read_capture_block((i * 512) * 2)
        if not b:
            break
        got += len(b)
        blk += 1
    dt = time.perf_counter() - t0
    return got, dt, _mbps(got, dt), blk


def test_d_stream_read(spi, nbytes=1 << 16, reps=3):
    """One big CS-held NOP read (streaming path), minimal per-block cost."""
    best = None
    for _ in range(reps):
        spi._drain()
        t0 = time.perf_counter()
        r = spi.stream_read(nbytes)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return len(r) if r else 0, best, _mbps(nbytes, best)


def main():
    speed = int(sys.argv[1]) if len(sys.argv) > 1 else 30_000_000
    print(f"== D2XX write-pacing probe (SPI clock target {speed/1e6:.1f} MHz) ==")

    dev = OLSDeviceSPI()
    dev.spi = OLS_SPI(speed_hz=speed)
    dev.spi.open()
    from spi_protocol import SPIDevice
    dev.pkt = SPIDevice(dev.spi)
    dev._detect_sample_clk()
    spi = dev.spi

    # --fix : re-issue the clock config with the CORRECT divide-by-5-disable
    # opcode (0x8A). The driver's open() uses 0x94 (a bad command), so the
    # /5 prescaler stays enabled and the wire runs at 1/5 of the intended
    # rate. If this flips B up ~5x, that one byte is the whole bottleneck.
    if '--fix' in sys.argv:
        div = max(0, 60_000_000 // (2 * speed) - 1)
        spi.dev.write(bytes([
            0x8A,                                 # CORRECT: disable clock /5
            0x86, div & 0xFF, (div >> 8) & 0xFF,  # re-load divisor (now /60MHz)
        ]))
        time.sleep(0.01)
        spi._drain()
        print("   [--fix] re-issued 0x8A (disable /5) + divisor "
              f"-> wire should now be ~{(60_000_000/(2*(div+1)))/1e6:.1f} MHz")

    # Report the actual MPSSE clock divisor / effective SPI clock.
    div = max(0, 60_000_000 // (2 * speed) - 1)
    eff = 60_000_000 / (2 * (div + 1))
    print(f"   MPSSE divisor={div} -> effective SPI clock ~{eff/1e6:.2f} MHz "
          f"(wire ceiling ~{eff/8/1e6:.2f} MB/s)")
    print(f"   sample_clk detected = {dev.sample_clk/1e6:.1f} MHz")
    print()

    n, dt, mb = test_a_raw_usb_write(spi)
    print(f"A  raw USB write   : {n} B in {dt*1e3:7.2f} ms = {mb:6.2f} MB/s")

    n, dt, mb = test_b_spi_clock_out(spi)
    print(f"B  SPI clock-out   : {n} B in {dt*1e3:7.2f} ms = {mb:6.2f} MB/s "
          f"(write-only, FIFO drains at wire)")

    n, dt, mb = test_d_stream_read(spi)
    print(f"D  CS-held stream  : {n} B in {dt*1e3:7.2f} ms = {mb:6.2f} MB/s")

    n, got, w_dt, r_dt, w_mb, r_mb = test_d_split(spi)
    print(f"D' split  write    : {n} B in {w_dt*1e3:7.2f} ms = {w_mb:6.2f} MB/s "
          f"(NOP-write half)")
    print(f"   split  read     : {got} B in {r_dt*1e3:7.2f} ms = {r_mb:6.2f} MB/s "
          f"(MISO-drain half)")

    try:
        got, dt, mb, blk = test_c_block_reads(dev)
        if blk == 0 or dt <= 0:
            print("C  block reads     : no data (capture returned empty)")
        else:
            print(f"C  block reads     : {got} B / {blk} blks in {dt*1e3:7.2f} ms "
                  f"= {mb:6.2f} MB/s = {(got//2)/dt/1e3:6.0f} kS/s")
    except Exception as e:
        print(f"C  block reads     : ERROR {e}")

    print()
    print("Read the differential against the table in the file header.")
    dev.close()


if __name__ == "__main__":
    main()
