# Hardware Validation

Hardware validation runs against the FPGA image flashed on the MAX1000. These
tests exercise register writes, capture timing, SDRAM readback, SPI transport,
and lossless decompression on the real board.

## Current exact-image result — 2026-10-13 (fitter seed 1)

The board now runs the current checked-in RTL built with Quartus Prime Lite
25.1, fitter seed `1` (the selected closing seed), programmed into
configuration flash on 2026-10-13. Post-fit timing gate **69/69**; slow-85C
setup slack is `fast_clk` **+0.024 ns**, `sys_clk` **+0.194 ns**, and
`sdram_core_clk` **+0.226 ns**; all setup, hold, recovery, removal, and
minimum pulse-width checks are positive (TNS 0.000). The fit uses
**8,027/8,064** logic elements, 5,048 registers, and 54,404 memory bits. The
assembler reports SOF checksum `0x0057C698`; POF SHA-256
`373C6C40CAEF54095B106142A4263C803AD3ABC0FC56B2F19AAA556B77A5D86E`; Programmer
checksum `0x01DDD65A`.

| Suite | Result | Scope |
|---|---:|---|
| `backend/hw_smoke_test.py` | **10/10** | Discovery, metadata, SDRAM capture, sanity, UART/RS-485/SPI/SWD exact loopback decode |
| `host/app/hw_validation.py` full suite (pre-fix) | **400/427 passed, 27 failed, 1 skipped** | Cold reflash followed by the complete suite on the 2026-10-13 bench (16→15 fly-wire, no analogue jumpers); 4 of the 27 (LIS3DH direct reads) are the state leak fixed on 2026-10-13 — see the attribution bullets |
| Fresh-boot `host/app/hw_validation.py accel` | **6/6** on seed-1, 09-28 and 08-27 images | LIS3DH I2C @50k/@100k WHO_AM_I, CTRL_REG1, SPI mode-3, attach-capture I2C/SPI |
| Fresh-boot `host/app/hw_validation.py jumper` | **8/12** (SPI/I2C matrix fail) | Identical on the seed-1 and 09-28 images; UART/live/ring all pass |

**Attribution of the 27 full-suite failures.** The identical full suite run
against the byte-matched 09-28 seed-7 image (a rebuild of the checked-out
`bf1ade3` tree, POF checksum `0x01DE1DE1`) reproduces the same **400/427** on
this bench — same failure set, run-to-run-only variation in the crosstalk
percentages. None of the 27 failures is therefore attributable to the reviewed
RTL bug fixes (`3f64bc0`, `759adc4`), the shared CRC-16 cone (`173e0a5`), or
the seed change; the reference-era 436/436 was achieved on a different bench
(layout 22→CH13 with analogue jumpers installed).

- **15 bench-state crosstalk/cleanliness failures** — rolling-gen mirrors on
  CH1/2/4/5 (50–68% partial coupling, below the 95% coherent-mirror gate),
  `trig_fall` clean checks, and rolling-gen UART decode — from floating
  adjacent wires on the current bench.
