# MCP4725 timed generator fix — physical evidence

These unedited app screenshots were captured on 28 September 2026 with the wired MAX1000/MCP4725: SDA D4 (J1/13), SCL D3 (J1/12), VOUT AIN4 (J1/6), address 0x60. [results.json](results.json) contains every browser request and full response. [The repeatable browser test](../../../frontend/tests/e2e/mcp4725-fast.evidence.mjs) generated the screenshots.

The generator now measures each distinct code through the analog input **before** its timed output pass. The timed pass uses the FPGA I²C receive FIFO to check real slave ACKs without an ADC/SDRAM readback between writes. The saved scope trace replays those measured code levels at the measured I²C cadence; its session notes explicitly state that it is a reconstructed trace, not a simultaneous analog recording of the fast pass.

The reported rate comes from monotonic host timestamps at each write. No independent oscilloscope timing measurement was made during the fast pass.

| Hardware test | Requested | Actual | Electrical checks | Screenshot |
| --- | ---: | ---: | --- | --- |
| Sine, 8 steps | 0.5 Hz | 0.5000 Hz | Voltage, timing, ACK pass | [0.5 Hz PASS](01-eight-steps-0_5hz.png) |
| Sine, 24 steps | 0.25 Hz | 0.2503 Hz | Voltage, timing, ACK pass | [24 step PASS](02-twentyfour-steps-0_25hz.png) |
| Square, 8 steps | 2 Hz | 1.9977 Hz | Voltage, timing, ACK pass | [2 Hz PASS](03-eight-steps-2hz.png) |
| Square, 10 steps (20 writes/s) | 2 Hz | 1.9868 Hz | Voltage, timing, ACK pass | [Maximum update rate PASS](04-twenty-writes-per-second.png) |
| Saved trace | 20 writes/s run | 158.9 displayed samples/s | Session names verified AIN4 and describes trace source | [Open scope trace](05-verified-scope-trace.png) |
| Wrong address 0x61 | 0.25 Hz | 0.2500 Hz | NACK detected; overall FAIL as expected | [NACK proof](06-fast-path-wrong-address.png) |
| Restored code 2048 | 1.65 V expected | 1.685 V measured | Three ACKs; PASS | [Final board state](07-restored-midscale.png) |

Before this change, the 0.5 Hz / 8 step and 0.25 Hz / 24 step requests missed timing because every update waited for a four channel ADC capture. [The original test record](../mcp4725-2026-09-28/README.md) preserves the before measurements. The new timed run verified both targets on the same wiring.

## Checks

```text
node tests/e2e/mcp4725-fast.evidence.mjs   7 physical evidence records and screenshots
python -m pytest app/tests/test_mcp4725.py -q   13 passed
npx vitest run src/components/MilMcp4725Card.test.tsx src/api/client.test.ts   103 passed
npm run build   passed
```

The browser test restored DAC code 2048 and released its control lock.
