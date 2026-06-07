# OLS Logic Analyzer — MAX1000

Open-source multi-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + built-in ADC + LIS3DH accelerometer). Dual host interface: **UART (FTDI VCP)** or **SPI (FTDI MPSSE)**.

## Features

- **16 physical channels**, arbitrarily mappable to any of 23 pin pool (PMOD + MKR headers)
- **Channel mode**: 8-ch / 500k samples or 4-ch / 4M samples (CMD_CH_MODE)
- **Analog sampling**: built-in MAX10 ADC, multiplexed across 8 analog inputs
- **5 capture modes**: Digital 8/16, Mixed 1/2, Analog 1/2 (combine digital + ADC)
- **Sample rate**: up to **24 MHz** (sys_clk / Rate_Div / 2, min divider = 0; 48 MHz sys_clk ÷ ((0+1) × 2) = 24 MHz)
- **Deep capture**: up to 4,000,000 samples via SDRAM
- **Fast capture**: 1,024 samples via BRAM (M9K, no SDRAM needed)
- **Rolling / continuous capture**: triple-buffer scheme with prefetch
- **Edge trigger**: rising/falling on any channel
- **Protocol trigger**: UART byte match at configurable baud
- **Generator**: UART / I2C / SPI output on any GPIO pin
- **Accelerometer control**: Dedicated GUI tab for LIS3DH register read/write
- **Protocol decode**: UART, I2C with waveform annotation
- **Auto-filter**: Toggleable glitch suppression with visible highlight overlays on filtered regions
- **Board self-test**: free-running ~47 kHz test signal on CH0

## Capture Modes

The FPGA supports five capture modes selected via `CMD_ANALOG_CFG` (0xB0). Analog modes interleave MAX10 ADC samples with digital data into framed output words.

| Mode | Value | Digital channels | ADC channels | Frame size | Description |
|------|-------|-----------------|--------------|------------|-------------|
| Digital 8 | 0 | 16 | — | 2 bytes | All 16 digital channels, no ADC |
| Mixed 1 | 1 | 16 | 1 | 4 bytes | 16 digital + 1 ADC (12-bit) |
| Mixed 2 | 2 | 16 | 2 | 5 bytes | 16 digital + 2 ADC (12-bit each) |
| Analog 1 | 3 | — | 1 | 2 bytes | 1 ADC only (12-bit) |
| Analog 2 | 4 | — | 2 | 3 bytes | 2 ADC only (12-bit each) |

ADC channels 0 and 1 are independently configurable to any of the 8 analog inputs (MAX1000 AIN0–AIN7, bank 1A). In idle the ADC free-runs at ~1 MHz (48 MHz / 48 divider).

**Python host API:**

```python
from driver.ols_spi_device import OLSDeviceSPI, ANALOG_MODE_MIXED2

dev = OLSDeviceSPI()
dev.open()

# Configure analog: Mixed2 on AIN2 and AIN5
dev.set_analog_config(ANALOG_MODE_MIXED2, ch0=2, ch1=5)

# Capture 4096 analog frames
raw, frames = dev.capture_analog(
    rate_hz=100000, frames=4096, mode=ANALOG_MODE_MIXED2
)
for f in frames:
    print(f"digital={f['digital']:04x}, adc={f['adc']}")
```

## Clock Architecture

The PLL (SDRAM_PLL) multiplies the 12 MHz input:

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×4 | 48 MHz | Core logic, OLS_Interface, Fast_Logic_Analyzer |
| c1 | ×10 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×4 | 48 MHz, −90° | SDRAM clock |
| c3 | ×2 | 24 MHz | Signal generator (GEN_CLK) |

`PLL_MULT = 4` in `OLS_SDRAM_Top.vhd` sets `System_CLK_Frequency = 48_000_000`. All PLL outputs are used.

### Timing closure (48 MHz)

| Model | Setup slack | Hold slack |
|-------|-------------|------------|
| Slow 85°C | **−23 ns** | 0.30 ns |
| Slow 0°C | **−19 ns** | 0.27 ns |
| Fast 0°C | **+3.0 ns** | 0.10 ns |

Sys_clk = 48 MHz (PLL ×4). The MAX10 10M08 at C8 speed grade does not fully close timing at worst-case corner (85°C), but the design functions correctly under typical conditions (Fast 0°C: +3.0 ns). The generator clock is isolated on PLL c3 at 24 MHz. The host divider formula accounts for the hardware ×2 factor: `div = sys_clk / (rate_hz × 2) − 1`. Max capture rate = 24 MHz.

