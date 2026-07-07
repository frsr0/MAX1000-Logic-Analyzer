# Machine-In-Loop (MIL)

**Directory:** `backend/app/mil/`

## Purpose

Automated Machine-In-Loop subsystem for running predefined test scenarios. Configures the signal generator to transmit protocol frames, captures the response, decodes it, and validates against expected values. Designed for CI and automated regression testing.

## Files

| File | Purpose |
|---|---|
| `service.py` (18.3 KB) | MIL orchestration logic, preset definitions |
| `model.py` (2.5 KB) | MIL Pydantic models and configuration |

## Protocol Support

| Protocol | Description |
|---|---|
| `uart` | UART loopback: generate bytes → capture → verify |
| `modbus_uart` | Modbus RTU query/response: generate frame → capture → decode → validate CRC |
| `rs485_modbus` | RS-485 half-duplex Modbus: generate with direction control → capture → validate |

## Models

### `MilConfig`

```python
class MilConfig(BaseModel):
    protocol: MilProtocol                    # uart | modbus_uart | rs485_modbus
    registers: List[MilRegister]             # register read/write definitions
    trigger: Optional[MilTrigger] = None     # trigger on specific register or value
    timing: MilTiming                        # inter-byte and inter-frame gaps
```

### `MilRegister`

```python
class MilRegister(BaseModel):
    address: int                             # device/register address
    value: Optional[int] = None              # expected or write value
    read_len: int = 1                        # number of registers to read
    description: str = ""
```

### `MilTiming`

```python
class MilTiming(BaseModel):
    inter_byte_gap_us: float = 0            # gap between bytes in a frame
    inter_frame_gap_us: float = 1000        # gap between frames (1 ms)
    response_timeout_ms: int = 100           # max wait for response
```

### `MilRuntimeStatus`

```python
class MilRuntimeStatus(BaseModel):
    running: bool
    current_step: int
    total_steps: int
    transactions: List[MilTransactionResponse]
```

## Presets

Pre-configured test scenarios for common devices:

| Preset | Protocol | Description |
|---|---|---|
| UART loopback | uart | Send bytes, capture loopback, compare |
| Modbus read holding registers | modbus_uart | Read 10 holding registers from slave 1 |
| Modbus write single register | modbus_uart | Write to single register, read back to verify |
| RS-485 Modbus poll | rs485_modbus | Poll sensor over RS-485 |

## Service

```python
class MilService:
    def list_presets() -> List[MilPresetSummary]
    def load_preset(preset_id: str) -> MilConfig
    def run(dev: HardwareDevice, config: MilConfig,
            progress: ProgressCb = None,
            stop_evt: threading.Event = None) -> MilRuntimeStatus
```

`run()` flow:
1. For each register in config:
   a. Configure generator with appropriate protocol frame
   b. Start generator
   c. Arm capture
   d. Wait for capture complete
   e. Decode captured data
   f. Validate decoded values against expectations
   g. Record transaction result
2. Return runtime status with all transaction results

## API Endpoints

```
GET  /api/mil/presets               → list available presets
POST /api/mil/configure             ← preset_id or MilConfig
POST /api/mil/start                 → start MIL run
POST /api/mil/stop                  → abort running test
GET  /api/mil/status                → runtime status
```

## Dependencies

| Module | File |
|---|---|
| `HardwareDevice` | `hardware/base.py` |
| `GeneratorConfig` | `generator/model.py` |
| `MilConfig`, `MilPresetSummary` | `mil/model.py` |
| `CaptureSettings` | `capture/session.py` |
| `decoder_registry` | `decoders/registry.py` |
