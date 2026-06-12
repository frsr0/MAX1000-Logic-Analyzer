# MAX1000 Mixed-Signal Analyser — Web Host App (v2)

Next-generation host application for the MAX1000 OLS logic analyser:
a **FastAPI backend** that owns the hardware connection, plus a
**React/TypeScript web frontend** usable from any phone, tablet, or laptop on
the same network. It replaces the tkinter desktop UI while **reusing the
existing, proven hardware driver** (`host/driver/`) unchanged.

```
browser (React + canvas waveform viewer)
   │  REST + WebSocket + binary waveform protocol
   ▼
backend (FastAPI)  ──  sessions / LOD / decoders / measurements / exports
   │
   ├── hardware/existing_host_adapter.py  →  host/driver/OLSDeviceSPI (UNCHANGED)
   └── hardware/mock_device.py            →  fully synthetic device
```

---

## Install

Requirements: Python ≥ 3.10, Node ≥ 18 (only to build the frontend),
and for real hardware the FTDI D2XX driver + `ftd2xx` Python package.

```bash
# backend
cd backend
pip install -r requirements.txt

# frontend (one-time build; backend then serves it)
cd ../frontend
npm install
npm run build
```

## Run

```bash
cd backend
python run.py                 # default port 8000
# or: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

On startup the server prints the URLs:

```
  Open the app at:
    http://localhost:8000
    http://192.168.x.x:8000
  Phone/tablet QR code:  http://192.168.x.x:8000/connect
```

API docs (Swagger): `http://localhost:8000/docs`.

### Frontend development mode

```bash
cd frontend
npm run dev          # Vite dev server on :5173, proxies /api and /ws to :8000
```

### Mock mode (no hardware required)

Open the **Device** page → connect **Mock MAX1000 Analyser** → pick a scenario
on the **Capture** panel (UART / I2C / SPI / PWM / glitchy / analog demo /
stress test) → **Capture**. Everything — decoders, measurements, exports,
generator loopback — works against synthetic data. Or from the CLI:

```bash
curl -X POST localhost:8000/api/connect -H 'Content-Type: application/json' -d '{"device_id":"mock"}'
curl -X POST localhost:8000/api/diagnostics/mock-capture -H 'Content-Type: application/json' \
     -d '{"scenario":"uart","num_samples":100000}'
```

### Real hardware mode

1. Install the FTDI D2XX driver and `pip install ftd2xx`.
2. Connect the MAX1000 (FT2232H Channel B is used for SPI).
3. Device page → **MAX1000 OLS Logic Analyzer** → Connect.

**Verify the live hardware in one command** (run on the machine the FPGA is
plugged into):

```bash
cd backend
python hw_smoke_test.py          # add --mock to self-check the script
```

This drives the same adapter path the web app uses: discovery → connect +
sample-clock detect → capabilities → device self-test (debug CH0 PWM loopback
capture) → 4096-sample digital capture + sanity checks → UART generator
loopback (`CMD_GEN_CAPTURE`) decoded and byte-compared. Exit code 0 = good;
the captures it takes are saved as sessions and can be inspected in the web
UI afterwards. If anything fails, the deeper 553-check suite is
`cd host && python -m app.hw_validation`.

The adapter (`backend/app/hardware/existing_host_adapter.py`) mirrors the
exact call sequence of the proven tkinter GUI (`host/app/OLS_Console.py`) —
register setup, `CMD_ARM_CAPTURE`, status polling, 1024-byte block readback,
stride-4 wire parsing, mixed-frame de-interleaving. The driver itself is not
modified. The full hardware validation suite remains available:
`cd host && python -m app.hw_validation`.

### Opening from another LAN device

The backend binds `0.0.0.0`. On the phone/tablet, open the printed LAN URL
or scan the QR at `http://<host-ip>:8000/connect`. Multiple clients can view
simultaneously; only the client holding the **control lock** can issue
hardware commands (Settings page → acquire/force/release, or read-only mode).

