# Software roadmap release notes

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
