# Mock Device

**File:** `backend/app/hardware/mock_device.py` (329 lines), `backend/app/hardware/mock_signals.py` (6.7 KB)

## Purpose

Fully functional mock device for development and testing without real hardware. Supports 10 synthetic scenarios that exercise the entire app — capture, decoders, measurements, exports, generator loopback. Mock analog channels exist **only** here; the real adapter never fabricates analog data.

## Architecture

```python
class MockDevice(HardwareDevice):
    SAMPLE_CLK = 200e6  # 200 MHz sample clock
```

## Mock Scenarios

| ID | Name | Description |
|---|---|---|
| `demo_mixed` | Counters + UART + I2C + SPI + PWM | Multiple protocols on different channels |
| `square_waves` | Square waves (per-channel frequencies) | Independent frequencies per pin |
| `uart` | UART frames on CH0 | `'Hello MAX1000!'` at 115200 baud |
| `i2c` | I2C transaction | SCL=CH1, SDA=CH2 with address/data |
| `spi` | SPI transaction | SCLK/MOSI/MISO/CS on CH4-7 |
| `pwm` | PWM sweep on CH3 | Duty cycle ramp |
| `glitchy` | Noisy/glitchy square on CH0 | With injected noise |
| `edge_cases` | All-zero, all-one, slow CH0 | Boundary conditions |
| `analog_demo` | Analog sine/square/ramp/noise | Mixed mode mock analog |
| `long_stress` | Long capture stress test | Deep capture scenario |

## Signal Generation (`mock_signals.py`)

Pure numpy signal generators:
- `square_wave(freq, sample_rate, num_samples)` — configurable duty cycle
- `uart_frame(data, sample_rate, baud)` — serial bit stream with start/stop
- `i2c_transaction(scl_freq, sda_data, sample_rate)` — SCL + SDA waveforms
- `spi_transaction(cpol, cpha, data, sample_rate)` — SCLK + MOSI + MISO
- `pwm_sweep(freq_center, duty_range, sample_rate)` — PWM with varying duty
- `analog_sine(freq, amplitude, dc_offset, sample_rate)` — analog sine wave
- `analog_noise(amplitude, sample_rate)` — uniform noise

## Generator Support

The mock device implements:
- `generator_status()` — reports supported with mock state
- `generator_configure(cfg)` — records config for verification
- `generator_start/stop()` — state machine for mock generation
- `capture_with_generator()` — returns scenario data as generator loopback result
- `self_test()` — runs mock checks (always passes)

## Command Log

The mock maintains a command log (last 250 entries) accessible via `get_debug_info()` for debugging the adapter protocol.

## Dependencies

| Module | File |
|---|---|
| `HardwareDevice`, `CaptureResult` | `hardware/base.py` |
| `CaptureSettings` | `capture/session.py` |
| `mock_signals` | `hardware/mock_signals.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_api.py` (67 tests) | API endpoints through mock device |
| E2E via `PLAYWRIGHT_USE_MOCK=1` | Full UI through mock |
