# Existing Host Adapter

**File:** `backend/app/hardware/existing_host_adapter.py` (738 lines)

## Purpose

`HardwareDevice` implementation that wraps the existing, proven `OLSDeviceSPI` host driver (`host/driver/ols_spi_device.py`). Delegates capture modes to mode-specific `CaptureStrategy` classes (per ADR-001). Reuses the host driver unchanged — the adapter mirrors the exact call sequence of the tkinter GUI.

## Key Constants

| Constant | Value | Description |
|---|---|---|
| `ADC_SCAN_FRAME_RATE_HZ` | 125,000.0 | Mixed/max-analog frame rate |
| `ADC_FAST_FRAME_RATE_HZ` | 1,000,000.0 | High-speed single-analog frame rate |
| `DIGITAL_LIVE_SAMPLE_RATE_HZ` | 50,000,000.0 | Tested ceiling for live rolling UI |
| `DIGITAL_FAST_BRAM_SAMPLES` | 1,024 | BRAM fast capture depth |
| `DIGITAL_SDRAM_WORDS` | 4,194,304 | Full SDRAM depth (hardware-validated) |
| `DIGITAL_NARROW_LOGICAL_SAMPLES` | 67,108,864 | Narrow digital: words × 16 |

## Architecture

```
ExistingHostAdapter
  │
  ├── holds OLSDeviceSPI instance
  │
  ├── capture(settings)
  │     └── _strategy_for(settings) → CaptureStrategy
  │           └── strategy.capture(dev, settings, ...)
  │                 └── template method with retry/recovery
  │
  ├── generator_configure/capture_with_generator
  ├── self_test()
  └── get_debug_info()
```

## Key Methods

### `capture(settings)`

1. Validates settings
2. Calls `_strategy_for(settings)` to get the appropriate `CaptureStrategy`
3. Delegates to `strategy.capture(dev, settings, progress, stop_evt)`
4. Returns `CaptureResult`

Strategy dispatch is a simple `if/elif` chain mapping mode strings to strategy instances:

| Mode | Strategy | Class |
|---|---|---|
| `"digital"` single-shot/rolling | `DigitalCaptureStrategy` | `strategies/digital.py` |
| `"mixed"` | `MixedCaptureStrategy` | `strategies/mixed.py` |
| `"analog_fast"` | `AnalogCaptureStrategy` | `strategies/analog.py` |
| `"analog_all"` | `AnalogAllCaptureStrategy` | `strategies/analog_all.py` |
| `"digital_narrow"` | `NarrowDigitalCaptureStrategy` | `strategies/narrow_digital.py` |

### `self_test()`

Drives the same adapter path the web app uses:
1. Connect + sample-clock detect
2. Capabilities query
3. Debug CH0 PWM loopback capture
4. 4096-sample digital capture + sanity checks
5. UART generator loopback (`CMD_GEN_CAPTURE`) decoded and byte-compared

The debug PWM is a real register-controlled FPGA source, not a mock or
host-bit-bang waveform. The adapter exposes it for diagnostics and capture
self-tests; generator output takes priority when both paths are enabled.

### `generator_configure(cfg)`

Translates `GeneratorConfig` protocol to register writes:
- UART: set baud, load bitbang symbols, start
- I2C/SPI: set protocol, pins, load bitbang symbols
- PWM: set period/duty on debug CH0

## Hardware Availability Check

```python
def hardware_available() -> bool:
    try:
        from ftd2xx import FTD2XX
        # check for FTDI device present
        return True
    except Exception:
        return False
```

## Dependencies

| Module | File |
|---|---|
| `OLSDeviceSPI` | `host/driver/ols_spi_device.py` |
| `wire_format` | `host/driver/wire_format.py` |
| `CaptureStrategy` base + 5 implementations | `hardware/strategies/*.py` |
| `CaptureSettings`, `DeviceMetadata` | `capture/session.py` |
| `max1000_board` pin maps | `hardware/max1000_board.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_existing_host_adapter.py` (19 tests) | Full adapter flow through mock driver |
| `hw_smoke_test.py` (7 tests) | Hardware smoke test driving adapter |
| `host/debug/hwt_test_debug_pwm_registers.py` | CH0 register/readback, PWM frequency/duty, disable, and codec sanity |
| `host/debug/hwt_test_compression_matrix.py` | 12-case direct raw-vs-RLE payload matrix with lossless checks |
