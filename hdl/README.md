# HDL — OLS Logic Analyzer FPGA Design

## Architecture Overview

The FPGA design targets the Intel MAX10 10M08SAU169C8G on the Arrow MAX1000 board. The PLL multiplies the 12 MHz input to generate three clock domains:

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×4 | 48 MHz | Core logic (OLS_Interface, FLA, SDRAM) |
| c1 | ×10 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×4 | 48 MHz, −90° | SDRAM clock |

The system clock frequency is computed as `12_000_000 * PLL_MULT / PLL_DIV` with defaults `PLL_MULT=4, PLL_DIV=1` → 48 MHz.

### Top-down hierarchy

```
OLS_Logic_Analyzer_wrapper      — pin assignment wrapper (auto-generated from CSV, in proj/)
└── OLS_SDRAM_Top               — system integration, I/O pin pool, capture mux
    ├── SDRAM_PLL               — PLL (3-output clock generation)
    ├── OLS_Logic_Analyzer      — core (command/control + capture + generator)
    │   ├── OLS_Interface       — SPI/UART command decoder & readout state machine
    │   ├── Fast_Logic_Analyzer_SDRAM — capture engine, triple buffer, SDRAM bridge
    │   │   └── SDRAM_Interface → SDRAM_Controller (Avalon-MM SDRAM controller)
    │   ├── Signal_Gen          — UART/I2C/SPI protocol generator with FIFO
    │   ├── Protocol_Trigger    — UART byte-level trigger detector
    │   ├── SPI_Slave2          — full-duplex SPI slave with CDC (sys → fast clock)
    │   └── UART_Interface      — UART 8N1 RX/TX with configurable baud
    ├── LED_Controller          — 8-LED animation engine (breathing, armed, capture)
    └── ADC_Controller          — MAX10 internal ADC wrapper (altera_modular_adc)
```

---

## RTL Modules

### `rtl/OLS_SDRAM_Top.vhd` (535 lines)
**Entity:** `OLS_SDRAM_Top`

System integration module connecting all subsystems. Key functions:

- **Clock generation**: Instantiates `SDRAM_PLL`, distributes sys_clk (48 MHz) and fast_clk (120 MHz)
- **Pin pool routing**: 23-pin pool (MKR_D[14:0] + PMOD[7:0]) mapped to 16 LA channels via `pin_map` register. The pin map is writable from the host via `CMD_PIN_MAP (0xBB)`, allowing any channel to probe any physical pin
- **Capture mux**: Per-channel combinatorial mux with uniform 2-cycle pipeline:
  - `capture_mux(LA_CHANNELS-1 downto 0)` = `gen_tx_d1` when gen_busy and gen_tx_pin = i
  - = `sen_sdo_d1` (or `sen_sdi_sync` in I2C test mode) for SEN/SDI signals
  - = `gpio_d1` (pipelined input) otherwise
  - SCL in I2C test mode = `gen_scl_d2` (2-cycle to match sen_sdi_sync pipeline)
- **Programmable pin map**: 16 entries, each selects one of 23 pool pins for an LA channel. Written via command interface with `pin_map_write`/`pin_map_channel`/`pin_map_pin` signals
- **Test divider**: Free-running 10-bit counter on sys_clk, bit 9 registered through `preserved_ch0` (with `preserve` attribute) → 46.875 kHz on CH0
- **SEN/CDE**: 2-FF synchroniser on `sen_sdi`, pipeline registers on gen_tx, gen_scl, sen_sdo, registered_ch0 to ensure uniform 2-cycle depth

### `rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd` (270 lines)
**Entity:** `OLS_Logic_Analyzer`

The core wrapper that instantiates and connects `OLS_Interface`, `Fast_Logic_Analyzer_SDRAM`, `Signal_Gen`, `Protocol_Trigger`, and `SPI_Slave2`. Defines sample width: `sub_steps = 16 / Channels`.

**Generics**:
- `Baud_Rate` (115200), `CLK_Frequency` (12e6 default, reset by top), `Max_Samples` (1M), `Channels` (4 default but overridden to 16 in synthesis)

Routes `Buffer_Full[2:0]` / `Buffer_Ack[2:0]` between interface and FLA for continuous capture handshake.

### `rtl/OLS_Interface.vhd` (1096 lines) — Largest module
**Entity:** `OLS_Interface`

Command/control interface implementing the OLS protocol. Parses SPI and UART command streams.

