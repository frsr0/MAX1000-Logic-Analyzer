# Capture Strategies

**Directory:** `backend/app/hardware/strategies/`

## Purpose

Strategy pattern (per ADR-001) that decomposes the monolithic `capture()` method into one class per capture mode. Each strategy implements a single-attempt capture; the base class handles retry and recovery via a template method.

## Architecture

```mermaid
classDiagram
    class CaptureStrategy {
        <<abstract>>
        +modes: ClassVar[Set[str]]
        +capture(dev, settings, progress, stop_evt) CaptureResult
        #_pre_capture(dev, settings)
        #_do_capture(dev, settings, trigger, progress, stop_evt) CaptureResult*
        #_recover(dev)
    }
    class DigitalCaptureStrategy {
        +modes = {"digital"}
        #_do_capture() CaptureResult
    }
    class MixedCaptureStrategy {
        +modes = {"mixed"}
        #_do_capture() CaptureResult
    }
    class AnalogCaptureStrategy {
        +modes = {"analog_fast", "analog"}
        #_do_capture() CaptureResult
    }
    class AnalogAllCaptureStrategy {
        +modes = {"analog_all"}
        #_do_capture() CaptureResult
    }
    class NarrowDigitalCaptureStrategy {
        +modes = {"digital_narrow"}
        #_do_capture() CaptureResult
    }
    CaptureStrategy <|-- DigitalCaptureStrategy
    CaptureStrategy <|-- MixedCaptureStrategy
    CaptureStrategy <|-- AnalogCaptureStrategy
    CaptureStrategy <|-- AnalogAllCaptureStrategy
    CaptureStrategy <|-- NarrowDigitalCaptureStrategy
```

## `CaptureDevice` Protocol

A narrow interface extracted from the 5 methods `OLSDeviceSPI` actually uses during capture:

```python
class CaptureDevice(Protocol):
    sample_clk: float
    def capture(self, mode: int, num_samples: int, start_offset: int,
                rate_divider: int, trigger_mask: int, trigger_value: int,
                progress: ProgressCb = None,
                stop_evt: threading.Event = None) -> bytes: ...
    def set_analog_config(self, mode: int, adc_channel: int = 1) -> None: ...
    def set_readback_compression(self, mode: str) -> None: ...
    def reset(self) -> None: ...
    def flush(self) -> None: ...
    raw_flags: int (property)
    fast_mode_enabled: bool (property)
```

## Template Method

```python
class CaptureStrategy(ABC):
    def capture(self, dev, settings, trigger, progress, stop_evt):
        try:
            self._pre_capture(dev, settings)     # configure device
            return self._do_capture(dev, settings, trigger, progress, stop_evt)
        except Exception:
            self._recover(dev)                    # recovery (reset + flush)
            # retry once
            return self._do_capture(dev, settings, trigger, progress, stop_evt)
```

## Strategy Details

### `DigitalCaptureStrategy` (digital.py)

- Configures: `MODE_DIGITAL`, set divider, sample count, trigger
- Handles: single-shot and rolling (repeated finite captures)
- Readback: `raw`, direct `rle`, or packed-delta-plus-RLE `delta_rle`
- BRAM fast path for ≤1024 samples

### `MixedCaptureStrategy` (mixed.py)

Readback compression is digital-only. `delta_rle` expands packed delta words
after RLE; `rle` expands full words directly. Mixed/analog readback remains raw.

- Configures: `MODE_MIXED`, ADC scan profile
- Captures: 16 digital channels + ADC0..ADC3 scan
- Frame rate: 125 kframes/s (ADC-limited)
- Readback: raw (no compression on analog frames)

### `AnalogCaptureStrategy` (analog.py)

- Configures: `MODE_ANALOG_FAST`, one ADC lane
- Captures: single analog channel at 1 MSPS
- Readback: raw

### `AnalogAllCaptureStrategy` (analog_all.py)

- Configures: `MODE_ANALOG_ALL`, 8 decoded ADC lanes in the raw frame
- Captures: the maximum analog frame format at 125 kframes/s
- Readback: raw

### `NarrowDigitalCaptureStrategy` (narrow_digital.py)

- Configures: `MODE_NARROW_DIGITAL`, channel select
- Captures: packed 1-channel high-speed at 200 MHz
- Depth: 67 million logical samples
- Readback: raw

## Retry / Recovery

- 2 capture attempts before propagating the error
- `_recover()`: calls `dev.reset()` followed by `dev.flush()`
- Each strategy can override `_pre_capture()` for mode-specific setup
- Base class catches `HardwareError` and `Exception`

## Factory Dispatch

In `ExistingHostAdapter`:

```python
def _strategy_for(self, settings: CaptureSettings) -> CaptureStrategy:
    mode = settings.mode
    if mode in ('digital',):
        return DigitalCaptureStrategy()
    elif mode == 'mixed':
        return MixedCaptureStrategy()
    elif mode in ('analog_fast', 'analog'):
        return AnalogCaptureStrategy()
    elif mode == 'analog_all':
        return AnalogAllCaptureStrategy()
    elif mode == 'digital_narrow':
        return NarrowDigitalCaptureStrategy()
    else:
        raise ValueError(f"Unknown capture mode: {mode}")
```

## Dependencies

| File | Purpose |
|---|---|
| `base.py` | `CaptureStrategy` ABC, `CaptureDevice` protocol |
| `digital.py` | Digital capture strategy |
| `mixed.py` | Mixed capture strategy |
| `analog.py` | Analog-fast capture strategy |
| `analog_all.py` | Maximum analog capture strategy |
| `narrow_digital.py` | Narrow packed digital capture strategy |
| `__init__.py` | Re-exports all strategy classes |

## Testing

Covered by `test_existing_host_adapter.py` (19 tests) which exercises each strategy through a mock `CaptureDevice`.
