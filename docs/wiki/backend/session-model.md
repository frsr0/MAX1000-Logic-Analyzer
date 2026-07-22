# Session Model

**File:** `backend/app/capture/session.py` (233 lines)

## Purpose

The core domain model. Every capture produces a `Session` — a Pydantic model that carries all metadata, channel configuration, trigger settings, decoder instances, measurements, markers, and export history. Raw waveform data lives in a separate NPZ file.

## Models

### `Session`

```python
class Session(BaseModel):
    id: str                             # "ses_<uuid10>"
    name: str
    created: datetime
    device: DeviceMetadata
    settings: CaptureSettings
    channels: List[ChannelInfo]         # digital + analog + derived
    trigger: Optional[TriggerConfig]
    decoders: List[DecoderInstance]     # decoder configurations
    measurements: List[MeasurementInstance]
    markers: List[Marker]
    exports: List[ExportRecord]
    num_samples: int
    sample_rate: float
    tags: List[str]
    notes: str
```

### `CaptureSettings`

```python
class CaptureSettings(BaseModel):
    sample_rate: float = 1_000_000.0
    num_samples: int = 1024
    mode: str = "digital"               # digital | mixed | analog_fast | analog_all | digital_narrow
    trigger: Optional[TriggerConfig] = None
    compression: ReadbackCompression = "raw"  # raw | delta_rle | rle
    source: str = "digital"
    acquisition: str = "single"         # single | live
    mock_scenario: Optional[str] = None # mock device only
```

### `ChannelInfo`

```python
class ChannelInfo(BaseModel):
    id: str                             # 'd0'..'d15', 'a0'.., 'x<id>' derived, 'bus<id>'
    label: str
    type: ChannelType                   # digital | analog | derived | decoder | bus
    index: int
    enabled: bool = True
    color: Optional[str] = None
    voltage_range: Optional[float] = None
    physical_pin: Optional[dict] = None  # board pin mapping
    physical_available: Optional[bool] = None
```

### `TriggerConfig`

```python
class TriggerConfig(BaseModel):
    type: Literal["rising_edge", "falling_edge", "any_edge", "pattern", "uart_byte", "immediate", "none"]
    channel_mask: Optional[int] = None  # bitmask of enabled trigger channels
    value: Optional[int] = None        # trigger pattern / UART byte
    execution: Literal["hardware", "post_capture", "unavailable"] = "hardware"
```

### `DecoderInstance`

```python
class DecoderInstance(BaseModel):
    id: str                             # "dec_<uuid10>"
    decoder_type: str                   # "uart", "i2c", "spi", ...
    label: str
    channel_map: Dict[str, str]        # role → channel id
    settings: Dict[str, Any]
    enabled: bool = True
    status: str = "idle"               # idle | running | complete | error
    event_count: int = 0
    warning_count: int = 0
```

### `MeasurementInstance`

```python
class MeasurementInstance(BaseModel):
    id: str                             # "mes_<uuid10>"
    measurement_type: str               # "frequency", "duty_cycle", "pulse_width", ...
    channel_id: str
    label: str
    settings: Dict[str, Any]
    status: str = "idle"
    result: Optional[Any] = None
    error: Optional[str] = None
```

### `Marker`

```python
class Marker(BaseModel):
    id: str                             # "mkr_<uuid10>"
    sample: int
    label: str
    color: Optional[str] = None
```

### `DeviceMetadata`

```python
class DeviceMetadata(BaseModel):
    driver: str = ""
    device_name: str = ""
    serial: str = ""
    firmware: str = ""
    sample_clk_hz: float = 0
    capabilities: Optional[DeviceCapabilities] = None
    extra: Dict[str, Any] = {}
```

## Channel Assignment

| Type | IDs | Count |
|---|---|---|
| Digital | `d0`..`d15` | 16 |
| Analog | `a0`..`a3` | 4 (physical) |
| Derived | `x_<name>` | variable (software filters) |
| Bus | `bus_<name>` | variable (grouped channels) |

## Default Channel Configs

- `default_digital_channels(count=16)` — creates digital channels with MAX1000 pin mapping
- `default_analog_channels(count=4, adc_channels=None)` — creates analog channels from board config

## Storage

The `Session` model is serialised to `session.json` in the session directory. NPZ waveform data lives alongside it in `waveform.npz`.

## Dependencies

| Module | File |
|---|---|
| `DeviceCapabilities` | `hardware/device_models.py` |
| `max1000_board` | `hardware/max1000_board.py` |

---

# Session Stores

**Files:** `backend/app/capture/session_store.py` (6.9 KB), `backend/app/capture/waveform_store.py` (6.9 KB), `backend/app/capture/chunk_store.py` (1.6 KB)

## SessionStore

Manages session CRUD on the filesystem:

```python
class SessionStore:
    def create(session: Session, waveform: WaveformData) -> Session
    def get(session_id: str) -> Optional[Session]
    def list_sessions() -> List[Session]
    def update(session: Session) -> bool
    def delete(session_id: str) -> bool
    def duplicate(session_id: str) -> Optional[Session]
```

- Sessions stored in `data/sessions/<id>/`
- `session.json` contains the Pydantic model
- `waveform.npz` contains raw sample data
- `decoders/<decoder_id>.json` contains decoder events

## WaveformStore

Manages waveform persistence and the LOD pyramid:

```python
class WaveformStore:
    def save(session_id: str, wf: WaveformData, lod: LodPyramid = None)
    def load(session_id: str) -> Optional[WaveformData]
    def load_lod(session_id: str) -> Optional[LodPyramid]
    def delete(session_id: str)
```

- Waveform saved as compressed NPZ with `digital` and `analog` arrays
- LOD pyramid saved alongside for fast zoomed-out rendering

## ChunkStore

Window/clamping utility for waveform queries:

```python
def clamp_window(start: int, end: int, num_samples: int) -> (int, int)
```

Ensures query windows stay within the capture bounds.