---

## Architecture

### Backend (`backend/app/`)

| Package | Purpose |
|---|---|
| `hardware/` | `HardwareDevice` interface, mock device, adapter wrapping `host/driver` |
| `capture/` | Session model/store (JSON + NPZ), capture manager, LOD pyramid, binary waveform encoding |
| `decoders/` | Plugin decoder framework + UART, I2C, SPI, PWM, parallel, 1-Wire, Modbus RTU (stacked on UART) |
| `measurements/` | Digital / analog / protocol measurement types |
| `triggers/` | Trigger model, hardware-vs-post-capture classification, software trigger search |
| `generator/` | Generator control + loopback self-test workflow (configure → capture → decode → compare) |
| `exports/` | CSV, JSON (round-trippable), VCD, NPZ, HTML report |
| `diagnostics/` | Ring-buffer log + WS stream, sanity checks, debug-bundle ZIP |
| `websocket/` | Topic-based broadcast manager + `/ws/*` endpoints |

**Sessions** are the core unit: every capture produces a session directory
`data/sessions/<id>/` containing `session.json` (metadata, channels, trigger,
decoders, measurements, markers, notes, tags, export history, diagnostics),
`waveform.npz` (raw samples — immutable) and `decoders/*.json` (events).

**Raw data is never modified.** Software filters (majority vote, debounce,
min-pulse, glitch suppression) and analog thresholds create *derived
channels* stored separately.

### Waveform performance

- The backend builds a **LOD pyramid** per session (bin sizes 16, 64, 256, …):
  digital = and/or masks + per-channel edge counts (transition density),
  analog = min/max.
- The viewer requests only the **visible window** at the resolution it needs;
  payloads use a compact **binary format** (`MSAW` magic + JSON header +
  4-byte-aligned typed arrays), parsed into zero-copy TypedArray views.
- Big captures never enter React state; rendering is canvas-based with
  transition-density shading when zoomed out.
- Decoders run on worker threads with progress + cancellation over WebSocket.

### API overview

REST (see `/docs` for full schemas):

```
GET  /api/status                         GET  /api/devices
POST /api/connect | /api/disconnect      GET  /api/device/{metadata,capabilities,debug}
POST /api/device/self-test               POST /api/control/{acquire,release}
POST /api/capture/{start,stop,arm,disarm}   GET /api/capture/state
POST /api/capture/settings/validate
GET|POST /api/sessions                   GET|PATCH|DELETE /api/sessions/{id}
POST /api/sessions/{id}/duplicate        POST /api/sessions/{id}/compare/{other}
GET  /api/sessions/{id}/{metadata,waveform,raw,overview,edges,value-at,sanity,spectrum}
POST /api/sessions/{id}/derived-channels POST /api/sessions/{id}/buses
GET  /api/decoders                       POST|PATCH|DELETE /api/sessions/{id}/decoders[/{dec}]
POST /api/sessions/{id}/decoders/{dec}/{run,cancel}
GET  /api/sessions/{id}/decoders/{dec}/{annotations,table}
GET  /api/sessions/{id}/decoder-events
GET  /api/measurements/types             POST|PATCH|DELETE /api/sessions/{id}/measurements[/{m}]
GET  /api/sessions/{id}/measurements/results?cursor_a=&cursor_b=
GET|POST|PATCH|DELETE /api/sessions/{id}/markers[/{m}]
POST /api/sessions/{id}/export/{csv,json,vcd,npz,report}
GET  /api/generator/{capabilities,status}  POST /api/generator/{configure,start,stop,send,self-test}
GET  /api/logs | /api/diagnostics        POST /api/diagnostics/{debug-bundle,run-self-test,mock-capture}
GET  /api/qr | /connect
```

WebSockets: `/ws/status`, `/ws/capture`, `/ws/logs`, `/ws/session/{id}`,
`/ws/decoder/{id}` — typed JSON messages (`device_connected`,
`capture_progress`, `capture_complete`, `session_created`, `waveform_ready`,
`decoder_progress`, `decoder_complete`, `measurement_updated`, `warning`,
`log`, …).

