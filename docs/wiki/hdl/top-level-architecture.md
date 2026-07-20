# Top-Level Architecture: `OLS_SDRAM_Top`

**File:** `hdl/rtl/OLS_SDRAM_Top.vhd` (1113 lines)

## Purpose

Top-level entity for the MAX1000 build. Integrates the SDRAM-based logic analyser core, clock generation, pin pool, ADC controller, LED controller, and all board-level I/O. Wires the SPI host interface, generator outputs, and capture datapath muxing across clock domains.

## Entity Signature

### Generics

| Generic | Default | Description |
|---|---|---|
| `TX_PIN` | 3 | Generator TX output pin (pool index) |
| `PLL_MULT` | 8 | PLL multiplier (×8 for FAST_SPEED = 100.2 MHz sys_clk from 12 MHz) |
| `PLL_DIV` | 1 | PLL divider |
| `Sim` | false | Simulation mode (disables PLL, uses gated clocks) |
| `FAST_SPEED` | false | When true: sys_clk=100.2 MHz, fast_clk=200.4 MHz, sdram_clk=167 MHz |
| `FAST_RAW_BUILD` | true | Excludes compression modules at elaboration time for timing closure |

### Ports

| Port | Direction | Width | Description |
|---|---|---|---|
| `CLK` | IN | 1 | Master 12 MHz oscillator input |
| `SPI_CS` | IN | 1 | SPI chip select (active low) |
| `SPI_SCK` | IN | 1 | SPI clock |
| `SPI_MOSI` | IN | 1 | SPI master-out-slave-in |
| `SPI_MISO` | OUT | 1 | SPI master-in-slave-out |
| `MKR_D` | INOUT | 15 | MikroBUS MKR digital pins D0-D14 |
| `PMOD` | INOUT | 8 | PMOD connector pins PIO_01-PIO_08 |
| `sdram_addr` | OUT | 12 | SDRAM address bus |
| `sdram_ba` | OUT | 2 | SDRAM bank address |
| `sdram_cas_n` | OUT | 1 | SDRAM column address strobe |
| `sdram_cke` | OUT | 1 | SDRAM clock enable |
| `sdram_cs_n` | OUT | 1 | SDRAM chip select |
| `sdram_dq` | INOUT | 16 | SDRAM data bus |
| `sdram_dqm` | OUT | 2 | SDRAM data mask |
| `sdram_ras_n` | OUT | 1 | SDRAM row address strobe |
| `sdram_we_n` | OUT | 1 | SDRAM write enable |
| `sdram_clk` | OUT | 1 | SDRAM forwarded clock |
| `SEN_SDI` | INOUT | 1 | LIS3DH accelerometer data |
| `SEN_SPC` | INOUT | 1 | LIS3DH accelerometer clock |
| `SEN_CS` | OUT | 1 | LIS3DH chip select (driven high) |
| `SEN_SDO` | IN | 1 | LIS3DH accelerometer SDO |
| `LED` | OUT | 8 | LED outputs (PWM-driven) |

## Internal Architecture

### Pin Pool (26-entry)

The 16 LA channels map to a 26-entry pool via the `pin_map` register array (addressable via `PIN_MAP_WRITE`/`PIN_MAP_CHANNEL`/`PIN_MAP_PIN`). Default mapping:

| LA Channel | Pool Index | Board Signal |
|---|---|---|
| 0-14 | 0-14 | MKR_D0-D14 |
| 15-22 | 15-22 | PMOD[0]-PMOD[7] |
| 23 | 23 | SEN_SDO (LIS3DH SDO) |
| 24 | 24 | SEN_SDI (LIS3DH SDA) |
| 25 | 25 | SEN_SPC (LIS3DH SCL) |

Each pool entry can independently be input (capture) or output (generator). The `pin_dir` and `pin_out` registers drive the bidirectional pads.

### Clock Generation

The PLL (instantiated as `PLL_inst`) generates five clocks from the 12 MHz master:

| Output | Frequency | Phase | Domain |
|---|---|---|---|
| c0 → `sys_clk` | 100.2 MHz | 0° | SPI, control, generator |
| c1 → `fast_clk` | 200.4 MHz | 0° | Sample capture, packing |
| c2 → `sdram_core_clk` | 167 MHz | 0° | SDRAM controller, write pump |
| c3 → `adc_conv_clk` | 12 MHz | 0° | MAX10 ADC conversion clock |
| c4 → `sdram_chip_clk` | 167 MHz | -1.5 ns | Forwarded SDRAM device clock |

In non-FAST_SPEED mode, sample_clk = sys_clk = 12 MHz × PLL_MULT / PLL_DIV.

