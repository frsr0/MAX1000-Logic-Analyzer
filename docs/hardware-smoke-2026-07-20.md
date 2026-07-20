# MAX1000 hardware smoke test — 2026-07-20

Target: safely connected MAX1000 OLS Logic Analyzer  
Observed sample clock: 200 MHz  
Generator routes reported: UART, RS-485, I²C, SPI, SWD, Bit Banger

Result: **10/10 checks passed**

| Check | Result |
| --- | --- |
| Device discovery | PASS |
| Connect and metadata | PASS |
| Capabilities | PASS — 16 digital channels, 200 MHz maximum |
| Device self-test | PASS |
| Digital capture 4096 samples at 1 MHz | PASS |
| Capture sanity checks | PASS — 17 findings, 0 warnings |
| UART generator loopback and decode | PASS |
| RS-485 generator loopback and decode | PASS |
| SPI generator loopback and decode | PASS |
| SWD generator loopback and decode | PASS — 1 SWD transaction captured |

The SPI self-test initially reported an extra `0x80` byte. Inspection of the
archived capture showed it was a one-bit partial word caused by the capture
window ending on a clock edge. The loopback comparator now ignores decoder
events marked as partial while retaining them in the waveform/session for
inspection. The rerun above passed without changing hardware or firmware.

The live route evidence confirms that this bitstream exposes only the
two-output generator paths: RS-485 A/B with internal direction timing, and SPI
MOSI/SCLK. No independent physical DE, CS, or MISO output route was advertised
or exercised, so those roadmap items remain correctly gated on a future
firmware route.

Command:

```text
cd backend
python hw_smoke_test.py
```
