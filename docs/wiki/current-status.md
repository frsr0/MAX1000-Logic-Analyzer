# Current Implementation Status

This is the authoritative snapshot of the `master` branch and the FPGA image
programmed on the MAX1000. Update it whenever the hardware contract, register
map, build result, or connected-board baseline changes.

## Validated target

| Item | Current value |
|---|---|
| Board | Arrow MAX1000, Intel MAX 10 `10M08SAU169C8G` |
| Transport | FT2232H Channel B, MPSSE SPI |
| Application version | 3.0.0 |
| Digital channels | 16 |
| FAST sample clock | 200.4 MHz nominal |
| Deep capture | 4,194,304 16-bit SDRAM words |
| Narrow capture | One selected digital input, 16 samples packed per SDRAM word |
| Generator pin pool | 26 entries: MKR D0-D14, PMOD PIO1-PIO8, `SEN_SDO`, `SEN_SDI`, `SEN_SPC` |
| Generator FIFO | 256 bytes = 1,024 two-bit symbols |
| Generator divider | 24 bits when metadata feature bit 0 is set; legacy 16-bit images remain supported |
| Programmed image | Current full mixed-signal seed-10 image in configuration flash, 2026-09-09 |
| Persistent image | Current seed-10 image, programmed into CFM on 2026-09-09 |
| SOF checksum | `0x0050492F` (Quartus Assembler report) |
| SOF SHA-256 | `2028A05F689EA966B359359D8F0F4000A19298B8CEE98AF6A76A52003108C7D1` |

The image was built by Quartus Prime Lite 25.1. The slow 1200 mV, 85 C
post-fit timing gate is fully clean:

| Domain | Setup slack | Hold slack |
|---|---:|---:|
| `fast_clk` | +0.253 ns | +0.291 ns |
| `sdram_core_clk` | +0.178 ns | +0.340 ns |
| `sys_clk` | +0.278 ns | +0.223 ns |
| `SDRAM_CHIP_CLK_OUT` | +1.098 ns | +1.808 ns |
| `SPI_SCK_EXT` | +12.025 ns | +0.394 ns |

The fit uses 7,713/8,064 logic elements (96%), 4,802 registers, and
38,020/387,072 memory bits. The design remains placement-sensitive: a
48-seed sweep found seed 10 best, so any RTL, QSF, SDC, or fitter change must
be rebuilt and re-swept before it inherits this timing claim.

## Capture profiles

| UI source | Acquisition | Validated rate and scope |
|---|---|---|
| Digital deep | Single | 10 kHz-200 MHz, 16 channels, up to 4,194,304 samples |
| Digital deep | Live | 10 kHz-50 MHz rolling window |
| Packed narrow | Live | One selected channel at 200.4 MHz; up to 16x the SDRAM logical depth |
| Analog fast | Single/live | One physical ADC lane, 100 kHz-1 MHz |
| Maximum analog | Single/live | Four physical lanes (`AIN3`, `AIN1`, `AIN4`, `AIN6`) at about 24 kS/s per lane |
| Mixed scan | Single/live | 16 digital plus physical ADC1/ADC2 results (`a1`/`a2`, AIN3/AIN1) at about 125 kframes/s |

Digital readback supports `raw`, direct full-word `rle`, and packed
`delta_rle` (`delta` remains a compatibility spelling). Analog and mixed
readback remain raw. Captured arrays are immutable; derived channels,
decoders, measurements, and exports are stored as analysis state.

## Generator support

The hardware exposes UART, RS-485, I2C, SPI, SWD, and raw Bit Banger routes.
The base engine produces data and clock symbols; optional FPGA-timed routes
provide RS-485 DE and SPI CS/MISO. Route descriptors from
`GET /api/generator/capabilities` are authoritative.

The 24-bit `REG_GEN_BAUD` represents rates down to roughly 6 symbols/s at the
100.2 MHz system clock. `CMD_GET_METADATA` byte 9 bit 0 advertises the wide
divider, and the host automatically selects a 16- or 24-bit mask. Generator
status and previews report the divider width, the exact on-wire symbol rate
(`sys_clk / (divider + 1.25)`), and periodic output frequency. The `square`
preset alternates every symbol, so its output frequency is half its symbol
rate.

UART, RS-485, and Bit Banger patterns can be armed with `live: true`. The
FPGA repeat flag keeps the FIFO pattern running across rolling-capture resets
until `POST /api/generator/stop`; the host does not schedule per-chunk restarts.

## Verification baseline

The current persistent image has the following connected-board evidence:

- backend hardware smoke test: **10/10 passed**;
- two consecutive full connected-board suites: **421/421 passed** each
  (**842/842 combined, 0 failed, 0 skipped**), including LIS3DH and
  physical-jumper generic-trigger checks;
- a subsequent physical unplug/replug followed by fresh USB discovery and the
  complete suite: **436/436 passed, 0 failed, 0 skipped**; both 60-second stress
  halves and final concurrent capture/readout passed;