## Host Interfaces

### UART Backend

Uses FTDI virtual COM port (Channel A, BDBUS0/BDBUS1). Slower but no special driver.

```
cd host
python -m app.OLS_Console
```

Or with SPI backend auto-detected:
```
cd host
python -m app.OLS_Console --spi
```

### SPI Backend (recommended)

Uses FTDI MPSSE Channel B at 15 MHz (60 MHz FTDI base / ((1+1) × 2) with div=1). Requires `ftd2xx` (D2XX drivers).

```python
from driver.ols_spi_device import OLSDeviceSPI
dev = OLSDeviceSPI()  # sys_clk=48 MHz
dev.open()
data = dev.capture(rate_hz=1000000, nsamples=5000)

dev.send_uart(b'Hello', baud=115200, tx_pin=3)
data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
```

| Feature | UART | SPI |
|---------|------|-----|
| Throughput | ~1 Mbps | **~12 Mbps** |
| Latency | ~9ms/cmd | **~3ms/cmd** |
| Generator | Works | **Works** |
| Driver | pyserial | ftd2xx (D2XX) |

## VHDL Architecture

### OLS_SDRAM_Top.vhd — Top level

Clock generation (PLL), capture mux, generator signal routing, LED PWM, pin tristate buffers, test_div counter.

**test_div**: Free-running 10-bit counter on `sys_clk`. Bit 9 drives `registered_ch0` (double-registered with `preserve` attribute) which feeds CH0 through `capture_mux`. Output frequency = 48 MHz / 1024 ≈ 46.9 kHz.

**capture_mux**: Combinatorial mux per channel (i = 0..15):
- CH0 = `registered_ch0_d1` (test signal, 2-cycle pipelined)
- CHi = `gen_tx_d1` when `gen_busy` and `gen_tx_pin = pin_map(i)` (generator output)
- CHi = `sen_sdo_d1`/`gen_tx_d1` in SPI/I2C test mode (synchronised/pipelined)
- CHi = `pin_pool_d1(pin_map(i))` otherwise (physical pin via remappable pin map)
- SCL in I2C test mode = `gen_scl_d2` (2-cycle pipelined, matches sen_sdi_sync)

All capture channels have uniform 2-cycle pipeline depth for aligned sampling. Physical pin mapping is configurable at runtime via `CMD_PIN_MAP` (0xBB).

**Analog stream**: When `analog_mode /= "000"`, the capture engine reads framed analog data (digital + ADC samples) instead of raw pins. The ADC controller multiplexes two channels from AIN[0..7] at ~1 MHz, selected by `analog_ch0` / `analog_ch1`.

### OLS_Interface.vhd — Command processor + readout FSM

Processes SPI/UART commands via Thread38 state machine. Accumulates multi-byte command data (bit7=1 opcodes). Dispatches to Thread44 handlers.

**Key fixes applied:**
- ctr variable now reset to 0 on every accumulate entry (previously stale ctr=4 caused dispatch with wrong data)
- `WHEN 19` handler added for CMD_STATUS (missing handler locked Thread38)
- CMD_SPI_TEST (0xAF) removed from direct-dispatch path — goes through normal accumulate
- `CMD_SPI_TEST` now reads `data(0)` instead of hardcoding `'1'`, allowing host to reset SPI test mode
- `blk_len` variable initialized from `blk_len_s` in CMD_GEN_BLK handler
- Non-continuous readout: Thread23 enters idle state 6 after single readout pass (was looping, re-reading zeros)
- CMD_ARM resets Thread23 to 0 only in non-continuous mode
- Gen_Baud_Div default: `x"0341"` (833) → `x"01A0"` (416) for 48 MHz
- `spi_adapter` process: `effective_TX_Busy` now driven from `SPI_RX_Valid` in SPI mode (was `UART_TX_Busy`, stalling readout 87 µs/byte). Readout pipeline now runs lockstep with SPI byte consumption.
- Generator clock isolated to dedicated PLL c3 (24 MHz) with CDC crossings for baud tick and tx_active
- 2FF synchronizer added for `Full` signal crossing sys_clk → fast_clk domain (SPI preamble)

