# Backend — Python Server Wiki

> FastAPI backend that owns the hardware connection, capture orchestration, session management, protocol decoders, measurements, exports, and real-time WebSocket notifications.

## Architecture

```
Client (browser / curl)
    │  REST + WebSocket
    ▼
┌────────────────────────────────────────┐
│            FastAPI (app/main.py)       │
│  ┌─────┐ ┌──────┐ ┌──────┐ ┌───────┐ │
│  │status│ │device│ │capture││session│ │
│  │  ws  │ │  api  │ │  api  │ │  api  │ │
│  └──┬──┘ └──┬───┘ └──┬───┘ └───┬───┘ │
│     │       │         │          │      │
│  ┌──┴───────┴─────────┴──────────┴──┐  │
│  │        CaptureManager            │  │
│  │  (worker thread, control lock)   │  │
│  └──┬───────────────────────────────┘  │
│     │                                  │
│  ┌──┴───────────────────────────────┐  │
│  │       HardwareDevice             │  │
│  │  ┌─────────────────┐ ┌────────┐  │  │
│  │  │ExistingHostAdapt│ │MockDev │  │  │
│  │  │  (real FPGA)    │ │ (synth)│  │  │
│  │  └───────┬─────────┘ └────────┘  │  │
│  └──────────┼───────────────────────┘  │
│             │                          │
│  ┌──────────┴───────────────────────┐  │
│  │     SessionStore + WaveformStore │  │
│  │     (data/sessions/<id>/)        │  │
│  └──────────────────────────────────┘  │
│                                         │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ │
│  │Decoders  │ │Measures │ │Exports   │ │
│  │(uart,i2c,│ │(dig,ana)│ │(CSV,VCD, │ │
│  │ spi,…)   │ │         │ │ JSON,NPZ)│ │
│  └──────────┘ └─────────┘ └──────────┘ │
└────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│  host/driver/ols_spi_device.py       │
│  (UNCHANGED — reused as-is)          │
│  → wire_format.py (pure functions)   │
│  → spi_protocol.py (packet protocol) │
│  → ols_spi.py (FTDI MPSSE transport) │
└──────────────────────────────────────┘
```

## Package Map

| Package | Files | Purpose |
|---|---|---|
| `app/hardware/` | `base.py`, `existing_host_adapter.py`, `mock_device.py`, `strategies/`, `max1000_board.py`, `device_models.py`, `mock_signals.py` | Hardware abstraction, real FPGA adapter, mock device, capture strategy classes, board pin maps |
| `app/capture/` | `capture_manager.py`, `session.py`, `session_store.py`, `waveform_store.py`, `waveform_query.py`, `lod.py`, `downsample.py`, `sample_format.py`, `chunk_store.py` | Capture orchestration, session data model, persistent storage, waveform query with LOD, binary encoding |
| `app/api/` | `status.py`, `devices.py`, `capture.py`, `sessions.py`, `waveform.py`, `decoders.py`, `measurements.py`, `exports.py`, `generator.py`, `mil.py`, `diagnostics.py` | REST endpoints + WebSocket routers |
| `app/decoders/` | `base.py`, `registry.py`, `service.py`, `uart.py`, `i2c.py`, `spi.py`, `parallel.py`, `onewire.py`, `modbus.py`, `rs485.py`, `pwm.py` | Plugin decoder framework + protocol implementations |
| `app/measurements/` | `base.py`, `digital.py`, `analogue.py`, `bus.py` | Measurement types (frequency, duty, pulse width, edge count, min/max/mean) |
| `app/triggers/` | `model.py`, `hardware_support.py`, `software_trigger.py` | Trigger model, hardware-vs-post-capture classification, software search |
| `app/generator/` | `controller.py`, `model.py` | Generator configuration and loopback self-test |
| `app/mil/` | `service.py`, `model.py` | Machine-in-loop automated test subsystem |
| `app/exports/` | `csv_export.py`, `json_export.py`, `vcd_export.py`, `npz_export.py`, `report_export.py` | Export formats |
| `app/websocket/` | `manager.py`, `status_ws.py` | Topic-based WebSocket broadcast |
| `app/diagnostics/` | `logger.py`, `debug_bundle.py`, `sanity_checks.py` | Logging, debug bundle, session sanity checks |
| `app/waveform/` | `digital.py`, `analogue.py`, `derived.py`, `bus.py` | Waveform data processing for derived channels |

## Wiki Pages

### Hardware Layer
- [Hardware and Capture Seam](hardware-capture-seam.md) — normalized capture contract, adapter seam, and validation rules
- [Hardware Abstraction](hardware-abstraction.md) — `HardwareDevice` ABC, `CaptureResult`, `HardwareError`
- [Existing Host Adapter](existing-host-adapter.md) — Wrapping `OLSDeviceSPI`, strategy dispatch, capture flow
- [Mock Device](mock-device.md) — 10 synthetic scenarios, mock-only analog
- [Capture Strategies](capture-strategies.md) — Template method + 5 strategy classes (Digital, Mixed, Analog, AnalogAll, NarrowDigital)

### Capture & Sessions
- [Capture Manager](capture-manager.md) — `ControlLock`, worker thread, capture life cycle, decoder run orchestration
- [Session Model](session-model.md) — All Pydantic models (Session, CaptureSettings, ChannelInfo, etc.)
- [Session Stores](session-stores.md) — JSON + NPZ persistence, waveform chunking

### API & WebSocket
- [API Layer](api-layer.md) — All REST endpoints by module, request/response schemas
- [WebSocket & Diagnostics](websocket-diagnostics.md) — Topic broadcast, ring-buffer logging, debug bundle, sanity checks

### Decoders
- [Decoder Framework](decoder-framework.md) — `Decoder` ABC, `DecodeContext`, event format, topological ordering, stacked decoders
- [Decoder Implementations](decoder-implementations.md) — UART, I2C, SPI, parallel, 1-Wire, Modbus, RS-485, PWM

### Waveform & Analysis
- [Waveform Service](waveform-service.md) — MSAW binary format, LOD pyramid, resolution decision tree, downsampling
- [Measurements](measurements.md) — Digital, analog, bus measurement types
- [Triggers](triggers.md) — Trigger model, hardware vs software, post-capture search
- [Export Formats](export-formats.md) — CSV, JSON, VCD, NPZ, HTML report

### Generator & MIL
- [Generator Controller](generator-controller.md) — Generator config, loopback self-test workflow, route validation
- [Generator Routing](../generator-routing.md) — Physical pin pool, auxiliary routes, capture channels, and register contract
- [Machine-In-Loop](machine-in-loop.md) — MIL subsystem: UART/modbus/RS485 automated testing
- [Recent Software Features](../recent-software-features.md) — expanded decoder catalog, analysis APIs, imports, triggers, and generator scripts

## Key Design Decisions

- Raw data is never modified — software filters create *derived channels*
- Sessions are directories containing `session.json` + `waveform.npz` + `decoders/*.json`
- Waveform queries use LOD pyramids (bin sizes 16, 64, 256, …) for fast zoomed-out rendering
- Binary `MSAW` format for waveform transport: magic + JSON header + 4-byte typed arrays
- Decoder framework supports stacking: Modbus RTU consumes UART byte events