- both strict 60-second rolling stress runs passed inside the full suite, with
  debug disabled and enabled, over 10 million samples captured in each run;
- all 57 HDL testbenches pass with zero expected failures and zero exclusions;
- browser hardware validation passes **5/5** feature tests (all **37/37**
  advertised mode/rate cases) and **34/34** hardware-aligned UI tests; all 26
  ordinary digital/mixed cases require physical 22→CH13 activity, with 40–875
  transitions observed;
- the current on-wire rate sweep passes **7/7** from 1,200 through 115,200
  baud, with every measured rate within +0.79%;
- the 2026-09-09 screenshot refresh checks exact session identity and loaded
  waveform state; visual review confirmed the expected UART, MIL, LIS3DH,
  analog-fast, four-lane analog, and mixed digital/analog traces;
- auto-discovered analogue jumpers on both installed paths, full-depth SDRAM,
  200.4 MHz narrow capture, packed MSO, pre-trigger, codec, readout-stress,
  and close/reopen lifecycle checks.

The 2026-08-27 image retains its historical 383/383 full-suite, 37/37
browser-matrix, 200 MHz million-sample, generator-rate, and LIS3DH evidence.
Those historical results are not used as evidence for the current image.

Software CI requires 100% statement and branch coverage for backend and host,
and 100% statement, branch, function, and line coverage for all production
frontend TypeScript/TSX, plus frontend build/typecheck,
Playwright mock E2E, and all 57 GHDL benches. The self-hosted hardware workflow fails when
its required MAX1000 is unavailable; it does not silently treat absence as a
pass.

The whole-frontend coverage requirement is met: **292 tests** cover all
**3,421 statements, 2,540 branches, 958 functions, and 2,906 lines** in the
configured production TypeScript/TSX scope. See
[Frontend Build and Test](frontend/build-and-test.md).

The latest host run passed **1,010 tests**, with all **8,342 statements** and
**2,488 branches** covered (zero missing or partial branches). The backend
run passed **542 tests**, covering all **8,822 statements** and **2,630
branches**. Coverage is
execution evidence, not proof of all possible behavior or electrical setups.

Run the normal software checks from the repository root:

```powershell
python -m pytest backend/app/tests -q
python -m pytest host/tests host/driver/tests -q

cd frontend
npm ci
npm run build
npm run test:unit
$env:PLAYWRIGHT_USE_MOCK='1'
npm run test:e2e -- hardware.spec.ts

cd ..
bash hdl/tb/run_all_tbs.sh
```

With the board connected, run:

```powershell
python backend/hw_smoke_test.py
python host/app/hw_validation.py
python host/debug/rate_sweep_probe.py
```

Build and persist a new image from `hdl/proj/`:

```powershell
$env:QUARTUS_DIR='C:\altera_lite\25.1std\quartus\bin64'
.\compile.ps1 -Flash -Seed 10
```

`-Flash` programs the POF/CFM image so it survives power cycles. Quartus 25.1
on this bench also needs the Arrow USB-Blaster plugin for the MAX1000's
on-board FTDI JTAG interface. See [Build Flow](hdl/build-flow.md).

## Known boundaries

- The generator pattern is bounded to 1,024 two-bit symbols. Large arbitrary
  waveforms must be shortened, chunked, or rejected.
- RS-485 loopback validates the generated data/DE timing and decode path, not
  differential voltage, termination, loading, or interoperability through a
  physical transceiver.
- CAN, LIN, SWD/JTAG target response, MIL, I2S, MIDI, PS/2, and 1-Wire still
  need suitable external electrical partners before decode or open-loop
  generation becomes board-level interoperability evidence.
- The installed analogue jumpers prove full-scale activity and cross-lane
  isolation on ADC3/AIN4 and ADC7/AIN5. They do not provide calibrated voltage,
  linearity, bandwidth, or all-lane stimulus. ADC1/AIN3 and ADC2/AIN1 are
  validated by independent-versus-mixed baseline agreement, not a calibrated
  driven source.
- Live readback capacity depends on USB transport and signal compressibility;
  the ring reports overwrite loss and retains the newest samples.
- Packed-live and the on-board generator share the FPGA Bit Engine/capture-arm
  state, so a driven packed-live electrical test needs an independent external
  source; the current on-board proof covers the packed hardware path and data
  contract.
- Cold USB/power-cycle persistence is now recorded: the board was physically
  unplugged/replugged, rediscovered without reprogramming, and passed 436/436.
- The HDL gate requires every one of the 57 `tb_*.vhd` benches; there are no
  exclusion or expected-failure lists. See [HDL Testbenches](hdl/testbenches.md).

## Contract change checklist

When adding a hardware-facing feature, update the RTL register definitions and
wiring, host protocol/driver, backend capabilities and adapter, frontend
controls, focused simulations, real-board tests, the feature matrix, and the
verification traceability page together.
