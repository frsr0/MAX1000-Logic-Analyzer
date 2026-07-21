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
| Latest programmed SOF | Full-feature seed-23 image, checksum `0x004EFFE9` |

The 2026-07-20 hardware smoke run passed all 10 checks, including discovery,
metadata, capabilities, self-test, digital capture, sanity checks, UART,
RS-485, SPI, and SWD generator loopback. The archived report is
[hardware-smoke-2026-07-20.md](../hardware-smoke-2026-07-20.md).

The 2026-07-21 full-feature validation passed protocol, single/FAST/continuous
capture, 200 MHz narrow packed, MSO packed, analog/mixed, trigger, generator,
and lifecycle sections; the jumper section passed its PMOD6/ADC3 path and
flagged the missing PMOD5/ADC7 fixture path. The codec matrix passed both `delta_rle` and
direct `rle` bit-exactly at 1, 10, 50, 100, and 200.4 MS/s. Live delta mode is
lossless through 500 kS/s, matching raw's measured ceiling.

The exact programmed image was revalidated on 2026-07-21: the full new-test
regression recorded 119/120 checks, with the only failure being the known
PMOD5-to-AIN5/ADC7 physical jumper path. The independent PMOD6-to-AIN4/ADC3
path passed, as did the MSO packed test with 500,000 words, four balanced
analog channels, digital RLE slices, and high-speed analog-only capture.

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
powershell -NoProfile -ExecutionPolicy Bypass -File .\compile.ps1 -NoFlash -Seed 23
& 'C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe' -c 1 -m JTAG -o "P;output_files\OLS_Logic_Analyzer.sof"
```

Re-run the hardware smoke test after programming. A passing software suite is
not evidence that a new bitstream has the expected routing.

The restored full-feature seed-23 RTL/SDC build fits at 6,333/8,064 LEs and
programs successfully. The authoritative post-fit query reports slow-85C
`fast_clk` worst setup slack `+0.049 ns`; the other corners report `+0.270 ns`
and `+1.286 ns`, with `sys_clk` and `sdram_core_clk` positive. Both compressed
modes are present and validated on the board; seed 23 changes placement only.

The closure came from keeping the live sample-budget dependency single-cycle,
removing the redundant nonzero-flag mux from the budget counter's data path,
and constraining only stable configuration/inactive branch-select paths in the
SDC. Sample data and the active countdown remain single-cycle paths.

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
