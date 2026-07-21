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
| Latest programmed SOF | Current corrected full image, checksum `0x004B11D4`; full profile remains timing-negative |

The 2026-07-20 hardware smoke run passed all 10 checks, including discovery,
metadata, capabilities, self-test, digital capture, sanity checks, UART,
RS-485, SPI, and SWD generator loopback. The archived report is
[hardware-smoke-2026-07-20.md](../hardware-smoke-2026-07-20.md).

The 2026-07-21 full host validation run reached the existing board image and
passed protocol, single/FAST/continuous capture, 200 MHz sample-count, narrow
packed, MSO packed, trigger, generator, jumper, codec, and lifecycle sections
before the long live-rate characterization. These results validate the
flashed image, not the unflashed corrected RTL branch.

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
- Digital compressed readback is exact full-word RLE. `delta_rle` remains the
  host-facing compatibility name for that path; mixed/analog readback remains
  raw.

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
powershell -NoProfile -ExecutionPolicy Bypass -File .\compile.ps1 -NoFlash -Seed 21
& 'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe' -c 1 -m JTAG -o "P;output_files\OLS_Logic_Analyzer.sof"
```

Re-run the hardware smoke test after programming. A passing software suite is
not evidence that a new bitstream has the expected routing.

The corrected full RTL currently reports slow-85C `fast_clk` setup slack
`-0.098 ns` and TNS `-0.147 ns` at seed 21. Do not use that image as a timing
signoff build until setup closes.

The full current eight-seed sweep still has no timing-closed MSO image: seed
21 is best at `-0.098 ns` FAST setup slack, while the other tested seeds range
down to `-0.413 ns`. See [the sweep table](../../hdl/proj/seed_sweep_results.txt) and
[the build-flow timing notes](hdl/build-flow.md).

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
- The validated SOF is volatile FPGA configuration unless it is separately
  persisted to board flash.

## Where to change the contract

When adding a hardware-facing feature, update all of these together:

1. `hdl/rtl/spi_protocol_pkg.vhd` and `OLS_Interface.vhd` register definitions.
2. `OLS_Logic_Analyzer_SDRAM_Core.vhd` and `OLS_SDRAM_Top.vhd` wiring/CDC.
3. `host/driver/spi_protocol.py` and `ols_spi_device.py`.
4. `backend/app/hardware/device_models.py`, `base.py`, and the real adapter.
5. Frontend generator controls and capability hints.
6. Unit tests, the real hardware smoke test, and this wiki.
