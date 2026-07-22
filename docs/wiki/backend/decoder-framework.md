# Decoder Framework

**Files:** `backend/app/decoders/base.py` (143 lines), `backend/app/decoders/registry.py` (1.0 KB), `backend/app/decoders/service.py` (153 lines)

## Purpose

Plugin-style protocol decoder framework. Decoders consume immutable `WaveformData` (digital sample arrays) and produce structured events. Supports stacked decoders where one decoder consumes another's events.

## Event Format

```python
{
    "id": "ev_<uuid4>",
    "decoder_id": "uart_1",
    "type": "uart_byte",
    "start_sample": 123,
    "end_sample": 456,
    "start_time": 0.000123,
    "end_time": 0.000456,
    "label": "0x48 'H'",
    "severity": "normal" | "warning" | "error",
    "fields": {"baud": 115200, "parity": "none", "bit_rate": ...}
}
```

Events are sample-index-based, not time-based internally — timestamps are derived from the sample rate for display.

## Decoder ABC

```python
class Decoder(ABC):
    id: str = ""                        # decoder type ID
    name: str = ""                      # human-readable name
    description: str = ""
    channel_roles: List[ChannelRole]    # what channels the decoder needs
    setting_fields: List[SettingField]  # configurable settings
    consumes: Optional[str] = None      # stacked: consumes events from another decoder

    @abstractmethod
    def decode(self, ctx: DecodeContext) -> DecoderResult: ...
```

### ChannelRole

```python
@dataclass
class ChannelRole:
    role: str                           # 'rx', 'tx', 'scl', 'sda', 'sclk', 'mosi', 'miso', 'cs'
    name: str
    min_count: int = 1
    max_count: int = 1
    types: List[str] = ["digital", "derived", "analog"]
```

### SettingField

```python
@dataclass
class SettingField:
    key: str
    label: str
    type: str                           # 'int', 'float', 'bool', 'select', 'text'
    default: Any
    options: Optional[list] = None      # for select type
    help: str = ""
```

### DecoderResult

```python
@dataclass
class DecoderResult:
    events: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)  # packet table columns
```

## DecodeContext

Runtime services passed to every decoder:

```python
class DecodeContext:
    def __init__(self, waveform: WaveformData, channel_map: dict,
                 settings: dict, progress_cb: ProgressCb,
                 cancel_evt: threading.Event):
        ...

    def samples(self, channel_id: str) -> np.ndarray:
        """Get digital sample array for a channel (0/1 values)."""

    def analog_samples(self, channel_id: str) -> np.ndarray:
        """Get analog sample array (float32 volts)."""

    def window(self, start: int, end: int) -> 'DecodeContext':
        """Get a sub-window context for region decoding."""

    def check_cancelled(self):
        """Raise DecodeCancelled if cancel requested."""

    def progress(self, fraction: float):
        """Report decoding progress (0..1)."""

    def events_from(self, decoder_id: str, start=None, end=None) -> List[dict]:
        """Get events from an upstream decoder (for stacked decoders)."""
```

## Stacked Decoders

A decoder can declare `consumes = "uart"` to receive events from another decoder instead of raw samples. For example, Modbus RTU stacks on UART:

```
UART decoder → raw samples → UART byte events
                                    ↓ (consumes="uart")
Modbus RTU decoder → UART byte events → Modbus frame events
```

## Registry

```python
# registry.py
decoder_types: Dict[str, Type[Decoder]] = {}  # "uart" → UartDecoder class

def register(decoder_cls: Type[Decoder]):
    """Register a decoder class so the API can discover it."""

def get(decoder_id: str) -> Type[Decoder]:
    """Get decoder class by ID."""

def list_types() -> List[dict]:
    """Return all registered decoder descriptions."""
```

Decoders self-register via `@register` decorator on their module import.

## DecoderService

Orchestrates decoder runs with dependency ordering:

```python
class DecoderService:
    def run(session: Session, inst: DecoderInstance) -> None
    def rerun_all(session_id: str) -> None
    def cancel(decoder_id: str) -> bool
    def events(session_id, decoder_id, start, end) -> List[dict]
```

- `run()` first ensures all upstream decoders are complete
- `rerun_all()` runs all enabled decoders in topological order
- Topological order via `_topological_order()`: consumers after their sources

## Implemented Decoders

| Decoder | ID | Consumes | Events |
|---|---|---|---|
| UART | `uart` | — | `uart_byte`, `uart_frame` |
| I2C | `i2c` | — | `i2c_start`, `i2c_stop`, `i2c_byte`, `i2c_ack` |
| SPI | `spi` | — | `spi_word`, `spi_frame` |
| Parallel | `parallel` | — | `parallel_word` |
| 1-Wire | `onewire` | — | `ow_reset`, `ow_byte` |
| PWM | `pwm` | — | `pwm_pulse` |
| Modbus RTU | `modbus_uart` | `uart` | `modbus_frame` |
| RS-485 | `rs485` | — | `rs485_frame` |

## Dependencies

| Module | File |
|---|---|
| `WaveformData` | `capture/sample_format.py` |
| `CaptureManager` | `capture/capture_manager.py` |
| `Session`, `DecoderInstance` | `capture/session.py` |
| `decoder_registry` | `decoders/registry.py` |
| Individual decoders | `decoders/*.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_api.py` | Decoder API through mock device |
