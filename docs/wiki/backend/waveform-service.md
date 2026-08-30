# Waveform Service

**Files:** `capture/sample_format.py`, `lod.py`, `downsample.py`,
`waveform_store.py`, and `chunk_store.py`

The waveform layer keeps captured data in a canonical immutable representation
and serves resolution-adaptive binary windows to the canvas renderer.

## Canonical arrays

```python
@dataclass
class WaveformData:
    sample_rate: float
    digital: np.ndarray | None          # uint16; bit n is digital channel n
    analog: dict[str, np.ndarray]       # float32 volts
    derived_digital: dict[str, np.ndarray]  # uint8 0/1, separate from raw data
```

`adc_to_volts` maps 12-bit codes with `code * 3.3 / 4095`. Digital wire words
are collapsed to their low 16-bit payload before entering `WaveformData`.

## MSAW binary format

```text
bytes 0..3      "MSAW"
bytes 4..7      little-endian uint32 padded-header length
bytes 8..       UTF-8 JSON header, padded to four bytes
remaining       four-byte-aligned typed arrays in header order
```

The header includes session/window metadata and array descriptors such as:

```json
{
  "mode": "raw",
  "samples_per_bin": 1,
  "arrays": [
    {"name": "digital", "dtype": "u2", "count": 4096},
    {"name": "analog:a1", "dtype": "f4", "count": 4096}
  ]
}
```

Supported dtypes are `u1`, `u2`, `u4`, and `f4`. The frontend creates
zero-copy TypedArray views over the transferred response buffer.

## Resolution selection

`window_payload` clamps the request and then:

1. returns raw packed digital, analog, and derived arrays when the requested
   window fits the point budget;
2. otherwise selects an aligned precomputed LOD level when available;
3. falls back to NumPy downsampling while a new LOD pyramid is unavailable.

Digital LOD stores per-bin AND/OR masks and per-channel edge counts. Analog LOD
stores min/max envelopes. Derived digital channels have their own AND/OR masks.
`overview_payload` creates whole-session minimap masks and combined activity
density.

## Endpoints

| Endpoint | Result |
|---|---|
| `/api/sessions/{id}/waveform` | Adaptive MSAW viewport |
| `/api/sessions/{id}/overview` | MSAW minimap overview |
| `/api/sessions/{id}/raw` | Small JSON/raw inspector window |
| `/api/sessions/{id}/edges` | Bounded transition indices |
| `/api/sessions/{id}/value-at` | Values at one sample |

Spectrum, spectrogram, correlation, envelope, threshold sweep, event
correlation, eye diagram, timing suspects, and sanity checks are adjacent
analysis endpoints described in [API Layer](api-layer.md).

## Live behavior

Every live chunk invalidates LOD state and publishes `waveform_ready`. The
frontend allows one viewport fetch in flight, coalesces later chunk requests,
and throttles overview updates. This prevents repeated invalidation from
cancelling every response before it reaches the canvas.

See [Session Storage](session-stores.md),
[Frontend Workers](../frontend/workers.md), and
[Waveform Viewer](../frontend/waveform-viewer.md).
