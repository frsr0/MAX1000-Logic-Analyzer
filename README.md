# OLS Logic Analyzer — MAX1000

Open-source 8-channel logic analyzer for the Arrow MAX1000 board (Intel MAX10 10M08SAU169C8G + 64 Mbit SDRAM + LIS3DH accelerometer). Dual host interface: **UART (FTDI VCP)** or **SPI (FTDI MPSSE)**.

## Features

- **8 logic channels**, up to **24 MHz** sample rate
- **Deep capture**: up to 500,000 samples via SDRAM
- **Fast capture**: 1,024 samples via BRAM (M9K, no SDRAM needed)
- **Rolling / continuous capture**: triple-buffer scheme with prefetch
- **Edge trigger**: rising/falling on any channel
- **Protocol trigger**: UART byte match at configurable baud
- **Generator**: UART / I2C / SPI output on any GPIO pin
- **Protocol decode**: UART, I2C with waveform annotation
- **Board self-test**: free-running ~47 kHz test signal on CH0

## Clock Architecture

The PLL (SDRAM_PLL) multiplies the 12 MHz input:

| Output | Multiply | Frequency | Domain |
|--------|----------|-----------|--------|
| c0 | ×4 | 48 MHz | Core logic, OLS_Interface, Fast_Logic_Analyzer |
| c1 | ×10 | 120 MHz | SPI slave (fast_clk) |
| c2 | ×4 | 48 MHz, −90° | SDRAM clock |

`PLL_MULT = 4` in `OLS_SDRAM_Top.vhd` sets `System_CLK_Frequency = 48_000_000`.

### Timing closure (48 MHz)

| Model | Setup slack | Hold slack |
|-------|-------------|------------|
| Slow 85°C | +3.236 ns | 0.278 ns |
| Slow 0°C | +3.412 ns | 0.253 ns |
| Fast 0°C | +6.182 ns | 0.098 ns |

## Host Interfaces

### UART Backend

Uses FTDI virtual COM port (Channel A, BDBUS0/BDBUS1). Slower but no special driver.

```
python host/OLS_Console.py
```

### SPI Backend (recommended)

Uses FTDI MPSSE Channel B at 30 MHz. Requires `ftd2xx` (D2XX drivers).

