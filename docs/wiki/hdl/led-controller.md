# LED Controller: `LED_Controller`

**File:** `hdl/rtl/LED_Controller.vhd` (6.5 KB)

## Purpose

Drives the 8 board LEDs with PWM brightness control and a smooth fade effect between configurable brightness targets.

## Entity Ports

| Port | Width | Direction | Description |
|---|---|---|---|
| `CLK` | 1 | IN | System clock (100 MHz) |
| `rst` | 1 | IN | Reset |
| `brightness` | 8 per LED | IN | Array of target brightness values |
| `led` | 8 | OUT | LED output pins |

## Internal Architecture

### PWM Engine

A shared PWM counter (`pwm_cnt`, 0..256) produces 256-level PWM on each LED. Each LED compares its current brightness register against `pwm_cnt` to generate the PWM output.

### Fade Controller

A fade counter (`fade_cnt`, 0..511) drives smooth brightness transitions:

1. `fade_tick` generated every 512 sys_clk cycles
2. Each LED has:
   - `led_bright[i]` — current PWM duty (0..256)
   - `led_target[i]` — target brightness (driven by host registers)
   - `led_fade_step[i]` — increment per fade tick
3. On each `fade_tick`, `led_bright[i]` steps toward `led_target[i]` by `led_fade_step[i]`

### Package

The `led_controller_pkg` package defines:
- `led_bright_array` — array of 8 naturals (0..256) for brightness values
- `led_step_array` — array of 8 naturals for fade step sizes
- `MAX_LEDS` = 8

## Dependencies

| Component | File |
|---|---|
| `OLS_SDRAM_Top` (instantiation) | `OLS_SDRAM_Top.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_led_controller.vhd` | PWM output, fade transitions |
