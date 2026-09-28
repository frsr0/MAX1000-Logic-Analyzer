# Final timing-clean MAX10 image and MCP4725 retest — 2026-09-28

The final FPGA build used Quartus seed `7` and was compiled and programmed with
`hdl/proj/compile.ps1 -Flash -Seed 7`. Compilation passed the post-fit timing
gate **66/66**. At slow 85 °C, setup slack was `fast_clk` **+0.046 ns**,
`sys_clk` **+0.183 ns**, and SDRAM **+0.396 ns**. The fit used 8,021/8,064
logic elements. Quartus verify-only passed at 17:46:54, before the physical
retests, confirming the final POF matched the image in MAX10 configuration
flash. The POF SHA-256 is
`C65DDA0E8042156024F1170EF967316D072CBF9BADFA67A735EC51BE7FFEC18C`; its
Programmer checksum is `0x01DE1DE1`.

The attached MAX1000 was wired to the MCP4725 at address `0x60`: SDA D4 (J1/13),
SCL D3 (J1/12), and VOUT AIN4 (J1/6). Each passing case reported voltage,
timing, and physical I²C ACK success. The displayed scope trace uses levels
measured before the timed output run and is a reconstruction, not a simultaneous
analog capture. `results.json` contains all browser requests and responses.

| Physical case | Requested | Measured | Result | Screenshot |
| --- | ---: | ---: | --- | --- |
| Sine, 8 steps | 0.5 Hz | 0.5000 Hz | Voltage, timing, ACK pass | [0.5 Hz](01-eight-steps-0_5hz.png) |
| Sine, 24 steps | 0.25 Hz | 0.2493 Hz | Voltage, timing, ACK pass | [24 steps](02-twentyfour-steps-0_25hz.png) |
| 8-step waveform | 2 Hz | 2.0023 Hz | Voltage, timing, ACK pass | [2 Hz](03-eight-steps-2hz.png) |
| Square, 10 steps (20 writes/s) | 2 Hz | 2.0548 Hz | Voltage, timing, ACK pass | [20 writes/s](04-twenty-writes-per-second.png) |
| Saved scope trace | 20 writes/s run | 164.4 displayed samples/s | AIN4 session verified | [Scope trace](05-verified-scope-trace.png) |
| Wrong address `0x61` | 0.25 Hz | 0.25 Hz | NACK; expected FAIL | [NACK check](06-fast-path-wrong-address.png) |
| Restored DAC code 2048 | Midscale | See `results.json` | ACKs; PASS | [Restored output](07-restored-midscale.png) |

Additional validation for this change:

- `host/app/hw_validation.py codec`: **31/31 passed**, no skips.
- Targeted narrow digital 200 MHz, packed MSO, high-speed analog, and mixed
  compressed rolling functions: **27/27 passed**.
- Packed ADC produced all four channel results: `[57, 57, 57, 57]`; analog-fast
  produced **512 frames**.
- Backend hardware self-test passed.
- Focused software runs passed: **96 backend**, **195 host**, and **110 frontend**
  tests. These were targeted runs, not full-suite reruns.

Native Questa 2025.2 passed `tb_mso_full_roundtrip.vhd` on 2026-09-28 at 17:56
(result observed in console output; no run log was saved in the repository).
The bench checked five IDLE frames, five TOGGLE frames, and five CYCLE frames.
Each of the 15 analog frames verifies its header, four channel anchors, and
payload shape; the run also checks per-section digital behavior and overflow
rules.

The earlier MCP4725 run and verify record concern an image with negative setup
slack and are retained as historical records: [earlier physical run](../mcp4725-2026-09-28/README.md),
[earlier image verification](../max10-flash-verify-2026-09-28.md).
