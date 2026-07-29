# Current Implementation Status

This page is the authoritative snapshot of the software and FPGA image on the
`codex/software-feature-roadmap` branch. It should be updated whenever the
hardware contract, register map, or validated build changes.

## Validated target

| Item | Current value |
|---|---|
| Board | Arrow MAX1000, Intel MAX 10 10M08 |
| Transport | FT2232H Channel B, MPSSE SPI |
| Digital channels | 16 |
| FAST sample clock | 200 MHz nominal; 200.4 MHz in the current build |
| Deep capture | 4,194,304 16-bit SDRAM words |
| Physical generator pin pool | 26 entries: MKR_D[14:0], PMOD[7:0], SEN_SDO, SEN_SDI, SEN_SPC |
| Generator FIFO | 256 bytes of host-encoded 2-bit symbols |
| Latest programmed SOF | Full-feature seed-30 image, checksum `0x0050CF93` (2026-07-26) |
| Persisted configuration POF | `OLS_Logic_Analyzer.pof`, checksum `0x01D65FD0`; verified after power cycle (2026-07-22) |

The 2026-07-23 re-build closed timing on fast_clk at 200.4 MHz with seed 44
(the only seed that fits at 99% density — the previous seed 23 no longer fits
after the packed-mode pipeline registers).  Post-fit STA reports:

| Clock | Frequency | Worst slack | Violations |
|---|---|---|---|
| `fast_clk` | 200.4 MHz | **+0.002 ns** | 0 |
| `sdram_core_clk` | 167.0 MHz | **+0.048 ns** | 0 |

See [Fast Capture Stream](hdl/fast-capture-stream.md) for the three register-stage
fixes and the dcfifo multicycle constraint.

A fresh focused recompile on fitter seed 29 is also timing-clean and is the
current best local checkpoint in the immediate seed neighborhood. The slow-85C
setup slacks are `fast_clk +0.084 ns`, `sdram_core_clk +0.087 ns`,
`sys_clk +0.403 ns`, and `SPI_SCK_EXT +11.290 ns`, with all hold checks
positive. That compile used 7,924/8,064 LEs (98%), 4,804 registers, and
38,020/387,072 memory bits.

The 2026-07-26 seed-30 image was rerun on the connected board. The baseline run
was **357/380 passed, 23 failed, 0 skipped**. After correcting
the mixed-frame contract and ring/codec test assumptions, focused regression is
**117/117 passed**; capture-visible LIS3DH I²C/SPI decode passes.
The final exhaustive hardware suite is **391/391 passed, 0 failed, 0 skipped**.

Two new hardware-trigger tests were added:

| Test | What it proves |
|---|---|
| **14f** — `test_generic_pattern_trigger_hw` | Internal `Generic_Pattern_Trigger` FSM: baud counter to shift register to comparator to trigger to capture complete |
| **14g** — `test_generic_pattern_trigger_jumper` | Full external path: Bit_Engine UART 0x55 to FPGA TX pin over jumper wire to FPGA RX pin; pattern trigger matches with `match_mask=0xFF` |

The on-board jumper (pool pin 22 to capture channel 13) is now discovered
at the start of the suite, and `_floating_except()` automatically excludes
the jumper RX channel from all noise-floor and cleanliness checks.

Both PMOD5-to-AIN5/ADC7 and PMOD6-to-AIN4/ADC3 produced full-scale UART
activity with cross-talk checks, alongside the MSO packed test with 500,000
words, four balanced analog channels, digital RLE slices, and high-speed
analog-only capture.

## Generator support

The real FPGA advertises these routes through
`GET /api/generator/capabilities`:

| Protocol | Hardware route | Capture behavior |
|---|---|---|
| UART | One configurable TX output | Optional loopback capture |
| RS-485 | Configurable A/B outputs, optional DE GPIO | DE is high for the active Bit_Engine burst |
| I²C | Configurable SDA/SCL outputs | Requires an external slave or electrical loopback |
| SPI | Configurable MOSI/SCLK, optional GPIO CS and MISO | MOSI/SCLK loopback plus direct CS/MISO capture channels |
| SWD | Configurable SWDIO/SWCLK outputs | Transaction capture; target response requires a connected target |
| Bit Banger | Two configurable outputs | Raw bounded symbol playback |

The mock device may provide richer synthetic signals than the physical board.
The backend must use route capability descriptors before accepting optional
physical pins; it must never silently pretend that a missing wire exists.

