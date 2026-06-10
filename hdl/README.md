# HDL — OLS Logic Analyzer FPGA Design

## Architecture Overview

Target: Intel MAX10 10M08SAU169C8G on Arrow MAX1000 board. PLL multiplies 12 MHz input to three clock domains. The FPGA has two build modes selected by `FAST_SPEED` generic in the wrapper:

### Speed mode (FAST_SPEED=true, current build)

| Output | Multiply | Divide | Frequency | Domain |
|--------|----------|--------|-----------|--------|
| c0 | ×50 | ÷6 | 100 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol |
| c1 | ×50 | ÷3 | 200 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×50 | ÷6 | 100 MHz, −90° | SDRAM clock (phase-shifted for data centering) |

VCO = 600 MHz. Timing closure: +0.097 ns at 200 MHz (Slow 85°C worst corner).

### Normal mode (FAST_SPEED=false)

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×8 | 96 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol |
| c1 | ×10 | 120 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×8 | 96 MHz, −90° | SDRAM clock (phase-shifted for data centering) |

Set `FAST_SPEED => false` in `OLS_Logic_Analyzer_wrapper.vhd` for normal mode. The PLL megafunction must be regenerated for different multiply/divide values.

### Two-Clock Domain Split

```
FAST_CLK (200 / 120 MHz, c1)             CLK (100 / 96 MHz, c0)
┌────────────────────────────┐          ┌───────────────────────────┐
│ sample divider (28-bit)    │          │ async FIFO read (dcfifo)  │
│ input packer (16→16-bit)   │──4096──▶│ SDRAM address assignment  │
│ pre-trigger BRAM (circular)│  dcfifo  │ single-word SDRAM writes  │
│ async FIFO push            │          │ triple-buffer management  │
│ overflow/sample-stop detect│          │ full detection + status   │
│                            │          │ readout                   │
│ Config handshake detect    │          │ OLS protocol / SPI        │
│ (cfg_valid_edge → latch)   │────────▶│ Config latch + toggle     │
└────────────────────────────┘          └───────────────────────────┘
```

CDC: async FIFO (dcfifo) for sample data, 2FF + toggle synchronizers for config/control. ADC runs independently on sys_clk.

Speed mode (200 MHz): 4-stage pipeline — sample pins → control decode → rate divider → BRAM/FIFO write.
Normal mode (120 MHz): single-cycle capture FSM with variable packing.

### Top-down hierarchy

```
OLS_Logic_Analyzer_wrapper      — pin assignment wrapper (auto-generated from CSV)
└── OLS_SDRAM_Top               — system integration, I/O pin pool, capture mux
    ├── SDRAM_PLL               — PLL (3-output clock generation)
    ├── OLS_Logic_Analyzer      — core (command/control + capture + generator)
    │   ├── OLS_Interface       — SPI command decoder & readout FSM
    │   ├── Fast_Logic_Analyzer_SDRAM — dual-clock capture engine, async FIFO
    │   │   └── SDRAM_Interface → SDRAM_Controller (Avalon-MM)
    │   ├── Signal_Gen          — UART/I2C/SPI protocol generator
    │   ├── Protocol_Trigger    — UART byte-level trigger detector
    │   └── SPI_Slave2          — full-duplex SPI slave with CDC
    ├── LED_Controller          — 8-LED animation engine
    └── ADC_Controller          — MAX10 internal ADC, 8 channels
```

---

## RTL Modules

### `rtl/Fast_Logic_Analyzer_SDRAM.vhd` (~700 lines)

**Entity:** `Fast_Logic_Analyzer_SDRAM`

Capture engine with two-clock domain split.

**FAST_CLK (200 / 120 MHz) processes:**
- **Config handshake**: Detects `cfg_valid_edge` (toggled by CLK domain on run start), latches `cfg_rate_div_f` and `cfg_samples_f`, acks via `cfg_ack_toggle`
- **Sample divider**: 28-bit down-counter, fires every `cfg_rate_div_f` cycles
- **Input packer**: Shifts 16 channel bits into 32-bit buffer, assembles 16-bit words
- **Pre-trigger BRAM**: When armed, writes samples to circular 1,024×16 M9K. On trigger (`cfg_valid_edge`), snapshots write pointer via `bram_wp_f`/`bram_cnt_f`
- **Async FIFO push**: Post-trigger, pushes 16-bit words to dcfifo (4,096 depth). Sets overflow on FIFO full or sample count reached
- **Snapshot CDC**: Toggle synchronizer for BRAM snapshot → CLK domain

