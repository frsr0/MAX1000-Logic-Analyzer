# MCP4725 analog MIL and signal generator evidence

**Historical measurements before the timed generator fix.** The same physical board now passes 0.5 Hz with 8 steps and 0.25 Hz with 24 steps. See the [after-fix screenshots and results](../mcp4725-fast-2026-09-28/README.md).

Captured on 28 September 2026 from the running app and a wired MAX1000/MCP4725. The physical route was SDA D4 (J1/13), SCL D3 (J1/12), VOUT AIN4 (J1/6), address 0x60, 3.3 V supply. The browser screenshots are unedited Playwright captures. [results.json](results.json) stores each request and full response, including all measured samples and pass criteria. [The historical browser script](../../../frontend/tests/e2e/mcp4725.evidence.mjs) produced these files on the pre-fix version.

## Physical DAC and analog input

| Check | Result | Screenshot |
| --- | --- | --- |
| Default controls and wiring labels | D4/D3, AIN4, 0x60, 0.25 Hz | [Controls](01-hardware-default-controls.png) |
| Three point DAC test | 0.0052 V, 1.6629 V, 3.2915 V for codes 0, 2048, 4095; all nine I²C bytes ACKed | [Three point PASS](02-hardware-three-point.png) |
| Single code 1024 | 0.8562 V; all three bytes ACKed | [Single code PASS](03-hardware-single-code.png) |
| Wrong address 0x61 | NACK and FAIL as expected | [Wrong address](04-hardware-wrong-address.png) |
| Wrong SCL D5 | NACK and FAIL as expected | [Wrong SCL](05-hardware-wrong-scl.png) |
| End state | Restored code 2048; 1.6742 V; three ACKs | [Restored midscale](17-hardware-restored-midscale.png) |

## Physical signal generator

Each shape used eight DAC steps per cycle, a 0.25 Hz request, and a 0.2–3.1 V range unless noted. Every passing run had voltage, timing, and I²C ACK checks pass.

| Check | Measured result | Screenshot |
| --- | --- | --- |
| Sine | 0.2489 Hz; 0.207–3.114 V | [Sine PASS](06-hardware-sine.png) |
| Triangle | 0.2478 Hz; 0.208–3.125 V | [Triangle PASS](07-hardware-triangle.png) |
| Sawtooth | 0.2478 Hz; 0.209–2.766 V | [Sawtooth PASS](08-hardware-sawtooth.png) |
| Square | 0.2522 Hz; 0.186–3.136 V | [Square PASS](09-hardware-square.png) |
| Two square cycles | 0.2500 Hz; 16 DAC writes | [Two cycles PASS](09a-hardware-two-cycles.png) |
| Custom 0.5–2.5 V range | 0.2500 Hz; 0.489–2.533 V | [Custom range PASS](10-hardware-custom-range.png) |
| Saved analog scope session | 64 displayed points at 16.0 Hz; ADC3/AIN4 trace | [Hardware scope](11-hardware-scope-session.png) |

The eight step sawtooth does not include the endpoint of a cycle, so its highest commanded voltage is below 3.1 V. The hold trace repeats each real ADC reading eight times for display; the hardware takes one analog measurement after each I²C write.

### Measured throughput limit

The DAC and voltage checks still passed at these settings, but the measured rate missed the requested timing target:

| Request | Actual | Screenshot |
| --- | --- | --- |
| 24 steps/cycle at 0.25 Hz | 0.0991 Hz | [24 step timing FAIL](09b-hardware-24-steps.png) |
| 8 steps/cycle at 0.5 Hz | 0.2995 Hz | [0.5 Hz timing FAIL](12-hardware-timing-limit.png) |

This generator verifies VOUT after every update, including a four channel ADC capture. These screenshots establish the tested physical performance limit; the app reports a timing failure when a setting exceeds it.

## Selection, simulation, and input validation

The UI exposed all eight MCP4725 addresses, 23 SDA choices, 23 SCL choices, four analog inputs, and four waveform shapes. The backend test `test_all_selectable_i2c_pin_pairs_route_to_physical_ack_capture` checked every 506 ordered pair of distinct digital pins with synthetic physical ACK captures. This checks software routing; only D4/D3 was physically wired to the DAC during this run.

| Check | Screenshot |
| --- | --- |
| Mock address 0x67, SDA PIO_08, SCL D0, ADC4 | [Selected route PASS](13-mock-selectable-route.png) |
| Mock ADC1, ADC2, ADC3 | [ADC1](131-mock-adc1.png), [ADC2](132-mock-adc2.png), [ADC3](133-mock-adc3.png) |
| Mock triangle, 12 steps, two cycles, 0.4–2.9 V | [Custom waveform PASS](14-mock-custom-waveform.png) |
| Saved mock scope session | [Mock scope](15-mock-scope-session.png) |
| Mock upper frequency setting, 2 Hz | [2 Hz simulation PASS](15a-mock-max-frequency.png) |
| Same SDA/SCL and inverted voltage range | [Buttons disabled](16-invalid-controls-disabled.png) |

Mock screenshots are labelled **SIMULATED**. They prove the UI and response path for the selected controls, not electrical performance on those unwired pins.

## Checks run

```text
node tests/e2e/mcp4725.evidence.mjs       23 evidence records, 23 screenshots
python -m pytest app/tests/test_mcp4725.py -q   11 passed
npx vitest run src/components/MilMcp4725Card.test.tsx src/api/client.test.ts   103 passed
npm run build                            passed
git diff --check                         passed
```

The browser run restored hardware mode and code 2048 after testing. It released its control lock.