**Key constants**: `ID = x"31414c53"` — ASCII "SLA1" (reversed), returned by CMD_ID.

**Command set** (via `command` byte + accumulate data):

| Opcode | Command | Function |
|--------|---------|----------|
| 0x00 | CMD_RESET | Reset interface state |
| 0x01 | CMD_ARM | Arm capture (sets Run) |
| 0x02 | CMD_ID | Return 4-byte device ID |
| 0x03 | CMD_SPI_STATUS | Return preamble + status |
| 0x04 | CMD_METADATA | Return 18-byte metadata block |
| 0x11 | CMD_XON | Resume capture output |
| 0x13 | CMD_XOFF | Pause capture output |
| 0x80 | CMD_SET_DIVIDER | Set sample rate divider |
| 0x82 | CMD_FLAGS | Set trigger flags/mask |
| 0x83 | CMD_DCOUNT | Set delay count |
| 0x84 | CMD_RCOUNT | Set sample count |
| 0xA0 | CMD_GEN_LOAD | Load byte into gen FIFO |
| 0xA1 | CMD_GEN_STRT | Start generator |
| 0xA2 | CMD_GEN_BAUD | Set generator baud divisor |
| 0xA3 | CMD_GEN_BLK | Block generator data transfer |
| 0xA4 | CMD_GEN_PROTO | Set generator protocol |
| 0xA6 | CMD_GEN_PINS | Set gen TX/SCL pins |
| 0xA7 | CMD_I2C_TEST | Enable I2C test mode |
| 0xA8 | CMD_FAST_MODE | Toggle fast capture mode |
| 0xAA | CMD_CONT_CAPTURE | Enable continuous capture |
| 0xAE | CMD_CH_MODE | Set channel mode |
| 0xAF | CMD_SPI_TEST | SPI test mode |
| 0xB0 | CMD_ANALOG_CFG | Configure analog mode |
| 0xBB | CMD_PIN_MAP | Write pin map register |
| 0xC0 | CMD_TMASK | Set trigger mask |
| 0xC1 | CMD_TVALUE | Set trigger values |
| 0xC2 | CMD_DELAY | Set trigger delay |

**State machine architecture**:
- **Thread38**: Main command processor state machine. States: `0`=idle, `1`=accumulate, `2`=dispatch, `3`=readout, `4`=single-byte dispatch, `5`=sequence, `6`=idle flush
- **Thread44**: Command dispatcher. Each opcode's handler selected via CASE within Thread38 state 2/4
- **Thread23/Thread26**: Readout state machine (continuous vs single-shot). State `0`=idle, `1`=wait_full, `2`=read, `3`=done, `4`=next_buf, `5`=ack_wait, `6`=post_idle

**Key timing**: Readout length = `Min(Max_Samples, Samples) * 4 / stride`, where stride = 2 for 8 ch, 4 for 16 ch. Metadata block is 18 bytes (ID, version, sample count, rate, flags, trigger config).

### `rtl/Fast_Logic_Analyzer_SDRAM.vhd` (625 lines)
**Entity:** `Fast_Logic_Analyzer_SDRAM`

High-speed capture engine. Samples up to 16 channels at programmable rates.

**Architecture**:
- **Divider**: Free-running counter, `sample_en` fires every `Rate_Div` sys_clk cycles
- **BRAM**: M9K block, 1024×16-bit, used in fast mode (no SDRAM needed). Written every `sub_steps` sample events. Circular buffer for pre-trigger
- **Write FIFO**: Depth 16, 38-bit entries (addr[21:0] + data[15:0]), bridges sample domain to SDRAM write bursts
- **SDRAM controller interface**: Address encodes sample index into SDRAM address space (22-bit, up to 4M samples)
- **Triple buffer** (continuous mode): `Buffer_Full[2:0]`/`Buffer_Ack[2:0]` handshake. While host reads buffer 0, FLA fills buffer 1, etc.
- **Analog stream mode**: `Analog_Frame_Data[63:0]` + `Analog_Frame_Len` for ADC sample streaming

**Constants**: `sub_steps = 16 / Channels` (2 for 8 ch, 1 for 16 ch). `samples_div_p` = `Samples * sub_steps` for BRAM mode, `samples_div6` = `Samples / 6` for SDRAM stride.

### `rtl/SPI_Slave2.vhd` (165 lines)
**Entity:** `SPI_Slave2`

Full-duplex SPI slave with clock-domain crossing.

