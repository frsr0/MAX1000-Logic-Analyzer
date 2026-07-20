# Generator Controller

**Directory:** `backend/app/generator/`

## Purpose

Controls the FPGA's on-board signal generator and orchestrates the loopback
self-test workflow. The real MAX1000 generator produces UART, RS-485, I²C, SPI,
SWD, and raw Bit Banger waveforms through a two-output symbol engine. Optional
RS-485 DE and SPI CS/MISO routes are validated against the connected device's
capability descriptor before hardware is touched.

## Files

| File | Purpose |
|---|---|
| `controller.py` (9 KB) | Generator command dispatch, loopback test orchestration |
| `model.py` (989 B) | GeneratorConfig and GeneratorStatus Pydantic models |

## GeneratorConfig

```python
class GeneratorConfig(BaseModel):
    protocol: str = "uart"              # uart|rs485|i2c|spi|swd|bitbang
    data_hex: str = ""                  # payload bytes
    baud: int = 115200
    tx_pin: int = 3                      # data/MOSI/SDA/SWDIO pool pin
    scl_pin: int = 1                     # clock/SCLK/SCL/SWCLK pool pin
    repeat: int = 1
    continuous: bool = False
    i2c_address: int = 0x19
    i2c_register: int = 0x0F
    i2c_read_len: int = 0
    extra: dict = {}                    # DE/CS/MISO pins and capture channels
```

## GeneratorStatus

```python
class GeneratorStatus(BaseModel):
    busy: bool = False
    running: bool = False
    protocol: Optional[str] = None
    config: Optional[dict] = None
    last_error: Optional[str] = None
    supported: bool = True
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
4. Set optional routes via `REG_GEN_AUX_PINS` (`0x35`)
5. Set direct auxiliary capture channels via `REG_GEN_CAPTURE_AUX` (`0x45`)
6. Load bit-bang symbols (pre-computed by `host/driver/bit_bang.py`)
7. Start generation via `CMD_GEN_START`

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
