# Triggers

**Directory:** `backend/app/triggers/`

## Purpose

Trigger configuration model, hardware-vs-post-capture classification, and software trigger search for patterns not implemented in FPGA hardware.

## Trigger Model (`model.py`)

```python
@dataclass
class TriggerConfig:
    type: Literal["rising_edge", "falling_edge", "any_edge", "pattern",
                  "uart_byte", "immediate", "none"]
    channel_mask: Optional[int] = None   # bitmask for edge triggers
    value: Optional[Union[int, bytes]] = None  # pattern / UART byte
    execution: Literal["hardware", "post_capture", "unavailable"] = "hardware"
    occurrence: int = 1              # first, second, nth matching result
    holdoff_us: float = 0             # ignore matches during holdoff
    width_s: Optional[float] = None   # pulse-width match target
```

### Trigger Types

| Type | Description |
|---|---|
| `rising_edge` | Armed channel(s) transition 0→1 |
| `falling_edge` | Armed channel(s) transition 1→0 |
| `any_edge` | Armed channel(s) any transition |
| `pattern` | Multi-channel digital pattern match |
| `uart_byte` | UART byte value match (hardware protocol trigger) |
| `immediate` | Capture starts immediately on arm |
| `none` | No trigger (manual stop) |

Software search can additionally match a bus value/byte, pulse width, protocol
event, or a selected edge after a start point. `occurrence` selects the nth
match, while `holdoff_us` suppresses closely spaced matches. The Trigger panel
provides previous/next match navigation and moves the waveform viewport to the
selected sample.

## Hardware Support (`hardware_support.py`)

Classifies triggers by execution capability:

| Trigger Type | FPGA Hardware | Post-capture (Software) |
|---|---|---|
| Rising edge | ✅ (any channel mask) | — |
| Falling edge | ✅ (any channel mask) | — |
| Any edge | ✅ (any channel mask) | — |
| Pattern | — | ✅ |
| UART byte | ✅ | — |
| Immediate | ✅ | — |

Hardware triggers are set via `REG_TRIGGER_MASK` (0x10) and `REG_TRIGGER_VALUE` (0x11) registers before arming.

## Software Trigger (`software_trigger.py`)

Post-capture trigger search for triggers not available in hardware:

```python
def find_software_trigger(
    digital_samples: np.ndarray,
    trigger_config: TriggerConfig
) -> Optional[int]:
    """Search digital samples for a trigger pattern.
    Returns the sample index of the first match, or None.
    """
```

- Pattern match: searches for a specific word value on enabled channels
- Edge search: finds first rising/falling edge after a configurable start point
- Used when `trigger.execution == "post_capture"`
- Applied after capture completes (post-hoc trigger point marking)

## Trigger Flow

1. User configures trigger in UI → sent as `CaptureSettings.trigger`
2. `ExistingHostAdapter` checks `trigger.execution`:
   - `"hardware"` → write trigger registers, arm
   - `"post_capture"` → arm with immediate trigger, run software search after capture
   - `"unavailable"` → reject with error
3. Post-capture: `CaptureManager` calls `find_software_trigger()` and sets `trigger_sample` on the session

## Dependencies

| File | Purpose |
|---|---|
| `model.py` | TriggerConfig dataclass |
| `hardware_support.py` | Execution capability classification |
| `software_trigger.py` | Post-capture trigger search |
| `CaptureSettings` | `capture/session.py` |
