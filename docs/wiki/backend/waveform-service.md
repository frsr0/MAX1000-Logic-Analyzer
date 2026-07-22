# Waveform Service

**Files:** `backend/app/capture/waveform_query.py` (181 lines), `backend/app/capture/lod.py` (141 lines), `backend/app/capture/downsample.py` (2.0 KB), `backend/app/capture/sample_format.py` (3.4 KB)

## Purpose

Resolution-adaptive waveform data access layer. Provides raw, LOD, and overview data for the frontend waveform viewer using the compact binary MSAW format.

## MSAW Binary Format

```
┌──────────────────────┐
│ MAGIC "MSAW" (4 B)   │
├──────────────────────┤
│ JSON header len (4 B)│  little-endian uint32
├──────────────────────┤
│ JSON header          │  {num_samples, sample_rate, mode, channels}
├──────────────────────┤
│ Channel data arrays  │  (4-byte aligned)
│   type tag (1 B)     │  0=digital, 1=analog, 2=derived
│   data len (4 B)     │  number of elements
│   data (N bytes)     │  digital: uint8 bit-packed
│                      │  analog: float32
│   ...                │  (per channel)
└──────────────────────┘
```

Parsed by the frontend into zero-copy TypedArray views. No per-sample copies.

## WaveformQuery

```python
class WaveformQuery:
    def __init__(self, wf: WaveformData, lod: Optional[LodPyramid] = None)
```

### Resolution Decision Tree

```
client requests window(start, end, max_points, channels)
    │
    ├─ raw_window() ─ if end - start < MAX_RAW_POINTS
    │     Return raw samples as binary with mode="raw"
    │
    └─ lod_window() ─ otherwise
          Compute LOD level where bin_size ≈ window / max_points
          Digital: and_mask | or_mask | edge_density per bin
          Analog: vmin | vmax per bin
          Return as binary with mode="lod"
```

### Methods

| Method | Returns | Use |
|---|---|---|
| `window(start, end, max_points, channels)` | `bytes` (MSAW) | Main adaptive endpoint |
| `raw_window(start, end, channels)` | `dict` (JSON) | Inspector, small windows |
| `overview(bins=1024)` | `bytes` (MSAW) | Minimap overview |

## LOD Pyramid (`lod.py`)

```python
class LodPyramid:
    bin_sizes: List[int]
    digital_levels: List[DigitalLodLevel]
    analog_levels: List[AnalogLodLevel]
```

Built once on capture, stored alongside the raw data.

### DigitalLodLevel

```python
@dataclass
class DigitalLodLevel:
    bin_size: int
    and_mask: np.ndarray     # uint16[bins]: all-1 mask per bin
    or_mask: np.ndarray      # uint16[bins]: any-1 mask per bin
    edges: np.ndarray        # uint32[channels, bins] transition count
```

### AnalogLodLevel

```python
@dataclass
class AnalogLodLevel:
    bin_size: int
    vmin: np.ndarray         # float32[bins]
    vmax: np.ndarray         # float32[bins]
```

### Bin Size Progression

`LOD_BASE = 16`, `LOD_FACTOR = 4`:
- Level 0: 16 samples/bin
- Level 1: 64 samples/bin
- Level 2: 256 samples/bin
- Level 3: 1024 samples/bin
- ...

## Downsample Utilities (`downsample.py`)

| Function | Input | Output | Description |
|---|---|---|---|
| `downsample_digital(bits, window_size, num_channels)` | uint8[N] | (uint16 masks, uint32 edges) | Per-window min/max/edges |
| `downsample_analog(samples, window_size)` | float32[N] | (float32 vmin, vmax) | Per-window analog range |
| `edge_density(bits, num_channels)` | uint8[N] | uint16[channels, bins] | Transition counts per channel |

## Sample Format (`sample_format.py`)

```python
class WaveformData:
    num_samples: int
    sample_rate: float
    digital: np.ndarray       # uint8[N], bit-packed: channel i = bit i of byte N
    analog: Dict[str, np.ndarray]  # channel_id → float32[N] volts

    def digital_channel(self, channel_idx: int) -> np.ndarray:
        """Extract single digital channel as uint8 0/1 array."""

    def analog_channel(self, channel_id: str) -> np.ndarray:
        """Get analog channel voltage array."""
```

### ADC Voltage Conversion

```python
def adc_to_volts(adc_code: np.ndarray, vref: float = 3.3) -> np.ndarray:
    """Convert 12-bit ADC codes to voltages."""
    return adc_code.astype(np.float32) * vref / 4096.0
```

## Waveform Encoding (`waveform_store.py`)

```python
def _encode(header: dict, arrays: dict) -> bytes:
    """Build MSAW binary payload from header + channel arrays."""
```

The `_encode` function serialises:
1. Magic bytes `MSAW`
2. JSON header as 4-byte-prefixed string
3. Each channel's type-tagged data array

## Dependencies

| Module | File |
|---|---|
| `WaveformData`, `adc_to_volts` | `capture/sample_format.py` |
| `LodPyramid`, `build_digital_levels`, `build_analog_levels` | `capture/lod.py` |
| `downsample_digital`, `downsample_analog` | `capture/downsample.py` |
| `_encode` | `capture/waveform_store.py` |
