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
| Persistent image | 2026-08-27 CFM image; unchanged by the current validation run |
| SOF checksum | `0x0050492F` (Quartus Assembler report) |
| SOF SHA-256 | `2C33472F5C07F60CF41ED155EB0EAAA58D7C23320EC9A861C86ED82764F9FE50` |

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
driver re-kicks the repeating pattern after each rolling-capture reset until
`POST /api/generator/stop`, allowing generator traffic to remain visible in
live captures.

## Verification baseline

The current volatile image has the following connected-board evidence:

- backend hardware smoke test: **10/10 passed**;
- focused changed-path hardware validation: **117/117 passed, 0 failed,
  0 skipped**, including both physical analogue jumpers;
- strict codec/rate rerun after the cancellation-race repair: **26/26
  passed**, with no suppressed transport errors;
- full connected-board suite: **403/403 passed, 0 failed, 0 skipped**,
  including LIS3DH and physical-jumper generic-trigger checks;
- both strict 60-second rolling stress runs passed inside the full suite, with
  debug disabled and enabled, over 10 million samples captured in each run;
- all 57 HDL testbenches pass with zero expected failures and zero exclusions;
- browser hardware validation passes **5/5** feature tests (all **37/37**
  advertised mode/rate cases) and **34/34** hardware-aligned UI tests;
- the 2026-09-09 screenshot refresh checks exact session identity and loaded
  waveform state; visual review confirmed the expected UART, MIL, LIS3DH,
  analog-fast, four-lane analog, and mixed digital/analog traces;
- auto-discovered analogue jumpers on both installed paths, full-depth SDRAM,
  200.4 MHz narrow capture, packed MSO, pre-trigger, codec, readout-stress,
  and close/reopen lifecycle checks.

The persistent 2026-08-27 image retains its historical 383/383 full-suite,
37/37 browser-matrix, 200 MHz million-sample, generator-rate, and LIS3DH
evidence. Those results are not attributed to the newer volatile image.

Software CI requires 100% statement and branch coverage for backend and host,
and 100% statement, branch, function, and line coverage for all production
frontend TypeScript/TSX, plus frontend build/typecheck,
Playwright mock E2E, and all 57 GHDL benches. The self-hosted hardware workflow fails when
its required MAX1000 is unavailable; it does not silently treat absence as a
pass.

The whole-frontend coverage requirement is met: **277 tests** cover all
**3,382 statements, 2,475 branches, 941 functions, and 2,871 lines** in the
configured production TypeScript/TSX scope. See
[Frontend Build and Test](frontend/build-and-test.md).

The latest host run passed **983 tests**, with all **8,241 statements** and
**2,482 branches** covered (zero missing or partial branches). The backend
run passed **541 tests**, covering all **8,800 statements** and **2,624
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
- RS-485 DE is an FPGA-timed output, not a transceiver or arbitration layer.
- I2C, SWD, CAN, LIN, MIL, and similar protocols need suitable external
  electrical partners before software decoding becomes board-level evidence.
- Live readback capacity depends on USB transport and signal compressibility;
  the ring reports overwrite loss and retains the newest samples.
- The HDL gate requires every one of the 57 `tb_*.vhd` benches; there are no
  exclusion or expected-failure lists. See [HDL Testbenches](hdl/testbenches.md).

## Contract change checklist

When adding a hardware-facing feature, update the RTL register definitions and
wiring, host protocol/driver, backend capabilities and adapter, frontend
controls, focused simulations, real-board tests, the feature matrix, and the
verification traceability page together.
