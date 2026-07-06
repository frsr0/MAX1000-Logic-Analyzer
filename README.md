# OLS Logic Analyzer - MAX1000

OLS is a mixed-signal logic analyzer and signal generator for the Arrow MAX1000
board built around an Intel MAX 10 10M08 device. The design keeps the full
digital capture path, mixed-signal capture, continuous ring readback, protocol
triggers, and the signal generator in one image.

The host stack has two entry points:
- The classic tkinter app and CLI in `host/`
- The optional browser-based front end described in [WEBAPP.md](WEBAPP.md)

## Current Build

The checked-in speed build is `FAST_SPEED => true` with Quartus seed `33`.
It currently closes the slow 85C timing model with:
- `clk[1]` setup: `+0.023 ns`
- `clk[2]` setup: `+0.197 ns`
- `clk[0]` setup: `+1.167 ns`

That build fits the 10M08 at about 95 percent logic utilization
(`7,664 / 8,064` LEs).

To rebuild it:

```powershell
cd hdl\proj
.\compile.ps1 -Seed 33
```

## What It Does

- 16-channel digital capture from a 26-entry programmable pin pool
- Narrow packed digital capture at 200 MHz for one selected channel
- Mixed-signal capture with MAX10 ADC scan modes
- Continuous SDRAM ring capture with oldest/newest/overrun metadata
- Raw, delta, and RLE readback codecs on the host side
- UART, I2C, and SPI signal generation
- Edge triggers, protocol triggers, and debug CH0 PWM
- Host-side digital glitch filtering and waveform decode/annotation

## Clock And Memory Summary

Speed mode uses one PLL with these main domains:

| Output | Frequency | Purpose |
|--------|-----------|---------|
| `c0 -> sys_clk` | 100.2 MHz | SPI packet protocol, generator, LED control |
| `c1 -> fast_clk` | 200.4 MHz | Sample capture and input packing |
| `c2 -> sdram_core_clk` | 167 MHz | SDRAM controller, write pump, readout |
| `c4 -> sdram_chip_clk` | 167 MHz, -1.5 ns | Forwarded SDRAM device clock |

Main storage paths:
- 1,024-word BRAM pre-trigger buffer
- 4,096-word async FIFO between capture and SDRAM
- 64 Mbit SDRAM for deep capture and rolling ring capture

## Readback And Capture Modes

The current build keeps the full feature set:
- Digital-only capture
- Narrow packed digital capture
- Mixed digital plus ADC capture
- High-speed analog capture
- Maximum analog scan mode

The host readback codec matrix supports:
- `raw`
- `delta`
- `rle`

Mixed-signal frame compression is separate from the raw SPI wire codecs.

## Hardware Validation

Run the hardware suite from `host/`:

```powershell
cd host
python -m app.hw_validation
```

That suite covers the SPI protocol, capture modes, generator modes, ring
capture, compression codecs, and the main regression paths around reset,
abort, and continuous readout.

## Repository Layout

- `hdl/` - RTL, Quartus project, timing constraints, simulation
- `host/` - Python app, driver stack, and hardware validation
- `docs/` - Design notes and mode plans
- `WEBAPP.md` - Browser-hosted UI notes

## Related Docs

- [HDL README](hdl/README.md)
- [Host README](host/README.md)
- [Timing summary](TIMING_REPORT_SUMMARY.md)

## License

MIT