**CLK (100 / 96 MHz) processes:**
- **BRAM read port**: Synchronous read on pclk
- **BRAM snapshot latch**: On `snap_valid_clk`, latches `bram_wp_snap`/`bram_cnt_snap` (2FF CDC)
- **BRAM flush**: After run_edge, reads pre-trigger data from BRAM using frozen snapshot, writes to SDRAM
- **SDRAM write pump**: Reads from dcfifo, assigns SDRAM addresses (22-bit), writes single words
- **Continuous mode**: Triple-buffer with `Buffer_Full[2:0]`/`Buffer_Ack[2:0]` handshake
- **Readout**: Address-driven SDRAM reads → `Outputs`
- **Full detection**: Asserts `full_i` when buffer exhausted + FIFO empty + flush complete

### `rtl/OLS_SDRAM_Top.vhd` (~870 lines)

**Entity:** `OLS_SDRAM_Top`

System integration. Instantiates `SDRAM_PLL`, distributes clocks. 26-pin pool (MKR_D[14:0] + PMOD[7:0] + SEN_SDI/SPC/SDO/CS). SEN_SDI and SEN_SPC use registered `pin_out`/`pin_dir` open-drain drive. 16 LA channels via programmable `pin_map`. Capture mux with generator loopback priority. ADC interface with 8 channels, 128-bit analog frame.

### `rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd` (314 lines)

**Entity:** `OLS_Logic_Analyzer`

Core wrapper. Instantiates `OLS_Interface`, `Fast_Logic_Analyzer_SDRAM`, `Signal_Gen`, `Protocol_Trigger`, `SPI_Slave2`. Routes triple-buffer handshake signals.

### `rtl/OLS_Interface.vhd` (~1,200 lines)

**Entity:** `OLS_Interface`

Command/control interface. 28 opcodes covering register access, capture control, generator, diagnostics. Thread38/Thread44/Thread23 FSMs. `ID = 0x31414c53` ("SLA1"). Synthesis `preserve` attributes on generator start chain and I2C/SPI test mode signals prevent optimization.

### `rtl/SPI_Slave2.vhd` (165 lines)

**Entity:** `SPI_Slave2`

Full-duplex SPI slave on `fast_clk` (200 / 120 MHz). CDC: 2FF for config (sys→fast), 3FF for RX valid (fast→sys). Preamble byte loaded at CS falling edge — first MISO byte is status with zero protocol waste.

### `rtl/SDRAM_Interface.vhd` (191 lines)

**Entity:** `SDRAM_Interface`

Wrapper around `SDRAM_Controller`. Reset: 480,000 cycles (5 ms @ 96 MHz). Avalon-MM signal mapping. Simulation mode uses local RAM.

### `rtl/SDRAM_Controller_Custom.vhd` (591 lines)

**Entity:** `SDRAM_Controller`

Custom SDRAM controller: power-on init, read, write, burst (4-word), auto-refresh. Page-mode (keeps row open). Burst FIFO (8-entry). Avalon-MM with `waitrequest`. Timing: RCD=2, RP=2, RFC=7, CL=2 at 96 MHz. Compatible with 64 Mbit SDRAM (12 row / 8 column / 2 bank).

### `rtl/SDRAM_PLL.vhd` (412 lines)

**Entity:** `SDRAM_PLL`

Altera ALTPLL (wizard-generated). 12 MHz input → c0 (×50/÷6 = 100 MHz), c1 (×50/÷3 = 200 MHz), c2 (×50/÷6, −90° for SDRAM). For normal mode the PLL must be regenerated with ×8/×10 ratios. Auto bandwidth, VCO = 600 MHz.

### `rtl/ADC_Controller.vhd` (283 lines)

**Entity:** `ADC_Controller`