**Key design**:
- SPI engine runs on `fast_clk` (120 MHz), TX/RX data crosses to `sys_clk` (48 MHz)
- 2-stage synchroniser for TX_Data and SPI_Preamble (sys → fast)
- 3-stage synchroniser for RX_Valid (fast → sys)
- Preamble byte loaded at CS falling edge → first MISO byte is status, zero-waste protocol
- Configurable `PipeDepth` (2..8) for pipeline balancing
- Bit counter: 0..7, `rx_valid_cnt` (0..127) counts bytes for continuous readout without re-arming

### `rtl/UART_Interface.vhd` (203 lines)
**Entity:** `UART_Interface`

Async UART transmitter and receiver. 8N1 default, configurable data width, parity, oversampling.

**TX path**: `idle` → `transmit`. Shifts out start bit (0), data (LSB first), stop bit (1) at `baud_pulse` rate.
**RX path**: `idle` → `receive`. Detects start bit (0), samples at midpoint (`actual_os_rate/2`), shifts in data, checks stop bit and parity.

Key detail: `actual_os_rate = min(OS_Rate, CLK_Frequency / Baud_Rate)` prevents over-constrained clock dividers at high baud rates. Both `baud_clocks` and `os_clocks` are clamped to minimum 1.

### `rtl/SDRAM_Interface.vhd` (191 lines)
**Entity:** `SDRAM_Interface`

Wrapper around `SDRAM_Controller`. Provides simplified address/data/control interface.

- Generates reset: holds `sdram_reset_n` low for 480,000 cycles (10 ms @ 48 MHz), then releases for PLL + SDRAM init
- Simulation mode (`Sim=true`): bypasses real controller, uses local RAM array for behavioral modelling
- Maps `Address[21:0]`, `Write_Enable`, `Write_Data[15:0]`, `Read_Enable` to Avalon-MM signals
- Exposes `Busy`, `Idle`, `Read_Valid` handshake

### `rtl/SDRAM_Controller_Custom.vhd` (591 lines)
**Entity:** `SDRAM_Controller`

Custom SDRAM controller with full state machine: power-on init (precharge, refresh, mode register set), read, write, burst, auto-refresh.

**Avalon-MM front-end**:
- `sdram_s_address[21:0]`, `sdram_s_byteenable_n[1:0]`, `sdram_s_chipselect`
- `sdram_s_writedata[15:0]` / `sdram_s_write_n`, `sdram_s_readdata[15:0]` / `sdram_s_read_n`
- `sdram_s_burst`, `sdram_s_readdatavalid`, `sdram_s_waitrequest`

**Timing**: RCD=2, RP=2, RFC=7, CL=2 (all clock cycles at 48 MHz, ~20.8 ns each). Compatible with 64 Mbit SDRAM (12 row / 8 column / 2 bank).

### `rtl/SDRAM_PLL.vhd` (412 lines)
**Entity:** `SDRAM_PLL`

Altera ALTPLL megafunction. 12 MHz input, three outputs: c0 (×4, 48 MHz), c1 (×10, 120 MHz), c2 (×4, 48 MHz, −90° phase shift). Has `locked` output for PLL lock detection.

### `rtl/Signal_Gen.vhd` (327 lines)
**Entity:** `Signal_Gen`

Configurable protocol signal generator with 256-byte FIFO.

**Protocols**:
- **UART** (Proto=0): Shifts out start bit + 8 data (LSB first) + stop bit. Baud rate from `Baud_Div` or `FIXED_BAUD_DIV (0x00F0=240)` fallback
- **I2C** (Proto=1): Master mode. Generates START, 7-bit address + R/W, ACK/NACK, data bytes, STOP. Supports multi-byte read via `I2C_Rd_Len` and `I2C_Dev_R`. CRC-16 option with configurable polynomial
- **SPI** (Proto=0, SPI_Mode=1): Mode 0 (CPOL=0, CPHA=0), MSB first, CS asserted per byte

**CRC**: CRC-16 with configurable polynomial (default `x"A001"` = MODBUS). Active after `CRC_En` is set, appends CRC after data.

**I2C state machine** (15 states, `i2c_state` 0..14): START→ADDR+W→ACK→DATA→ACK→...→STOP for writes; adds repeated START→ADDR+R→RD_DATA→NACK→STOP for reads.

### `rtl/Protocol_Trigger.vhd` (87 lines)
**Entity:** `Protocol_Trigger`

