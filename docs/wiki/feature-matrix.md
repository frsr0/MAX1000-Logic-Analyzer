# Feature and Coverage Matrix

This page is the cross-layer index for the implemented product. It identifies
where a feature lives, how it is exposed, and what evidence supports it. A
feature marked **HW** has been exercised on the physical MAX1000; **SW** means
software/mock or simulation coverage; **limited** means the feature exists but
has an explicit scope boundary.

## Acquisition and signal processing

| Feature | Frontend / backend surface | HDL / host implementation | Evidence and scope |
|---|---|---|---|
| Digital capture | Capture controls, CaptureManager | `Fast_Logic_Analyzer_SDRAM`, `OLSDeviceSPI` | **HW**; 16 channels, up to 200.4 MHz |
| Deep SDRAM capture | Depth selector, capture strategies | SDRAM controller and write pump | **HW**; 4,194,304 × 16-bit words |
| BRAM fast capture | Fast/narrow strategy | 1,024-word BRAM path | **HW** |
| Continuous/ring capture | Live capture and session streaming | Triple-buffer ring path | **HW**; lifecycle and recovery checks |
| Narrow packed capture | Narrow mode control and decoder | 16 samples/channel packed into one word | **HW** on 2026-07-22 image; `tb_fast_analyzer` narrow regression is **SIM** |
| Analog-fast | Analog-fast mode and analog waveform | One MAX10 ADC lane | **HW**; ADC1/AIN3 profile and physical analog checks |
| Analog-all | Maximum-analog mode | Four ADC inputs | **HW**; four balanced channels |
| Mixed-signal capture | Mixed mode and analog/digital panels | `mso_capture`, analog packer, stream mux | **HW**; 500,000 packed words |
| Digital RLE | Readback codec selection | Full-word/direct RLE | **HW**; bit-exact matrix through 200.4 MHz |
| Packed `delta_rle` | Readback codec selection | Delta calculator + digital RLE + MSO stream | **HW**; lossless finite matrix and live characterization |
| Raw readback | Session waveform transport | SPI block read path | **HW** |
| Triggering | Trigger panel and backend trigger service | UART byte hardware trigger plus software search | **HW/SW**; protocol-trigger scope is UART byte matching |
| Measurements | Measurements panel/API | Digital, analog, bus measurement services | **SW** and frontend E2E coverage |
| Waveform LOD/downsampling | Canvas viewer | WaveformStore, worker, MSAW/LOD services | **SW**; transport and zoom performance coverage |

## Protocol decoding and generation

| Protocol / feature | Decode support | Generate / hardware support | Evidence and scope |
|---|---|---|---|
| UART | UART decoder, parity/framing/break | Bit Banger UART | **HW** loopback and decoder tests |
| I²C | START/STOP, address, ACK/NACK, 7/10-bit | Master write/read Bit Banger | **HW** via jumper and LIS3DH |
| SPI | Configurable CPOL/CPHA, MOSI/MISO/CS | SPI mode 0 and mode 3, CS/MISO routes | **HW** loopback and LIS3DH mode-3 reads |
| RS-485 | Half-duplex decoder | UART-compatible generator plus DE route | **HW** loopback and generator matrix |
| SWD | SWD packet decoder | SWD line-reset/turnaround/ACK generator | **SW** protocol path; no external SWD target attached |
| Modbus RTU | Stacked UART decoder with CRC/function parsing | MIL/generator workflows | **SW/HW** where a loopback or device is available |
| Parallel bus | Clocked multi-channel decoder | Not a dedicated generator protocol | **SW** decoder coverage |
| 1-Wire | Reset, presence, and byte decoder | No dedicated hardware generator | **SW** decoder coverage |
| PWM | Pulse/frequency/duty decoder | Bit Engine/Bit Banger PWM templates, optional FPGA repeat | **HW** loopback and repeat path on 2026-07-22 image |
| Raw Bit Banger | Raw waveform capture through normal channels | Two-output 2-bit symbol engine | **HW** through loopback and peripheral routes |
| LIS3DH accelerometer | Standard I²C decoder on live session | I²C and SPI register reads | **HW**; see [accelerometer.md](accelerometer.md) |
| Manchester / differential Manchester | Manchester decoder | Software encoder and preview | **SW** decoder/encoder tests |
| NRZ | NRZ decoder | Software encoder and preview | **SW** |
| I²S | I²S decoder | Preview/decode only; requires data, clock, and word-select | **SW** |
| CAN/CAN-FD-style | CAN decoder with CRC/frame fields | No dedicated board generator route | **SW** |
| LIN | LIN decoder with PID/checksum | No dedicated board generator route | **SW** |
| MIDI | MIDI decoder with running status | No dedicated board generator route | **SW** |
| PS/2 | PS/2 scan-code decoder | No dedicated board generator route | **SW** |
| Quadrature | A/B count/direction decoder | No dedicated board generator route | **SW** |
| HDLC | Flag, unstuffing, CRC decoder | No dedicated board generator route | **SW** |
| JTAG | TAP and scan decoder | SWD/JTAG generator path is separate | **SW** decoder; external target required |
| Infrared | NEC/RC5/RC6 decoder | No dedicated board generator route | **SW** |
| SMBus/PMBus | SMBus decoder with PEC | No dedicated board generator route | **SW** |