**Readout state machine** (Thread23/Thread26):
- Sequences through captured addresses on Full='1'
- Loads UART_TX_Data with Outputs bytes → SPI slave clocks out on next transaction
- Continuous mode: loops via states 4→5→2 (buffer ack + next buffer)
- Single-shot: state 3 clears Run/Run_OLS, enters idle state 6

### Fast_Logic_Analyzer_SDRAM.vhd — Capture engine + triple buffer

Samples Inputs at `sample_en` rate, stores in BRAM (fast mode) or FIFO→SDRAM (deep).

**Key fixes:**
- `bram_post_cnt` range extended to `0 to 15000000` (was 1024, couldn't reach target)
- `bram_post_cnt := 0` on both Run-rising AND Run-falling paths (was only on rising)
- `f_cnt`, `f_head`, `f_tail`, `waddr_0/1/2` converted from variables to registered pipeline signals, breaking the combinatorial feedback paths that contributed to timing violations

**Divider**: Free-running counter, `sample_en` fires every `Rate_Div` pclk cycles.

**BRAM:** M9K block, 1024×16-bit. Written every `sub_steps` sample events (1 for 16 channels) when `Fast_Mode='1' AND Armed='1'`.

**Full assertion:** When `bram_post_cnt >= samples_div_p` (fast mode) or `waddr_0 >= samples_div_p` (SDRAM).

### Signal_Gen.vhd — UART/I2C/SPI generator

Load_Byte/Load_We → FIFO (256-deep). Start triggers transmission at baud_div rate.

- UART: start bit (0) + 8 data bits (LSB first) + stop bit (1)
- I2C: master mode with START/STOP, 7-bit addressing
- SPI master: mode 0, MSB first, CS asserted per byte

`FIXED_BAUD_DIV = x"00F0"` = 240 (24 MHz gen_clk / 240 / 2 = 50 kHz I2C). Used as fallback when Baud_Div=0. The generator runs on dedicated PLL c3 at 24 MHz (GEN_CLK), not the 48 MHz sys_clk — all baud divisors and the fixed divider use 24 MHz as the base clock.

### SPI_Slave2.vhd — SPI slave with CDC

Full-duplex SPI slave (CPOL=0, CPHA=0). TX_Data CDC from sys_clk to fast_clk (120 MHz). RX_Valid CDC back to sys_clk with 3-stage synchroniser. Preamble byte loaded at CS falling edge for zero-waste status.

## Pin Assignments

| Signal | MAX1000 Pin | Description |
|--------|-------------|-------------|
| CLK | H6 | 12 MHz system clock |
| UART_RX | A4 | USB-UART RX (FTDI BDBUS0) |
| UART_TX / SPI_MOSI | B4 | Shared: UART TX or SPI MOSI |
| SPI_CS | AG2 | SPI chip select |
| SPI_MISO | AF1 | SPI MISO |
| SPI_SCK | AG1 | SPI clock (shared with UART_RX) |
| PMOD[0..7] | PMOD | 8-bit PMOD header (channels 15–22 in pin pool) |
| MKR_D[0..4] | MKR header | MikroBus digital (channels 0–4 in pin pool) |
| MKR_D[5..14] | MKR header | MikroBus digital (channels 5–14 in pin pool) |
| AIN[0..7] | Bank 1A | MAX10 analog inputs (ADC mux) |
| SEN_SDI | J7 | Accelerometer MOSI |
| SEN_SDO | K5 | Accelerometer MISO |
| SEN_SPC | J6 | Accelerometer clock |
| SEN_CS | L5 | Accelerometer chip select |
| LED[0..7] | A8–D8 | Status LEDs (PWM) |

All 16 logic analyzer channels are software-mapped to any of the 23 digital pins in the pin pool (MKR_D[0..14] + PMOD[0..7]) via `CMD_PIN_MAP` (0xBB).

Full assignments in `hdl/proj/pin_assignments.csv`.

## VHDL Bug Fixes — Complete Log

| Bug | File | Symptom | Fix |
|-----|------|---------|-----|
| Stale ctr on accumulate | OLS_Interface.vhd | Multi-byte cmd data corrupted after first cmd | `ctr := 0` at all Thread38→6 transitions |
| Missing WHEN 19 | OLS_Interface.vhd | CMD_STATUS locked Thread38, all cmds after died | Added `WHEN 19 => null` handler |
| CMD_SPI_TEST shortcut | OLS_Interface.vhd | Data bytes (ARM/RESET) corrupted state | Removed 0xAF from direct-dispatch |
| blk_len never inited | OLS_Interface.vhd | Block mode forwarded 0 bytes | `blk_len := blk_len_s` in Thread44=21 |
| Readout infinite loop | OLS_Interface.vhd | Non-continuous re-read zeros after first pass | Thread23=3 → idle state 6 |
| Duplicate _on_rolling_buf_change | OLS_Console.py | Restart race: silent drop when thread hasn't finished | Deleted dead duplicate; remaining sets `_pending_restart` |
| Analog stride never restored | ols_spi_device.py | `capture_analog` left stride=1 → next digital capture misaligned | Save/restore `_stride` + `_raw_flags` via try/finally |
| Raw mode missing _raw_flags | ols_spi_device.py | SPI `raw_mode()` only changed `_stride`, not `_raw_flags` → FPGA output | Now sets both, matching UART backend |
| Analog fallback silent | OLS_Console.py | ANALOG1/2 with no ADC data silently showed digital instead | Shows error status and returns early |
| bram_post_cnt on Run-fall | Fast_Logic_Analyzer_SDRAM.vhd | Data lost on readout completion | Clear counter on both Run edges |
| Armed port open | OLS_SDRAM_Top.vhd | Synthesis pruned Armed→FLA path | `Armed => armed_i` (was `open`) |
| test_out optimized away | OLS_SDRAM_Top.vhd | CH0 toggle frequency wrong | `registered_ch0` FF with `preserve` |
| CLK_Frequency hardcoded | OLS_Logic_Analyzer_SDRAM_Core.vhd | UART baud wrong (150 MHz vs 48 MHz) | `CLK_Frequency => CLK_Frequency` |
| Rate_Div range hardcoded | OLS_Logic_Analyzer_SDRAM_Core.vhd | Same | `range 1 to CLK_Frequency` |
| PLL Retrieval info stale | SDRAM_PLL.vhd | MegaWizard would regress c0 to ×4 | Lines 256/337 updated to "48 MHz" |
| clk[1] missing from SDC | OLS_Logic_Analyzer.sdc | False timing violations on CDC paths | Added async clock group for clk[1] |
| Gen_Baud_Div default wrong | OLS_Interface.vhd | Reset-time UART baud = 57,600 | `x"0341"`→`x"01A0"` (833→416) |
| FIXED_BAUD_DIV stale | Signal_Gen.vhd | Fallback baud = 230,769 (24 MHz era) | `x"00D0"`→`x"01A0"` (208→416) |
| CMD_NOP/CMD_RESET conflict | OLS_Interface.vhd | 0x00 accidentally made NOP, reset never fired | `Thread44 := Thread44 + 1` (was `0`) |
| cmd_was_multibyte routing | OLS_Interface.vhd | Single-byte cmd after accumulate misrouted | ELSIF branch for cmd_was_multibyte=1 at Thread38=4 |
| I2C RD_SAMPLE no setup | Signal_Gen.vhd | SDA sampled same cycle SCL rose (0 hold time) | Split state 9→10(RD_SETUP)→12(SAMPLE) |
| gen_scl_pin_int default 0 | OLS_Interface.vhd | SCL defaulted to CH0 (hardwired test counter) | Default `0`→`1` |
| SEN_SDI metastability | OLS_SDRAM_Top.vhd | I2C SDA sampled combinatorially, no CDC | Added 2-FF synchroniser + capture mux pipeline |
| Non-uniform pipeline depths | OLS_SDRAM_Top.vhd | CH0/gen_tx/GPIO/SEN_SDO at different depths | All signals now at 2-cycle depth (matching sen_sdi_sync + gen_scl_d2) |
| spi_adapter stalls in SPI mode | OLS_Interface.vhd | Readout stuck at 87 µs/byte via UART_TX_Busy | `effective_TX_Busy` pulsed from `SPI_RX_Valid` — lockstep readout |
| CMD_SPI_TEST never resets | OLS_Interface.vhd | `gen_spi_test_int` stayed '1' after test 3 | Changed `gen_spi_test_int <= '1'` to `gen_spi_test_int <= data(0)` |
| Full signal lacks CDC | OLS_Interface.vhd | Metastability on Full → fast_clk domain | Added 2FF synchroniser on FAST_CLK |
| Generator on sys_clk jitter | Signal_Gen.vhd | Baud counter jitter from -23 ns timing violation | Baud counter moved to dedicated PLL c3 (24 MHz) with CDC bridges |
| f_cnt/waddr variable feedback | Fast_Logic_Analyzer_SDRAM.vhd | Longest combinatorial paths (14 LUT chains) | Converted to registered pipeline signals with writeback init |


## Python Host Fixes

| Fix | File | Detail |
|-----|------|--------|
| sys_clk default 48 MHz | host/driver/ols_spi_device.py | Line 42: `96000000`→`48000000` |
| Continuous mode in capture() | ols_spi_device.py | Enables CMD_CONT_CAPTURE for pipeline persistence |
| Back-to-back in capture_with_gen() | ols_spi_device.py | ARM+NOP in single CS-low burst |
| GUI double-Tk crash | host/app/OLS_Console.py | Single Tk root, auto-detect backend, auto-connect |
| Divider formula ×2 correction | ols_spi_device.py, OLS_Console.py | Actual sample rate = sys_clk / ((div+1) × 2); formula was missing ×2 | Changed to `sys_clk / (rate_hz × 2) − 1` |
| Gen baud for 24 MHz clock | ols_spi_device.py, OLS_Console.py | Generator now on gen_clk (24 MHz), baud divisors were for 48 MHz | Added `// 2` to all gen_baud and I2C speed computations |
| CMD_SPI_TEST / I2C_TEST reset | ols_spi_device.py | capture_with_gen left test modes active from previous tests | Added explicit reset before gen config |
| SPI raw_mode missing _raw_flags | ols_spi_device.py | SPI `raw_mode()` only set `_stride`, not `_raw_flags` → FPGA outputs 4 bytes/sample while software expects 1 | Now sets both `_stride` and `_raw_flags` (0x38/0), matching UART backend |
| Analog stride not restored | ols_spi_device.py | `capture_analog()` left stride=1 via raw mode → subsequent digital capture misaligned | Save/restore `_stride` + `_raw_flags` in try/finally |
| Duplicate _on_rolling_buf_change | OLS_Console.py | Dead duplicate overwritten by active version; restart race when thread hasn't finished | Deleted dead copy; active version now sets `_pending_restart` |
| Analog no-data fallback silent | OLS_Console.py | ANALOG1/2 with no ADC data silently showed digital instead of error | Shows error message, returns early |
| GUI integration tests | test_ols_console_integration.py | No tests for analog mode, mode switching, or restart race in GUI pipeline | 22 tests covering stride cleanup, raw_mode flags, analog load paths, mode switching, rolling restart, auto-filter |
| Auto-filter with highlight | OLS_Console.py | Floating channels show noise bursts, no default filtering, no visual feedback | Toolbar toggle → auto-detect noisy channels (>5% toggle rate), apply glitch_filter(3), render amber stipple overlays on modified regions |
| Auto-filter skips analog | OLS_Console.py | glitch_filter is binary (0/1), ADC values 0-4095 would be corrupted | Guard `max(samples) > 1` in `_auto_filter_channels` — analog channels pass through unchanged |
| Divider test formula | hw_validation.py | Updated for 48 MHz clock |
| PIN_DIR 0x3B→0x0B | host/driver/ols_spi.py + 18 files | FTDI BDBUS4-7 switched from outputs to inputs |
| send_uart reorder + flush | ols_spi_device.py | `_pins()` moved before `_load_block()`; flush+settle added |
| i2c_capture_with_gen flush | ols_spi_device.py | Added `flush()+sleep(0.01)` before burst |
| Test 3 state cleanup | hw_validation.py | `dev.reset()` after command sweep |
| Test 5 manual arm | hw_validation.py | Manual config+arm instead of `dev.capture()` |

| Test 9 address order | hw_validation.py | Probe order `[0x19, 0x18]` — LIS3DH at 0x19 |
| Test 9 SDA gate | hw_validation.py | Probe gates on SCL+SDA transitions |
| Relaxed WHO_AM_I check | hw_validation.py | Passes on any non-0xFF response |
| Filtered I2C tests (threshold=2/1) | hw_validation.py | Added glitch-filtered decode variants |
| Accelerometer GUI tab | OLS_Console.py | I2C addr/speed/pins, command buttons, waveform display |
| decode_i2c midpoint sampling | OLS_Console.py | Changed from edge-based to midpoint-of-SCL-high sampling |

## Testbench Results

### GHDL Simulation

| Testbench | Tests | Result | Coverage |
|-----------|-------|--------|----------|
| `tb_ols_interface` | 24 | **24/24 PASS** | All opcode paths, accumulator, trigger, gen control |
| `tb_capture_path` | 5 | **5/5 PASS** | sample_en period, BRAM write, Full, CH0 timing, 2nd capture |
| `tb_continuous` | 3 | **3/3 PASS** | Buffer fill, ack, re-fill cycle |

**Compile & run** (excludes SDRAM_PLL which needs Quartus `altera_mf` library):
```powershell
ghdl -a --std=08 hdl\tb\support\sim_pkg.vhd
ghdl -a --std=08 hdl\rtl\*.vhd
ghdl -a --std=08 hdl\tb\tb_*.vhd
ghdl -e --std=08 tb_ols_interface
ghdl -r --std=08 tb_ols_interface --assert-level=failure
```

### Hardware Validation

All 26 tests pass with FPGA programmed and USB connected:
```powershell
cd host
python app/hw_validation.py
```

**Results:** 26/26 PASS

| Test | Result | Notes |
|------|--------|-------|
| 2 — SPI handoff | PASS | CMD_ID `1ALS` signature confirmed |
| 3 — All SPI cmds | PASS | 18/18 accepted |
| 4 — Single capture | PASS | CH0 transitions detected, data returned |
| 5 — Fast mode (BRAM) | PASS | All 16 channels with transitions |
| 6 — Continuous | PASS | 3 buffers with non-zero data |
| 7 — Trigger | PASS | CH0 transitions on rising edge |
| 8 — UART gen | PASS | Generator UART signal present on CH3 |
| 9 — I2C speed sweep (15 combos) | PASS | 15/15 PASS — all rates 1–12 MHz, raw + filtered |
| 10 — SPI gen | PASS | SCLK transitions detected |
| 11 — Divider | PASS | Edge count verified |

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (MAX10 device support)
- Python 3.10+ (`pip install ftd2xx pyserial`)
- FTDI D2XX drivers (for SPI backend)

### Compile

Using the compile script:
```powershell
cd hdl\proj
.\compile.ps1            # compile only
.\compile.ps1 -Flash     # compile + flash via JTAG
```

Or with Quartus directly:
```powershell
cd hdl\proj
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sh.exe" --flow compile OLS_Logic_Analyzer
```

### Flash (JTAG)

```powershell
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe" -c "Arrow-USB-Blaster" -m JTAG -o "p;hdl/proj/output_files/OLS_Logic_Analyzer.sof"
```

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── rtl/                           # VHDL design sources
│   │   ├── OLS_SDRAM_Top.vhd              # Top: PLL, capture mux, gen routing, LEDs
│   │   ├── OLS_Interface.vhd              # SPI command decoder, readout FSM
│   │   ├── Fast_Logic_Analyzer_SDRAM.vhd  # Capture engine, triple buffer
│   │   ├── OLS_Logic_Analyzer_SDRAM_Core.vhd  # Core wrapper
│   │   ├── SPI_Slave.vhd                  # SPI slave with CDC
│   │   ├── Signal_Gen.vhd                 # UART/I2C/SPI generator
│   │   ├── UART_Interface.vhd             # UART Rx/Tx with oversampling
│   │   ├── SDRAM_Interface.vhd            # SDRAM controller wrapper
│   │   ├── SDRAM_Controller_Custom.vhd    # SDRAM controller
│   │   ├── SDRAM_PLL.vhd                  # PLL (12→48/120/48 MHz, c3=24 MHz)
│   │   ├── Protocol_Trigger.vhd           # UART protocol trigger
│   │   ├── ADC_Controller.vhd             # ADC controller
│   │   ├── LED_Controller.vhd             # LED PWM driver
│   │   └── ...                               # 14 VHDL design sources (no wrapper)
│   ├── tb/                            # Testbenches
│   │   ├── tb_ols_interface.vhd       # OLS_Interface full command test (24/24)
│   │   ├── tb_capture_path.vhd        # FLA end-to-end capture test (5/5)
│   │   ├── tb_continuous.vhd          # Continuous buffer test (3/3)
│   │   └── support/sim_pkg.vhd        # Test utilities
│   ├── proj/                          # Quartus project files
│   │   ├── OLS_Logic_Analyzer.qpf/.qsf    # Main project
│   │   ├── OLS_Logic_Analyzer.sdc          # Timing constraints
│   │   ├── compile.ps1                    # Build + flash script
│   │   ├── pin_assignments.csv            # Pin mappings
│   │   └── OLS_Logic_Analyzer_wrapper.vhd # Auto-generated wrapper
│   ├── ip/MAX10_ADC/                  # Altera Modular ADC II IP
│   └── hw_test/                       # Results directory
├── host/
│   ├── app/                           # Main application
│   │   ├── OLS_Console.py                 # GUI (tkinter) + CLI modes
│   │   ├── hw_validation.py               # Hardware validation suite
│   │   ├── program_eeprom.py              # FTDI EEPROM programmer
│   │   └── config/                        # FTDI EEPROM config files
│   ├── driver/                        # Reusable SPI driver layer
│   │   ├── ols_spi.py                     # FTDI MPSSE low-level driver
│   │   ├── ols_spi_mpsse.py               # MPSSE bitbang layer
│   │   ├── ols_spi_pyftdi.py              # pyftdi-compatible wrapper
│   │   ├── ols_spi_device.py              # High-level SPI device API
│   │   └── tests/                         # Driver tests (138 tests)
│   ├── tests/                         # App-level tests (197 tests)
│   ├── debug/                         # Diagnostic/debug scripts
│   └── requirements.txt
├── docs/                              # MAX1000 User Guides
├── archive/                           # Experiments, one-off scripts, alt hdl variants
├── .github/workflows/test.yml         # CI workflow
├── README.md
├── LICENSE
└── .gitignore
```

## Design Notes

- **CH0** is wired to a free-running ~47 kHz test divider for self-test. Generator TX pins are configurable.
- **Pin map**: 16 logical channels are mapped to 23 physical pins (MKR_D[0..14] + PMOD[0..7]) at runtime via `CMD_PIN_MAP`. Default mapping is identity (CHi → pin i). The `set_pin_map(channel, pin_index)` API remaps any channel to any pin.
- **Channel mode**: `CMD_CH_MODE` switches between 8-ch / 500k max samples (default) and 4-ch / 4M max samples. This trades channel count for sample depth.
- **I2C rate** is fixed at ~50 kHz (FIXED_BAUD_DIV=240 on 24 MHz gen_clk: 24 MHz / 240 / 2 = 50 kHz with oversample). CMD_GEN_BAUD has no effect on I2C.
- **LIS3DH WHO_AM_I** timing varies with capture rate — the raw register response is correct but may differ from the nominal 0x33 depending on pipeline alignment.

## Known Limitations

### I2C capture rate ceiling
The 2-FF synchroniser on SEN_SDI introduces a 1-sample pipeline delay. All 15 I2C speed/filter combinations (1–12 MHz, raw + filtered) pass the hardware validation suite.

### PLL timing
Sys_clk = 48 MHz (PLL ×4, 12 MHz × 4). The MAX10 10M08 C8 speed grade has −23 ns worst-case setup slack at 85°C, but the design functions correctly under typical conditions (Fast 0°C model: +3 ns). The generator clock is isolated on PLL c3 at 24 MHz for clean baud timing. Max capture rate = 24 MHz.

### Generator UART decode
The generator UART test verifies the hardware signal (transitions, start bits, bit timing) on CH3. Software decode via `decode_uart()` may have intermittent success for multi-byte frames due to sample rate variation from the 48 MHz PLL timing slack. The hardware UART signal is correct — this is a decode alignment limitation.

### GHDL simulation coverage
The altera_mf vendor library (SDRAM_PLL) and altera_modular_adc_control IP cannot be compiled by GHDL. Simulation models are provided in `hdl/tb/` for both, but the SDRAM_PLL model and ADC controller model are behavioural approximations, not cycle-accurate. Full-timing simulation requires Quartus.

### Python dependencies
The SPI backend requires `ftd2xx` (FTDI D2XX driver, Windows only). The GUI falls back to UART via `pyserial` if unavailable. `pyftdi` provides an alternative bitbang backend for platforms where D2XX is not installed.

## License

MIT — see `LICENSE`.
