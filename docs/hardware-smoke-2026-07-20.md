# MAX1000 hardware smoke test — 2026-07-20

Target: safely connected MAX1000 OLS Logic Analyzer  
Observed sample clock: 200 MHz  
Generator routes reported: UART, RS-485, I²C, SPI, SWD, Bit Banger

Result: **10/10 checks passed**

The auxiliary-route RTL compiled successfully and was programmed to the
connected 10M08 device before this run (SOF checksum `0x004ADCB4`).

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

The programmed bitstream also exposes the auxiliary routes now covered by the
host API: RS-485 DE on a configurable GPIO (tested with DE pin 6), and SPI CS
on a configurable GPIO (tested with CS pin 7) plus MISO input selection (tested
with the on-board sensor SDO pin 23 mapped to capture channel 15). The fixed
sensor CS/SDO route remains available when no custom CS pin is selected.

Command:

```text
cd backend
python hw_smoke_test.py
```
