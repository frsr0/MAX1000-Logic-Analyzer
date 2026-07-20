# Software feature capability matrix

This matrix describes what can be exercised without changing the FPGA image or
board. “Post-capture” means the feature consumes an immutable saved waveform.

| Capability | Mock device | MAX1000 hardware | Post-capture only | Notes |
| --- | --- | --- | --- | --- |
| Digital capture, rolling, compression | Yes | Yes, subject to board limits | No | Existing capture paths are unchanged. |
| Analog/mixed capture | Synthetic profiles | Existing ADC profiles only | Analysis works on saved analog data | No claim of extra analog bandwidth. |
| UART / RS-485 / I²C / SPI generation | Loopback | UART/RS-485/I²C; SPI requires send + capture | No | Real SPI loopback is MOSI/SCLK only. |
| Raw two-output Bit Banger | Loopback | Standalone FPGA Bit Banger | Preview and analysis | 1024-symbol FIFO; open-drain readback is host-emulated. Software templates include RS-485, SPI modes, I²C, 1-Wire, PWM, SWD, and fault injection. |
| Protocol decoders | Yes | Yes on captured channels | Yes | UART, I²C, SPI, LIN, MIDI, PS/2, I²S, CAN, JTAG, HDLC, SMBus/PMBus, IR, and others. |
| Software triggers/search | Yes | Search saved captures | Yes | Hardware execution is shown separately in the trigger matrix. |
| Filtering, thresholds, spectrum, spectrogram, correlation, eye diagrams, timing suspects | Yes | Yes on available analog/digital data | Yes | Derived channels never replace raw samples. |
| Reports and exports | Yes | Yes | No | JSON, CSV, VCD, PulseView-compatible VCD, NPZ, HTML report, and dependency-free PDF report. |
| CAN electrical connectivity | No | No without external transceiver | Decoder only | Logic-level CAN decode is supported; the board is not a CAN transceiver. |

## Verification baseline

Baseline branch: `codex/software-feature-roadmap`.

Required commands:

```text
cd backend; python -m pytest app/tests -q
cd host; python -m pytest -q
cd frontend; npm run typecheck; npm run build
```

Host tests are required when host-driver code changes. Hardware smoke tests
remain conditional on a connected and safely mapped MAX1000 device.
