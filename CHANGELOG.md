# Changelog

## 2.0.0 - 2026-06-16

### Release Summary

Version 2.0.0 is the browser-host, capture-contract, and hardware-validation
release. Compared with 1.0, it turns the project from a primarily local
tkinter/SPI tool into a full web-hosted logic-analyzer stack with a FastAPI
backend, React waveform UI, session storage, richer decoders,
measurement/export flows, mock-device mode, and a substantially stronger
FPGA/host capture contract.

The original host driver and tkinter console remain available.

This release has been validated on the current flashed MAX1000 image:

- Full hardware validation: 580 / 580 passed.
- Targeted new hardware validation: 25 / 25 passed.
- Host Python tests: 319 / 319 passed.
- Focused driver tests: 85 / 85 passed.
- HDL regressions: `tb_ols_capture_contract` passed; `tb_continuous_rate1`
  passed to stop-time.

### Capture Contract And FPGA Firmware

- Added true continuous SDRAM ring metadata:
  - `producer_index`
  - `oldest_index`
  - `newest_index`
  - `overrun_count`
- Added monotonic `capture_seq`, incremented on every arm, so the host can
  prove readback freshness.
- Replaced advisory DONE behavior with a sticky completion contract:
  - DONE latches on completion.
  - DONE clears on explicit ACK, abort, or next arm.
  - Added `CMD_ACK_CAPTURE_DONE`.
  - Added `REG_DONE_LATCHED`.
- Added capture metadata registers:
  - `REG_CAPTURE_SEQ`
  - `REG_PRODUCER_INDEX`
  - `REG_OLDEST_INDEX`
  - `REG_NEWEST_INDEX`
  - `REG_OVERRUN_COUNT`
  - `REG_DONE_LATCHED`
- Continuous capture now writes into a bounded SDRAM ring and reports overwrite
  loss through `overrun_count` instead of relying only on polling/readback
  timing.
- Added indexed continuous readback by absolute sample index.
- Arm now rewrites the full mode state:
  - digital/mixed mode
  - fast mode
  - continuous mode
  - sample count
  - divider
  - trigger mask/value
  - delay/start offset
- Fixed abort behavior so stale `Full` cannot immediately re-latch DONE.
- Fixed mixed/analog mode reset by validating mixed -> digital -> mixed
  back-to-back capture and frame phase.
- Fixed continuous capture at fastest divider by gating start until config ACK
  and divider reload are complete.
- Added max-rate continuous overrun validation at `div=0`.
- Increased atomic generator-capture guard so UART captures include an
  idle-high lead-in before the start bit.

### Host Driver / API

- Added extended status parsing for:
  - `capture_seq`
  - `producer_index`
  - `oldest_index`
  - `newest_index`
  - `overrun_count`
  - `done_latched`
- Added `SPIDevice.ack_capture_done()`.
- Added `OLSDeviceSPI.ack_capture_done()`.
- Added `read_capture_range(start_sample, sample_count)` for indexed SDRAM/ring
  reads.
- Added `continuous_ring_capture(...)` as the true 2.0 FPGA ring API:
  - arms continuous mode once
  - follows producer/oldest/newest metadata
  - reads by absolute sample index
  - skips to `oldest_index` after overrun
  - exposes the latest ring status including `overrun_count`
- Preserved existing `rolling_capture()` compatibility behavior.
- Updated single-shot capture paths to use the new sequence/DONE contract
  internally where firmware metadata is available.
- Preserved compatibility with legacy short `CMD_GET_STATUS` responses.

### Web Application

- Added a FastAPI backend for device control, capture orchestration, sessions,
  waveform data, decoders, measurements, exports, diagnostics, generator
  control, and status WebSockets.
- Added a React/Vite frontend with:
  - canvas waveform viewer
  - minimap
  - capture controls
  - channel panel
  - trigger panel
  - decoder panel
  - measurement panel
  - marker panel
  - export panel
  - raw inspector
  - sessions/devices/generator/diagnostics/settings pages