### Capture Mux (FAST_CLK domain)

Registered input sampling at fast_clk with three mux paths:
- **Fast speed path**: `capture_data_fast_speed_r` — direct registered inputs with Fast_Mode=1
- **Normal path**: `capture_data_fast_normal_r` — inputs synchronised through pin pool
- **Mapped path**: `capture_data_fast_mapped_r` — pin_map selected

### Generator Wiring

The base `gen_tx`/`gen_scl` outputs are complemented by optional `gen_de` and
`gen_cs` physical outputs and a `gen_miso` input selector. RS-485 DE is active
for the Bit_Engine burst; SPI CS is active during an SPI burst; MISO can use a
GPIO pool entry or sensor SDO pool pin 23. `REG_GEN_CAPTURE_AUX` selects the
logical capture channels for CS/MISO because runtime general pin-map writes are
frozen in the FAST build.

The generator (`Signal_Gen` entity) connects through a set of cross-domain signals:
- `gen_busy` — indicates generator actively transmitting
- `gen_tx` / `gen_scl` — data and clock outputs routed to selected pool pins
- `gen_proto` — protocol select (0=UART, 1=I2C, 2=SPI, etc.)
- `gen_baud_div_s` — baud rate divider
- `gen_fifo_count` / `gen_rx_data` / `gen_rx_used` — RX FIFO readback for loopback

Generator outputs cross from sys_clk to fast_clk through 2-FF synchronisers.

### ADC Controller

The `altera_modular_adc_control` block controls the MAX10 ADC. It outputs:
- `adc0..adc3_result` (12-bit) — conversion results
- `adc0..adc3_valid` — per-channel valid strobes
- `analog_frame_data` (128-bit) — packed analog frame to capture engine
- `analog_frame_len` (1..14) — number of valid samples in frame
- `analog_profile` (2-bit) — selects scan mode

### LED Controller

`LED_Controller` drives the 8 board LEDs with PWM brightness and fade effects. Uses a shared PWM counter and per-LED target/brightness registers.

### Debug CH0

The current implementation is authoritative as follows: `REG_DEBUG_CH0_ENABLE`
is register `0x42`, `REG_DEBUG_CH0_PERIOD` is `0x43`, and
`REG_DEBUG_CH0_DUTY` is `0x44`. The PWM is generated in `sys_clk`, drives
physical channel/pin 0, defaults to period `0x400` and duty `0x200` (50%), and
is overridden by generator output when the generator is active. The counter
resets when disabled or when the period is less than two.

A configurable PWM loopback generator on LA channel 0 for self-test:
- The legacy names above are superseded by the SPI register names documented
  above; the active implementation has no programmable channel selector.

## Clock Domain Crossings

All signals crossing from sys_clk → fast_clk go through 2-FF synchronisers:
- `pin_pool_f1` / `pin_pool_f2` — input pins
- `gen_tx_f1` / `gen_tx_f2` — generator TX
- `registered_ch0_f1` / `registered_ch0_f2` — debug CH0
- `gen_capture_active_f1` / `gen_capture_active_f2`
- `debug_ch0_enable_f1` / `debug_ch0_enable_f2`
- `pin_map_wr_t_s1` / `pin_map_wr_t_s2` — pin map write toggle

## Key Constants

| Constant | Value | Description |
|---|---|---|
| `LA_CHANNELS` | 16 | Number of logic analyser channels |
| `PIN_POOL_SIZE` | 26 | Number of configurable I/O pool entries |
| `GEN_LED_STRETCH_TOP` | 25,000,000 | ~0.25s LED stretch at 100 MHz |
| `System_CLK_Frequency` | 100,200,000 | sys_clk in FAST_SPEED mode |
| `SDRAM_CLK_HZ` | 167,000,000 | SDRAM clock frequency |
| `SAMPLE_CLK_HZ` | 200,400,000 | Sample clock frequency |

## Dependencies

| Component | File |
|---|---|
| `OLS_Logic_Analyzer` (entity) | `OLS_Logic_Analyzer_SDRAM_Core.vhd` |
| `SDRAM_PLL` (entity) | `SDRAM_PLL.vhd` |
| `altera_modular_adc_control` | Quartus IP (MAX10 ADC) |
| `LED_Controller` (entity) | `LED_Controller.vhd` |
| `led_controller_pkg` | `LED_Controller.vhd` (body) |

## Testing

Covered by top-level testbenches:
- `tb_top.vhd` — full-system simulation
- `tb_capture_path.vhd` — capture datapath through top
- `tb_analog_preamble.vhd` — analog frame path verification
- `tb_fast_analyzer.vhd` — fast capture mode
