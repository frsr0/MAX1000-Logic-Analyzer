# OLS Logic Analyzer

## Features

- **8 logic channels**, up to **48 MHz** sample rate
- **Deep capture**: up to 500,000 samples via SDRAM
- **Fast capture**: 1,024 samples via BRAM (48 MHz, no SDRAM needed)
- **Rolling capture**: continuous acquisition, PC-buffered, configurable buffer size
- **Continuous dual/triple-buffer mode**: gap-free capture using `CMD_CONT_CAPTURE`
  — FPGA alternates between 3 SDRAM buffers, seamless handoff with prefetch
- **Protocol trigger**: arm on UART byte match at configurable baud rate
- **Edge trigger**: rising/falling edge on any channel
- **Generator**: UART / I2C / Modbus output on any GPIO pin
- **Protocol decode**: UART, I2C, Modbus with annotation on waveform
- **Data logger**: CSV logging with trigger events
- **Raw mode**: up to 13× faster readout (1 byte/sample instead of 4)
- **Export**: OLS format, Sigrok SR format, clipboard
- **Live waveform**: point-by-point drawing during capture with scrolling
- **Measurement markers**: click to place M1/M2, delta time shown

## Architecture: Continuous Capture

The rolling capture uses a **dual/triple-buffer** scheme in SDRAM with **prefetch handoff**:

1. FPGA fills buffer A, then B, then C (3 buffers, each ~⅓ of total sample count)
2. Backpressure `Full` only asserted when **all 3** buffers are full
3. Host reads a filled buffer via UART while the FPGA continues filling the others
4. On the last word of a buffer, the next buffer's first address is prefetched (SDRAM read
   happens in parallel with the last UART byte transmission)
5. Buffer handoff completes in **1 cycle** (a pulse), no re-arm delay

## Hardware Requirements

- **MAX1000** board (10M08SAU169C8G, 8 MB SDRAM)
- USB cable (JTAG programming + UART serial)
- Optional: signals to probe on GPIO[0..7]

### Pin Assignments

| Signal | MAX1000 Pin | Description |
|--------|-------------|-------------|
| CLK | H6 | 12 MHz system clock |
| UART_RX | A4 | USB-UART RX (FTDI) |
| UART_TX | B4 | USB-UART TX (FTDI) |
| GPIO[0..7] | M3, L3, M2, M1, N3, N2, K2, K1 | Logic analyzer channels |
| sdram_* | See `vhdplus/pin_assignments.csv` | SDRAM interface |
| LED[0..7] | A8–D8 | Status LEDs |
| SEN_SDI/SEN_SPC | J7, J6 | Accelerometer I2C (LIS3DH) |

Full pin assignments in `vhdplus/pin_assignments.csv` — importable in VHDPlus IDE Pin Planner.

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (with MAX 10 device support)
- VHDPlus IDE (optional, for editing `.vhdp` files)

### Compile & Flash

Run the compile script from PowerShell:

```powershell
cd vhdplus
.\compile.ps1 -Flash
```

The script will:
1. Read pin assignments from `pin_assignments.csv`
2. Regenerate the wrapper VHDL with matching `chip_pin` attributes
3. Create a Quartus project with the wrapper as top-level entity
4. Compile through Quartus
5. Program the MAX1000 via JTAG

To compile without flashing: `.\compile.ps1`

### Customising pins in VHDPlus IDE

1. Open `OLS_Logic_Analyzer.vhdpproj` in VHDPlus IDE
2. **Tools → Pin Planner** — edit pin assignments visually
3. Close the IDE (changes save to `pin_assignments.csv`)
4. Run `.\compile.ps1 -Flash` — the script picks up the new assignments

> The VHDPlus IDE's own compile step regenerates the Quartus project with the VHDP-generated
> entity as top-level. For a full-featured bitstream (including signal generator, LEDs,
> accelerometer), always use `compile.ps1` instead of compiling from the IDE.

## Simulation (VHDL Testbenches)