---

## Decoder usage

1. Capture (e.g. mock UART scenario).
2. Side panel → **Decoders** → *Add decoder* → pick type, assign channels
   (any digital or derived channel), adjust settings → **Add & run**.
3. Annotations appear above the waveform; the packet table opens at the
   bottom — search, severity filter, click a row to jump the waveform.
4. *Run on selection* decodes only the shift-drag-selected region.
5. Stacked decoders: run **UART** first, then add **Modbus RTU** — it consumes
   the UART byte events.
6. Export decoded packets: side panel → **Export** → *Decoded CSV*.

Decoders implemented: UART (auto-baud, parity/framing errors), I2C (START/
repeated-START/STOP, address+R/W, ACK/NACK; 7-bit with a 10-bit extension
point), SPI (CPOL/CPHA/bit-order/word-size/CS), PWM/frequency, parallel bus,
1-Wire, Modbus RTU. New decoders register in `backend/app/decoders/registry.py`.

## Export usage

Side panel → **Export**: raw CSV (whole capture or selection), JSON session
(round-trippable — import on the Sessions page), VCD (digital + derived),
NumPy NPZ, HTML report (metadata, settings, SVG waveform overview,
measurements, decoder summaries, markers, diagnostics), PNG screenshot of the
viewer, and per-decoder CSV. Diagnostics page → **Debug bundle** downloads a
ZIP with status, logs, device debug info and recent session metadata.

## Tests

```bash
cd backend && python -m pytest app/tests        # 38 tests: decoders, LOD, exports, API E2E
cd host && python -m pytest tests driver/tests  # 310 legacy tests (untouched)
cd frontend && npm run typecheck && npm run build
```

Manual E2E flow (mock): connect mock → capture UART demo → add UART decoder →
packet table shows "Hello MAX1000!" → click packet jumps waveform → place
cursors A/B (double-click / keys a,b) and read Δt + frequency → add
"between cursors" measurement → export report → run I2C/SPI/analog demos →
save (ctrl+S) and re-import the JSON on the Sessions page.

---

## Known limitations / TODO (hardware-blocked or planned)

**Blocked by current FPGA firmware/hardware:**
- Hardware triggers limited to rising/falling edge (any channel mask) and the
  UART-byte protocol trigger. All other trigger types are clearly labelled
  *post-capture* and run as software searches.
- No analogue front-end beyond the MAX10 ADC (8 ch, ~101 kHz update, 3.3 V
  internal reference). AC coupling, probe relays, per-channel gain are
  **marked unavailable** — never faked. Mock analog exists only in mock mode.
- Generator protocols on hardware: UART, I2C, PWM (debug CH0). SPI/pattern/
  PRBS generators exist in mock only until firmware support lands.
- Segmented/burst capture modes and hardware sequence triggers are not in the
  current core; the capture-mode model has fields reserved for them.
- Rolling capture on real hardware is bounded by SPI readback (~30 MB/s); the
  adapter currently exposes single/repeat capture and reuses the driver's
  rolling generator for future streaming UI.

**Planned (software):**
- FFT/spectrum view exists as an API endpoint (`/spectrum`) — dedicated UI
  panel, histogram and persistence views are future modules.
- Decoders to add on the existing framework: Manchester, NRZ, I2S, CAN, LIN,
  MIDI, PS/2, JTAG/SWD, SMBus/PMBus, custom framed serial.
- Web Workers: server-side LOD makes client-side parsing cheap (zero-copy
  TypedArray views), so workers are not yet needed; revisit if client-side
  filtering/FFT is added.
- Drag-and-drop channel reorder (buttons exist), VCD/CSV import, PDF report,
  command palette.
- Session storage uses NPZ per session; a chunked store for >10M-sample
  captures is architected (`chunk_store.py`) but not yet needed at current
  hardware depths (1M samples).
