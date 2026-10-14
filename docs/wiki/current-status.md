# Current Implementation Status

The full-device reference signoff below describes the 2026-09-09 seed-10 image
(kept as a historical benchmark). The board now runs the current checked-in RTL:
fitter seed **`1`**, post-fit timing gate **66/66**, slow 85 °C setup slack
`fast_clk` **+0.024 ns**, `sys_clk` **+0.194 ns**, and `sdram_core_clk`
**+0.226 ns**; the fit uses **8,027/8,064** logic elements, 5,048 registers,
and 54,404 memory bits. Flashed into configuration flash on 2026-10-13. The
assembler reports SOF checksum `0x0057C698`; the POF SHA-256 is
`373C6C40CAEF54095B106142A4263C803AD3ABC0FC56B2F19AAA556B77A5D86E` (SOF
`94777E2131B6E1459DAED8DEC5AA156270F9B4190E84C5A9939A62CD3CE61D75`) and the
Programmer checksum is `0x01DDD65A`.

The previous image flashed on 2026-09-28 (fitter seed `7`; timing gate 66/66,
`fast_clk` **+0.046 ns**, `sys_clk` **+0.183 ns**, SDRAM **+0.396 ns**,
8,021/8,064 LEs, POF SHA-256
`C65DDA0E8042156024F1170EF967316D072CBF9BADFA67A735EC51BE7FFEC18C`, Programmer
checksum `0x01DE1DE1`) remains the historical record; it is byte-matched by a
rebuild of the checked-out `bf1ade3` tree.

Hardware validation on the flashed seed-1 image (2026-10-13):
`backend/hw_smoke_test.py` **10/10**; `host/app/hw_validation.py` full suite
**400/427 passed, 27 failed, 1 skipped** pre-fix and **406/427 passed, 21
failed, 1 skipped** after the two-layer aux-route fix (`14fe006`, `cdcb1f6`).
On 2026-10-14 a clean rebuild of the checked-in tree reproduced SOF checksum
`0x0057C698` (66/66 timing checks), and after the test12g analogue-mode
teardown fix the full suite on the same board and bench scores **415/421
passed, 6 failed, 1 skipped** — only the 6 SPI/I2C fly-wire fixture failures
remain. An A/B run of the identical suite against the byte-matched 09-28
seed-7 image reproduces the **same 400/427** on this bench, so none of the 27
failures is attributable to the reviewed RTL bug fixes, the shared CRC-16
cone, or the seed change. The failures group as:

- **15–16 rolling-gen/`trig_fall` failures** (rolling-gen mirrors on CH1/2/4/5,
  rolling-gen UART decode, `trig_fall` clean on CH1–CH5/6) — originally
  attributed to floating adjacent wires, but root-caused on 2026-10-14 as a
  **host-side state leak**: when test12g finds no analogue jumpers it skipped
  before its teardown, leaving the device in analog-fast mode
  (`analog_mode=24`), which survives `dev.reset()`. Every later digital
  capture then read ADC frame bits as channel activity. Run in isolation both
  tests pass; bisecting the suite order isolates test12g; disabling analogue
  mode restores a clean pass. Fixed by routing the test12g skip path through
  its `set_analog_enable(False)` teardown; the total check count drops to 421
  because the coherent-mirror checks are only emitted for channels with
  unexpected activity (only the genuine CH3→CH5 100% mirror remains).
- **6 physical-jumper SPI/I2C fixture failures** — I2C loopback + matrix and
  SPI loopback + matrix decode nothing or only a 1-byte `af` fraction through
  the 16→15 fly-wire. Fail identically on a fresh 09-28 image; I2C over the
  wire needs a pull-up on the open-drain SDA, which this fixture lacks.
- **2 Bit_Engine RX FIFO oracle failures** (test8 ×2) — root-caused to the
  same aux-route leak as the LIS3DH direct reads: the oracle drains the RX
  FIFO whose input the stale `REG_GEN_AUX_PINS` route had redirected to an
  idle external pin (`0x35` state maps 1:1 to the outcome). Fixed by the
  routing-restore teardown (`cdcb1f6`) and now entry-state independent; both
  passed in the post-fix suite.
- **4 LIS3DH direct-read failures** (test36: I2C @50k/@100k, CTRL_REG1, SPI) —
  root-caused and fixed on 2026-10-13 as a host state leak (see the note
  below): the jumper SPI/I2C matrix leaves the `REG_GEN_AUX_PINS` MISO aux
  route latched, redirecting the RX sampler to an idle external pin. The
  attach-capture decode still passed in the failing state (`320f3333`), which
  is what pinned the fault to the RX input routing, not the sensor or RTL.
  Post-fix the four direct reads pass in the full suite, in a direct `accel`
  run with `0x35` re-armed (2/6 → 6/6), and in a targeted run from the armed
  state.
- **1 skip** — `test12g` analogue jumpers: no analogue jumpers installed on the
  current bench, so that fixture-dependent test skips.