MAX10 internal ADC controller, **8 channels** (ch0–ch7). State machine: INIT→IDLE→SEND_CMD→WAIT_RSP→DONE. Sequentially scans requested channels. ADC clock: ~6.9 MHz (divider=13 from 100 MHz sys_clk in speed mode, 96 MHz in normal mode → 7.15–7.38 MHz actual).

### `rtl/LED_Controller.vhd` (~400 lines)

**Entity:** `LED_Controller`

8-LED animation engine with input pipeline registers and `r_speed` pipeline (breaks multiply/divide chain in rolling animation). States: idle (breathing), host confirm, armed (rapid pulse), single capture (flash), continuous (rolling with FIFO activity).

### `rtl/Signal_Gen.vhd` (479 lines)

**Entity:** `Signal_Gen`

Configurable generator (UART/I2C/SPI) with 256-byte FIFO and CRC-16 append option. I2C master FSM handles write and read phases with open-drain SDA/SCL drive.

### `rtl/Protocol_Trigger.vhd` (87 lines)

**Entity:** `Protocol_Trigger`

UART byte-level trigger. State: `IDLE→START→BITS→STOP→CHECK`.

---

## Project Files

### `proj/compile.ps1`

Build automation:
1. Parse `pin_assignments.csv` → build pin/IO maps
2. Generate `OLS_Logic_Analyzer_wrapper.vhd` with `chip_pin` attributes
3. Write `OLS_Logic_Analyzer.qsf` (device 10M08SAU169C8G)
4. Compile via `quartus_sh --flow compile`
5. Optional `-Flash`: program `.sof` via `quartus_pgm -m JTAG`

### `proj/OLS_Logic_Analyzer.sdc`

Timing constraints: 12 MHz input clock, `derive_pll_clocks`, `derive_clock_uncertainty`. CDC false paths between all three PLL outputs via `set_clock_groups -asynchronous`. Async FIFO gray-code CDC false paths for the dcfifo megafunction. No multicycle constraints on the capture path.

---

## Testbenches

Run with GHDL:
```powershell
ghdl -a --std=08 hdl\tb\support\sim_pkg.vhd
ghdl -a --std=08 hdl\rtl\*.vhd
ghdl -a --std=08 hdl\tb\*.vhd
ghdl -e --std=08 <testbench>
ghdl -r --std=08 <testbench> --assert-level=failure
```

| Testbench | Lines | Coverage |
|-----------|-------|----------|
| `tb_top` | 458 | **Full end-to-end**: PLL lock, clock path, packet protocol, debug CH0, UART loopback on all pins 0–15, **I2C generator drive on SEN_SDI/SEN_SPC** |
| `tb_core` | — | Core integration with signal generator |
| `tb_ols_interface` | 467 | All opcodes, trigger, gen control, readout |
| `tb_capture_path` | 227 | sample_en timing, BRAM write, Full, CH0 |
| `tb_continuous` | 92 | Buffer fill/ack/refill |
| `tb_fast_analyzer` | 206 | FLA with SDRAM model, pattern gen |
| `tb_sdram_interface` | 156 | SDRAM read/write |
| `tb_sdram_controller` | 175 | Avalon-MM SDRAM transactions |
| `tb_spi_slave` | 107 | Full-duplex at 10 MHz |
| `tb_signal_gen` | 221 | FIFO load, UART 0x55 at 115200 |
| `tb_led_controller` | 205 | PWM, fade, all animation states |
| `tb_adc_controller` | 102 | ADC single conv, multi-channel scan |
| `tb_protocol_trigger` | 94 | Matches 0xA5, rejects 0x5A |

Support packages: `sim_pkg.vhd`, `adxl345_model.vhd`, `sdram_model.vhd`, `pll_model.vhd`.

---

## Hardware Diagnostics

| Script | Tests |
|--------|-------|
| `hw_test/diag_clean.py` | Fresh FTDI per test: status, reset+ARM, generator, Full bit |
| `hw_test/diag_arm_test.py` | CMD_ARM via SPI: 5 arm methods |
| `hw_test/diag_gen_busy.py` | Generator UART output, fast capture, chained readback |
| `hw_test/diag_gen_data.py` | Generator FIFO load and transmission |

---

## IP Cores

### `ip/MAX10_ADC/`
Altera Modular ADC II for MAX10. All 8 analog channels enabled. Avalon-ST command/response interface.
