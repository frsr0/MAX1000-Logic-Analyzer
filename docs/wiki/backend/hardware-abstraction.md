# Hardware Abstraction

**Files:** `backend/app/hardware/base.py`, `backend/app/hardware/device_models.py`, `backend/app/hardware/protocol.py`

## Purpose

Abstract base class for all hardware backends (real FPGA via existing host driver, mock device, future hardware). Defines the `HardwareDevice` interface that the `CaptureManager` uses to talk to devices.

## Core Classes

### `HardwareDevice` (ABC)

```python
class HardwareDevice(ABC):
    def connect(self) -> DeviceMetadata: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def get_metadata(self) -> DeviceMetadata: ...
    def get_capabilities(self) -> DeviceCapabilities: ...
    def capture(self, settings: CaptureSettings,
                progress: ProgressCb = None,
                stop_evt: threading.Event = None) -> CaptureResult: ...
    def get_debug_info(self) -> DebugInfo: ...
    def validate_settings(self, settings: CaptureSettings) -> list: ...
    def generator_status(self) -> GeneratorStatus: ...
    def generator_configure(self, cfg: GeneratorConfig) -> None: ...
    def generator_start(self) -> None: ...
    def generator_stop(self) -> None: ...
    def capture_with_generator(self, settings, cfg, progress=None, stop_evt=None) -> CaptureResult: ...
    def self_test(self) -> dict: ...
```

### `CaptureResult` (dataclass)

```python
@dataclass
class CaptureResult:
    sample_rate: float
    digital: Optional[np.ndarray] = None       # packed uint16
    analog: Dict[str, np.ndarray] = field(default_factory=dict)  # volts f32
    trigger_sample: Optional[int] = None
    divider: Optional[int] = None
    warnings: list = field(default_factory=list)
```

### `HardwareError` (Exception)

Raised for all hardware errors. Caught by the FastAPI exception handler and returned as HTTP 502.

### `ProgressCb`

```python
ProgressCb = Callable[[int, int, str], None]  # (read, total, phase)
```

## Device Models (`device_models.py`)

Pydantic models for structured device information:

| Model | Key Fields |
|---|---|
| `DeviceCapabilities` | `modes`, `max_sample_rate`, `max_depth`, `analog_channels`, `trigger_types`, `compression`, `generator_protocols` |
| `GeneratorConfig` | `protocol` (uart/i2c/spi/pwm/pattern), `baud_rate`, `data` (hex string), `repeat`, `pins` |
| `GeneratorStatus` | `supported`, `running`, `fifo_fill`, `detail` |
| `DebugInfo` | `registers`, `device_status`, `metadata`, `command_log` |
| `TriggerCapability` | `type`, `available`, `min_pulse` |

## Protocol Module (`protocol.py`)

Lazy loader for `host.driver.ols_spi_device`:
```python
def import_host_driver():
    # Lazily import from host.driver.ols_spi_device
    # Returns OLSDeviceSPI class and constants
```

## Two Implementations

| Implementation | File | Mock? | Description |
|---|---|---|---|
| `ExistingHostAdapter` | `existing_host_adapter.py` | No | Wraps real `OLSDeviceSPI` from host driver |
| `MockDevice` | `mock_device.py` | Yes | Fully synthetic waveforms, no hardware needed |

## `validate_settings(settings) -> list`

Returns validation findings for the current settings against hardware capabilities. Checks:
- Sample rate against device max
- Capture depth against device max
- Mode availability (analog/mixed/narrow)
- Trigger configuration validity

## Dependencies

| Module | File |
|---|---|
| `CaptureSettings`, `DeviceMetadata` | `capture/session.py` |
| `CaptureResult`, `HardwareError`, `ProgressCb` | `hardware/base.py` |
| `DeviceCapabilities`, `GeneratorConfig`, etc. | `hardware/device_models.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_existing_host_adapter.py` (19 tests) | Hardware adapter through real or mock driver |
| `test_core.py` | Core capture flow validation |
