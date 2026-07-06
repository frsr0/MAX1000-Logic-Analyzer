# HDL - OLS Logic Analyzer FPGA Design

This directory contains the FPGA implementation for the MAX1000 build.
The maintained configuration is the speed build with `FAST_SPEED => true`.

## Top-Level Architecture

The current clock plan is:

| Output | Frequency | Use |
|--------|-----------|-----|
| `c0` -> `sys_clk` | 100.2 MHz | SPI packet protocol, generator, LED control |
| `c1` -> `fast_clk` | 200.4 MHz | Sample capture and input packing |
| `c2` -> `sdram_core_clk` | 167 MHz | SDRAM controller, write pump, readout |
| `c4` -> `sdram_chip_clk` | 167 MHz, -1.5 ns | Forwarded SDRAM device clock |
| `c3` -> `adc_conv_clk` | 12 MHz | MAX10 ADC hard IP input |

The core design split is:

- `OLS_SDRAM_Top` integrates the board I/O, pin pool, clock generation, and
  capture muxing.
- `OLS_Logic_Analyzer` contains command/control, capture control, generator
  control, and the SPI-facing interface.
- `Fast_Logic_Analyzer_SDRAM` handles capture, pre-trigger buffering,
  narrow packed digital mode, and SDRAM writeout.
- `SDRAM_Controller` implements the open-page SDRAM write pump and readout.

The maintained speed build uses a 200 MHz sample domain, a 167 MHz SDRAM
domain, and a 100 MHz system domain. The async FIFO bridges capture to SDRAM,
and the producer-done completion path is used for single-shot deep capture.

## Major Modules

### `rtl/Fast_Logic_Analyzer_SDRAM.vhd`

Capture engine and SDRAM write path.

- Registered input sampling in `FAST_CLK`
- Sample divider and trigger gating
- Circular BRAM pre-trigger capture
- Narrow packed digital capture for one selected channel
- Async FIFO push into the SDRAM domain
- Continuous ring metadata and producer-done handling

### `rtl/OLS_SDRAM_Top.vhd`

System integration and board-facing muxing.

- Fast and normal capture input paths
- Programmable 26-entry pin pool
- Accelerometer pin mirroring when attached
- ADC profile selection and packed mixed-signal mode wiring

### `rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd`

Core control wrapper.

- SPI command interface
- Capture arm/abort logic
- Ring metadata exposure
- Generator control
- Packed mixed-signal path hookup

### `rtl/OLS_Interface.vhd`

Packet decoder and register interface.

- Register reads/writes
- Capture sequencing
- Sticky DONE handling
- Status and metadata readback

### `rtl/SDRAM_Controller_Custom.vhd`

Custom SDRAM controller.

- Open-page streaming writes
- Read/write/burst handling
- Capture stream handshake
- Single-shot completion support

### `rtl/ADC_Controller.vhd`

MAX10 ADC controller for mixed and analog-only modes.

- One-slot high-speed analog
- Multi-slot mixed and maximum analog scanning
- ADC mux selection for the board-specific channel map

## Project Files

- `proj/compile.ps1` generates the wrapper, updates the QSF, and runs Quartus
- `proj/OLS_Logic_Analyzer.sdc` holds the clock and interface constraints
- `proj/seed_sweep.ps1` sweeps Quartus seeds and records the timing summary
- `../TIMING_REPORT_SUMMARY.md` captures the latest signoff-oriented timing

## Testbenches

The HDL simulation suite covers:

- SPI packet protocol
- Capture arm/abort and sticky DONE
- Fast capture and ring capture
- SDRAM controller behavior
- Narrow packed digital capture
- Mixed-signal and analog paths
- Generator and protocol trigger logic

Use GHDL from `hdl/` or run the focused regression scripts in `hdl/tb/`.

## Notes

- The generated wrapper in `proj/` is overwritten by `compile.ps1`.
- `seed 33` is the currently validated speed-build placement in this branch.
- No feature removals are implied by the timing work; the current build keeps
  the full digital, mixed-signal, and generator paths.
