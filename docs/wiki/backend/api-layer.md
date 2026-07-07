# API Layer

**Directory:** `backend/app/api/`

## Purpose

REST and WebSocket endpoints that make the backend controllable from any HTTP client (browser, curl, mobile app). All hardware access is mediated through the `CaptureManager` and `SessionStore`.

## Router Index

| Router | File | Endpoints |
|---|---|---|
| `status` | `api/status.py` | `GET /api/status` — backend status, device state, last session |
| `devices` | `api/devices.py` | `GET /api/devices`, `POST /api/connect`, `POST /api/disconnect`, `GET /api/device/metadata`/`capabilities`/`debug`, `POST /api/device/self-test` |
| `capture` | `api/capture.py` | `POST /api/capture/start`, `GET /api/capture/state`, `POST /api/capture/stop`, `POST /api/capture/settings/validate`, `GET /api/capture/scenarios` (mock) |
| `sessions` | `api/sessions.py` | Full CRUD on `/api/sessions`, duplicate, compare, markers, buses |
| `waveform` | `api/waveform.py` | Binary waveform window, raw JSON, overview, edges, value-at, derived channels, spectrum, sanity |
| `decoders` | `api/decoders.py` | List types, CRUD instances, run/cancel, annotations, packet table, decoder events |
| `measurements` | `api/measurements.py` | List types, CRUD instances, results between cursors |
| `exports` | `api/exports.py` | Export CSV/JSON/VCD/NPZ/report, export by format |
| `generator` | `api/generator.py` | Capabilities, status, configure, start, stop, send, self-test |
| `mil` | `api/mil.py` | MIL presets, config, capture, runtime status |
| `diagnostics` | `api/diagnostics.py` | Logs, debug bundle ZIP, run self-test, mock capture |

## Key Endpoints

### Status

```
GET /api/status
→ {connected, device, mock, capture_state, last_session_id, uptime}
```

### Device Connection

```
GET  /api/devices                        → {devices: [{id, name, mock, available}]}
POST /api/connect                        ← {device_id: "real"|"mock"}
POST /api/disconnect                     → {disconnected: true}
POST /api/device/self-test               → {passed, checks, message}
```

### Capture

```
POST /api/capture/start                  ← {settings, name?}
     → {started: true, state}
POST /api/capture/stop                   → {stopping, state}
GET  /api/capture/state                  → {state, progress, last_session_id, last_error}
```

### Sessions

```
GET    /api/sessions                     → {sessions: [Summary]}
GET    /api/sessions/{id}                → Session model
PATCH  /api/sessions/{id}                ← partial update
DELETE /api/sessions/{id}                → {deleted: true}
POST   /api/sessions/{id}/duplicate      → copy summary
POST   /api/sessions/{id}/compare/{other} → comparison result
```

### Waveform Binary Protocol

```
GET /api/sessions/{id}/waveform?start=&end=&channels=
    → binary MSAW payload (application/octet-stream)
GET /api/sessions/{id}/overview?bins=1024
    → binary MSAW overview
GET /api/sessions/{id}/raw?start=&end=&channels=
    → JSON raw sample window (small windows only)
```

All waveform endpoints support `channels` parameter as comma-separated channel IDs.

### Decoders

```
GET  /api/decoders                       → {types: [DecoderDescription]}
POST /api/sessions/{id}/decoders         ← create instance
POST /api/sessions/{id}/decoders/{dec}/run    → start decoding
POST /api/sessions/{id}/decoders/{dec}/cancel → cancel running
GET  /api/sessions/{id}/decoder-events?start=&end=  → events overlapping window
```

### Exports

```
GET|POST /api/sessions/{id}/export/{csv,json,vcd,npz,report}
    → file download with Content-Disposition
```

### Generator

```
GET  /api/generator/capabilities         → supported protocols
POST /api/generator/configure            ← GeneratorConfig
POST /api/generator/start/stop           → state change
POST /api/generator/self-test            → loopback capture + compare
```

### Diagnostics

```
GET  /api/logs                           → recent log entries
POST /api/diagnostics/debug-bundle       → ZIP download
POST /api/diagnostics/run-self-test      → hardware checks
POST /api/diagnostics/mock-capture       ← scenario, num_samples
```

## WebSocket Endpoints

| Path | Messages |
|---|---|
| `/ws/status` | `device_connected`, `device_disconnected`, `capture_state`, `session_created` |
| `/ws/capture` | `capture_progress`, `capture_complete`, `capture_error` |
| `/ws/logs` | `log` entries |
| `/ws/session/{id}` | `waveform_ready`, `session_updated`, `decoder_updated`, `measurement_updated` |
| `/ws/decoder/{id}` | `decoder_progress`, `decoder_complete`, `decoder_error` |

Messages are typed JSON with `{type, data, timestamp}` structure.

## Dependencies

| Module | File |
|---|---|
| `CaptureManager` | `capture/capture_manager.py` |
| `SessionStore` | `capture/session_store.py` |
| `WaveformData` | `capture/sample_format.py` |
| `Decoder` registry | `decoders/registry.py` |
| `WebSocket manager` | `websocket/manager.py` |