- **6 physical-jumper SPI/I2C fixture failures** — I2C loopback + matrix
  decode nothing; SPI loopback + matrix decode only a 1-byte `af` fraction —
  fail identically on a fresh 09-28 image. I2C over the fly-wire lacks the
  required pull-up on the open-drain SDA (raw capture shows SDA held low on
  the wire while the generator's own dialogue `a62d08` decodes perfectly).
- **2 Bit_Engine RX FIFO oracle failures** (test8 ×2) — the oracle compares
  the RX FIFO samples against the UART TX pattern, but the on-board UART
  pins have no physical return loopback into the Bit_Engine RX input, so the
  FIFO captures the idle line. The test's own design comment marks these
  checks informational for UART, not a generator-correctness gate.
- **4 LIS3DH direct-read failures** (test36: I2C @50k/@100k, CTRL_REG1, SPI) —
  root-caused and fixed on 2026-10-13 as a **host-side state leak, not an RTL
  defect**. The jumper SPI/I2C generator matrix latches the `REG_GEN_AUX_PINS`
  MISO route (bit 21 → pool pin 16) and never disarms it; the Bit_Engine RX
  input mux then samples that idle external pin instead of `SEN_SDI`, so every
  sensor direct-read burst fills the RX FIFO with all-`0xFF` (decode reports
  "no response/NACK") even though the attach-mirror capture proves both
  directions toggle on the wire. The attach-capture checks were therefore
  unaffected in every failing run (`320f3333` still decoded). Fix:
  `ols_spi_device._gen_run_and_rx` now clears `REG_GEN_AUX_PINS` on every full
  burst setup. Post-fix, with the leaked register still armed (`0x35=0x300000`),
  `python app/hw_validation.py accel` directly after a jumper-mode run goes
  from **2/6 to 6/6**, and a burst-level FIFO drain shows the real dialogue
  bytes (tail `…0x33`) instead of all-`0xFF`.
- **1 skip** — `test12g` analogue jumpers (not installed on this bench).

## Historical exact-image result — 2026-09-09 (seed 10 reference)

That image was built with Quartus Prime Lite 25.1, fitter seed 10, and
programmed into configuration flash. The assembler reported SOF checksum
`0x0050492F` (SOF SHA-256
`2028A05F689EA966B359359D8F0F4000A19298B8CEE98AF6A76A52003108C7D1`).
Slow-85C setup slack was `fast_clk +0.253 ns`, `sdram_core_clk +0.178 ns`, and
`sys_clk +0.278 ns`. That run's bench used a different physical layout
(digital pool pin 22 → capture channel 13) with both analogue jumpers
installed, which the 2026-10-13 bench does not.

| Suite | Result | Scope |
|---|---:|---|
| `backend/hw_smoke_test.py` | **10/10** | Discovery, metadata, SDRAM capture, sanity, UART/RS-485/SPI/SWD exact loopback decode |
| Cold-power-cycle `python host/app/hw_validation.py` | **436/436**, 0 failed, 0 skipped | Unplug/replug boot followed by the full suite, including active five-rate bit-exact codecs, 75-case crosstalk sweep, LIS3DH, physical-jumper generic trigger and exact protocols, analogue/MSO, lifecycle, concurrent readout, live codec ceilings, and both strict 60-second stress runs |
| Two-run repeat validation | **842/842**, 0 failed, 0 skipped | Two consecutive full runs, 57 artifacts each, from 17:45:56–18:04:26 on 2026-09-09 |
| Browser hardware suites | **5/5** feature tests and **34/34** hardware-aligned UI tests | Includes all 37 advertised acquisition mode/rate combinations against the attached board |
| `bash hdl/tb/run_all_tbs.sh` | **57/57** | Every HDL bench, zero XFAIL/XPASS/exclusions |

The browser suites and all 37 hardware-matrix acquisitions were rerun on
2026-09-16 after strengthening the screenshot contract. Each live gallery
image now proves the named session is active and its waveform payload has
loaded. The 1440×1400 captures were visually checked for distinct UART, MIL,
LIS3DH, analog-fast, maximum-analog, and mixed traces; the current files and
manifest are indexed in [Hardware Screenshot Matrix](hardware-screenshot-matrix.md).
The 26 ordinary digital and mixed cases also start a 1 MHz alternating UART
pattern on pool pin 22 and require transitions on CH13 in the stored waveform;
the observed range was 40–875 transitions. Packed-live capture is checked as
its own one-channel hardware path, but cannot use that same FPGA Bit Engine as
an independent electrical source because arming the packed ring clears it.

The board was physically unplugged and replugged on 2026-09-09, then discovered
from a fresh USB connection without reprogramming. The complete validation ran
from 18:20:44 to 18:29:38 and passed 436/436 checks. It rediscovered digital
pin 22 -> CH13 and both analogue jumpers, completed both 60-second stress halves
without exceptions, observed 60,795 driven-CH0 transitions, and passed the final
concurrent capture/readout test. The local evidence is preserved as
`run-summary-cold-power-cycle.json`.

The first live-rate run exposed a host cancellation race: a watchdog could be
set after the ring loop condition but before stream transport entry. The
transport correctly sent nothing, but the protocol layer labelled that clean
cancellation as “no stream ack”, and the characterization caught and ignored
the exception. Cancellation now returns an empty result, genuine transport
exceptions increment the hardware failure count, and focused unit tests cover
both paths. The current suite measures sustained throughput after the first
block; the 10 kHz source reached a lossless 0.50 MS/s ceiling in both raw and
delta-RLE modes, with no transport errors.

A subsequent test audit found that the `new` and `codec` entrypoints could
return success after an exception before any checks ran. Both now record a
failed check and return a failing status; regression tests reproduce that
case with initially zero failures. The stress test also used only 10 seconds
despite longer-duration comments. Its default is now 60 seconds, with an
explicit elapsed-time check and no suppression of stalled-read timeouts.
Fault-injection tests reject short streams and stalled reads. The corrected
60-second checks now run as required parts of the 421-check full suite.

The debug-enabled stress oracle now requires driven CH0 activity. The two
repeat runs captured more than 12.6 million samples in every 60-second half,
with 95,473 and 88,219 driven-CH0 transitions respectively. All four stress
halves completed without an exception. The suite discovered digital pool pin 22 to capture channel 13,
identified CH5 as a 100%-matching physical mirror of driven CH3, and found
both analogue paths (pool pin 20 to ADC7/AIN5 and pin 21 to ADC3/AIN4). Each
analogue path reached the full 4095-code span. Mixed ADC1/AIN3 and ADC2/AIN1
lanes also matched immediate independent analog-fast baselines.

A preliminary repeat completed 435/436 checks before these two clean runs.
That older summary format retained only the failure count, so the individual
failed check could not be recovered from truncated console output. This is why
the manifest now records the exact failure messages. The two fully attributable
repeat runs performed after that instrumentation change are the 842/842 result
reported above; the preliminary run is not included in that total.

The authoritative machine-readable record is generated locally as
`hdl/hw_test/hw_results/run-summary-full.json` (the hardware-results directory
is intentionally git-ignored). Focused modes write separate manifests so they
cannot overwrite the full-suite evidence. Run summaries now include the exact
failure messages as well as counts, so an intermittent failure remains
attributable even when console output is truncated. The two repeat manifests
were preserved locally as `run-summary-full-repeat-1.json` and
`run-summary-full-repeat-2.json`.

The current image's direct on-wire generator sweep also passed **7/7** at
1,200, 2,400, 4,800, 9,600, 19,200, 57,600, and 115,200 baud. Every measured
rate was within +0.79% of its request.

## Software and live-transport validation — 2026-09-16

With the same board connected through the backend at `127.0.0.1:8002`, the
focused live-transport checks reported:

| Check | Result | Scope |
|---|---:|---|
| Backend live backlog watch | **1/1 passed** in about 5.07 s | `MAX1000_LIVE_BACKLOG_TEST=1`; connected binary stream, bounded backlog observation |
| Frontend connected binary-packet test | **11/11 reported passing** | Worker decoder exercised against the live board stream |
| Backend software gate | **633 passed**, 1 opt-in hardware skip | 10,968 statements and 3,386 branches at 100% |
| Frontend software gate | **391 passed**, 1 opt-in hardware skip | 4,412 statements, 3,359 branches, 1,110 functions, and 3,780 lines at 100% |
| Frontend production build | **Passed** | TypeScript/Vite production build |

The hardware-gated tests are opt-in (`MAX1000_LIVE_BACKLOG_TEST=1` for the
backend and `LIVE_HW_TEST=1` for the frontend), so an ordinary software run
reports them as skipped rather than silently claiming board coverage. These
focused checks complement the 436/436 connected-board regression; they do not
replace a full-duration FIFO/USB soak or sensor calibration test.

The temporary live server was stopped and disconnected after validation. A
later teardown status reported a direct FIFO-overflow fault from the
long-running stream. That observation is recorded as an operational boundary,
not claimed as a pass for indefinite direct streaming. The browser and backend
diagnostics must therefore be read by layer: `source_backlog_samples=0` proves
the FPGA/ring source is clear, while packet counts, sequence gaps, disconnects,
and decode latency measure the browser transport separately.

## Historical exact-image result — 2026-08-27 to 2026-08-30

That historical full mixed-signal image was built with Quartus Prime Lite 25.1,
fitter seed 10, and the 24-bit generator divider, then programmed into MAX 10
configuration flash on 2026-08-27. The assembler reports SOF checksum
`0x0050ADC8`. Slow-85C setup slack is `fast_clk +0.083 ns`,
`sdram_core_clk +0.111 ns`, and `sys_clk +0.410 ns`; all reported setup and
hold domains are positive.

Connected-board evidence for this image:

| Suite | Result | Scope |
|---|---:|---|
| `backend/hw_smoke_test.py` | **10/10** | Discovery, metadata, capture, routes, generator loopbacks, decode |
| `host/app/hw_validation.py` | **383/383** | Register/capture contract, SDRAM, codecs, analog/MSO, triggers, generator, recovery, LIS3DH |
| Generator rate sweep | **6/6** | 1,200-115,200 baud, every case within +0.79% |
| Browser hardware matrix | **37/37** | Every advertised source/acquisition/rate combination |

Additional focused checks passed a 1,000,000-sample capture at 200 MHz,
generator traffic during rolling capture, and clean live stop/recovery. The
historical matrix run recorded `failed=0`; its PNGs remain under the older
`hardware-matrix-*` names. The machine-readable `hardware-validated-matrix`
manifest is intentionally regenerated by the current run and now records the
2026-09-09 persistent image.

Test 12g no longer assumes fixed analogue jumper wiring. It drives UART from
each generator pool pin, watches each ADC lane in analog-fast mode, discovers
connected pairs, and runs the analogue checks for the detected fixture. With
no analogue jumper, that fixture-dependent test skips explicitly rather than
producing a false failure. Digital jumper discovery remains independent.

The host SPI decoder also bounds sampling of the final SCLK plateau. After a
burst, SCLK may remain high while the released data line falls; sampling the
geometric middle of that unbounded idle plateau intermittently changed the
last byte from `0xff` to `0xfe`. The current decoder samples an anomalously
long final plateau at a normal bit offset.

## Exact-image rerun — 2026-07-26 / exhaustive closure — 2026-07-27

The repaired seed-30 SOF (`0x0050CF93`) was rerun on the connected board after
the packed-MSO fixes. The suite completed **357/380 passed, 23 failed, 0
skipped**. After correcting the mixed-frame contract and ring/codec test
assumptions, focused regression completed **117/117 passed** and the
accelerometer capture check completed **2/2 passed**.

The final exhaustive rerun completed **391/391 passed, 0 failed, 0 skipped**.

Full log: `host/fullsuite_postfix_2026-07-27-rerun.txt`.

## Historical web-app smoke result — 2026-08-07

The connected MAX1000 passed the current `backend/hw_smoke_test.py` real-hardware path **10/10** at a 200 MHz sample clock. This covers the same `CaptureManager -> ExistingHostAdapter -> host/driver/OLSDeviceSPI` path used by the web app. The default plain capture now uses **10 MHz and 40,960 samples** (about 4.1 ms), keeping the same observation window as the old 1 MHz / 4,096-sample check while giving ten times more samples per pulse.

| Check | Result |
|---|---|
| Discovery, connect, metadata | PASS — 200 MHz sample clock |
| Capabilities and control-plane self-test | PASS — 16 digital channels, 200 MHz maximum, RS-485 DE and SPI CS/MISO routes advertised |
| 40,960-sample digital capture and sanity checks | PASS — 40,960 samples; 0 edges and 0 warnings after the pin/pull-up fix |
| UART generator loopback/decode | PASS — session `ses_6016ac5eaa` |
| RS-485 generator loopback/decode | PASS — session `ses_cb40d27542` |
| SPI generator loopback/decode | PASS — session `ses_92ff31d215` |
| SWD generator loopback/decode | PASS — one decoded transaction, session `ses_114c6beaca` |

The pre-fix digital capture was saved as session `ses_1aae405834` and reported one CH13 narrow-pulse warning. This was not intermittent readback corruption: repeated direct captures changed only bit 13, and the existing jumper-discovery routine identified a real generator path from pre-fix pool pin 22 to capture channel 13. A controlled UART burst on that path decoded correctly in both SDRAM and fast capture modes.

The investigation found two hardware-definition issues behind the warning's misleading label. The pre-fix fitted Quartus report showed the QSF `GPIO[0..7]` weak-pull assignments were ignored, while the actual `MKR_D` inputs—including `MKR_D[13]`—had weak pull-up **Off**. The descending `MKR_D(14 downto 0)` / `PMOD(7 downto 0)` vectors were also assigned a D0-to-D14 / PIO1-to-PIO8 pin list, reversing the physical labels: fitted `MKR_D[13]` was board D1/K10, not D13/H13.

The fix reversed the wrapper pin lists and moved pull-up assignments to the real `MKR_D`/`PMOD` ports. Quartus compiled successfully with SOF checksum `0x0051801E`; the image was programmed to the attached board and persisted as POF checksum `0x01D53118`. The final post-program smoke capture is session `ses_440cd83d82`, with 40,960 samples, 0 edges, and 0 warnings. Twelve additional direct captures at 10 MHz / 40,960 samples all remained constant with no changed bits. Post-fix jumper discovery now reports the expected pool pin 1 to capture channel 15 pair.

`--rate` and `--samples` remain available for targeted rate or duration experiments; when `--samples` is omitted, the test preserves the ~4.1 ms observation window as the rate changes.

Reproduce from `backend/` with the MAX1000 and FTDI driver connected:

```text
python hw_smoke_test.py
```

The focused packed-MSO check and both physical analogue jumper checks passed.
The decodable analogue MSO proof is
[mso-analog-uart-live.png](../../frontend/test-results/screenshots/mso-analog-uart-live.png).

## Historical smoke result — 2026-07-20

On 2026-07-20 the connected MAX1000 was programmed with SOF checksum
`0x004ADCB4`. The end-to-end smoke test passed **10/10**:

| Check | Result |
|---|---|
| Discovery, connect, metadata | PASS |
| Capabilities and self-test | PASS |
| 4096-sample digital capture and sanity checks | PASS |
| UART loopback/decode | PASS |
| RS-485 loopback/decode with DE route | PASS |
| SPI loopback/decode with CS/MISO auxiliary capture | PASS |
| SWD generator loopback/decode | PASS |

The full evidence, including session IDs and the programmed image, is in
[docs/hardware-smoke-2026-07-20.md](../hardware-smoke-2026-07-20.md).

The previous successful real-hardware run on 2026-07-23 completed
**358/358 passed, 0 failed, 0 skipped** against the freshly rebuilt and
programmed seed-44 SOF checksum `0x00515DB0`.  Two new hardware-trigger tests
were added:

| Test | What it proves |
|---|---|
| **14f** — `test_generic_pattern_trigger_hw` | Internal `Generic_Pattern_Trigger` FSM: baud counter to shift register to comparator to trigger to capture complete (match_mask=0, any pattern) |
| **14g** — `test_generic_pattern_trigger_jumper` | Full external path: Bit_Engine UART 0x55 through physical jumper wire to pattern trigger with match_mask=0xFF |

The on-board jumper (post-fix pool pin 1 to capture channel 15; pre-fix it
appeared as pool pin 22 to capture channel 13) is now discovered
at the start of the suite via `_get_jumper_pair()`, and `_floating_except()`
automatically excludes the jumper RX channel from noise-floor checks.
Continuous ring, 200 MHz narrow capture, packed digital/MSO, analog/mixed
signal, codec, readout-stress, generator matrix, trigger, full-depth SDRAM,
both physical analog jumper paths, lifecycle, and on-board LIS3DH
accelerometer I2C/SPI checks all passed.

The post-regression automation checks also cover the operational paths around
that hardware run: the live CLI queue command completed a real capture as
`ses_ffa381dba9`, and the pre-trigger strategy test verifies 25%, 50%, and 75%
sample positions reach the device driver and are recorded as the trigger
sample. The Sessions page now requests filtered, bounded pages (100 rows by
default), so comparison remains usable on large soak-test libraries.

CAN/LIN health remains a hardware-fixture dependency: the software and mock
dashboard checks pass, but an electrical end-to-end check still requires a
CAN transceiver and an active LIN source connected to the board.

The compact evidence record and reproduction commands are in
[hardware-validation-2026-07-22.md](../hardware-validation-2026-07-22.md).

The earlier complete suite on the prior flashed image was **432/434 passed,
2 failed, 0 skipped**.

The PMOD5 jumper was reseated before the final run; it now reaches 4095-code
full scale with 728 detected edges, while the cross-check ADC remains free of
repeated activity. PMOD6 likewise passes at 4095 codes and 727 edges.

The raw live-ring characterization measured an approximately 1.00 MS/s
lossless ceiling on this USB host path; packed `delta_rle` measured an
approximately 0.50 MS/s lossless ceiling. Direct `rle` and `delta_rle` remain
bit-exact in the finite readback matrix through 200.4 MS/s; live throughput is
reported separately because it is transport- and source-dependent.

Run the smoke test from `backend/`; run the legacy validation/debug tools from
`host/`, with the FTDI/JTAG hardware connected:

```powershell
cd ..\backend
python hw_smoke_test.py
cd ..\host
python debug/hwt_test_bitbang_pwm.py
python debug/hwt_test_compression_matrix.py
python -m app.hw_validation
python -m app.hw_validation analog
```

## PWM/Bit Engine regression

PWM hardware tests use the normal two-output Bit Engine/Bit Banger path. Encode
a finite period/duty symbol pattern, load it through the generator API, capture
the loopback, and check edge count, duty ratio, and compression round trips.
The old debug-CH0 register test is retired because registers `0x42-0x44` no
longer exist in the production HDL.

The 2026-07-22 board result includes the `89b84898` FPGA-side repeat-mode
change for raw symbols, PWM, and RS-485; focused simulation, host-driver, and
full connected-board coverage now agree on that path.

## Compression matrix

`hwt_test_compression_matrix.py` captures 4096 digital samples for idle,
10 kHz PWM, 100 kHz PWM, and 1 MHz PWM at 1 MHz, 10 MHz, and 50 MHz sample
rates. Every compressed result is expanded and compared byte-for-byte with
raw readback.

Historical 2026-07 connected-board ratios:

| Source | 1 MHz | 10 MHz | 50 MHz |
|---|---:|---:|---:|
| Idle | 85.33x | 256.00x | 256.00x |
| PWM 10 kHz | 13.04x | 128.00x | 128.00x |
| PWM 100 kHz | 2.36x | 22.76x | 24.38x |
| PWM 1 MHz | 256.00x* | 2.49x | 2.69x |

Ratios are encoded payload bytes only; SPI packet headers, USB latency, and
host decode time are excluded. The 1 MHz PWM sampled at 1 MHz is
phase/alias-sensitive and is not a useful waveform-compression benchmark.

The readback matrix covers raw, exact full-word `rle`, and packed-delta-plus-RLE
`delta_rle`; the two compressed modes are distinct hardware selections.

## Existing validation coverage

`host/app/hw_validation.py` also contains a codec readback matrix that checks
bit-exact raw/RLE/delta-RLE round trips across rates, live-ring throughput
characterization for all three digital modes, and digital, analog, mixed-signal,
generator, trigger, reset, and recovery tests. The live-ring throughput check
reports the measured ceiling for each codec; raw remains a hard pass gate, and
`delta_rle` is reported as a bounded characterization point rather than a
minimum-lossless assertion.

The analogue fixture is discovered instead of hard-coded. The validator sweeps
generator pool pins against ADC lanes, drives a UART pattern through each
detected jumper, requires repeated full-scale activity on the selected lane,
and checks other connected ADCs for cross-talk. `python -m app.hw_validation
analog` remains the focused command. The complete suite includes the same
check but records an explicit skip when no analogue jumper is installed.
Digital pin-to-pin jumper tests are separate and discover their own route.