Requires [GHDL](https://github.com/ghdl/ghdl) with `--std=08` support.

### Run all testbenches

```powershell
cd sim
.\compile.ps1
.\run.ps1
```

Tests the `Fast_Logic_Analyzer_SDRAM` and `OLS_Interface` modules with `Sim => true`:

### FLA testbench (`tb_double_buffer`)

| Test | What it verifies |
|------|-----------------|
| `tc_single_buffer` | Single-buffer capture (legacy): Full at correct count, readback valid |
| `tc_buffer_swap` | Triple-buffer: A fills → B → C, backpressure only when all 3 full |
| `tc_edge_timing` | Known square wave on CH0 — edge spacing uniform, **0 gaps** |
| `tc_read_while_write` | Read buffer A while B/C fill, ack A, read B — independent read/write |

### OLS Interface testbench (`tb_interface_cont`)

| Test | What it verifies |
|------|-----------------|
| `tc_cont_cmd` | `CMD_CONT_CAPTURE` (0xAA) sets `Continuous_Mode`, reset clears it |
| `tc_cont_reset` | Reset stops continuous mode, `Continuous_Mode` goes low |

### Triple-buffer testbench (`tb_pipelined_handoff`)

| Test | What it verifies |
|------|-----------------|
| `tc_prefetch` | Prefetch triggers on last address of buffer, next buffer read starts during last UART byte |
| `tc_triple_fill` | Buffers fill A→B→C→A cyclically, backpressure only when all 3 full |

### UART baud rate testbench (`tb_uart_baud`)

| Test | What it verifies |
|------|-----------------|
| `tc_baud_*` | Loopback TX→RX at rates from 921600 to 48 Mbps (parameterized by `BAUD` generic) |

### Test results (8/8 PASS)

| Testbench | Test | Result |
|-----------|------|--------|
| `tb_double_buffer` | tc_single_buffer | PASS |
| `tb_double_buffer` | tc_buffer_swap | PASS |
| `tb_double_buffer` | tc_edge_timing | PASS |
| `tb_double_buffer` | tc_read_while_write | PASS |
| `tb_interface_cont` | tc_cont_cmd | PASS |
| `tb_interface_cont` | tc_cont_reset | PASS |
| `tb_pipelined_handoff` | tc_prefetch | PASS |
| `tb_pipelined_handoff` | tc_triple_fill | PASS |

## Hardware Tests

Run diagnostics against a programmed device:

```powershell
python host/test_diag.py --port COM5
python host/test_edge_timing.py --port COM5 --continuous --cycles 30
```

| Test | Result |
|------|--------|
| `host/test_diag.py` | PASS (48 MHz divider fix, auto-port) |
| `host/test_edge_timing.py --continuous` | PASS — 0 gaps at 750 kHz, std=0.000 |
| `host/test_edge_timing.py --legacy` | PASS — detects gaps (ARM-loop baseline) |
| `host/test_cont_capture.py` | PASS (continuous mode end-to-end) |

## Known Limitations

### UART bandwidth bottleneck

The FTDI UART at 921600 baud (upgraded to 12 Mbps) limits how much data can be
streamed to the PC. The continuous triple-buffer mode is gap-free only when the
UART readout is faster than the FPGA capture:

| UART baud | Max gap-free sample rate | Readout time (per 16666-sample buffer) |
|-----------|--------------------------|---------------------------------------|
| 921,600   | **92 kHz**               | 181 ms |
| 12 Mbps   | **1.2 MHz**              | 13.9 ms |

Above these rates, the FPGA fills all 3 SDRAM buffers before the UART finishes
reading one. Backpressure stalls the capture until a buffer is freed, creating
periodic gaps in the data stream. This is a hardware limitation of the FTDI UART
interface — the FPGA logic itself is verified gap-free at any rate.

To go beyond 1.2 MHz without gaps, a faster readout interface is needed
(USB 2.0+ HS, Ethernet, or direct memory access).

### UART OS_Rate vs baud rate

The UART receiver uses `OS_Rate=13` oversampling. At baud rates > 3.69 MHz
(48 MHz / 13), the oversampling counter underflows (range `0 TO -1`). The
`UART_Interface.vhd` has a dynamic `actual_os_rate` that clamps to
`min(OS_Rate, CLK_Frequency / Baud_Rate)`, minimum 1. At 12 Mbps the effective
oversampling is 4×, at 48 Mbps it's 1×. The UART still functions, but
reliability at the highest rates depends on signal quality.

### Sub-step interleaving

With 8 channels, each 16-bit SDRAM word contains two 8-channel sub-samples
taken `Rate_Div` clock cycles apart. Raw mode sends these as consecutive bytes.
When the input signal toggles between sub-steps, it creates an extra edge in the
raw data (spacing of 1 byte). Decimation (taking every other byte) removes this
artifact. The edge timing test (`test_edge_timing.py`) handles this automatically.

### SDRAM read latency at buffer handoff

At triple-buffer handoff, the first address read of the new buffer has a
~30-cycle SDRAM read latency. With prefetch, this is hidden behind the last
UART byte transmission. A single 1-sample artifact remains (spacing = 4 vs
expected 8 at 1.5 MHz decimated). This is the minimum achievable with a
single-port SDRAM read. Eliminating it entirely would require dual-port RAM or
a different architecture.

### FPGA state after failed continuous capture

If `CMD_CONT_CAPTURE` is interrupted (USB disconnect, timeout), the FPGA may
be left with `Full` high and the readout state machine in an intermediate state.
Short-resetting is not always enough. **Full power cycle** (unplug USB) or
**JTAG re-programming** recovers the device. The `rolling_capture` `finally`
block sends `CMD_RESET` to mitigate this.

## Install the Python host app

Requirements: **Python 3.10+**

```bash
pip install pyserial
```

## Run

```bash
python host/OLS_Console.py
```

The app scans for the MAX1000 on all COM ports and connects automatically.

### CLI mode

```bash
python host/OLS_Console.py --cli capture --rate 1000000 --samples 5000
python host/OLS_Console.py --cli decode --input capture.raw --protocol uart
python host/OLS_Console.py --cli send --data "Hello" --baud 115200
```

## Project Structure

```
OLS_Logic_Analyzer/
├── src/              # VHDL source files
│   ├── OLS_SDRAM_Top.vhd
│   ├── OLS_Logic_Analyzer_SDRAM_Core.vhd
│   ├── Fast_Logic_Analyzer_SDRAM.vhd    # Capture engine + triple-buffer
│   ├── OLS_Interface.vhd                # UART cmd processor + readout FSM
│   ├── UART_Interface.vhd               # UART TX/RX with dynamic OS_Rate
│   ├── SDRAM_Interface.vhd
│   ├── SDRAM_Controller_Custom.vhd
│   ├── Protocol_Trigger.vhd
│   └── Signal_Gen.vhd
├── host/             # Python host application
│   ├── OLS_Console.py
│   ├── test_diag.py                     # Hardware diagnostic
│   ├── test_edge_timing.py              # Edge timing consistency test
│   └── test_cont_capture.py             # Continuous capture hardware test
├── vhdplus/          # VHDPlus project + Quartus build
│   ├── OLS_Logic_Analyzer.vhdpproj
│   ├── OLS_Logic_Analyzer.vhdp
│   ├── OLS_Logic_Analyzer_wrapper.vhd   # Auto-generated pin wrapper
│   ├── pin_assignments.csv
│   └── compile.ps1                      # Build + flash script
├── sim/              # GHDL testbench + simulation
│   ├── compile.ps1
│   ├── run.ps1
│   ├── tb_double_buffer.vhd            # FLA buffer tests
│   ├── tb_interface_cont.vhd           # OLS continuous-mode tests
│   ├── tb_pipelined_handoff.vhd        # Prefetch + triple-buffer tests
│   ├── tb_uart_baud.vhd                # UART baud rate loopback
│   └── stubs/                          # PLL model, SDRAM controller stub
├── README.md
├── LICENSE
└── requirements.txt
```

## License

MIT — see `LICENSE` for details.