See [Hardware Validation](hardware-validation.md) for the full record.

The retested MCP4725 cases all passed voltage, timing, and ACK checks at 0.5 Hz
/ 8 steps, 0.25 Hz / 24 steps, 2 Hz / 8 steps, and 2 Hz / 10 steps. Wrong
address produced the expected NACK and failure. The codec validation passed
31/31 with no skips; targeted narrow digital 200 MHz, packed MSO, high-speed
analog, and mixed compressed rolling checks passed 27/27, including four packed
ADC channels and 512 analog-fast frames. The source-level Questa
`tb_mso_full_roundtrip.vhd` simulation passed in native Questa 2025.2 on
2026-09-28 at 17:56: 15 analog frames across idle, toggle, and cycle sections,
with four anchors verified per frame. Focused software checks passed: backend 96,
host 195, and frontend 110 tests. Backend hardware self-test passed. These are
targeted results, not full-suite reruns. See the
[final image and board evidence](../evidence/mcp4725-fast-timing-clean/README.md)
and [MCP4725 guide](mcp4725.md). The earlier timing-failing image's verification
record remains [historical](../evidence/max10-flash-verify-2026-09-28.md).

## Full-device reference target (2026-09-09)

| Item | Reference value (2026-09-09) |
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
| Historical image | Full mixed-signal seed-10 image programmed on 2026-09-09 (superseded by the 2026-09-28 seed-7 image) |
| Reference SOF checksum | `0x0050492F` (Quartus Assembler report) |
| Reference SOF SHA-256 | `2028A05F689EA966B359359D8F0F4000A19298B8CEE98AF6A76A52003108C7D1` |

The reference image was built by Quartus Prime Lite 25.1. Its slow 1200 mV,
85 C post-fit timing gate was clean:

| Domain | Setup slack | Hold slack |
|---|---:|---:|
| `fast_clk` | +0.253 ns | +0.291 ns |
| `sdram_core_clk` | +0.178 ns | +0.340 ns |
| `sys_clk` | +0.278 ns | +0.223 ns |
| `SDRAM_CHIP_CLK_OUT` | +1.098 ns | +1.808 ns |
| `SPI_SCK_EXT` | +12.025 ns | +0.394 ns |

That fit used 7,713/8,064 logic elements (96%), 4,802 registers, and
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

The MIL tab also exposes the [MCP4725 DAC test and stepped waveform
generator](mcp4725.md). It can route SDA/SCL across 23 external digital pins,
select one of four analogue inputs, check a 12-bit output against VOUT, and
save a preflight-measured scope reconstruction. D4/D3/AIN4 is the physically
verified route; software routing tests cover all 506 ordered pin pairs,
including that route.

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

### Software baseline — 2026-09-16

The software baseline recorded here is commit `47bce3d`, following the live
accelerometer transport hardening in `a84501e`. At that baseline, global
coverage gates passed over the complete configured production scope rather
than only the accelerometer or transport modules:

| Scope | Result | Coverage scope |
|---|---:|---|
| Backend | **633 passed, 1 opt-in hardware skip** | 10,968 statements and 3,386 branches; 100%, zero partial branches |
| Frontend | **391 passed, 1 opt-in hardware skip** | 4,412 statements, 3,359 branches, 1,110 functions, 3,780 lines; 100% in every metric |
| Frontend build | **Passed** | TypeScript check plus Vite production bundle |

The one normal-run skip in each suite is deliberately hardware-gated. The
backend test requires `MAX1000_LIVE_BACKLOG_TEST=1` and a running real-board
server; the frontend test requires `LIVE_HW_TEST=1` and a live binary waveform
WebSocket. Enabling those flags makes the checks execute; they are not
coverage exclusions.

The completed coverage work added worker-level binary-stream tests, exercised
the main-thread fallback and reconnect/error paths, and closed pre-existing
backend gaps in the WebSocket manager, capture manager, host adapter, MIL
service, and accelerometer service. Coverage is still execution evidence: it
does not replace FPGA simulation, timing analysis, or connected-board tests.

The 2026-09-09 reference image has the following connected-board evidence:

- backend hardware smoke test: **10/10 passed**;
- two consecutive full connected-board suites: **421/421 passed** each
  (**842/842 combined, 0 failed, 0 skipped**), including LIS3DH and
  physical-jumper generic-trigger checks;
- a subsequent physical unplug/replug followed by fresh USB discovery and the
  complete suite: **436/436 passed, 0 failed, 0 skipped**; both 60-second stress
  halves and final concurrent capture/readout passed;
- both strict 60-second rolling stress runs passed inside the full suite, with
  debug disabled and enabled, over 10 million samples captured in each run;
- all 57 HDL testbenches passed with zero expected failures and zero exclusions;
- browser hardware validation passed **5/5** feature tests (all **37/37**
  advertised mode/rate cases) and **34/34** hardware-aligned UI tests; all 26
  ordinary digital/mixed cases require physical 22→CH13 activity, with 40–875
  transitions observed;