UART byte-level trigger. Monitors one input channel. On falling edge (start bit), half-bit centre point then samples 8 data bits at baud rate. On stop bit, compares shift register to `Match_Value` and asserts `Trigger` on match.

State machine: `IDLE → START → BITS → STOP → CHECK`. Only one protocol (UART) is implemented (Protocol=0), others reserved.

### `rtl/ADC_Controller.vhd` (196 lines)
**Entity:** `ADC_Controller`

Controls the Altera Modular ADC II (MAX10 internal ADC). Two independent channels, each with start/busy/result/valid handshake.

**State machine**: `INIT` (4K cycle wait) → `IDLE` → `SEND_CMD` → `WAIT_RSP` → `DONE0`/`DONE1`.
- Sends command via Avalon-ST (`cmd_valid`, `cmd_channel[4:0]`, `cmd_sop`, `cmd_eop`)
- Waits for response (`rsp_valid`, `rsp_data[11:0]`)
- Channel select from `ch0_sel`/`ch1_sel` (0..15, mapping to MAX10 ADC input pins)

ADC clock divider = 4, prescaler = 0, refsel = 1 (internal VREF).

### `rtl/LED_Controller.vhd` (376 lines)
**Entity:** `LED_Controller`

8-LED animation engine using PWM + fade-step interpolation. Package `led_controller_pkg` defines `led_bright_array` and `led_step_array` types.

**Animation states**:
| State | Trigger | Pattern |
|-------|---------|---------|
| ST_IDLE | Default | Breathing: triangle-wave fade 0→255→0 on all LEDs |
| ST_HOST_CONFIRM | host_connected rising | 3× confirmation flash (rapid rise/fall) |
| ST_TRIGGER_ARMED | armed=1 | Rapid pulsing: 85-step rise, 33 ticks on, 85-step fall |
| ST_SINGLE_CAPTURE | capture_run | Rolling pattern: phase-shifted brightness waves |
| ST_ROLLING_CAPTURE | continuous | Rolling pattern with FIFO activity influence |

All timing parameters are generics (see entity declaration): fade resolution (steps 0..511), PWM carrier (0..256), breathing idle timing, confirmation speed, armed flash rate, rolling phase step. Triangle and sine interpolation functions.

---

## Project Files

### `proj/compile.ps1` (241 lines)

Build automation script. Workflow:
1. Parse `pin_assignments.csv` → build `$pinMap` and `$ioMap` hashtables
2. Generate `OLS_Logic_Analyzer_wrapper.vhd` with `chip_pin` attributes for every signal
3. Rewrite `OLS_Logic_Analyzer.qsf` with 14 VHDL file references (using `../rtl/` prefix), ADC IP QIP, and weak pull-up assignments
4. Create QPF if missing (single-revision `OLS_Logic_Analyzer`)
5. Compile via `quartus_sh --flow compile OLS_Logic_Analyzer`
6. Optional `-Flash` flag: program `.sof` via `quartus_pgm -m JTAG`

### `proj/pin_assignments.csv` (64 lines)

Four-column CSV: `Signal`, `Pin`, `Direction`, `I/O Standard`. Maps I/O standards:
- CLK, UART, SPI, SDRAM, SEN, GPIO → 3.3-V LVCMOS
- LEDs → 2.5 V (MAX1000 LED voltage rail)

### `proj/OLS_Logic_Analyzer.qsf`

Device: MAX10 10M08SAU169C8G, top-level entity `OLS_Logic_Analyzer_wrapper`. Includes all 14 RTL VHDL files (no wrapper in rtl/ — the auto-generated wrapper lives in proj/), and `MAX10_ADC.qip`. Weak pull-ups on GPIO[0..7] and SEN_SDI/SEN_SPC. 16 parallel processors. Single image with ERAM flash update mode.

### `proj/OLS_Logic_Analyzer.qpf`

Quartus Prime project file (revision `OLS_Logic_Analyzer`). Target device MAX10 10M08SAU169C8G.

### Other variants

Variant project files (Fast_Logic_Analyzer for Cyclone V, pulldown/pullup configurations) are archived in `archive/hdl/`.

---

## Testbenches

All in `tb/` with `support/` utilities. Run with GHDL:
```powershell
ghdl -a --std=08 hdl\tb\support\sim_pkg.vhd
ghdl -a --std=08 hdl\rtl\*.vhd
ghdl -a --std=08 hdl\tb\*.vhd
ghdl -e --std=08 <testbench>
ghdl -r --std=08 <testbench> --assert-level=failure
```

