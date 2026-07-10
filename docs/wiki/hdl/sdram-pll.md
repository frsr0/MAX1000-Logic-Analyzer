# SDRAM PLL: `SDRAM_PLL`

**File:** `hdl/rtl/SDRAM_PLL.vhd` (20.8 KB)

## Purpose

Phase-Locked Loop configuration for the Intel MAX 10 device. Generates all clock domains from the 12 MHz master oscillator.

## Clock Outputs

| Output | Frequency | Phase Shift | Domain |
|---|---|---|---|
| c0 → `sys_clk` | 100.2 MHz | 0° | System clock: SPI, control logic, generator |
| c1 → `fast_clk` | 200.4 MHz | 0° | Fast sample clock: digital capture, packing |
| c2 → `sdram_core_clk` | 167 MHz | 0° | SDRAM controller core: write pump, readout |
| c3 → `adc_conv_clk` | 12 MHz | 0° | MAX10 ADC conversion clock |
| c4 → `sdram_chip_clk_out` | 167 MHz | -1.5 ns | Forwarded SDRAM device clock, **legacy path only** (see below) |

## SDRAM Chip Clock Forward

The clock actually driven onto the `sdram_clk` pin is selected by the `USE_DDIO_CLK_FORWARD` generic on `OLS_SDRAM_Top` (default `true`; `compile.ps1 -LegacyClkForward` selects the other path):

- **DDIO forward (default, `OLS_SDRAM_Top.vhd` `gen_ddio_clk_forward`):** an `altddio_out` re-times `sdram_core_clk` through the MAX 10 IOE's dedicated DDIO output register instead of forwarding PLL tap c4 directly. The SDC constrains this pin as `create_generated_clock ... -invert` off `sdram_core_clk` (`hdl/proj/OLS_Logic_Analyzer.sdc`, `SDRAM_CHIP_CLK_OUT`) — i.e. the chip is expected to sample write commands/DQ roughly half a period (~3 ns) *after* the FPGA's launch edge, not at it. The DDIO's `datain_h`/`datain_l` inputs must therefore be driven **inverted** (`"0"`/`"1"`) to actually produce that phase relationship in silicon.

  **2026-07-10 incident:** the DDIO forward was introduced (commit `f911e9f2`) with `datain_h="1"`/`datain_l="0"` — non-inverted — while the SDC's `-invert` constraint was added in the same commit. STA validated a clock phase that never existed on the board: the SDRAM chip sampled writes at the FPGA's launch edge instead of after it, and silently dropped ~6% of write commands on any capture with changing data (static/constant data was immune, and reads survived via the existing CL3 + prime-read margin — see [`sdram-controller.md`](sdram-controller.md)). This was the actual cause of a `/api/generator/self-test` failure that had been misattributed to the generator-loopback mux; fixed by inverting the DDIO data inputs to match the SDC. See the `fast-capture-write-scramble` project history for the full investigation.

- **Legacy direct forward (`gen_use_pll_fast_direct`/`gen_use_pll_normal_direct`, `-LegacyClkForward`):** PLL tap c4 (`sdram_chip_clk_out`, phase `-1.5 ns` off c2) drives the pin directly, no DDIO stage. Predates the DDIO path; kept as a fallback, not the default build.

## PLL Parameters

| Parameter | Value |
|---|---|
| Reference clock | 12 MHz (MAX1000 onboard oscillator) |
| PLL multiplier (`PLL_MULT` generic) | 8 |
| PLL divider (`PLL_DIV` generic) | 1 |
| VCO frequency | 12 MHz × 8 = 96 MHz (× internal multipliers for each output) |

## FAST_SPEED Build

When `FAST_SPEED = true`:
- PLL c0 → 100.2 MHz (×8.35 from 12 MHz)
- PLL c1 → 200.4 MHz (×16.7)
- PLL c2/c4 → 167 MHz (×13.9)
- PLL c3 → 12 MHz (×1.0, passthrough)

## PLL Lock

`pll_locked` signal from the PLL is used by the top-level reset logic. The SDRAM controller and capture engine wait for PLL lock before starting.

## PLL Reset

`adc_reset_hold` counter ensures the ADC is held in reset for 31 sys_clk cycles after PLL lock, while `adc_lock_settle` (0..4095) provides additional settling delay.

## Dependencies

| Component | File |
|---|---|
| `altera_pll` (Intel IP) | Quartus IP; simulated via `pll_model.vhd` |
| `OLS_SDRAM_Top` | `OLS_SDRAM_Top.vhd` |

## Simulation

In simulation (`Sim = true`), the PLL model (`hdl/tb/support/pll_model.vhd`) provides gated clocks directly without the PLL lock delay.