- that image's on-wire rate sweep passed **7/7** from 1,200 through 115,200
  baud, with every measured rate within +0.79%;
- the 2026-09-09 screenshot refresh checks exact session identity and loaded
  waveform state; visual review confirmed the expected UART, MIL, LIS3DH,
  analog-fast, four-lane analog, and mixed digital/analog traces;
- auto-discovered analogue jumpers on both installed paths, full-depth SDRAM,
  200.4 MHz narrow capture, packed MSO, pre-trigger, codec, readout-stress,
  and close/reopen lifecycle checks.

The 2026-08-27 image retains its historical 383/383 full-suite, 37/37
browser-matrix, 200 MHz million-sample, generator-rate, and LIS3DH evidence.
Those historical results are not evidence for the later MCP4725 pin-map change.

Software CI requires 100% statement and branch coverage for backend and host,
and 100% statement, branch, function, and line coverage for all production
frontend TypeScript/TSX, plus frontend build/typecheck,
Playwright mock E2E, and all 58 GHDL benches. The self-hosted hardware workflow fails when
its required MAX1000 is unavailable; it does not silently treat absence as a
pass.

At the 2026-09-16 baseline, **391 frontend tests passed**, covering all
**4,412 statements, 3,359 branches, 1,110 functions, and 3,780 lines** in the
configured production TypeScript/TSX scope. See
[Frontend Build and Test](frontend/build-and-test.md).

At the 2026-09-16 software baseline, the host run passed **1,010 tests**, with
all **8,342 statements** and **2,488 branches** covered (zero missing or partial
branches). The backend run passed **633 tests**, covering all **10,968 statements**
and **3,386 branches**. These baseline results are execution evidence, not proof
of all possible behavior or electrical setups.

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
.\compile.ps1 -Flash -Seed 1
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
- The analogue-jumper checks (ADC3/AIN4, ADC7/AIN5 full-scale activity and
  cross-lane isolation) apply **when the analogue jumpers are installed**. The
  2026-10-13 bench has no analogue jumpers, so `test12g` skips and the
  uncalibrated stimulus/ADC1·ADC2 baseline caveats below are not asserted on
  that run: analogue jumpers do not provide calibrated voltage, linearity,
  bandwidth, or all-lane stimulus, and ADC1/AIN3 and ADC2/AIN1 are validated by
  independent-versus-mixed baseline agreement, not a calibrated driven source.
- The digital fly-wire jumper is a bench fixture: a 16→15 single-wire loopback
  without a pull-up cannot carry open-drain I2C SDA (the generator dialogue
  decodes on the internal mirror while the wire holds SDA low), and SPI master
  loopback through the same route recovers only a truncated frame. UART and
  live/ring paths through the same wire pass.
- The four LIS3DH direct checks and two Bit_Engine RX FIFO oracle checks that
  failed in the post-full-suite state on the 2026-10-13 bench were root-caused
  on that date: the jumper SPI/I2C generator matrix leaves the `REG_GEN_AUX_PINS`
  MISO aux route latched (bit 21, pool pin 16), which redirects the Bit_Engine
  RX sampler away from `SEN_SDI` to an idle external pin (all-`0xFF` FIFO → the
  direct reads report "no response/NACK" while the attach mirror still decodes
  the dialogue). No RTL defect was involved. Fixed in two layers:
  `_gen_run_and_rx` clears the aux route on every full burst setup (`14fe006`),
  and `_restore_pin_map` clears it when restoring routing defaults (`cdcb1f6`),
  making both the direct reads and the test8 RX oracle entry-state independent.
  Post-fix evidence: full suite **406/427** (all four direct reads + both RX
  oracles pass); `accel` immediately after a jumper-mode run goes 2/6 → 6/6
  with the leaked register re-armed; a targeted `test_gen_uart` +
  `test_accelerometer_whoami` run from the armed `0x35=0x300000` state passes
  and disarms the register itself; a FIFO burst drain shows `…0x33` dialogue
  bytes instead of all-`0xFF`.
- Live readback capacity depends on USB transport and signal compressibility;
  the ring reports overwrite loss and retains the newest samples.
- Packed-live and the on-board generator share the FPGA Bit Engine/capture-arm
  state, so a driven packed-live electrical test needs an independent external
  source; the current on-board proof covers the packed hardware path and data
  contract.
- The reference-board cold USB/power-cycle check recorded that the board was physically
  unplugged/replugged, rediscovered without reprogramming, and passed 436/436.
- The HDL gate requires every one of the 58 `tb_*.vhd` benches; there are no
  exclusion or expected-failure lists. See [HDL Testbenches](hdl/testbenches.md).

## Contract change checklist

When adding a hardware-facing feature, update the RTL register definitions and
wiring, host protocol/driver, backend capabilities and adapter, frontend
controls, focused simulations, real-board tests, the feature matrix, and the
verification traceability page together.
