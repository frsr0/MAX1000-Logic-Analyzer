# API Layer

**Directory:** `backend/app/api/`

FastAPI exposes device control, capture orchestration, sessions, analysis, and
downloads. Mutating control-plane calls use the `X-Client-Id` header so the
backend control lock can distinguish browser clients.

## Router index

| Router | Main endpoints |
|---|---|
| Status/control | `GET /api/status`, `GET /api/control`, `POST /api/control/acquire`, `POST /api/control/release` |
| Devices | discovery/connect/disconnect, metadata, capabilities, debug, self-test |
| Capture | jobs, start/arm/stop/disarm/state, settings validation, mock scenarios |
| Sessions | paged/searchable CRUD, import, duplicate, compare, trigger search, dashboard, buses, markers |
| Waveform | metadata, binary window/overview, raw, edges, value-at, derived channels, analysis APIs |
| Decoders | catalog, instance CRUD, run/cancel, annotations/table/event queries |
| Measurements | catalog, instance create/update/delete, result computation |
| Exports | CSV, JSON, VCD, PulseView-VCD, NPZ, HTML, PDF |
| Generator | sweep preview/capture, capabilities, configure/start/stop/status/preview, presets, send, self-test |
| MIL | presets, status, load/start/stop, transaction |
| Serial | physical/FTDI/layout discovery and virtual bridge lifecycle |
| Diagnostics | logs, diagnostics, debug bundle, self-test, live accelerometer session, mock capture, QR/connect helpers |
| Validation | `POST /api/sessions/{id}/validate` |

Interactive OpenAPI documentation is available at `/docs` while the backend is
running; it is the exact schema reference for request models.

## Capture and sessions

```text
POST /api/capture/jobs
GET  /api/capture/jobs/{job_id}
POST /api/capture/start
POST /api/capture/arm
POST /api/capture/stop
POST /api/capture/disarm
GET  /api/capture/state
POST /api/capture/settings/validate

GET    /api/sessions?search=&offset=&limit=
POST   /api/sessions
GET    /api/sessions/{id}
PATCH  /api/sessions/{id}
DELETE /api/sessions/{id}
POST   /api/sessions/{id}/duplicate
POST   /api/sessions/{id}/compare/{other_id}
POST   /api/sessions/{id}/trigger-search
GET    /api/sessions/{id}/dashboard
```

`POST /api/sessions` accepts an MSA JSON export or CSV/VCD source text. List
responses are bounded and include total/offset/limit metadata.

## Waveform and analysis

```text
GET  /api/sessions/{id}/metadata
GET  /api/sessions/{id}/waveform?start=&end=&resolution=&channels=
GET  /api/sessions/{id}/overview?bins=
GET  /api/sessions/{id}/raw?start=&end=
GET  /api/sessions/{id}/edges?channel=&kind=&start=&end=&limit=
GET  /api/sessions/{id}/value-at?sample=&channels=
POST /api/sessions/{id}/derived-channels
GET  /api/sessions/{id}/spectrum
GET  /api/sessions/{id}/spectrogram
GET  /api/sessions/{id}/correlation
GET  /api/sessions/{id}/envelope
GET  /api/sessions/{id}/threshold-sweep
GET  /api/sessions/{id}/event-correlation
GET  /api/sessions/{id}/eye
GET  /api/sessions/{id}/timing-suspects
GET  /api/sessions/{id}/sanity
```

Waveform windows and overviews return `application/octet-stream` MSAW data;
analysis endpoints return JSON. There is no `/xy` endpoint in the current
API.

## Generator

```text
POST /api/generator/sweep-preview
POST /api/generator/sweep-capture
GET  /api/generator/capabilities
POST /api/generator/configure
POST /api/generator/start
POST /api/generator/stop
GET  /api/generator/status
POST /api/generator/preview
GET  /api/generator/bitbang/presets
POST /api/generator/send
POST /api/generator/self-test
```

`send` accepts `{config, capture, live, capture_rate, capture_samples,
expected_hex, decoder_id}`. `live` is supported for the repeating UART,
RS-485, and Bit Banger hardware paths.

## Exports

All export endpoints are `POST` requests:

```text
/api/sessions/{id}/export/csv
/api/sessions/{id}/export/json
/api/sessions/{id}/export/vcd
/api/sessions/{id}/export/pulseview
/api/sessions/{id}/export/npz
/api/sessions/{id}/export/report
/api/sessions/{id}/export/pdf
```

See [Export and Import Formats](export-formats.md) for per-format options.

## WebSockets

| Path | Principal messages |
|---|---|
| `/ws/status` | `status_snapshot`, device connect/disconnect, `session_created` |
| `/ws/capture` | armed/started/progress/complete/cancelled/error/warning |
| `/ws/logs` | `log` |
| `/ws/session/{id}` | `waveform_ready`, `measurement_updated`, session changes |
| `/ws/decoder/{id}` | decoder completion/error/cancellation state |

Messages use `{type, data, timestamp}`. See
[WebSocket & Diagnostics](websocket-diagnostics.md).

## Error behavior

Hardware failures are translated by the application exception handler into
HTTP errors with useful details. Missing sessions/waveforms return 404;
invalid settings/routes return 4xx before hardware is touched; lock conflicts
identify the owning client. The frontend wraps non-success responses in
`ApiError(status, message)`.
