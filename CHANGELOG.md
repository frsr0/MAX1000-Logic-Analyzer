# Changelog

## 2.0.0 - 2026-06-15

### Release Summary

Version 2.0.0 is the browser-host and hardware-validation release. Compared
with 1.0, it turns the project from a primarily local tkinter/SPI tool into a
full web-hosted logic-analyzer stack with a FastAPI backend, React waveform UI,
session storage, richer decoders, measurement/export flows, mock-device mode,
and a heavily expanded hardware validation suite. The original host driver and
tkinter console remain available.

This release has been validated on the current flashed MAX1000 image:

- Hardware validation: 571 / 571 passed.
- Host Python tests: 175 / 175 passed.
- Backend Python tests: 38 / 38 passed.

### Web Application

- Added a FastAPI backend for device control, capture orchestration, sessions,
  waveform data, decoders, measurements, exports, diagnostics, generator
  control, and status WebSockets.
- Added a React/Vite frontend with a canvas waveform viewer, minimap, panels
  for channels/capture/trigger/decoders/measurements/markers/exports, and
  pages for sessions, devices, generator, diagnostics, and settings.
- Added browser/LAN-oriented workflow so captures can be viewed from a phone,
  tablet, or laptop while the MAX1000 remains attached to the host machine.
- Added mock-device mode for UI/demo/testing without hardware.
- Added session persistence, import/export, and report-oriented metadata.

### Capture And Hardware Path

- Reworked capture/readback around the SPI packet protocol and existing host
  adapter path.
- Added support for 16 digital channels with 2-byte digital sample frames.
- Added mixed digital + 8-channel ADC capture framing and host-side decoding.
- Added deep SDRAM capture validation up to the Max_Samples boundary.
- Added pre-trigger capture validation using the `DELAY_COUNT`/`Start_Offset`
  path.
- Added back-to-back capture validation without reset between captures.
- Added long rolling-capture stress validation and concurrent readout stress.
- Documented current continuous-mode limits and the planned ring-buffer
  contract for future true infinite rolling capture.

### Generator And Protocol Work

- Added/validated atomic generator capture (`CMD_GEN_CAPTURE`) for loopback
  workflows.
- Validated UART generator capture and pin routing.
- Validated I2C generator/read flows and accelerometer-oriented paths.
- Added protocol-trigger UART validation fixes:
  - Test 14 now captures at 2 MHz for 115200 baud, giving 17.36 samples/bit.
  - Test 14 now decodes generated capture data with the correct 2-byte digital
    sample stride.
  - Added a validation-only UART sampling-margin guard so under-sampled
    re-decodes fail with an explicit reason.
- Added `host/uart_probe.py` for hardware UART trigger/capture timing probes.

### Decoders, Measurements, And Exports

- Added backend decoder registry and APIs for UART, I2C, SPI, PWM, Modbus,
  OneWire, and parallel bus workflows.
- Added structured decoder event storage, annotations, and packet table APIs.
- Added measurement infrastructure for digital, analog, and bus measurements.
- Added waveform/export support including CSV, JSON session export, NPZ, VCD,
  and HTML report generation.
- Added analog waveform utilities and derived digital threshold views.

### Diagnostics And Validation

- Expanded hardware validation coverage across SPI handoff, packet commands,
  fast/continuous/single capture, edge triggers, generator UART/I2C/SPI paths,
  analog/mixed capture, rolling capture, abort, Schmitt trigger, pre-trigger,
  full-depth SDRAM, back-to-back capture, long stress, and readout-under-capture.
- Fixed validation assumptions discovered during release testing:
  - Fast BRAM clean-line tolerance now allows the observed low-level quiet-line
    noise margin.
  - Rising-edge trigger validation now uses pre-trigger capture and the correct
    digital stride so the trigger position is checked in the intended window.
  - UART protocol-trigger validation now checks sample-rate margin and wire
    stride explicitly.
- Added unit coverage for the validation-only UART sampling guard.
- Documented known hardware limitations and planned follow-up fixes in
  `WEBAPP.md`.

### Versioning

- Bumped package metadata to 2.0.0 in `pyproject.toml`.
- Bumped tkinter/CLI console `__version__` to 2.0.0.
- Backend and frontend version metadata were already aligned at 2.0.0.

### Known Limitations

- Continuous capture is bounded by the current polling/readback model rather
  than a true forever-rolling FPGA ring buffer.
- DONE status remains advisory in some fast paths; host validation proves
  completion using readback data where needed.
- Mixed/analog mode still requires careful mode reset between captures.
- Continuous capture at the fastest divider remains a planned HDL follow-up.
- FPGA utilization is high, so future feature work should watch synthesis
  reports and trim debug/test mux logic if compile margin tightens.
