# Changelog

## Unreleased

- Bit Banger `square` preset now emits one symbol per level, so the output
  frequency is symbol_rate/2 (previously 2 symbols per level gave /4 — the
  requested symbol rate was never what the wire produced).
- Bit Banger previews and generator status now report the exact on-wire rate
  (the Bit_Engine divider model `sys_clk / (Bit_Div + 1.25)`), the TX output
  frequency for periodic presets, and a warning when the requested rate falls
  below the 16-bit `REG_GEN_BAUD` floor (~1.5 kHz) where the FPGA truncates
  the divider and silently runs faster. The one-shot send paths log the same
  warning instead of silently emitting the wrong rate.
- The Bit_Engine generator divider (`REG_GEN_BAUD`) is widened from 16 to 24
  bits in the RTL, so symbol rates down to ~6 Hz at 100 MHz are representable
  (1200 baud no longer wraps to ~5.6 kHz). `CMD_GET_METADATA` gains a feature
  byte (bit0 = 24-bit divider); the host driver auto-switches its divider mask
  from the flashed bitstream's width, and the generator status reports
  `divider_width`.
- **Flashed to the MAX1000 (2026-08-27, seed 33, Quartus 25.1).** The timing
  gate passed: the swept 24-bit build closes every clock domain with margin
  (fast_clk +0.210, sys_clk +0.310, sdram_core +0.060 ns — better than the
  16-bit baseline's best under the same toolchain). On-wire validation:
  1200–115200 baud all measure within +0.79% of request
  (`host/debug/rate_sweep_probe.py`), hardware smoke test 10/10, live
  generator streaming and the 37-capture hardware matrix pass. Programming
  the board uses the Arrow USB-Blaster plugin (Quartus 25.1 no longer ships
  the FTDI VID_0403 match that the MAX1000's on-board JTAG needs; the Arrow
  USB Programmer2 plugin DLL + JTAGServer registry key are installed).
- `compile.ps1` now locates Quartus via `$env:QUARTUS_DIR` (default
  `C:\altera_lite\25.1std\quartus\bin64`) and flashes the persistent `.pof`
  (CFM) image; `hdl/proj/WIDE_DIVIDER_REBUILD.md` documents the rebuild,
  timing gate and seed sweep.

- Fixed live (rolling) capture showing no signals: the waveform worker
  resolved concurrent window/overview requests FIFO instead of by request id,
  so the live window fetch received the overview payload (or nothing) and the
  canvas stayed empty. The worker protocol now echoes a request id.
- Live window fetches are no longer aborted on every appended chunk; in-flight
  fetches are coalesced and re-run once they land, so a slow LOD rebuild no
  longer starves the viewport payload.
- Live chunk appends no longer rewrite the waveform npz compressed
  (`savez_compressed` cost ~150 ms per chunk at a 2.5M-sample window —
  slower than the chunk cadence, stalling rolling capture and LOD rebuilds);
  sessions now persist with plain `savez`.
- Live overview (minimap) refreshes are throttled and fire-and-forget instead
  of queueing a blocking overview fetch per chunk.
- The FTDI SPI driver now bounds all FT_Read/FT_Write calls (500 ms timeouts)
  and drains via queue polling; a partial FPGA response after a wedged or
  aborted transfer previously blocked forever, hanging the app mid-live-capture
  ("hang" reports). It now fails fast and recovers on the next reset.
- The generator can now stream into rolling/live captures: `POST
  /api/generator/send` accepts `live: true` (UART, RS-485, bit-banger), which
  arms the pattern as a hardware-repeating Bit_Engine loop. The capture path
  re-kicks the pattern after every chunk's reset (same mechanism as the
  debug-CH0 PWM), so a live capture continuously shows the generator output on
  its pin instead of the one-shot burst always playing in the inter-chunk gap
  and never being sampled. `generator_stop` clears it. The frontend Generator
  page exposes this as a "Send live" button; the adapter reports the armed
  pattern as running even before the first capture re-kick.

## 3.0.0 - 2026-07-22

## 3.0.0 - 2026-07-22

- Added MAX1000 physical pin metadata for the full RTL pin pool: MKR D0-D14,
  PMOD PIO_01-PIO_08, and LIS3DH `SEN_*` pins.
