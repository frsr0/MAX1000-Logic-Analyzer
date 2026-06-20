# HDL — OLS Logic Analyzer FPGA Design

## Architecture Overview

Target: Intel MAX10 10M08SAU169C8G on Arrow MAX1000 board. PLL derives four active outputs from the 12 MHz input:

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×8.33 | 100 MHz | SDRAM write pump, buffer mgmt, readout, OLS protocol |
| c1 | ×16.67 | 200 MHz | **Sample capture** (FAST_CLK), SPI slave |
| c2 | ×8.33 | 100 MHz, −90° | SDRAM clock (phase-shifted for data centering) |
| c3 | ×4.17 | 50 MHz VCO tap / 12 MHz output | MAX10 ADC hard-IP input (`clkdiv=1` inside ADC IP) |

Current generated wrapper uses `FAST_SPEED => true` for a 100 MHz system clock and 200 MHz sample clock. The same PLL instance also feeds the ADC hard-IP from `c3`, which is why the analogue path can run at the validated 1 MSPS single-channel / 125 kframes/s 8-slot rates. Normal mode remains available through the top-level generics but requires changing `proj/compile.ps1`; the generated wrapper is overwritten on each build.

### Two-Clock Domain Split

```
FAST_CLK (200 MHz, c1)                   CLK (100 MHz, c0)
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

### Top-down hierarchy

```
OLS_Logic_Analyzer_wrapper      — pin assignment wrapper (auto-generated from CSV)
└── OLS_SDRAM_Top               — system integration, I/O pin pool, capture mux
    ├── SDRAM_PLL               — PLL (4-output clock generation, including ADC c3)
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

**FAST_CLK (200 MHz) processes:**
- **Config handshake**: Detects `cfg_valid_edge` (toggled by CLK domain on run start), latches `cfg_rate_div_f` and `cfg_samples_f`, acks via `cfg_ack_toggle`
- **Sample divider**: 28-bit down-counter, fires every `cfg_rate_div_f + 1` cycles; a start gate waits for config ACK/divider reload before sampling, including `Rate_Div=1`
- **Input packer**: Shifts 16 channel bits into 32-bit buffer, assembles 16-bit words
- **Narrow digital packer**: When `Narrow_Enable` is set, samples one selected digital channel at FAST_CLK rate and packs 16 consecutive time samples into one 16-bit FIFO word, bit 0 earliest
- **Pre-trigger BRAM**: When armed, writes samples to circular 1,024×16 M9K. On trigger (`cfg_valid_edge`), snapshots write pointer via `bram_wp_f`/`bram_cnt_f`
- **Async FIFO push**: Post-trigger, pushes 16-bit words to dcfifo (4,096 depth). Sets overflow on FIFO full or sample count reached
- **Snapshot CDC**: Toggle synchronizer for BRAM snapshot → CLK domain

**CLK (100 MHz in the current speed build) processes:**
- **BRAM read port**: Synchronous read on pclk
- **BRAM snapshot latch**: On `snap_valid_clk`, latches `bram_wp_snap`/`bram_cnt_snap` (2FF CDC)
- **BRAM flush**: After run_edge, reads pre-trigger data from BRAM using frozen snapshot, writes to SDRAM
- **SDRAM write pump**: Reads from dcfifo, assigns SDRAM addresses (22-bit), writes single words
- **Continuous mode**: SDRAM ring buffer with monotonic `producer_index`, retained `oldest_index`/`newest_index`, and `overrun_count`; legacy buffer flags remain as readiness markers
- **Readout**: Address-driven SDRAM reads → `Outputs`; continuous readout maps absolute sample indexes into the ring
- **Full detection**: Asserts `full_i` when buffer exhausted + FIFO empty + flush complete

### `rtl/OLS_SDRAM_Top.vhd` (~670 lines)

**Entity:** `OLS_SDRAM_Top`

System integration. Instantiates `SDRAM_PLL`, distributes clocks. The RTL pin pool has 26 entries: MKR_D[14:0], PMOD[7:0], and the LIS3DH `SEN_SDO`/`SEN_SDI`/`SEN_SPC` pins. Sixteen LA channels select from that pool via programmable `pin_map` (default: MKR D0-D14 plus `SEN_SDI`). Capture mux has generator loopback priority. ADC profile selection is controlled by `REG_FLAGS`: digital-only (2-byte frame), narrow packed digital (`bit13`, one selected channel from bits17:14, 2-byte word per 16 time samples), mixed 16 digital + ADC0-ADC7 (`bit3`, 14-byte frame), high-speed analog (`bit3|bit4`, profile `00`, 2-byte frame), or maximum analog (`bit3|bit4`, profile `01`, 12-byte frame for ADC1,2,3,4,5,7,8,16). See [`docs/ANALOG_MODE_PLAN.md`](../docs/ANALOG_MODE_PLAN.md).

### `rtl/OLS_Logic_Analyzer_SDRAM_Core.vhd` (298 lines)

**Entity:** `OLS_Logic_Analyzer`

