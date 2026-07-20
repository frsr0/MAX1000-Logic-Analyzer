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
UI afterwards. If anything fails, the deeper 577-check suite is

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
| `decoders/` | Plugin decoder framework + UART, I2C, SPI, PWM, parallel, 1-Wire, Modbus RTU, RS-485, SWD, Manchester, clocked NRZ, LIN, MIDI, PS/2, quadrature, and I²S |
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

**Physical pin metadata is explicit.** Real-hardware capabilities expose the
MAX1000 board map for the RTL pin pool (MKR D0-D14, PMOD PIO_01-PIO_08, and
the LIS3DH bus pins) plus the board-guide analogue inputs. Captured sessions
carry header/FPGA-pin metadata on channels, and the Device page shows the full
digital and analogue pin maps.

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
POST /api/sessions/{id}/trigger-search
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
1-Wire, Modbus RTU, RS-485, SWD, Manchester, clocked NRZ, LIN, MIDI, PS/2,
quadrature, I²S, and classical CAN. New decoders
register in `backend/app/decoders/registry.py`.

## Export usage

Side panel → **Export**: raw CSV (whole capture or selection), JSON session
(round-trippable — import on the Sessions page), VCD (digital + derived),
NumPy NPZ, HTML report (metadata, settings, SVG waveform overview,
measurements, decoder summaries, markers, diagnostics), PNG screenshot of the
viewer, and per-decoder CSV. Diagnostics page → **Debug bundle** downloads a
ZIP with status, logs, device debug info and recent session metadata.

## Tests

```bash
cd backend && python -m pytest app/tests        # 67 tests: API, decoders, LOD, exports, hardware adapter
cd host && python -m pytest tests driver/tests  # 333 host/driver tests
cd frontend && npm run typecheck && npm run build
```

Manual E2E flow (mock): connect mock → capture UART demo → add UART decoder →
packet table shows "Hello MAX1000!" → click packet jumps waveform → place
cursors A/B (double-click / keys a,b) and read Δt + frequency → add
"between cursors" measurement → export report → run I2C/SPI/analog demos →
save (ctrl+S) and re-import the JSON on the Sessions page.

---

## Known limitations / TODO (hardware-blocked or planned)

**Current FPGA/hardware boundaries:**
- **Block-boundary readback corruption — FIXED.** The old "handful of corrupted
  words around every 256-word read block" (first read of each block came back
  stale `0xFFFF` because the bus idled across the inter-block gap) is resolved by
  a layered fix: FPGA-side prime reads, CL2→CL3 at the 167 MHz SDRAM clock, and a
  host-side offset-0 discard in `read_capture_range`. Two-alignment XOR = 0
  across trials.
- **Deep-capture write path — FIXED.** Deep SDRAM capture previously had a
  throughput ceiling (~5.5 MHz, close-page ACT-per-sample) and could hang above
  ~12–18 MHz (completion waited on an exact write-count the producer never quite
  reached). Open-page policy + producer-done completion fix both: single-shot
  deep capture now completes and reads back clean at every rate up to the full
  200 MHz sample clock (validated 36/36 captures, 0 isolated dropped samples,
  18–200 MHz, full 4,194,304-word depth).
- `CMD_GEN_CAPTURE` generator loopback is covered by hardware smoke/API tests
  and the full host validation suite. The UART loopback path is decoded through
  the same backend decoder path used by user captures.
- Hardware triggers cover rising/falling edge, level triggers (high/low/
  pattern/bus_value — REG_TRIGGER_MASK level matcher), and the UART-byte
  protocol trigger. All other trigger types are clearly labelled
  *post-capture* and run as software searches.
- No analogue front-end beyond the MAX10 ADC (1 MSPS single-channel,
  125 kframes/s 4-input physical analog scan, 3.3 V internal reference).
  Mixed mode scans ADC0-ADC3 at the same scan frame rate. High-speed analog
  uses one selected ADC mux channel; maximum analog scans the physical profile
  ADC1,2,3,4,5,7,8,16. AC coupling,
  probe relays, per-channel gain
  are **marked unavailable** — never faked. Mock analog exists only in mock mode.
- The four capture modes are full digital, mixed, high-speed single-analog,
  and maximum physical-analog; see `docs/ANALOG_MODE_PLAN.md` for the RTL
  profile bits and validation status.
- The extra 200 MHz narrow rolling option is digital-only: it packs one
  selected digital channel into 16-sample words so the FPGA can produce a
  200 MHz rolling stream with much lower memory/readback pressure than
  16-channel full-width digital.
- Generator protocols on hardware: UART, RS-485, I2C, SPI (send + capture
  only — loops MOSI/SCLK into the capture stream, no CS/MISO), and raw
  two-output Bit Banger playback. PWM (debug CH0), pattern, counter, and PRBS
  remain mock-only. Raw Bit Banger playback is bounded by the 1024-symbol
  generator FIFO.
- Segmented/burst capture modes and hardware sequence triggers are not in the
  current core; the capture-mode model has fields reserved for them.
- Rolling capture on real hardware is bounded by SDRAM write bandwidth, FIFO
  burst cushion, retained ring depth, and SPI readback (~30 MB/s). At high
  rates, especially 200 MHz digital capture, the contract is newest-retained
  samples plus explicit overrun reporting, not arbitrary-length lossless
  storage.
- Current continuous mode exposes the FPGA ring-buffer contract with producer
  index, retained oldest/newest indexes, overrun count and host ACK, while
  keeping the API bounded.
- Capture DONE is latched until ACK/abort/next arm and each capture carries a
  monotonic sequence ID. Host validation asserts fresh readback by sequence
  when firmware metadata is available.
- Mixed/analog/digital recovery is validated by back-to-back hardware tests;
  each capture setup writes the complete mode state.
- Continuous `Rate_Div=1` startup is covered by HDL and hardware validation.
- FPGA utilization on the current image is ~87% logic elements / 79%
  combinational / 41% registers / 75% memory bits. Planned: trim duplicate
  debug/test mux logic guided by synthesis reports; do not block feature fixes
  on logic cleanup unless compile fails.

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
  full-width hardware depths (4,194,304 samples).
