# Measurements

**Directory:** `backend/app/measurements/`

## Purpose

Measurement types that compute quantitative values from waveform data. Each measurement type operates on a single channel (digital or analog) with cursor-based ranges.

## Measurement Types

### Digital (`measurements/digital.py`)

| Type | Unit | Description |
|---|---|---|
| `frequency` | Hz | Signal frequency from edge count / window |
| `period` | s | Average period between edges |
| `duty_cycle` | % | High time / total period |
| `pulse_width` | s | High (or low) pulse duration |
| `edge_count` | — | Number of rising (or falling) edges |
| `min_pulse` | s | Shortest high pulse in window |
| `max_pulse` | s | Longest high pulse in window |

### Analog (`measurements/analogue.py`)

| Type | Unit | Description |
|---|---|---|
| `min` | V | Minimum voltage in window |
| `max` | V | Maximum voltage in window |
| `peak_to_peak` | V | max − min |
| `mean` | V | Average voltage |
| `rms` | V | Root mean square |
| `frequency` | Hz | Dominant frequency (zero-crossing) |

### Bus (`measurements/bus.py`)

| Type | Unit | Description |
|---|---|---|
| `bus_value` | — | Sampled binary value on bus channels |

## Measurement Model

```python
@dataclass
class MeasurementBase(ABC):
    id: str
    channel_id: str
    cursor_a: Optional[int] = None       # start sample
    cursor_b: Optional[int] = None       # end sample

    def compute(waveform: WaveformData) -> Any: ...
```

## API

```
GET  /api/measurements/types -> {types: [{id, name, channel_type, unit, ...}]}
POST /api/sessions/{id}/measurements -> create measurement instance
GET  /api/sessions/{id}/measurements -> list instances
PATCH /api/sessions/{id}/measurements/{m} -> update settings
DELETE /api/sessions/{id}/measurements/{m} -> delete
GET  /api/sessions/{id}/measurements/results?cursor_a=&cursor_b= -> compute results
```

# Triggers

**Directory:** `backend/app/triggers/`

## Purpose

Trigger configuration model, hardware-vs-post-capture classification, and software trigger search for patterns not supported in hardware.

## Trigger Model (`model.py`)

```python
@dataclass
class TriggerConfig:
    type: Literal["rising_edge", "falling_edge", "any_edge", "pattern",
                  "uart_byte", "immediate", "none"]
    channel_mask: Optional[int] = None   # bitmask for edge triggers
    value: Optional[Union[int, bytes]] = None  # pattern / UART byte
    execution: Literal["hardware", "post_capture", "unavailable"] = "hardware"
```

## Hardware Support (`hardware_support.py`)

Classifies triggers by hardware capability:

| Trigger | Hardware | Software |
|---|---|---|
| Rising/falling edge (any channel mask) | ✅ | — |
| UART byte protocol trigger | ✅ | — |
| Pattern (multi-channel value) | — | ✅ (post-capture) |
| Immediate | ✅ | — |

## Software Trigger (`software_trigger.py`)

Post-capture trigger search for patterns not available in hardware:

```python
def find_software_trigger(digital_samples, trigger_config) -> Optional[int]:
    """Search digital samples for a trigger pattern.
    Returns the sample index of the first match, or None."""
```

- Pattern match: find word value on enabled channels
- Edge search: find first rising/falling edge after a given sample
- Used when `trigger.execution == "post_capture"`

# Waveform Service

**Files:** `backend/app/capture/waveform_query.py` (181 lines), `backend/app/capture/lod.py` (141 lines), `backend/app/capture/downsample.py` (2.0 KB), `backend/app/capture/sample_format.py` (3.4 KB)

## Purpose

Waveform data access layer: provides resolution-adaptive queries (raw / LOD / overview) for the frontend waveform viewer using the compact binary MSAW format.

## MSAW Binary Format

```
MAGIC "MSAW" (4 bytes)
JSON header length (4 bytes, little-endian)
JSON header (variable):
  { num_samples, sample_rate, mode, channels: [{id, type, index}] }
Channel data arrays (4-byte aligned, per channel):
  type tag (1 byte: 0=digital, 1=analog, 2=derived)
  data length (4 bytes, elements)
  data (digital: uint8 bit-packed; analog: float32)
```

Parsed by the frontend into zero-copy TypedArray views.

## WaveformQuery

```python
class WaveformQuery:
    def window(start, end, max_points, channels) -> bytes
        """Resolution-adaptive window: raw if small enough, else LOD."""
    def raw_window(start, end, channels) -> dict
        """Raw samples as JSON (small windows only)."""
    def overview(bins=1024) -> bytes
        """Whole-capture overview for minimap."""
```

### Resolution Decision Tree

1. If `num_samples_per_channel ≤ MAX_RAW_POINTS` (config): return raw data
2. Else: compute LOD level where `bin_size ≈ window_bins / max_points`
3. Digital: and_mask/or_mask + edge density per bin
4. Analog: min/max per bin
5. Encode as MSAW binary

## LOD Pyramid (`lod.py`)

```python
class LodPyramid:
    def __init__(self, digital, analog)
    def level_at(bin_size) -> (DigitalLodLevel, AnalogLodLevel)
```

Per level k: bin size = `LOD_BASE * LOD_FACTOR**k`

- Digital per bin: `and_mask` (all-high), `or_mask` (any-high), `edges[16]` (transition count)
- Analog per bin: `vmin`, `vmax` (float32)
- Built once on capture and stored alongside the raw data

## Downsample Utilities (`downsample.py`)

| Function | Input | Output | Description |
|---|---|---|---|
| `downsample_digital(bits, window_size, num_channels)` | uint8 array | uint16 masks + edge counts | Digital min/max/edges per window |
| `downsample_analog(samples, window_size)` | float32 array | float32 min/max | Analog range per window |
| `edge_density(bits, num_channels)` | uint8 array | uint16 array | Transitions per channel in capture |

## Sample Format (`sample_format.py`)

```python
class WaveformData:
    num_samples: int
    sample_rate: float
    digital: np.ndarray       # uint8[N], bit-packed (channel i = bit i of byte N)
    analog: Dict[str, np.ndarray]  # channel_id → float32[N] volts

    def digital_channel(self, channel_idx: int) -> np.ndarray
    def analog_channel(self, channel_id: str) -> np.ndarray
```

### Reference to Volts Conversion (`adc_to_volts()`)

ADC 12-bit codes → voltage: `volts = code * VREF / 4096` where VREF = 3.3 V.

## Dependencies

| Module | File |
|---|---|
| `WaveformData` | `capture/sample_format.py` |
| `LodPyramid` | `capture/lod.py` |
| `downsample` | `capture/downsample.py` |
| `WaveformStore` | `capture/waveform_store.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_core.py` | Waveform query, LOD building |
| `test_api.py` | Waveform API endpoints |
