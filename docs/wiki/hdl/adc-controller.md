# ADC Controller: `ADC_Controller`

**File:** `hdl/rtl/ADC_Controller.vhd` (8.0 KB)

## Purpose

Controls the MAX10 built-in ADC (altera_modular_adc_control hard IP). Configures scan profiles for one-slot high-speed analog, multi-slot mixed-signal, and maximum-analog scanning modes.

## Entity Ports

| Port | Width | Direction | Description |
|---|---|---|---|
| `CLK` | 1 | IN | System clock |
| `adc_conv_clk` | 1 | IN | ADC conversion clock (12 MHz from PLL c3) |
| `rst` | 1 | IN | Reset |
| `enable` | 1 | IN | ADC enable |
| `analog_profile` | 2 | IN | Scan profile select |
| `analog_channel` | 5 | IN | Channel select (single-slot mode) |
| `adc0..3_result` | 12 | OUT | Conversion results |
| `adc0..3_valid` | 1 | OUT | Per-channel valid |
| `adc_start` | 1 | OUT | Start conversion |
| `adc_ready` | 1 | OUT | ADC ready |

## Scan Profiles

| Profile | Mode | Channels Scanned | Frame Rate |
|---|---|---|---|
| 00 | High-speed analog | 1 selected ADC lane | 1 MSPS |
| 01 | Mixed | ADC0..ADC7 scan | 125 kframes/s |
| 10 | Maximum analog | Physical profile ADC1..4,5,7,8,16 | 125 kframes/s |
| 11 | (Reserved) | — | — |

## ADC Mux Channel Map

The MAX1000 board wiring maps ADC channels to physical inputs:

| ADC Channel | Board Label | FPGA Pin | Header |
|---|---|---|---|
| 1 | AIN3 | PIN_D1 | J1/5 |
| 2 | AIN1 | PIN_C2 | J1/3 |
| 3 | AIN4 | PIN_E3 | J1/6 |
| 4 | AIN6 | PIN_E4 | J1/8 |

## Analog Frame Output

The controller packs multiple ADC results into a 128-bit `analog_frame_data` bus with `analog_frame_len` (1..14) indicating how many samples are valid. Frame rate depends on profile:

- High-speed single: 1 MSPS (1 sample per frame)
- Mixed scan: 125 kframes/s (up to 8 samples per frame)
- Max analog: 125 kframes/s (physical profile scan)

## Analog Profile Flags (from REG_FLAGS)

The OLS_Interface decodes REG_FLAGS to set analog mode:

| Mode | Flags | Description |
|---|---|---|
| `MODE_DIGITAL` | 0x000000 | Digital-only capture |
| `MODE_MIXED` | 0x000008 | Mixed digital+analog |
| `MODE_ANALOG_ONLY` | 0x000010 | Analog-only (no digital) |
| `MODE_ANALOG_FAST` | 0x000018 | = mixed + analog_only: one high-speed ADC lane |
| `MODE_ANALOG_ALL` | 0x000038 | = analog_fast + 0x20: maximum physical analog |
| `MODE_NARROW_DIGITAL` | 0x002000 | Narrow packed digital |

## Dependencies

| Component | File |
|---|---|
| `altera_modular_adc_control` | Quartus IP (altera_modular_adc_control_model.vhd for sim) |
| `OLS_SDRAM_Top` | `OLS_SDRAM_Top.vhd` |

## Testing

| Testbench | What it covers |
|---|---|
| `tb_adc_controller.vhd` | ADC scan profiles, channel mux |
| `tb_analog_preamble.vhd` | Analog frame preamble through ADC |