- Added board-guide analogue input mapping and exposed it through device
  capabilities/session channel metadata. The docs now distinguish board
  analogue pins from the mixed ADC0-ADC7 stream and the maximum-analog
  physical profile. MKR `AIN0` (ADC8) and dedicated `AIN` (ADC16) are captured
  by maximum analog mode.
- Updated READMEs to stop advertising a simple `AIN0..AIN7` user-facing analog
  channel set and to document the non-linear MAX1000 ADC mux mapping.
- Added `docs/ANALOG_MODE_PLAN.md` to define the intended four capture modes
  and the RTL work needed for high-speed single-analog and maximum
  physical-analog capture.
- Implemented RTL ADC profiles for high-speed single-channel analog and maximum
  physical-analog capture. `REG_FLAGS` now carries analog profile/channel bits,
  the ADC mux range covers ADC8 and ADC16, and host decoding handles 2-byte,
  12-byte, and 14-byte analog frame layouts.
- Raised the MAX10 ADC hard-IP path to a 12 MHz conversion clock with
  `clkdiv=1`; hardware timing now supports the 1 MSPS high-speed analog profile
  and a 125 kframes/s mixed/maximum analog scan profile.
- Added 200 MHz narrow packed digital mode: one selected digital channel is
  sampled at full rate and packed as 16 time samples per 16-bit word for
  high-speed rolling capture with much lower storage pressure than full-width
  digital.
- Validated the current hardware image with smoke, API sweep, and full host
  hardware validation. Latest full host validation on the programmed image is
  564/564, including narrow packed digital finite/continuous checks.
- Fixed several RTL bugs found in a full audit: the generator status signals
  (`Start_Ack`/`Start_Reject`/`Done_Pulse`) are now forwarded through the core
  so `CMD_GEN_STATUS` reports correctly; the SPI packet receiver resets on a
  mid-packet CS deassert so a truncated frame can no longer strand the FSM and
  corrupt the next packet; `Protocol_Trigger` is guarded against `Baud_Div < 2`
  (which previously stranded its state machine so the trigger never fired); and
  the LED7 generator-activity indicator now clears when the generator goes idle
  instead of latching on permanently after the first use.
- Moved the digital glitch / hysteresis filter ("Schmitt") from the FPGA to
  host software. On the FPGA it ran only in the `sys_clk` domain and silently
  did nothing in fast-capture mode; it now runs on the captured sample stream in
  the host, so it works in every capture mode, is non-destructive, and is
  re-tunable without re-capturing. Registers `0x41`/`0x42` are retired;
  `dev.set_schmitt()` keeps the same API.
- Removed dead/vestigial RTL: the unreachable `cont_readout` continuous-readout
  branch, the vestigial top-level `Buffer_Full`/`Buffer_Ack` core ports, and the
  unused `spi_command_dispatch` module.
- Fixed and triaged the GHDL testbenches: rewrote `tb_fast_analyzer`'s
  continuous test against the current SDRAM-ring model, dropped a stale generic
  in `tb_spi_slave`, fixed invalid VHDL in `tb_crc`, and retired the obsolete
  `tb_core` (superseded by `tb_gen_loopback`). The full maintained GHDL suite
  and 333 host tests pass.
- Refreshed the READMEs for the software glitch filter and corrected stale
  module line counts and the RTL source-file count (now 16 files).
- Rebuilt the FPGA image (SEED 3) with the above changes; logic usage dropped
  to 7,537 LE (93%). Timing still closes at 200 MHz (clk[1] setup +0.20 ns), and
  a seed sweep confirmed Restricted Fmax is pinned at 204 MHz by the device
  minimum-pulse-width (`tmin`) limit — seed- and logic-invariant, i.e. the
  200 MHz capture clock is at the part's ceiling.

## 2.0.0 - 2026-06-16

### Release Summary

Version 2.0.0 is the browser-host, capture-contract, and hardware-validation
release. Compared with 1.0, it turns the project from a primarily local
tkinter/SPI tool into a full web-hosted logic-analyzer stack with a FastAPI
backend, React waveform UI, session storage, richer decoders,
measurement/export flows, mock-device mode, and a substantially stronger
FPGA/host capture contract.

The original host driver and tkinter console remain available.

These results were measured against the MAX1000 FPGA bitstream shipped with
2.0.0 (later images report different counts as the validation suite grows):

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