| Testbench | Entity | Lines | Tests | Coverage |
|-----------|--------|-------|-------|----------|
| `tb_ols_interface` | `tb_ols_interface` | 467 | 24/24 PASS | All opcodes, accumulator, trigger, gen control, readout |
| `tb_capture_path` | `tb_capture_path` | 227 | 5/5 PASS | sample_en period, BRAM write, Full, CH0 timing, 2nd capture |
| `tb_continuous` | `tb_continuous` | 92 | 3/3 PASS | Buffer fill, ack, re-fill cycle |
| `tb_fast_analyzer` | `tb_fast_analyzer` | 206 | — | FLA capture engine with SDRAM model, pattern gen on CH0 |
| `tb_sdram_interface` | `tb_sdram_interface` | 156 | — | SDRAM read/write through interface wrapper |
| `tb_sdram_controller` | `tb_sdram_controller` | 175 | — | Avalon-MM SDRAM controller transactions |
| `tb_uart_interface` | `tb_uart_interface` | 142 | — | TX timing, data loopback, RX error detection |
| `tb_spi_slave` | `tb_spi_slave` | 107 | — | Full-duplex at 10 MHz, preamble, RX handshake |
| `tb_signal_gen` | `tb_signal_gen` | 221 | — | FIFO load, UART 0x55 at 115200 baud, pulse measurement |
| `tb_led_controller` | `tb_led_controller` | 205 | — | PWM, fade, all animation states |
| `tb_adc_controller` | `tb_adc_controller` | 102 | — | Single conv, multi-channel scan, back-to-back, reset |
| `tb_protocol_trigger` | `tb_protocol_trigger` | 94 | — | Matches 0xA5, rejects 0x5A, disabled state |
| `tb_top` | `tb_top` | 305 | — | Full-system: PLL, SPI commands, ADXL345 over SEN bus |
| `tb_core` | `tb_core` | 263 | — | Core-level: SPI, SDRAM, gen, analog mode signals |

### Support packages
- `support/sim_pkg.vhd` (297 lines): Clock gen, SPI/UART test procedures, assertions, pulse measurement
- `support/adxl345_model.vhd` (325 lines): ADXL345 behavioural model (SPI+I2C, register map, configurable accel values)
- `support/sdram_model.vhd` (136 lines): SDRAM behavioural model with configurable timing (T_RCD, T_RP, T_RFC, CL)
- `support/pll_model.vhd` (86 lines): PLL behavioural model with configurable multiply/divide/phase
- `tb/SDRAM_PLL.vhd` (53 lines): Simulation wrapper instantiating PLL_Model

---

## Hardware Diagnostics

### `hw_test/diag_arm_test.py` (108 lines)
Tests CMD_ARM via SPI. Verifies Run_OLS bit in preamble byte after arm. Tests 5 arm methods (raw xfer, spi.arm(), 0x00 padding, single-byte 0x31 block, metadata readout).

### `hw_test/diag_gen_busy.py` (145 lines)
Generator UART output test (0x55 on GPIO3). Verifies Run_OLS after ARM, fast capture with BRAM, chained readback.

### `hw_test/diag_gen_data.py` (154 lines)
Generator FIFO load and transmission test. Verifies TX data changes. Tests ARM after metadata read and sim-matching ARM sequence.

### `hw_test/diag_clean.py` (208 lines)
Clean-room diagnostic (fresh FTDI open per test). Seven scenarios: initial status, reset+ARM, fresh+no-reset+ARM, sim-matching, ARM+immediate NOP read, generator UART, Full bit check.

### `hw_test/hw_results/`
Output directory for `hw_validation.py` results (JSON files with pass/fail status per test).

---

## IP Cores

### `ip/MAX10_ADC/`
Altera Modular ADC II for MAX10. Includes:
- `synthesis/MAX10_ADC.vhd` — Synthesis wrapper
- `synthesis/submodules/altera_modular_adc_control.sdc` — Timing constraints (false paths on ADC async signals)
- `simulation/MAX10_ADC.vhd` — Simulation wrapper
- `simulation/submodules/MAX10_ADC_modular_adc_0.vhd` — ADC submodule

Entity `MAX10_ADC` has Avalon-ST command/response interface: `command_valid/channel/sop/eop/ready`, `response_valid/channel/data/sop/eop`. ADC clock from PLL c0 (48 MHz), PLL locked signal as reset qualifier.
