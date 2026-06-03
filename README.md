# OLS Logic Analyzer

Open-source logic analyzer for the MAX1000 board (Intel MAX10 FPGA + SDRAM).
Supports both UART and **SPI (FTDI MPSSE)** host interfaces.

## Features

- **8 logic channels**, up to **24 MHz** sample rate
- **Deep capture**: up to 500,000 samples via SDRAM
- **Fast capture**: 1,024 samples via BRAM (no SDRAM needed)
- **Rolling / continuous capture**: triple-buffer scheme with prefetch handoff
- **Protocol trigger**: arm on UART byte match at configurable baud
- **Edge trigger**: rising/falling edge on any channel
- **Generator**: UART / I2C / Modbus output on any GPIO pin
- **Protocol decode**: UART, I2C, Modbus with waveform annotation
- **Dual host interface**: UART (FTDI serial) or **SPI (FTDI MPSSE)**

## Host Interfaces

### UART Backend (legacy)

Uses the FTDI virtual COM port. Slower but simpler — no special driver needed.

```
python host/OLS_Console.py
```

### SPI Backend (recommended)

Uses FTDI MPSSE (Channel B) for higher throughput and lower latency.
Requires `ftd2xx` library (FTDI D2XX drivers).

```python
from ols_spi_device import OLSDeviceSPI

dev = OLSDeviceSPI(sys_clk_hz=24000000)
dev.open()
data = dev.capture(rate_hz=1000000, nsamples=5000)

dev.send_uart(b'Hello', baud=115200, tx_pin=3)
data = dev.capture_with_gen(rate_hz=1000000, nsamples=5000)
```

| Feature | UART | SPI |
|---------|------|-----|
| Throughput | ~1 Mbps | **~12 Mbps** |
| Latency | ~9ms per command | **~3ms per command** |
| Generator | Works | **Works** |
| Driver | pyserial | ftd2xx (D2XX) |

## FPGA Firmware Fixes (current state)

### PLL: 24 MHz system clock (`PLL_MULT=2`)

The original 48 MHz clock (`PLL_MULT=4`) placed the design at **-33 ns setup slack**
(Slow 85°C model), causing routing-dependent failures on every recompile.

Changed to 24 MHz (`clk0_multiply_by => 2`), improving timing to **-12 ns slack**.
The SDRAM clock (PLL c2) remains at 48 MHz with -90° phase shift.

**Timing (24 MHz):**

| Model | Setup slack | Hold slack |
|-------|-------------|------------|
| Slow 85°C | -12.4 ns | 0.31 ns |
| Slow 0°C | -11.2 ns | 0.28 ns |
| Fast 0°C | **+22.2 ns** | 0.10 ns |

### `bram_post_cnt` range extended

In `Fast_Logic_Analyzer_SDRAM.vhd`, the variable `bram_post_cnt` had range
`0 to BRAM_SIZE` (1024) but was compared against `Samples / sub_steps`
(e.g., 2500 for RCOUNT=5000). The counter could never reach the target, so
`full_i` was never asserted, and the capture never completed.

Fixed range to `0 to 15000000`.

### SPI TX Handshake (`effective_TX_Busy`)

The original `effective_TX_Busy` handshake (Bug 2 fix) cleared the busy flag
one cycle after `UART_TX_Enable`, making the byte-send FSM (Thread31) run
~5× faster than the SPI byte rate. This caused `TX_Data` to be overwritten
2-3 times per SPI byte, corrupting the readout.

**Reverted** to the original synchronisation: `effective_TX_Busy` clears on
`SPI_RX_Valid` (when a new SPI byte is received). This ties the byte-send
FSM to the SPI rate, ensuring `TX_Data` stays stable until the SPI slave
latches it.

### SPI Mode Switch (`CMD_IFACE_MODE = 0xAB`)

The FPGA defaults to UART mode after reset (`interface_mode_i = '0'`).
Without switching to SPI mode, the data readout state machine (Thread23)
never activates because it requires `(Full='1' AND (interface_mode_i='1' OR Run='1'))`.

The `reset()` method now sends command `0xAB` with `data(0)=1` to set SPI mode:

```python
self._long(0xAB, 1)
```

### Capture-with-Gen Batch Fix

The `capture_with_gen` method sent ARM as a single-byte `0x31, 0, 0` transaction.
The ARM command sets `Thread44 = 4` (multi-byte mode), and the immediately
following `GEN_STRT` byte was consumed as data for the ARM — **GEN_STRT never
executed**, so the generator never started.

Fixed by sending both ARM and GEN_STRT as proper **5-byte commands** with
`0x31, 4, 0`:

```python
# ARM with 0x11 padding
d.write(bytes([0x31, 4, 0]))
d.write(bytes([CMD_ARM, 0x11, 0x11, 0x11, 0x11]))
# GEN_STRT as 5-byte command  
d.write(bytes([0x31, 4, 0]))
d.write(bytes([CMD_GEN_STRT, 0x00, 0x00, 0x00, 0x00]))
```

