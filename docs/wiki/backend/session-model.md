# Session Model

**File:** `backend/app/capture/session.py`

Every capture, import, and generated loopback is represented by a Pydantic
`Session`. Metadata and analysis state are JSON; large waveform arrays are
stored separately in `waveform.npz`.

## Session

```python
class Session(BaseModel):
    id: str                    # ses_<10 hex chars>
    name: str
    created_at: float
    modified_at: float
    app_version: str
    device: DeviceMetadata
    settings: CaptureSettings
    sample_rate: float
    divider: int | None
    sample_clk_hz: float
    num_samples: int
    trigger_sample: int | None
    channels: list[ChannelInfo]
    decoders: list[DecoderInstance]
    measurements: list[MeasurementInstance]
    markers: list[Marker]
    notes: str
    tags: list[str]
    exports: list[ExportRecord]
    diagnostics: list[dict]
    generator: dict | None
```

`summary()` returns the bounded list-page representation, including duration,
channel/decoder/marker counts, notes preview, device name, and mock flag.

## Capture settings

```python
class CaptureSettings(BaseModel):
    sample_rate: float = 1_000_000.0
    num_samples: int = 10_000
    mode: Literal[
        "single", "continuous", "rolling", "triggered", "digital_narrow",
        "analog", "mixed", "analog_fast", "analog_all",
        "analog_continuous", "analog_all_continuous", "mixed_continuous",
    ] = "single"
    analog_enabled: bool = False
    enabled_digital: list[int] = list(range(16))
    trigger: TriggerConfig
    auto_rearm: bool = False
    repeat_count: int = 1
    auto_save: bool = False
    readback_compression: Literal["raw", "delta_rle", "delta", "rle"] = "raw"
    packed_mode: bool = False
    mock_scenario: str | None = None
```

The source/acquisition selector in the frontend maps its simpler choices onto
these wire-facing modes. In particular, maximum-analog and mixed live capture
use `analog_all_continuous` and `mixed_continuous`.

## Channels

`ChannelInfo` supports digital, analog, derived, decoder, and bus rows. It
stores display state, analog calibration and threshold fields, bus members,
derived-channel provenance, and physical board mapping (`board_label`,
`fpga_pin`, `header`, `pin_index`, `adc_channel`, `physical_available`).

Default digital IDs are `d0`-`d15`. Analog IDs follow their actual ADC mux
channel (`a1`, `a2`, and so on), so IDs are not guaranteed to be dense.
Derived and bus IDs are allocated by their services.

## Trigger configuration

`TriggerConfig` covers edge/level, pattern/bus, pulse/timeout/sequence,
protocol byte/address/NACK, glitch, decoder-error, and generic FPGA pattern
triggers. It carries channel references, value/mask, baud/clock framing,
qualification windows, occurrence, holdoff/rearm, pre-trigger samples,
position, and the resolved execution class (`hardware`, `post_capture`, or
`unavailable`). See [Triggers](triggers.md).

## Analysis records

| Model | Important fields |
|---|---|
| `DecoderInstance` | Registry `decoder_id`, channel-role map, settings, region, status, event/warning counts, quality score |
| `MeasurementInstance` | Measurement type, channels, scope/region, settings, result/error |
| `Marker` | Sample, label/note, kind, optional channel/color |
| `ExportRecord` | Format, filename, timestamp, request options |

Decoder events are stored in per-instance files rather than embedded in
`session.json`, keeping the session response bounded.

## Device provenance

`DeviceMetadata` records the driver, device/connection/port, firmware and
protocol versions, system/sample clocks, mock flag, and extensible metadata.
Generator-assisted captures additionally store generator provenance on the
session.

## Storage and mutation rules

- `session.json` is the mutable metadata/analysis record.
- `waveform.npz` is the captured raw data and is not changed by decoders,
  filters, measurements, or exports.
- Live chunk appends use uncompressed NumPy persistence to avoid blocking the
  rolling capture cadence.
- `touch()` updates `modified_at` before saving metadata changes.
- Session duplication assigns a new ID while preserving waveform and analysis
  artifacts.

See [Session Stores](session-stores.md) for filesystem layout and lifecycle.
