# Generator Controller

**Directory:** `backend/app/generator/`

## Purpose

Controls the FPGA's on-board signal generator and orchestrates the loopback self-test workflow. The generator produces UART, I2C, SPI, PWM, or pattern waveforms on selected output pins.

## Files

| File | Purpose |
|---|---|
| `controller.py` (9 KB) | Generator command dispatch, loopback test orchestration |
| `model.py` (989 B) | GeneratorConfig and GeneratorStatus Pydantic models |

## GeneratorConfig

```python
class GeneratorConfig(BaseModel):
    protocol: str = "uart"              # uart, i2c, spi, pwm, pattern
    baud_rate: int = 115200             # symbol rate for serial protocols
    data: str = ""                      # hex string of bytes to transmit
    pin_tx: Optional[int] = None        # TX data pin (pool index)
    pin_scl: Optional[int] = None       # SCL/clock pin (pool index)
    repeat: bool = False                # loop data continuously
    i2c_read_len: Optional[int] = None  # I2C read transaction length
    i2c_dev_r: Optional[str] = None     # I2C device address for read
```

## GeneratorStatus

```python
class GeneratorStatus(BaseModel):
    supported: bool = True
    running: bool = False
    fifo_fill: int = 0                  # generator FIFO fill level (0..256)
    detail: str = ""
```

## Controller

```python
class GeneratorController:
    def configure(self, dev: HardwareDevice, cfg: GeneratorConfig) -> None
    def start(self, dev: HardwareDevice) -> None
    def stop(self, dev: HardwareDevice) -> None
    def status(self, dev: HardwareDevice) -> GeneratorStatus
```

The controller translates `GeneratorConfig` to the hardware register writes:
1. Set protocol via `REG_GEN_PROTO`
2. Set baud rate via `REG_GEN_BAUD`
3. Set pins via `REG_GEN_PINS`
4. Load bit-bang symbols (pre-computed by `host/driver/bit_bang.py`)
5. Start generation via `CMD_GEN_START`

## Loopback Self-Test

The generator self-test workflow validates the entire capture-generate-decode pipeline:

```python
def run_loopback_test(
    dev: HardwareDevice,
    settings: CaptureSettings,
    gen_cfg: GeneratorConfig
) -> dict:
    """Configure generator, capture output, decode, compare."""
    1. dev.generator_configure(gen_cfg)
    2. dev.generator_start()
    3. Capture: dev.capture_with_generator(settings, gen_cfg)
       or dev.capture(settings) with active generator
    4. dev.generator_stop()
    5. Decode captured data using appropriate protocol decoder
    6. Compare decoded data with original gen_cfg.data
    7. Return {passed, expected, decoded, errors}
```

Used by:
- `hw_smoke_test.py` — hardware validation
- Generator page self-test button in the UI

## API Endpoints

```
GET  /api/generator/capabilities    → supported protocols + constraints
GET  /api/generator/status          → current generator state
POST /api/generator/configure       ← GeneratorConfig
POST /api/generator/start           → start generation
POST /api/generator/stop            → stop generation
POST /api/generator/send            ← data (append to FIFO while running)
POST /api/generator/self-test       → loopback capture + decode + compare
```

## Dependencies

| Module | File |
|---|---|
| `HardwareDevice` | `hardware/base.py` |
| `GeneratorConfig`, `GeneratorStatus` | `generator/model.py` |
| `CaptureSettings` | `capture/session.py` |
| `bit_bang` symbol encoders | `host/driver/bit_bang.py` |
