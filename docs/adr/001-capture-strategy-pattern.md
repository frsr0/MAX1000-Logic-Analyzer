# ADR-001: Capture Mode Strategy Pattern

**Status:** Accepted  
**Date:** 2026-07-07  
**Deciders:** Architecture review grilling session

## Context

`ExistingHostAdapter.capture()` in `backend/app/hardware/existing_host_adapter.py` is a 300-line method with 5 distinct capture sub-paths (mixed, analog-fast, analog-all, narrow-digital, standard digital) intertwined via `elif` branches. Each path duplicates the same retry-on-failure pattern. The method is **shallow** — its interface (`capture(settings)`) is nearly as complex as reading the implementation.

Running the deletion test: removing the capture body would concentrate complexity across the adapter — every path is tangled with retry, recovery, and logging logic that can't simply move elsewhere. This is the signal for a deepening opportunity.

The grilling session (2026-07-07) settled the following design decisions.

## Decision

Decompose the monolithic capture method into a **strategy pattern**: one class per capture mode, each implementing a uniform interface, dispatched via a factory method.

### Interface

```python
class CaptureStrategy(ABC):
    mode: ClassVar[set[str]]  # capture modes handled

    def capture(
        self, dev: CaptureDevice,
        settings: CaptureSettings,
        progress: ProgressCb | None = None,
        stop_evt: threading.Event | None = None,
    ) -> CaptureResult: ...
```

The strategy receives the full `CaptureSettings` object — it self-selects the fields it needs. No parallel settings hierarchy.

### Dispatch

A factory method `_strategy_for(settings) -> CaptureStrategy` in `ExistingHostAdapter` maps mode strings to strategy instances via an `if/elif` chain. Explicit, traceable, testable in isolation.

### Retry / recovery

The base class uses a **template method**: `capture()` calls an abstract `_do_capture()` and wraps it with retry logic (2 attempts + `_recover()` hook). Subclasses implement only the single-attempt capture logic.

### Device interface

Strategies receive a `CaptureDevice` protocol — a narrow interface extracted from the 5 methods `OLSDeviceSPI` actually uses during capture. Wire-format parsing (`wire_to_payload`, `decode_analog_frames`) is done via pure functions, not driver internals.

### Strategy count

5 strategies, one per distinct capture path:
- `DigitalCaptureStrategy` — single-shot and rolling general-purpose digital
- `MixedCaptureStrategy` — time-correlated digital+analog frames
- `AnalogCaptureStrategy` — analog-fast (1 ADC lane)
- `AnalogAllCaptureStrategy` — maximum analog (4 ADC lanes)
- `NarrowDigitalCaptureStrategy` — packed 1-channel high-speed digital

`stream_capture()` (narrow-digital streaming generator) stays in the adapter as a special case — it's a different abstraction (generator, no retry).

### File layout

```
backend/app/hardware/strategies/
  __init__.py
  base.py             # CaptureStrategy ABC, CaptureDevice protocol
  digital.py
  mixed.py
  analog.py
  analog_all.py
  narrow_digital.py
```

## Consequences

**Positive:**
- Each capture path is contained in a ~60-100 line file — no scrolling 300 lines to find the mixed-mode branch.
- Adding a new capture mode (e.g. PACKED_MSO) is a single new file with zero changes to existing strategies.
- Retry/recovery logic lives in one place and can be tested with a mock strategy.
- `CaptureDevice` protocol makes strategies testable without importing the real hardware driver.

**Negative:**
- More files (6 new) — but each is self-contained and follows a uniform pattern.
- Factory method must be updated when a new strategy is added — but that's one line in one file, explicitly visible.

## Alternatives considered

- **Registry dict** (strategies self-register by mode): harder to trace which strategy handles which mode; requires grepping all subclasses.
- **Caller dispatch** (keep `if/elif` in `capture()` but delegate each branch body): doesn't reduce the adapter's interface surface.
- **Keep monolithic**: continues to accumulate mode-specific complexity in one method.
