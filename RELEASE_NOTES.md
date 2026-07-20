# Software roadmap release notes

## Current branch

The `codex/software-feature-roadmap` branch adds host-only analysis and
exerciser capabilities without changing the FPGA image or board wiring:

- Bit Banger scripts, presets, protocol templates, JSON import/export, bounded previews, and generator session metadata.
- New protocol decoders for CAN, I²S, LIN, MIDI, PS/2, quadrature, JTAG, HDLC/PPP, infrared, and SMBus/PMBus.
- Protocol/sequence/nth-match trigger search, duration/consecutive qualifiers, and match navigation.
- Timing/analog statistics, spectrograms, spectrum peaks, correlation delay, derived filters, and a protocol dashboard.
- Stable JSON/CSV/VCD/NPZ/HTML exports, batch/CLI workflows, and CI assertions with JUnit output.

The FPGA image, capture limits, analog bandwidth, and physical routing remain
unchanged. See `FEATURE_CAPABILITY_MATRIX.md` for mock/hardware boundaries.
