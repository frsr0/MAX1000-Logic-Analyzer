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
| c4 → `sdram_chip_clk` | 167 MHz | -1.5 ns | Forwarded SDRAM device clock (phase-aligned to compensate board trace delay) |

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
