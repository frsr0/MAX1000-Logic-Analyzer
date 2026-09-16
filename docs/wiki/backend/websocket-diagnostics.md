# WebSocket & Diagnostics

**Directory:** `backend/app/websocket/`, `backend/app/diagnostics/`

## WebSocket Manager (`websocket/manager.py`)

Topic-based broadcast manager for real-time UI updates. Worker threads publish
through `publish_threadsafe()`; the manager schedules asynchronous sends on the
server loop so a slow client cannot block capture or decoding.

```python
class ConnectionManager:
    async def connect(websocket: WebSocket, topic: str) -> None
    def disconnect(websocket: WebSocket, topic: str) -> None
    def publish_threadsafe(topic: str, type_: str, data: dict) -> None
    def publish_binary_threadsafe(topic: str, data: bytes) -> None
```

### Topics

| Topic | Messages Sent | Frequency |
|---|---|---|
| `status` | `device_connected`, `device_disconnected`, `capture_state`, `session_created` | On change |
| `capture` | `capture_progress` (read/total/phase), `capture_complete`, `capture_error` | ~10 Hz during capture |
| `logs` | `log` entries (level, message, source, timestamp) | As produced |
| `session/{id}` | `waveform_ready`, `session_updated`, `decoder_updated`, `measurement_updated` | On session modification |
| `decoder/{id}` | `decoder_progress` (0..1), `decoder_complete`, `decoder_error` | During decode |
| `accelerometer` | Every validated decoded XYZ sample | Optional lossless history |
| `accelerometer_pose` | Latest decoded pose target | Replaceable presentation mailbox |
| `accelerometer_waveform` | Ordered JSON/base64 waveform chunks | Legacy worker fallback |
| `accelerometer_waveform_binary` | Ordered binary waveform chunks | Primary worker transport |

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
| `/ws/accelerometer` | `accelerometer` |
| `/ws/accelerometer/pose` | `accelerometer_pose` |
| `/ws/accelerometer/waveform` | `accelerometer_waveform` |
| `/ws/accelerometer/waveform-binary` | `accelerometer_waveform_binary` |

### Live accelerometer delivery policy

The live waveform topics are ordered transports. Binary and legacy text
waveform messages use independent per-client FIFOs with a maximum of two
pending chunks. A healthy client receives every packet in order. When a client
cannot drain within the 10 ms send deadline, the manager removes it from the
topic and explicitly closes its socket with code 1013 and reason `slow
websocket consumer`. This is required because the frontend reconnects from
`onclose`; removing the client from the topic alone would leave a socket that
appears connected but receives nothing.

Send failures and task timeouts are isolated to the affected client. The
legacy text path follows the same lossless/disconnect policy as binary; it is
not a shared drop-oldest queue. The decoded `accelerometer` history topic is
also per-client lossless, while `accelerometer_pose` deliberately replaces
stale pending values because it is only a visualization target.

The backend intentionally keeps these measurements separate:

| Measurement | Owner | Meaning |
|---|---|---|
| `source_backlog_samples` | FPGA/ring source | Unread source samples after the returned capture window |
| `source_age_samples` | Backend transport | Age of the decoded source point |
| `waveform_packet_count` | Frontend worker | Ordered raw packets received |
| `waveform_sequence_gaps` | Frontend worker | Missing packet sequence boundaries |
| `waveform_disconnects` | Frontend worker | Raw waveform socket disconnects |
| `frontend_decode_latency_ms` | Frontend worker | Browser receive-to-decode time |
| `capture_to_publish_latency_ms` | Backend | Source capture to publication |
| `decoded_to_publish_latency_ms` | Backend | Backend decode to publication |
| `WebSocket → render` | Frontend card | Browser receive/target selection to paint |

`source_backlog_samples=0` therefore proves only that the FPGA/ring source is
clear; it does not prove that a browser socket has no queued data.

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