### Chained Read Alignment

The `chained_read` method had a 1-byte alignment mismatch: the SPI preamble
(status byte) was included as the first data byte, shifting all sample data
by 1 byte. Fixed by reading an extra byte and skipping both the GPIO readback
and the preamble.

## Generator Status

The signal generator can produce UART output on any GPIO pin:

| Test | CH0 | CH3 | Status |
|------|-----|-----|--------|
| `capture()` — basic | 470 tr (test counter) | N/A | **PASS** |
| `send_uart()` + `capture_with_gen()` | 468 tr | **424 tr** | **PASS** |

The decoded UART data shows partial correctness (`'e'`, `'l'` from `'Hello'`),
with some character errors due to endianness differences between the Python
`struct.pack` and the VHDL multi-byte data register. This is a known issue for
later review.

## Build

### Prerequisites

- Quartus Prime Lite 18.1 (with MAX 10 device support)
- Python 3.10+ (`pip install ftd2xx pyserial`)

### Compile & Flash

```powershell
cd vhdplus
.\compile.ps1 -Flash
```

The script generates the wrapper VHDL from `pin_assignments.csv`, then compiles
and programs via JTAG.

### Python host tests

```powershell
python host/test_final_fix.py
python host/test_diag.py
```

## Hardware Requirements

- **MAX1000** board (10M08SAU169C8G, 8 MB SDRAM)
- USB cable (JTAG programming + SPI/UART)
- FTDI D2XX drivers for SPI backend
- Optional: signals to probe on GPIO[0..7]

### Pin Assignments

| Signal | MAX1000 Pin | Description |
|--------|-------------|-------------|
| CLK | H6 | 12 MHz system clock |
| UART_RX | A4 | USB-UART RX (FTDI) |
| UART_TX / SPI_MOSI | B4 | Shared: UART TX or SPI MOSI |
| SPI_CS | AG2 | SPI chip select |
| SPI_MISO | AF1 | SPI MISO |
| SPI_SCK | AG1 | SPI clock |
| GPIO[0..7] | M3, L3, M2, M1, N3, N2, K2, K1 | Logic analyzer channels |
| sdram_* | See `pin_assignments.csv` | SDRAM interface |
| LED[0..7] | A8–D8 | Status LEDs |

Full pin assignments in `vhdplus/pin_assignments.csv` — importable in VHDPlus
IDE Pin Planner.

## Project Structure

```
OLS_Logic_Analyzer/
├── src/                    # VHDL source files
│   ├── OLS_SDRAM_Top.vhd              # Top-level, PLL, generator, capture mux
│   ├── OLS_Interface.vhd              # Command processor + readout FSM
│   ├── Fast_Logic_Analyzer_SDRAM.vhd  # Capture engine + triple-buffer
│   ├── Signal_Gen.vhd                 # UART/I2C/Modbus generator
│   ├── SPI_Slave.vhd                  # SPI slave with reload handshake
│   └── ...                            # SDRAM interface, UART, protocol trigger
├── host/                  # Python host applications
│   ├── ols_spi.py                     # FTDI MPSSE SPI driver
│   ├── ols_spi_device.py              # High-level SPI device backend
│   ├── OLS_Console.py                 # GUI console (UART + shared logic)
│   ├── test_final_fix.py              # Generator + capture integration test
│   └── ...
├── vhdplus/               # VHDPlus IDE project + Quartus build
│   ├── compile.ps1                    # Build + flash script
│   ├── pin_assignments.csv
│   └── ...
├── sim/                   # GHDL testbenches
│   └── ...
├── FIXES_NOTE.txt         # Fixes applied vs MAX1000-fixed reference
└── README.md
```

## Known Issues

### Readout byte alignment

The Thread23 byte-send FSM outputs bytes in the order `[pad, sample, pad, pad]`
for each 4-byte sample group, instead of the expected `[sample, pad, pad, pad]`.
The `chained_read` method applies a software byte-swap to correct this.
The root cause is an endianness mismatch between the Python `struct.pack('<I', ...)`
(little-endian) and the VHDL multi-byte data register (big-endian).

### Generator decode not 100% correct

The UART decoder finds `'e'`, `'l'` from `'Hello'` but with interspersed
wrong characters. This is likely the same endianness issue affecting the
baud rate divider and other configuration values.

### Timing violations at 48 MHz

The original 48 MHz PLL configuration has -33 ns setup slack at worst case.
While the Fast 0°C model shows +1 ns slack (marginal pass), reliability
varies between devices and temperatures. The current 24 MHz configuration
is robust.

### UART bandwidth bottleneck

The FTDI UART at 921600 baud (upgraded to 12 Mbps) limits streaming throughput.
For gap-free continuous capture above ~1.2 MHz, a faster readout interface is
needed. The SPI backend partially addresses this with lower latency per command.

## License

MIT — see `LICENSE` for details.