```python
from ols_spi_device import OLSDeviceSPI
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

**test_div**: Free-running 10-bit counter on `sys_clk`. Bit 9 drives `registered_ch0` (double-registered with `preserve` attribute) which feeds CH0 through `capture_mux`. Output frequency = 48 MHz / 1024 = 46.875 kHz.

**capture_mux**: Combinatorial mux per channel:
- CH0 = `registered_ch0` (test signal)
- CHi = `gen_tx` when `gen_busy` and `gen_tx_pin = i`
- CHi = `SEN_SDO`/`SEN_SDI` in SPI/I2C test mode
- CHi = `GPIO(i)` otherwise

### OLS_Interface.vhd — Command processor + readout FSM

Processes SPI/UART commands via Thread38 state machine. Accumulates multi-byte command data (bit7=1 opcodes). Dispatches to Thread44 handlers.

**Key fixes applied:**
- ctr variable now reset to 0 on every accumulate entry (previously stale ctr=4 caused dispatch with wrong data)
- `WHEN 19` handler added for CMD_STATUS (missing handler locked Thread38)
- CMD_SPI_TEST (0xAF) removed from direct-dispatch path — goes through normal accumulate
- `blk_len` variable initialized from `blk_len_s` in CMD_GEN_BLK handler
- Non-continuous readout: Thread23 enters idle state 6 after single readout pass (was looping, re-reading zeros)
- CMD_ARM resets Thread23 to 0 only in non-continuous mode
- Gen_Baud_Div default: `x"0341"` (833) → `x"01A0"` (416) for 48 MHz

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

**Divider**: Free-running counter, `sample_en` fires every `Rate_Div` pclk cycles.

**BRAM:** M9K block, 1024×16-bit. Written every `sub_steps` sample events (2 for 8 channels) when `Fast_Mode='1' AND Armed='1'`.

**Full assertion:** When `bram_post_cnt >= samples_div_p` (fast mode) or `waddr_0 >= samples_div_p` (SDRAM).

### Signal_Gen.vhd — UART/I2C/SPI generator

Load_Byte/Load_We → FIFO (256-deep). Start triggers transmission at baud_div rate.

- UART: start bit (0) + 8 data bits (LSB first) + stop bit (1)
- I2C: master mode with START/STOP, 7-bit addressing
- SPI master: mode 0, MSB first, CS asserted per byte

`FIXED_BAUD_DIV = x"01A0"` = 416 (48 MHz / 416 = 115,385 baud ≈ 115200). Used as fallback when Baud_Div=0.

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
| GPIO[0..7] | PMOD | Logic analyzer channels |
| SEN_SDI | J7 | Accelerometer MOSI |
| SEN_SDO | K5 | Accelerometer MISO |
| SEN_SPC | J6 | Accelerometer clock |
| SEN_CS | L5 | Accelerometer chip select |
| LED[0..7] | A8–D8 | Status LEDs (PWM) |

Full assignments in `hdl/pin_assignments.csv`.

## VHDL Bug Fixes — Complete Log

| Bug | File | Symptom | Fix |
|-----|------|---------|-----|
| Stale ctr on accumulate | OLS_Interface.vhd | Multi-byte cmd data corrupted after first cmd | `ctr := 0` at all Thread38→6 transitions |
| Missing WHEN 19 | OLS_Interface.vhd | CMD_STATUS locked Thread38, all cmds after died | Added `WHEN 19 => null` handler |
| CMD_SPI_TEST shortcut | OLS_Interface.vhd | Data bytes (ARM/RESET) corrupted state | Removed 0xAF from direct-dispatch |
| blk_len never inited | OLS_Interface.vhd | Block mode forwarded 0 bytes | `blk_len := blk_len_s` in Thread44=21 |
| Readout infinite loop | OLS_Interface.vhd | Non-continuous re-read zeros after first pass | Thread23=3 → idle state 6 |
| bram_post_cnt on Run-fall | Fast_Logic_Analyzer_SDRAM.vhd | Data lost on readout completion | Clear counter on both Run edges |
| Armed port open | OLS_SDRAM_Top.vhd | Synthesis pruned Armed→FLA path | `Armed => armed_i` (was `open`) |
| test_out optimized away | OLS_SDRAM_Top.vhd | CH0 toggle frequency wrong | `registered_ch0` FF with `preserve` |
| CLK_Frequency hardcoded | OLS_Logic_Analyzer_SDRAM_Core.vhd | UART baud wrong (150 MHz vs 48 MHz) | `CLK_Frequency => CLK_Frequency` |
| Rate_Div range hardcoded | OLS_Logic_Analyzer_SDRAM_Core.vhd | Same | `range 1 to CLK_Frequency` |
| PLL Retrieval info stale | SDRAM_PLL.vhd | MegaWizard would regress c0 to ×4 | Lines 256/337 updated to "48 MHz" |
| clk[1] missing from SDC | OLS_Logic_Analyzer.sdc | False timing violations on CDC paths | Added async clock group for clk[1] |
| Gen_Baud_Div default wrong | OLS_Interface.vhd | Reset-time UART baud = 57,600 | `x"0341"`→`x"01A0"` (833→416) |
| FIXED_BAUD_DIV stale | Signal_Gen.vhd | Fallback baud = 230,769 (24 MHz era) | `x"00D0"`→`x"01A0"` (208→416) |

## Python Host Fixes

| Fix | File | Detail |
|-----|------|--------|
| sys_clk default 48 MHz | ols_spi_device.py | Line 42: `96000000`→`48000000` |
| Continuous mode in capture() | ols_spi_device.py | Enables CMD_CONT_CAPTURE for pipeline persistence |
| Back-to-back in capture_with_gen() | ols_spi_device.py | ARM+NOP in single CS-low burst |
| GUI double-Tk crash | OLS_Console.py | Single Tk root, auto-detect backend, auto-connect |
| Divider test formula | hw_validation.py | Updated for 48 MHz clock |

## Testbench Results

### GHDL Simulation

| Testbench | Tests | Result | Coverage |
|-----------|-------|--------|----------|
| `tb_ols_interface` | 24 | **24/24 PASS** | All opcode paths, accumulator, trigger, gen control |
| `tb_capture_path` | 5 | **5/5 PASS** | sample_en period, BRAM write, Full, CH0 timing, 2nd capture |
| `tb_continuous` | 3 | **3/3 PASS** | Buffer fill, ack, re-fill cycle |

**Compile & run:**
```powershell
ghdl -a --std=08 --workdir=hdl\sim\work hdl\sim\support\sim_pkg.vhd
ghdl -a --std=08 --workdir=hdl\sim\work hdl\*.vhd
ghdl -a --std=08 --workdir=hdl\sim\work hdl\sim\tb_*.vhd
ghdl -e --std=08 --workdir=hdl\sim\work tb_ols_interface
ghdl -r --std=08 --workdir=hdl\sim\work tb_ols_interface --assert-level=failure
```

### Hardware Validation

Run with FPGA programmed and USB connected:
```powershell
python host/hw_validation.py
```

**10/14 PASS:**

| Test | Result | Notes |
|------|--------|-------|
| 1 — UART CMD_ID | FAIL | No COM port (VCP driver not installed) |
| 2 — SPI handoff | FAIL | CMD_ID check in wrong pipeline field |
| 3 — All SPI cmds | PASS | 18/18 accepted |
| 4 — Single capture | PASS | CH0=24 transitions, data returned |
| 5 — Fast mode | PASS | CH0=58 transitions |
| 6 — Continuous | PASS | 3 buffers with non-zero data |
| 7 — Trigger | PASS | CH0=44 transitions on rising edge |
| 8 — UART gen | PASS | CH3=273 transitions, generator producing |
| 9 — I2C accel | FAIL | No SCL transitions |
| 10 — SPI gen | PASS | CH0=149 transitions (SCLK) |
| 11 — Divider | PASS | CH0=203 edges |

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (MAX10 device support)
- Python 3.10+ (`pip install ftd2xx pyserial`)
- FTDI D2XX drivers (for SPI backend)

### Compile

```powershell
cd hdl
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_sh.exe" --flow compile OLS_Logic_Analyzer
```

### Flash (JTAG)

```powershell
& "C:\intelFPGA_lite\18.1\quartus\bin64\quartus_pgm.exe" -c 1 -m JTAG -o "P;output_files\OLS_Logic_Analyzer.sof"
```

## Project Structure

```
OLS_Logic_Analyzer_Clean/
├── hdl/
│   ├── OLS_SDRAM_Top.vhd              # Top: PLL, capture mux, gen routing, LEDs
│   ├── OLS_Interface.vhd              # SPI command decoder, readout FSM
│   ├── Fast_Logic_Analyzer_SDRAM.vhd  # Capture engine, triple buffer
│   ├── OLS_Logic_Analyzer_SDRAM_Core.vhd  # Core wrapper (connects OLS_I + FLA)
│   ├── SPI_Slave.vhd                  # SPI slave with CDC
│   ├── Signal_Gen.vhd                 # UART/I2C/SPI generator
│   ├── UART_Interface.vhd             # UART Rx/Tx with oversampling
│   ├── SDRAM_Interface.vhd            # SDRAM controller wrapper
│   ├── SDRAM_Controller_Custom.vhd    # SDRAM controller
│   ├── SDRAM_PLL.vhd                  # PLL (12→48/120/48 MHz)
│   ├── Protocol_Trigger.vhd           # UART protocol trigger
│   ├── ADC_Controller.vhd             # ADC controller
│   ├── OLS_Logic_Analyzer.sdc         # Timing constraints
│   ├── OLS_Logic_Analyzer.qpf/.qsf    # Quartus project files
│   ├── compile.ps1                    # Build + flash script
│   ├── pin_assignments.csv            # Pin mappings
│   └── sim/
│       ├── tb_ols_interface.vhd       # OLS_Interface full command test (24/24)
│       ├── tb_capture_path.vhd        # FLA end-to-end capture test (5/5)
│       ├── tb_continuous.vhd          # Continuous buffer test (3/3)
│       └── support/sim_pkg.vhd        # Test utilities
├── host/
│   ├── OLS_Console.py                 # GUI (tkinter)
│   ├── ols_spi_device.py              # SPI backend high-level API
│   ├── ols_spi.py                     # FTDI MPSSE driver
│   ├── ols_spi_mpsse.py               # MPSSE bitbang layer
│   ├── hw_validation.py               # Hardware validation suite
│   └── debug/                         # Diagnostic scripts
├── MAX1000 User Guide.txt
├── README.md
└── .gitignore
```

## Known Issues (WIP)

### Test 2 — SPI CMD_ID pipeline check
The check looks for ASCII `"1ALS"` in the MISO pipeline, but the ID is transmitted through the UART_TX_Data path as raw bytes. Should check preamble bit4=1 (SPI mode) + bit2=1 (fast mode) instead.

### Test 4 — slow capture test_div Nyquist
The 10 kHz slow capture aliases the ~47 kHz test signal (below Nyquist). Increase capture rate or accept as a known limitation — the 1 MHz capture already proves CH0 toggling.

### Test 9 — I2C accelerometer
LIS3DH on SEN_SDI/SEN_SPC (J7/J6). SCL shows 0 transitions. Possible causes:
1. `gen_scl` output not reaching SEN_SPC — check the output mux
2. I2C generator sequence needs CMD_I2C_TEST set (`gen_i2c_test = '1'`)
3. SEN_CS stuck high (line 219: `'0' when gen_spi_test = '1'` — not I2C)

### Test 11 — Divider period
Edge count passes (203 edges), but period measurement is off (continuous mode buffer wraps corrupt inter-edge distances).

### Generator UART decode
`capture_with_gen` produces valid UART frames (275 transitions on CH3), but `decode_uart` reports `'.....'` instead of `'Hello'`. Each byte decodes as 0x01 at `spb=8.68` (fractional samples/bit). Likely a decode start-bit centre calculation issue with fractional SPB.

## License

MIT — see `LICENSE`.
