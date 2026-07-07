# Capture Manager

**File:** `backend/app/capture/capture_manager.py` (509 lines)

## Purpose

Orchestrates capture life cycle: owns the hardware device instance, manages the single-client control lock, runs capture on worker threads, coordinates decoder runs, and broadcasts state changes via WebSocket.

## Architecture

```
CaptureManager
  │
  ├── ControlLock (single-client hardware access)
  ├── HardwareDevice (adapter or mock)
  ├── SessionStore (persistence)
  │
  ├── start_capture(settings, name) → spawns worker thread
  ├── stop_capture() → signals stop event
  ├── get_capture_state() → idle/arming/armed/busy/done/error
  │
  ├── run_decoder(session, inst) → spawns decoder worker
  ├── cancel_decoder(decoder_id) → signals cancellation
  │
  └── WebSocket notifications:
        device_connected, capture_progress, capture_complete,
        session_created, decoder_progress, decoder_complete, warning, log
```

## ControlLock

Ensures only one client controls the hardware at a time:

```python
class ControlLock:
    def acquire(client_id, name="", force=False) -> bool
    def release(client_id) -> bool
    def check(client_id) -> bool          # permission check
    def info() -> dict                    # {held, holder, holder_name, acquired_at}
```

- Default: first client to acquire gets the lock
- `force=True`: steals lock from current holder
- REST handlers check `require_control(client_id)` before hardware commands
- `Control-Lock` header on Settings page UI

## Capture State Machine

```
IDLE → ARMED → BUSY → DONE → IDLE
                 ↓       ↑
               ERROR ────┘
```

States and transitions:
- `IDLE`: waiting for capture start
- `ARMING`: hardware arm in progress
- `ARMED`: hardware armed, waiting for trigger
- `BUSY`: capture running (polling for completion)
- `DONE`: capture complete, data read back
- `ERROR`: capture failed

## Worker Threads

### Capture Thread

```
capture worker:
  1. get device + settings
  2. check control lock
  3. dev.capture(settings, progress_cb, stop_evt)
  4. on complete: save session, broadcast notification
  5. on error: broadcast error, set state
```

### Decoder Thread

```
decoder worker:
  1. load session waveform data
  2. instantiate decoder from registry
  3. run decoder with progress/cancellation callbacks
  4. on complete: save events, broadcast
  5. on cancel: raise DecodeCancelled
```

## Progress Reporting

Capture progress is reported via `ProgressCb` (read_count, total_count, phase_name):
- Phase: `"arm"`, `"waiting"`, `"readback"`, `"done"`
- WebSocket: `capture_progress` message with `{read, total, phase}`

## Session Persistence

After capture:
1. Create session directory `data/sessions/<id>/`
2. Write `session.json` (metadata, channels, settings)
3. Write `waveform.npz` (raw sample data, immutable)
4. Broadcast `session_created` via WebSocket

## Dependencies

| Module | File |
|---|---|
| `HardwareDevice`, `CaptureResult` | `hardware/base.py` |
| `ExistingHostAdapter`, `hardware_available` | `hardware/existing_host_adapter.py` |
| `MockDevice` | `hardware/mock_device.py` |
| `Session`, `CaptureSettings` | `capture/session.py` |
| `SessionStore` | `capture/session_store.py` |
| `WebSocket manager` | `websocket/manager.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_core.py` | Capture manager lifecycle |
| `test_api.py` | API endpoints through manager |
