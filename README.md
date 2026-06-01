# OLS Logic Analyzer

## Features

- **8 logic channels**, up to **48 MHz** sample rate
- **Deep capture**: up to 500,000 samples via SDRAM
- **Fast capture**: 1,024 samples via BRAM (48 MHz, no SDRAM needed)
- **Rolling capture**: continuous acquisition, PC-buffered, configurable buffer size
- **Protocol trigger**: arm on UART byte match at configurable baud rate
- **Edge trigger**: rising/falling edge on any channel
- **Generator**: UART / I2C / Modbus output on any GPIO pin
- **Protocol decode**: UART, I2C, Modbus with annotation on waveform
- **Data logger**: CSV logging with trigger events
- **Raw mode**: 4× faster readout (1 byte/sample instead of 4)
- **Export**: OLS format, Sigrok SR format, clipboard
- **Live waveform**: point-by-point drawing during capture with scrolling
- **Measurement markers**: click to place M1/M2, delta time shown

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

### Test status

All hardware tests pass (run against the programmed device):

| Test | Result |
|------|--------|
| `test_gpio_only.py` | PASS |
| `test_fix.py` | PASS |
| `test_final.py` | PASS |
| `test_no_reset.py` | PASS |
| `test_multi.py` | PASS |
| `test_immediate.py` | PASS |

(`test_diag.py` has a pre-existing divider mismatch — fails identically on all builds.)

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

## Project Structure

```
OLS_Logic_Analyzer/
├── src/              # VHDL source files
│   ├── OLS_SDRAM_Top.vhd
│   ├── OLS_Logic_Analyzer_SDRAM_Core.vhd
│   ├── Fast_Logic_Analyzer_SDRAM.vhd
│   ├── OLS_Interface.vhd
│   ├── UART_Interface.vhd
│   ├── SDRAM_Interface.vhd
│   ├── SDRAM_Controller_Custom.vhd
│   ├── Protocol_Trigger.vhd
│   └── Signal_Gen.vhd
├── host/             # Python host application
│   └── OLS_Console.py
├── vhdplus/          # VHDPlus project files
│   ├── OLS_Logic_Analyzer.vhdpproj
│   ├── OLS_Logic_Analyzer.vhdp
│   ├── OLS_Logic_Analyzer_wrapper.vhd
│   ├── pin_assignments.csv
│   └── compile.ps1
├── README.md
├── LICENSE
└── requirements.txt
```

## License

MIT — see `LICENSE` for details.
