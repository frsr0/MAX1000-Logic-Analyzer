# WebSocket & Diagnostics

**Directory:** `backend/app/websocket/`, `backend/app/diagnostics/`

## WebSocket Manager (`websocket/manager.py`)

Topic-based broadcast manager for real-time UI updates:

```python
class WebSocketManager:
    def subscribe(topic: str, websocket: WebSocket) -> None
    def unsubscribe(topic: str, websocket: WebSocket) -> None
    def broadcast(topic: str, message: dict) -> None
```

### Topics

| Topic | Messages Sent | Frequency |
|---|---|---|
| `status` | `device_connected`, `device_disconnected`, `capture_state`, `session_created` | On change |
| `capture` | `capture_progress` (read/total/phase), `capture_complete`, `capture_error` | ~10 Hz during capture |
| `logs` | `log` entries (level, message, source, timestamp) | As produced |
| `session/{id}` | `waveform_ready`, `session_updated`, `decoder_updated`, `measurement_updated` | On session modification |
| `decoder/{id}` | `decoder_progress` (0..1), `decoder_complete`, `decoder_error` | During decode |

### Message Format

```python
{
    "type": "capture_progress",
    "data": {"read": 2048, "total": 4096, "phase": "readback"},
    "timestamp": "2026-07-07T12:34:56.789Z"
}
```

### WebSocket Endpoints

| Path | Topic |
|---|---|
| `/ws/status` | `status` |
| `/ws/capture` | `capture` |
| `/ws/logs` | `logs` |
| `/ws/session/{session_id}` | `session/{id}` |
| `/ws/decoder/{session_id}` | `decoder/{session_id}` (routes to per-decoder topics) |

## Ring Logger (`diagnostics/logger.py`)

Thread-safe ring-buffer log for diagnostics:

```python
class RingLogger:
    MAX_ENTRIES = 1000

    def log(level: str, message: str, source: str = "") -> None:
        """Add log entry, broadcast via WS if connected."""

    def get_recent(count: int = 100) -> List[LogEntry]:
        """Return last N entries sorted newest-first."""
```

Log entry: `{level, message, source, timestamp}`

Levels: `debug`, `info`, `warning`, `error`

## Debug Bundle (`diagnostics/debug_bundle.py`)

ZIP archive generator for diagnostic export:

```python
def generate_debug_bundle() -> BytesIO:
    """Create ZIP with status, device info, logs, recent sessions."""
```

Bundle contents:
- `status.json` — current backend status
- `device_debug.json` — command log, registers
- `logs.json` — last 250 ring-log entries
- `sessions/` — last 5 session metadata files

API: `POST /api/diagnostics/debug-bundle` → ZIP download (Content-Disposition: attachment)

## Sanity Checks (`diagnostics/sanity_checks.py`)

Per-session data integrity validation:

```python
def run_sanity_checks(session: Session, wf: WaveformData) -> List[dict]:
    """Check waveform data against session metadata."""
    findings = []
    # sample count matches metadata
    if wf.num_samples != session.num_samples:
        findings.append({"level": "error", "message": ...})
    # digital data is valid 0/1 after unpacking
    # analog data has no NaN/Inf values
    # ADC voltages within expected range
    return findings
```

API: `GET /api/sessions/{id}/sanity`

## Dependencies

| Module | File |
|---|---|
| `WebSocketManager` | `websocket/manager.py` |
| `status_ws` router | `websocket/status_ws.py` |
| `RingLogger` | `diagnostics/logger.py` |
| `debug_bundle.py` | `diagnostics/debug_bundle.py` |
| `sanity_checks.py` | `diagnostics/sanity_checks.py` |

## Testing

| Test | What it covers |
|---|---|
| `test_core.py` | Session sanity checks |
| `test_api.py` | Log and diagnostics endpoints |
