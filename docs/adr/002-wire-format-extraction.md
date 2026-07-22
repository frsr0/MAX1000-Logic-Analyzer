# ADR-002: Extract Wire Format Module from Driver

**Status:** Accepted  
**Date:** 2026-07-07  
**Deciders:** Architecture review grilling session

## Context

`host/driver/ols_spi_device.py` (2355 lines) conflates three separate concerns:

1. **Wire-format parsing**: `wire_to_payload()`, `decode_analog_frames()`, `unpack_narrow_digital_words()`, `apply_glitch_filter()` — pure functions with no device state.
2. **Device constants**: `MODE_DIGITAL`, `MODE_MIXED`, `MODE_ANALOG_FAST`, `MODE_NARROW_DIGITAL`, frame-stride calculations.
3. **Device orchestration**: `OLSDeviceSPI` class — capture, generator loaders, accelerometer dialogue.

The backend's `ExistingHostAdapter` imports driver internals directly (`from driver.ols_spi_device import MODE_MIXED, analog_frame_stride, wire_to_payload`), creating a tight coupling that prevents testing the adapter without the full driver stack. This coupling also makes the depth of the wire-format concern invisible — it's buried inside a 2355-line file.

ADR-001 (Capture Strategy Pattern) creates a `CaptureDevice` protocol that strategies depend on, which requires the wire-format functions to be importable without pulling in the full device class.

## Decision

Extract all wire-format functions and mode constants into a new pure module at `host/driver/wire_format.py`.

### Scope

**Functions moved:**
- `wire_to_payload(data, mode)` — strip per-frame padding
- `payload_to_wire(data, mode)` — add per-frame padding
- `analog_frame_stride(mode)` — payload bytes per frame
- `analog_wire_stride(mode)` — wire bytes per frame
- `decode_analog_frames(payload, mode)` — parse packed ADC frames
- `unpack_narrow_digital_words(data, channel, sample_count)` — expand 1-bit packed stream
- `apply_glitch_filter(data, threshold, num_channels)` — digital hysteresis
- `narrow_digital_flags(channel)` — encode narrow channel mode flag

**Constants moved:**
- `MODE_DIGITAL`, `MODE_MIXED`, `MODE_ANALOG_FAST`, `MODE_ANALOG_ALL`, `MODE_NARROW_DIGITAL`
- All helper constants that only these functions reference (`ADC_VREF`, `ADC_BITS`, frame-stride helpers)

**Internal helpers moved:**
- `_decode_adc()` — only called by `decode_analog_frames()`

### Internal import style

`ols_spi_device.py` imports from the new module via `from .wire_format import wire_to_payload, ...` and calls functions directly. No indirection, no re-export — the device module is a consumer like any other client.

### Testing

`wire_format.py` is testable without hardware — all functions are pure NumPy. Existing `ols_spi_device` tests continue to pass unchanged.

## Consequences

**Positive:**
- Wire-format concern visible in its own file (~250 lines), not buried in a 2355-line device driver.
- `ExistingHostAdapter` (and strategies from ADR-001) import only `wire_format`, not the full device class — decoupling the seam.
- Pure functions are testable without hardware or device instantiation.
- Backend no longer depends on `driver.ols_spi_device` internal structure.

**Negative:**
- One extra import path to maintain. Both `ols_spi_device.py` and `existing_host_adapter.py` now import from `wire_format`.
- The split requires updating all inline imports in the backend adapter (4-5 import lines).

## Alternatives considered

- **Leave in place**: ADR-001 strategies would continue importing from `ols_spi_device` — preserves the leaky seam and prevents testing strategies without the real driver.
- **Narrower extraction** (only what the backend currently imports): defers the rest of the split, leaving the function definitions duplicated in concept across two files.
- **Split further** into `wire_format.py` + `capture_modes.py`: creates two tiny files with no clearer seam.