- Added browser/LAN workflow so captures can be viewed from a phone, tablet, or
  laptop while the MAX1000 remains attached to the host machine.
- Added mock-device mode for UI/demo/testing without hardware.
- Added session persistence, waveform storage, import/export, and
  report-oriented metadata.
- Added `WEBAPP.md`.

### Decoders, Measurements, And Exports

- Added backend decoder registry and APIs for:
  - UART
  - I2C
  - SPI
  - PWM
  - Modbus
  - OneWire
  - parallel bus
- Added structured decoder event storage, annotations, and packet table APIs.
- Added measurement infrastructure for digital, analog, and bus measurements.
- Added waveform/export support:
  - CSV
  - JSON session export
  - NPZ
  - VCD
  - HTML report generation
- Added analog waveform utilities and derived digital threshold views.

### Generator And Protocol Work

- Added/validated atomic generator capture (`CMD_GEN_CAPTURE`) for loopback
  workflows.
- Validated UART generator capture and pin routing.
- Validated I2C generator/read flows and accelerometer-oriented paths.
- Added protocol-trigger UART validation fixes:
  - capture at 2 MHz for 115200 baud, giving 17.36 samples/bit
  - correct 2-byte digital sample stride
  - validation-only UART sampling-margin guard
- Added `host/uart_probe.py` for hardware UART trigger/capture timing probes.

### Build / Timing

- `compile.ps1` now regenerates the Quartus wrapper with `FAST_SPEED => true`
  for the 100 MHz system / 200 MHz sample-clock build.
- Restored speed-mode fitter settings for the 200 MHz build.
- Current flashed build reports:
  - `sys_clk = 100 MHz`
  - `sample_clk = 200 MHz`
- Updated docs to note that editing the generated wrapper directly is
  overwritten by the next `compile.ps1` run.

### HDL Tests / Simulation

- Added `tb_ols_capture_contract.vhd` for:
  - sticky DONE
  - ACK clear
  - abort clear/stale-Full suppression
  - `capture_seq` increment
  - mixed -> digital -> mixed mode reset
- Extended `tb_continuous_rate1.vhd` for continuous max-rate `Rate_Div=1`.
- Added/updated regression coverage for:
  - continuous wedge recovery
  - analog/mixed frame preamble and alignment
  - generator loopback
  - capture path
  - flush path
  - fast analyzer
  - interface packet protocol
- Added simulation support models:
  - `dcfifo_sim.vhd`
  - `sdram_pin_model.vhd`
  - simulated PLL support

### Hardware Validation

- Expanded hardware validation coverage to 580 checks.
- Added max-rate continuous ring overrun test at `div=0`.
- Added indexed ring tail readback after overrun.
- Added mixed -> digital -> mixed back-to-back hardware test.
- Added or strengthened coverage for:
  - sticky DONE / abort behavior
  - pre-trigger capture
  - full-depth SDRAM boundary capture
  - back-to-back captures without reset
  - capture/readout stress
  - long rolling stress
  - protocol trigger
  - noise floor
  - Schmitt trigger
  - UART/I2C/SPI generator paths
  - 200 MHz max-speed capture

### Versioning

- Bumped package metadata to 2.0.0 in `pyproject.toml`.
- Bumped tkinter/CLI console `__version__` to 2.0.0.
- Backend and frontend version metadata are aligned at 2.0.0.

### Compatibility Notes

- Existing `rolling_capture()` remains available for compatibility.
- New true FPGA ring behavior is exposed as `continuous_ring_capture()`.
- Existing single-shot `capture()` behavior is preserved.
- Legacy short `CMD_GET_STATUS` payloads remain parseable by the host driver.

### Known Limitations

- Continuous capture is still bounded by physical SDRAM capacity; "forever"
  rolling means overwrite-with-overrun-reporting, not infinite retention.
- Lossless live readback is limited by SPI throughput. Above readback
  throughput, the FPGA ring continues running and `overrun_count` reports
  overwritten samples.
- FPGA utilization is high, so future feature work should continue watching
  synthesis/timing reports and trim debug/test mux logic if margin tightens.
