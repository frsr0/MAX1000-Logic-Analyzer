# Export Formats

**Directory:** `backend/app/exports/`

## Purpose

Export captured sessions in various formats for external analysis, sharing, and archival.

## Formats

### CSV (`csv_export.py`)

Raw sample data as comma-separated values.

- One column per channel (digital: 0/1, analog: voltage)
- One row per sample
- Optional: selection window (start/end sample)
- Optional: header row with channel metadata
- Usable with: spreadsheet apps, Python pandas, R

### JSON (`json_export.py`)

Session export in a round-trippable JSON format that can be re-imported on the Sessions page.

- Full session model (settings, channels, decoders, measurements, markers)
- Waveform data as base64-encoded arrays
- Import endpoint: `POST /api/sessions` with `json_text` body

### VCD (`vcd_export.py`)

Value Change Dump (IEEE 1364-2001) — standard format for digital waveforms.

- Digital channels only (analog channels are omitted)
- Uses `$var` declarations with `wire` type
- All samples emitted as value changes
- Compatible with: GTKWave, Sigrok PulseView, waveform viewers

### NPZ (`npz_export.py`)

NumPy compressed archive (`.npz` file).

- Arrays: `digital` (bit-packed uint8), `channel_*` (per-channel analog)
- Metadata JSON in `metadata` key
- Usable with: Python/NumPy scientific analysis

### HTML Report (`report_export.py`)

Self-contained HTML report with:

- Session metadata and settings summary
- SVG waveform overview (sparkline per channel)
- Decoder event table (with severity colouring)
- Measurement results
- Marker positions
- Diagnostics (if any)

## API

```python
POST /api/sessions/{id}/export/csv      # → CSV file download
POST /api/sessions/{id}/export/json     # → JSON file download
POST /api/sessions/{id}/export/vcd      # → VCD file download
POST /api/sessions/{id}/export/npz      # → NPZ file download
POST /api/sessions/{id}/export/report   # → HTML file download
```

Each supports an optional `start`/`end` parameter for windowed export, and `channels` for selective export.

## Dependencies

| File | Purpose |
|---|---|
| `csv_export.py` | CSV export |
| `json_export.py` | JSON round-trip export |
| `vcd_export.py` | Value Change Dump |
| `npz_export.py` | NumPy archive |
| `report_export.py` | HTML report generator |
| `Session`, `ChannelInfo` | `capture/session.py` |
| `WaveformData` | `capture/sample_format.py` |

---

# Generator Controller

**Directory:** `backend/app/generator/`

## Purpose

Controls the FPGA's signal generator and orchestrates the loopback self-test workflow (configure → capture → decode → compare).

## Files

| File | Purpose |
|---|---|
| `controller.py` (9 KB) | Generator command dispatch, loopback test orchestration |
| `model.py` (989 B) | GeneratorConfig and GeneratorStatus Pydantic models |

## GeneratorConfig

```python
class GeneratorConfig(BaseModel):
    protocol: str = "uart"              # uart|rs485|i2c|spi|swd|bitbang
    baud: int = 115200
    data_hex: str = ""                  # payload bytes as hex
    tx_pin: int = 3
    scl_pin: int = 1
    repeat: int = 1
    continuous: bool = False
    i2c_read_len: int = 0
    extra: dict = {}                    # DE/CS/MISO routes and capture channels
```

## Loopback Self-Test

```python
def run_loopback_test(dev: HardwareDevice, settings: CaptureSettings,
                      gen_cfg: GeneratorConfig) -> dict:
    """Configure generator, capture generator output, decode, compare."""
    1. dev.generator_configure(gen_cfg)
    2. dev.generator_start()
    3. dev.capture_with_generator(settings, gen_cfg) or
       dev.capture(settings) with active generator
    4. dev.generator_stop()
    5. Decode captured data with appropriate protocol decoder
    6. Compare decoded data with original `data` bytes
    7. Return {passed, expected, decoded, errors}
```

Used by `hw_smoke_test.py` and the Generator page self-test button.

---

# Machine-In-Loop (MIL)

**Directory:** `backend/app/mil/`

## Purpose

Automated Machine-In-Loop subsystem: configures a test scenario (UART/modbus/RS485), runs the generator, captures the response, and validates the decoded data against expected values.

## Files

| File | Purpose |
|---|---|
| `service.py` (18.3 KB) | MIL orchestration logic |
| `model.py` (2.5 KB) | MIL Pydantic models |

## Presets

Pre-configured test scenarios:
- UART loopback: generate UART bytes → capture → verify
- Modbus RTU query: generate Modbus frame → capture → decode → validate CRC/response
- RS-485 half-duplex: generate RS-485 frame → capture → verify direction control

## Models

```python
class MilConfig(BaseModel):
    protocol: MilProtocol               # uart | modbus_uart | rs485_modbus
    registers: List[MilRegister]        # register read/write definitions
    trigger: Optional[MilTrigger]       # trigger on specific register/value
    timing: MilTiming                   # inter-byte and inter-frame gaps

class MilRuntimeStatus(BaseModel):
    running: bool
    current_step: int
    total_steps: int
    transactions: List[MilTransactionResponse]
```

---

# WebSocket & Diagnostics

**Directory:** `backend/app/websocket/`, `backend/app/diagnostics/`

## WebSocket Manager (`websocket/manager.py`)

Topic-based broadcast manager:

```python
class WebSocketManager:
    def subscribe(topic: str, websocket: WebSocket) -> None
    def unsubscribe(topic: str, websocket: WebSocket) -> None
    def broadcast(topic: str, message: dict) -> None
```

Topics: `status`, `capture`, `logs`, `session/{id}`, `decoder/{id}`

## Status WebSocket (`websocket/status_ws.py`)

FastAPI WebSocket router at `/ws/status`. Sends `device_connected`, `capture_state`, `session_created` events.

## Diagnostics

### Logger (`diagnostics/logger.py`)

Ring-buffer log (last N entries) with WebSocket broadcast:
```python
class RingLogger:
    def log(level, message, source=None) -> None
    def get_recent(count=100) -> List[LogEntry]
```

### Debug Bundle (`diagnostics/debug_bundle.py`)

ZIP archive containing:
- Current status snapshot
- Device debug info (command log, registers)
- Last 250 log entries
- Recent session metadata

API: `POST /api/diagnostics/debug-bundle` → ZIP download

### Sanity Checks (`diagnostics/sanity_checks.py`)

Per-session data integrity checks:
- Sample count matches session metadata
- No NaN/Inf in analog data
- Digital data within valid range (0/1)
- ADC voltage within scaling range