## Application features

| Feature | Documentation | Evidence / boundary |
|---|---|---|
| Device discovery and capabilities | [API Layer](backend/api-layer.md), [Hardware Abstraction](backend/hardware-abstraction.md) | **HW** backend smoke |
| Real and mock hardware adapters | [Existing Host Adapter](backend/existing-host-adapter.md), [Mock Device](backend/mock-device.md) | **SW/HW** |
| Session persistence | [Session Model](backend/session-model.md), [Session Stores](backend/session-stores.md) | **SW** JSON/NPZ/session tests |
| REST API | [API Layer](backend/api-layer.md) | **SW** backend test suite |
| WebSockets and live diagnostics | [WebSocket Diagnostics](backend/websocket-diagnostics.md) | **SW** reconnect/topic tests; live browser evidence |
| CSV/JSON/VCD/NPZ/HTML report export | [Export Formats](backend/export-formats.md) | **SW** export tests |
| Decoder annotations and packet table | [Decoder UI](frontend/decoder-ui.md) | **SW** Playwright coverage |
| Cursor, markers, zoom, density shading | [Waveform Viewer](frontend/waveform-viewer.md) | **SW** Playwright coverage |
| Generator page and route capability gating | [Generator Controller](backend/generator-controller.md), [Generator Routing](generator-routing.md) | **HW/SW** |
| Machine-in-loop testing | [Machine-In-Loop](backend/machine-in-loop.md) | **SW** service/API coverage; external fixtures required for device claims |
| Debug bundles and sanity checks | [WebSocket Diagnostics](backend/websocket-diagnostics.md) | **SW** |
| Frontend screenshot/e2e suite | [Build and Test](frontend/build-and-test.md) | Typecheck/build pass; hardware scenario includes real-board sessions |
| Bit Banger scripts and presets | [Recent Software Features](recent-software-features.md) | **SW/HW**; 1,024-symbol FIFO boundary |
| CSV/VCD waveform import | [Recent Software Features](recent-software-features.md) | **SW** importer and session tests |
| Session comparison | [Recent Software Features](recent-software-features.md) | **SW** alignment/divergence tests |
| Analysis views | [Recent Software Features](recent-software-features.md) | **SW** spectrum, spectrogram, XY, correlation, envelope, threshold sweep |
| Decoder quality scores | [Recent Software Features](recent-software-features.md) | **SW** host confidence estimate, 0–1 |
| Protocol activity dashboard | [Recent Software Features](recent-software-features.md) | **SW** event-density/error summary |
| Command palette | [Recent Software Features](recent-software-features.md) | **SW** Ctrl/Cmd+K navigation/actions |

## Hardware and build contract

| Item | Current validated value |
|---|---|
| FPGA | Intel MAX 10 `10M08SAU169C8G` |
| Full build | `FAST_SPEED=true`, `FAST_RAW_BUILD=false`, seed 23 |
| Timing | Slow-85C `fast_clk` `+0.124 ns`, `sdram_core_clk` `+0.426 ns`; no setup/hold violations |
| Logic use | 7,875/8,064 LEs (98%); 4,593 registers; 63 pins |
| Current SOF | `0x004FDDF3` on the validation board |
| Final hardware regression | 369/369 passed, 0 failed, 0 skipped on 2026-07-22 |

## How to interpret coverage

Passing the complete hardware regression proves the analyzer’s current
board-level contract, not every possible external electrical configuration.
In particular, SWD requires a real target for response validation; MIL tests
need their external device fixtures; and accelerometer validation is a
protocol/connection check rather than a sensor calibration characterization.
The authoritative current snapshot is [Current Status](current-status.md),
while the detailed test evidence is in [Hardware Validation](hardware-validation.md).
