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
| Programmed image | Full mixed-signal seed-10 CFM image, 2026-08-27 |
| SOF checksum | `0x0050ADC8` (Quartus Assembler report) |
| SOF SHA-256 | `98AC43B50072A10DAD40930E3AB3BEEAEEBDC4887B07F23E14145A3E8E06828D` |

The image was built by Quartus Prime Lite 25.1. The slow 1200 mV, 85 C
post-fit timing gate is fully clean:

| Domain | Setup slack | Hold slack |
|---|---:|---:|
| `fast_clk` | +0.083 ns | +0.340 ns |
| `sdram_core_clk` | +0.111 ns | +0.326 ns |
| `sys_clk` | +0.410 ns | +0.293 ns |
| `SDRAM_CHIP_CLK_OUT` | +1.098 ns | +1.808 ns |
| `SPI_SCK_EXT` | +12.456 ns | +0.394 ns |

The fit uses 7,761/8,064 logic elements (96%), 4,821 registers, and
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
| Mixed scan | Single/live | 16 digital plus two ADC lanes at about 125 kframes/s |

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

The programmed image has the following connected-board evidence:

- full host hardware validation: **383/383 passed**;
- backend hardware smoke test: **10/10 passed**;
- real-browser capture matrix: **37/37 passed** across every advertised
  source/acquisition/rate case;
- 1,000,000-sample digital SDRAM capture at 200 MHz;
- generator live-stream capture and clean stop/recovery;
- on-wire generator sweep from 1,200 to 115,200 baud, all within +0.79%;
- auto-discovered digital and analogue jumpers, with analogue checks skipped
  cleanly when no physical analogue jumper is present;
- capture-visible LIS3DH I2C and SPI transactions.

Software CI separately gates backend branch coverage at 88%, host branch
coverage at 50%, frontend TypeScript/build/unit tests, Playwright mock E2E,
and the maintained GHDL suite. The self-hosted hardware workflow fails when
its required MAX1000 is unavailable; it does not silently treat absence as a
pass.

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
- The maintained HDL gate explicitly separates passing benches from orphaned,
  GHDL-blocked, and known-failing benches. See [HDL Testbenches](hdl/testbenches.md).

## Contract change checklist

When adding a hardware-facing feature, update the RTL register definitions and
wiring, host protocol/driver, backend capabilities and adapter, frontend
controls, focused simulations, real-board tests, the feature matrix, and the
verification traceability page together.
