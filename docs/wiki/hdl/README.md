# HDL — FPGA Design Wiki

## Clock Domains

| PLL Output | Frequency | Domain | Purpose |
|---|---|---|---|
| c0 → sys_clk | 100.2 MHz | System | SPI packet protocol, generator control, LED, status |
| c1 → fast_clk | 200.4 MHz | Fast sample | Digital capture, input packing, MSO pipeline |
| c2 → sdram_core_clk | 167 MHz | SDRAM core | SDRAM controller, write pump, readout |
| c4 → sdram_chip_clk | 167 MHz (-1.5 ns) | SDRAM chip | Forwarded SDRAM device clock |
| c3 → adc_conv_clk | 12 MHz | ADC | MAX10 ADC hard IP |

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      OLS_SDRAM_Top                          │
│  ┌──────────┐  ┌─────────────────────────────────────┐     │
│  │SDRAM_PLL │  │    OLS_Logic_Analyzer_SDRAM_Core    │     │
│  │ c0..c4   │  │  ┌──────────┐  ┌───────────────┐   │     │
│  └──────────┘  │  │OLS_Intrf │  │Fast_LA_SDRAM │   │     │
│                │  │(SPI cmd) │  │(capture eng)  │   │     │
│  ┌──────────┐  │  └──────────┘  └───────┬───────┘   │     │
│  │ADC_Ctrl  │  │  ┌──────────┐         │            │     │
│  │(MAX10)   │  │  │Signal_Gen│         │            │     │
│  └──────────┘  │  │(UART,I2C,│    ┌────┴────┐       │     │
│               │  │ SPI,PWM) │    │SDRAM_Ctl│       │     │
│  ┌──────────┐  │  └──────────┘    └─────────┘       │     │
│  │LED_Ctrl  │  └─────────────────────────────────────┘     │
│  └──────────┘                                              │
│  Pin Pool (26-entry) ← MKR_D[14:0] + PMOD[7:0] + LIS3DH   │
│  SPI (FT2232H Channel B) ← CS/SCK/MOSI/MISO                │
│  SDRAM (64 Mbit x16) ← addr/dq/ba/cas/ras/we/cke/cs/clk   │
└─────────────────────────────────────────────────────────────┘
```

## Memory Map

| Storage | Size | Purpose |
|---|---|---|
| BRAM (pre-trigger) | 1,024 words | Fast small captures (FAST_MODE) |
| Async FIFO (bridge) | 1,024 words | FAST_CLK → SDRAM_CLK domain crossing |
| SDRAM | 4,194,304 words × 16-bit | Deep single-shot / bounded ring captures |
| Generator FIFO | 256 bytes | Protocol symbol data for Signal_Gen / Bit_Engine |
| Raw stream compressor FIFO | 8 words | On-chip RLE streaming buffer |

## Wiki Pages

### Top-level & Integration
- [Top-Level Architecture](top-level-architecture.md) — `OLS_SDRAM_Top`, pin pool, clock gen, I/O muxing
- [Core Wrapper](core-wrapper.md) — `OLS_Logic_Analyzer_SDRAM_Core` component wiring

### Capture Subsystem

The active readback codecs are direct full-word RLE and packed delta-RLE in
`OLS_Interface`. See
[Hardware Validation](../hardware-validation.md) for the direct compression
matrix and measured ratios.
- [Capture Engine](capture-engine.md) — `Fast_Logic_Analyzer_SDRAM`: sample divider, BRAM, FIFO, SDRAM write pump, triple-buffer continuous mode, narrow digital, packed mode
- [SDRAM Controller](sdram-controller.md) — `SDRAM_Controller_Custom`: open-page streaming, read/write/burst, single-shot completion
- [MSO Capture Pipeline](mso-capture.md) — `mso_capture`: mixed-signal bit-packing, delta_calc → analog_packer, digital_rle → stream_mux
- [Delta Calculator](delta-calc.md) — `delta_calc`: per-channel anchor capture, signed delta, 3-stage pipeline
- [Analog Packer](analog-packer.md) — `analog_packer`: v1/v2 packed analog block frames
- [Digital RLE](digital-rle.md) — `digital_rle`: 4-slice run-length encoder
- [RLE Compressor](rle-compressor.md) — Generic RLE compressor core
- [Historical Delta-RLE Compressor](delta-rle-compressor.md) - Retired delta-to-RLE design and rationale
- [Historical Capture Compressor](capture-compressor.md) - Legacy wrapper retained for reference
- [Stream Mux](stream-mux.md) — `mso_stream_mux`: analog/digital sub-stream arbiter

### SPI / Control
- [SPI Packet Protocol](spi-packet-protocol.md) — packet framing, CRC-16, command set, status codes, register map, RX/TX FSMs
- [SPI Interface](spi-interface.md) — `OLS_Interface`: command dispatch, block readout, raw streaming compressor, generator capture FSM, sticky DONE/ACK

### Signal Generator
- [Signal Generator & Bit Engine](signal-generator.md) — `Signal_Gen` + `Bit_Engine`: protocol generation, 2-bit symbol encoding, host-side encoders

### Peripherals
- [ADC Controller](adc-controller.md) — `ADC_Controller`: MAX10 ADC, one-slot high-speed, multi-slot scanning
- [Protocol Trigger](protocol-trigger.md) — `Protocol_Trigger`: UART byte trigger
- [UART Interface](uart-interface.md) — `UART_Interface`: async receiver
- [LED Controller](led-controller.md) — `LED_Controller`: PWM, fade engine
- [SDRAM PLL](sdram-pll.md) — `SDRAM_PLL`: clock generation, phase shifts

### Project
- [Build Flow](build-flow.md) — Quartus project, constraints, compile/seed-sweep scripts, timing
- [Testbenches](testbenches.md) — All 40+ HDL simulation testbenches

## Build Target

## Current timing note

The corrected RTL reference at fitter seed 21 reports slow-85C `fast_clk`
setup slack `-0.139 ns` and TNS `-1.380 ns`; this is not a signoff-closed
200 MHz image. See [Build Flow](build-flow.md) and
[FAST Capture Stream](fast-capture-stream.md) for the current timing gate and
the tested stream seams.

| Property | Value |
|---|---|
| FPGA | Intel MAX 10 10M08DAF484C8G |
| Speed grade | C8 |
| Build profile | FAST_SPEED (200 MHz sample clock) |
| Validated seed | 3 (2026-07-10; re-sweep after any RTL change — see `TIMING_REPORT_SUMMARY.md`) |
| Utilisation | 84% LEs (6,750/8,064) |
| Toolchain | Quartus Prime (proj/compile.ps1) |