## Capture and analysis

- Digital single-shot and rolling capture use the FPGA's SDRAM/BRAM paths.
- Narrow digital mode packs one selected channel for high-rate, deep capture.
- Analog and mixed modes use the MAX 10 ADC and are rate-limited by the ADC.
- Hardware triggers are limited to the trigger types exposed by the connected
  device; unsupported protocol searches run post-capture in software.
- Raw capture data is preserved. Derived channels, decoder events,
  measurements, and exports are generated without mutating the source session.
- Digital compressed readback supports direct full-word `rle` and the restored
  packed-delta-plus-RLE `delta_rle` codec. Mixed/analog readback remains raw.

## Verification baseline

Run the following from the repository root:

```powershell
cd backend
python -m pytest -q
python hw_smoke_test.py

cd ..\host
python -m pytest -q

cd ..\frontend
npm run typecheck
npm run build
```
For a new RTL image:

```powershell
cd hdl\proj
& 'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe' -c 1 -m JTAG -o "P;output_files\OLS_Logic_Analyzer.sof"
```

Re-run the hardware smoke test after programming. A passing software suite is
not evidence that a new bitstream has the expected routing.

The full-feature seed-44 RTL/SDC build fits at 504/504 LABs (100%)
and compiles successfully.  Post-fit STA reports slow-85C `fast_clk` setup
slack **+0.002 ns** and `sdram_core_clk` **+0.048 ns** with positive hold margins.
Seed sensitivity is high — only seeds 44 and 57 fit at this density.
Seed 57 gave `-0.762 ns` timing, so 44 is the stable build seed.

The fast_clk timing closure required three changes, all matching the
non-packed skid-buffer pattern already in the design:

1. **Register `Packed_Ready_r`** — the five-term AND is now evaluated into
   a single register, breaking the combinational path across three hierarchy
   levels to `analog_packer`'s BRAM address register.  Improved worst slack
   from **-0.695 to -0.310 ns**.
2. **Register `packed_buf_in_valid_r` + `Packed_Data_r`** — one pipeline
   stage between the producer signals and the elastic buffer's push/enable
   logic.  Improved worst slack from **-0.501 to -0.074 ns**.
3. **dcfifo multicycle constraint** — the last -74 ps was inside the
   dcfifo write-side gray-code synchroniser pipe to counter path.
   With `sync_depth=4` a 2-cycle setup multicycle is safe.
   Improved worst slack from **-0.074 to +0.002 ns**, 0 violations.

The on-board LIS3DH is a validated external protocol partner: the final
regression reads `WHO_AM_I = 0x33` over I2C at 50/100 kHz and over SPI
mode 3, also reads `CTRL_REG1`, and decodes capture-visible I2C/SPI
dialogues.  See [accelerometer.md](accelerometer.md) for the complete
peripheral contract.

The current programmed image includes the timing closure, repeat-mode, FAST
timing, narrow packed, pattern trigger, and jumper-discovery changes listed
above.  See [Verification and Change Traceability](verification-traceability.md)
for the exact evidence chain.
## Known boundaries

- The 256-byte generator FIFO is finite. Large arbitrary waveforms must be
  chunked or rejected; protocol encoders choose bounded burst sizes.
- GPIO auxiliary routes use pool indices `0..25`; capture channels use
  logical channels `0..15`.
- RS-485 DE is an FPGA-timed active-burst signal. It is not a full external
  transceiver configuration or bus arbitration layer.
- SPI CS/MISO auxiliary routing is implemented in dedicated fast capture
  muxes because runtime general pin-map writes are frozen in the FAST build.
- I²C and SWD need external electrical partners for meaningful response tests.
- The validated image is now persisted to the MAX 10 configuration flash via
  POF checksum `0x01D65FD0`; a future replacement image must be programmed to
  both SRAM (SOF) and configuration flash (POF) when persistence is required.

## Where to change the contract

When adding a hardware-facing feature, update all of these together:

1. `hdl/rtl/spi_protocol_pkg.vhd` and `OLS_Interface.vhd` register definitions.
2. `OLS_Logic_Analyzer_SDRAM_Core.vhd` and `OLS_SDRAM_Top.vhd` wiring/CDC.
3. `host/driver/spi_protocol.py` and `ols_spi_device.py`.
4. `backend/app/hardware/device_models.py`, `base.py`, and the real adapter.
5. Frontend generator controls and capability hints.
6. Unit tests, the real hardware smoke test, and this wiki.