Core wrapper. Instantiates `OLS_Interface`, `Fast_Logic_Analyzer_SDRAM`, `Signal_Gen`, `Protocol_Trigger`, `SPI_Slave2`. Routes capture control, generator control, buffer handshakes, and continuous ring metadata.

### `rtl/OLS_Interface.vhd` (~1,172 lines)

**Entity:** `OLS_Interface`

Command/control interface. Packet opcodes cover register access, capture control, generator, diagnostics, sticky DONE ACK, and metadata readback. DONE latches until ACK, abort, or the next arm; `capture_seq` increments on every arm so the host can prove readback freshness. `REG_FLAGS` includes analog profile/channel bits plus narrow digital enable/channel bits. `ID = 0x31414c53` ("SLA1").

### `rtl/SPI_Slave2.vhd` (165 lines)

**Entity:** `SPI_Slave2`

Full-duplex SPI slave on `fast_clk` (200 MHz in speed build). CDC: 2FF for config/control crossings and 3FF for RX valid. Preamble byte loaded at CS falling edge — first MISO byte is status with zero protocol waste.

### `rtl/SDRAM_Interface.vhd` (191 lines)

**Entity:** `SDRAM_Interface`

Wrapper around `SDRAM_Controller`. Reset: 480,000 cycles (5 ms @ 96 MHz). Avalon-MM signal mapping. Simulation mode uses local RAM.

### `rtl/SDRAM_Controller_Custom.vhd` (591 lines)

**Entity:** `SDRAM_Controller`

Custom SDRAM controller: power-on init, read, write, burst (4-word), auto-refresh. Page-mode (keeps row open). Burst FIFO (8-entry). Avalon-MM with `waitrequest`. Timing: RCD=2, RP=2, RFC=7, CL=2 at 96 MHz. Compatible with 64 Mbit SDRAM (12 row / 8 column / 2 bank).

### `rtl/SDRAM_PLL.vhd` (412 lines)

**Entity:** `SDRAM_PLL`

Altera ALTPLL. 12 MHz input → c0 (100 MHz), c1 (200 MHz), c2 (100 MHz, −90°), and c3 feeding the MAX10 ADC hard-IP at 12 MHz in the current speed build. Auto bandwidth.

### `rtl/ADC_Controller.vhd` (283 lines)

**Entity:** `ADC_Controller`

MAX10 internal ADC controller, **8 mux slots** (ch0-ch7). State machine: INIT→IDLE→SEND_CMD→WAIT_RSP→DONE. Sequentially scans requested slots; each slot can select ADC mux channel 0-31. ADC hard-IP clock is SDRAM_PLL c3 at 12 MHz with `clkdiv=1`; measured direct hardware timing is about 1 MSPS for the one-slot high-speed analog profile and about 125 kframes/s for the 8-slot mixed/maximum profiles.

MAX1000 board-guide analogue mapping is non-linear: MKR `AIN1`=ADC2, `AIN2`=ADC5, `AIN3`=ADC1, `AIN4`=ADC3, `AIN5`=ADC7, `AIN6`=ADC4, and `AIN0`=ADC8. Mixed mode scans ADC0-ADC7, so ADC0 and ADC6 remain unmapped mux slots. Maximum analog scans the physical profile ADC1,2,3,4,5,7,8,16, adding MKR `AIN0` and the dedicated `AIN` pin.

`REG_FLAGS` bits 6:5 select the analog profile and bits 12:8 select the
high-speed analog ADC mux channel. Bit 13 enables narrow packed digital and
bits 17:14 select the digital channel for that mode. Frame completion uses
`adc0_valid` for the one-slot high-speed profile and `adc7_valid` for 8-slot
mixed/maximum profiles.

### `rtl/LED_Controller.vhd` (~400 lines)

**Entity:** `LED_Controller`

8-LED animation engine with input pipeline registers and `r_speed` pipeline (breaks multiply/divide chain in rolling animation). States: idle (breathing), host confirm, armed (rapid pulse), single capture (flash), continuous (rolling with FIFO activity).

### `rtl/Signal_Gen.vhd` (441 lines)

**Entity:** `Signal_Gen`

Configurable generator (UART/I2C/SPI) with 256-byte FIFO. CRC-16 append option.

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

Timing constraints: 12 MHz input clock, `derive_pll_clocks`, CDC false paths between c0 and c1, LED controller multicycle path (1M cycles), and a false path from the slow-domain pin-map registers into the pipelined fast capture input map. No multicycle constraints are applied to the capture datapath.

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
| `tb_ols_interface` | 467 | All opcodes, trigger, gen control, readout |
| `tb_ols_capture_contract` | 220 | Sticky DONE/ACK/abort, capture_seq, mixed→digital→mixed mode reset |
| `tb_capture_path` | 227 | sample_en timing, BRAM write, Full, CH0 |
| `tb_continuous` | 92 | Buffer fill/ack/refill |
| `tb_continuous_rate1` | 94 | Continuous auto-rotation at max-rate `Rate_Div=1` |
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
Altera Modular ADC II for MAX10. The IP mask enables ADC0-ADC8 plus the dedicated analogue input ADC16. Avalon-ST command/response interface.
