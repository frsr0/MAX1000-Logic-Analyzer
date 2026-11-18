# Software roadmap release notes

## 2026-11-18 - FPGA timing closure and faster readback

The FPGA image is now seed 16. MISO is constrained at its real 30 MHz (setup
-3.2 ns -> +13.4 ns), live `rle` capture reaches about 4.5 MS/s with no dropped
samples at 100 MS/s, and compressed readback is about twice as fast.

- FPGA: three narrow compares out of the worst timing paths and a shared watchdog tick (seed 7, then seed 16).
- FPGA: MISO launched on the SCK rising edge; SPI constrained at its real 30 MHz (MISO setup -3.2 ns -> +13.4 ns).
- FPGA: continuous-read cool-down 80 -> 16 cycles (live rle ceiling ~3.7 -> ~4.5 MS/s, 0 dropped samples at 100 MS/s).
- FPGA: current image is seed 16 (sys_clk +0.043 ns, fast_clk +0.047 ns, sdram_core_clk +0.311 ns; 8,014/8,064 LEs).
- Host: batched compressed readback slot sized from observed responses (rle readback 1.6 s -> 0.74 s per 4M samples; busy signals 3.35 -> 1.9 s).
- Host: delta_rle falls back to rle on overflow.
- Host: live rle requests up to 16384 samples on images with a generation (0.25 -> 1+ MS/s at 4096 windows); 1 KB live read chunks; request and first chunk in one USB round trip.
- Host: chained-delta decoder (gated on feature bit 6, unused by the current image).
- Tests: hardware tests 36-39 (dropped samples, live bit-exact, sustained rate, readback speed).
- Rejected: response-kind rsp_buf rework (bigger), chained delta anchors in RTL (did not fit), seed-16 cone fixes (0/12 seeds closed).

## 3.0.0 - 2026-07-22

The `codex/software-feature-roadmap` branch delivers the 3.0.0 host, backend,
frontend, and FPGA capability set. The exact seed-23 image was rebuilt and
programmed on 2026-07-22 with SOF checksum `0x004FDDF3`; its POF was written
to configuration flash with checksum `0x01D65FD0`.

The board was power-cycled after programming and reconnected successfully from
the persisted configuration; post-boot metadata, status, capture, and full
hardware validation completed without failures.

- Bit Banger scripts, presets, protocol templates, JSON import/export, bounded previews, and generator session metadata.
- New protocol decoders for CAN, I²S, LIN, MIDI, PS/2, quadrature, JTAG, HDLC/PPP, infrared, and SMBus/PMBus.
- Protocol/sequence/nth-match trigger search, duration/consecutive qualifiers, and match navigation.
- Timing/analog statistics, spectrograms, spectrum peaks, correlation delay, derived filters, and a protocol dashboard.
- Stable JSON/CSV/VCD/NPZ/HTML exports, batch/CLI workflows, and CI assertions with JUnit output.

The 2026-07-22 full connected-board regression recorded 369/369 checks passed,
with 0 failures and 0 skips after the digital loopback jumper was installed.
See `FEATURE_CAPABILITY_MATRIX.md` for
mock/hardware boundaries.
