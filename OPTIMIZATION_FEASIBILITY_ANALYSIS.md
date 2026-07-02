# Data-Rate Status & Optimization Analysis (rewritten 2026-07-02, evening update)

> The previous version of this document analyzed optimizations on top of
> "2.8 MB/s compressed streaming". That baseline was invalid: the
> CMD_START_STREAM raw/compressed wire paths never returned capture data on
> this branch (the FPGA repeats an idle byte after the ack), so the number
> measured garbage throughput. Everything below is re-derived from validated
> measurements (debug-ch0 square wave + bit-exact repeat-read checks).

## What is validated today (hw_validation_results.txt)

All numbers are REAL samples (the write-pump duplication is fixed — see the
evening-update section; historic numbers counted every sample twice).

- **SPI link:** 30 MHz SCK, FT232H MPSSE, source-synchronous MISO. Stable.
- **Readback (post-capture):** batched CS-held `CMD_READ_CAPTURE` block reads —
  **2.1–2.3 MB/s = ~1.1 M real samples/s**, bit-exact (0 mismatches on repeat
  and phase-shifted reads), 4M-real-sample deep capture read in ~4 s.
- **Continuous live streaming (gapless):** lossless at 0.5 MS/s (zero
  overruns), reader ceiling ~0.9–1.0 MS/s (wire-limited); interval-exact
  waveform data at every rate tested.
- **During-capture batched reads:** 32/32 blocks at 1/2/5 MS/s, no watchdogs,
  no stale data (was: total wedge).
- **UART generator round-trip:** 115200 request decodes at 115200 (the old
  //2 divider fudge made the generator physically transmit at 2× baud).
- **Deep-capture integrity:** 98% interval purity @ 2 MS/s (was 87% — most of
  the old "glitches" were duplication artifacts, not write drops).

## Evening update — the three RTL fixes that got here

1. **Idle-prefetch removal (OLS_Interface):** a prefetched block read was
   never released; its stuck ack made the next host request serve the wrong
   address's data while that request's own issue was swallowed, and the
   prefetch reissued endlessly during capture, stealing the SDRAM bus.
2. **Packet-parser self-heal + CS_Rise toggle CDC (spi_packet_rx/SPI_Slave2):**
   the SPI slave delivers each transaction's final byte after CS rise; when
   the racy 5 ns CS_Rise pulse was caught, the parser lost sync-hunt parity
   and silently swallowed the next frame (lost ARMs, lost read requests).
3. **FLA read watchdog+retry and write-pump pop bubble
   (Fast_Logic_Analyzer_SDRAM):** see hdl/README.md. The pop bubble is the
   big one — every sample was stored twice, so half of all SDRAM/wire
   bandwidth was waste and every timebase was displayed at half frequency.

Bitstream: seed 2, all corners closed (+0.081 ns slow-85C setup), 98% LE.

## How the host path works now

One MPSSE transaction per ~64 blocks under held CS
(`SPIDevice.read_capture_blocks`): each slot = 12-byte request + 208 clocks
of gap (FPGA fetch latency is a deterministic 166 bytes / 44 µs at 30 MHz)
+ 1056 clocks for the 1032-byte response. Enablers:

1. `crc16` via crcmod/table (the old bit-loop capped the whole host at ~1 MB/s);
2. `stream_payload` drains RX in a thread concurrently with the write
   (write-then-read stalls the MPSSE once the ~64 KB FTDI buffering fills);
3. per-block first-sample drop (overlap addressing) removes the stale-read
   block-boundary artifact.

Wire-efficiency ceiling of this framing at 30 MHz is ~3.0 MB/s; we get
2.26 MB/s (75%), the gap being USB turnarounds between batches and Python
parsing. Pipelining parse-with-next-batch could reach ~2.6-2.8 MB/s.

## Why continuous (gapless) live streaming is blocked — FPGA side

During active capture, SDRAM block reads wedge: after ~5k samples' worth of
concurrent capture inside one CS-held burst, block requests stop being
served (WAIT_BLOCK watchdog returns ST_CAPTURE_IDLE), and every
during-capture read also produces a spurious ST_CAPTURE_IDLE response
~444 µs (+1665 bytes at 30 MHz) after the data response, which collides
with the next request slot. Suppressing the idle-loop prefetch with a
START_STREAM prefix does not fix it. Root cause is in the FLA
readout/write-pump arbitration and needs RTL work.

## Dead ends (do not resurrect without RTL redesign)

- **CMD_START_STREAM raw streaming** — no FPGA-side byte streamer exists on
  this branch (the June 30 fix was never committed and is lost).
- **Delta-compressed streaming** — (a) `Compress_Enable` is hardwired '0' in
  the current bitstream (LE budget: device 100% full, last fit FAILED at
  8038/8064); (b) the protocol drains a fixed 192 words per 512 samples but
  `capture_compressor` is variable-rate (overflow inserts keyframe words) and
  its keyframes drop bit 15 — arbitrary digital data cannot round-trip.
  A sound redesign needs bounded worst-case packing + padding + 16-bit-safe
  keyframes, plus LEs that don't exist on this MAX10.

## Next real levers (in order of value)

1. **RTL: fix the during-capture read wedge** (and remove/disable the idle
   prefetch loop, which is dead weight now) → enables true continuous
   streaming at up to ~1.4 MS/s lossless (wire-limited). Requires refit on a
   100%-full device; removals should net LEs back.
2. **Host: pipeline parsing with the next batch** → 2.26 → ~2.6-2.8 MB/s
   readback.
3. **Capture-side write-drop fix** (SignalTap session; pre-existing) — raises
   trustworthy capture rates above ~2-5 MS/s.
