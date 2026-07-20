# Frontend — React/TypeScript Wiki

> Browser-based mixed-signal analyser UI: waveform viewer, capture controls, protocol decoders, measurements, generator, device management.

The Generator page is capability-driven. It reads the connected device route
descriptor before showing optional RS-485 DE and SPI CS/MISO controls; the
backend still validates every request. See [Generator Routing](../generator-routing.md)
for the physical and register contract.

## Technology Stack

| Tool | Version | Purpose |
|---|---|---|
| React | ^18.3.1 | UI framework |
| Zustand | ^4.5.4 | State management (app-level only) |
| Vite | ^5.4.0 | Build tool + dev server |
| TypeScript | ^5.5.3 | Type safety |
| Playwright | ^1.61.1 | E2E testing |
| Canvas API | (native) | Waveform rendering |

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      App Entry (App.tsx)                      │
│  - WebSocket init (status, capture, decoder, session topics) │
│  - Store refresh on mount                                     │
│  └── <AppShell />                                            │
├──────────────────────────────────────────────────────────────┤
│  AppShell (layout/AppShell.tsx)                               │
│  - Sidebar nav (7 pages)                                      │
│  - Top bar (device badge, sample clock, capture state)        │
│  - Status bar + toast system                                  │
│  - Global keyboard shortcuts (space, ctrl+s)                  │
│  └── <CurrentPage />                                          │
├──────────────────────────────────────────────────────────────┤
│  Pages                                                        │
│  ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │
│  │ Capture  │ │Session │ │  Device  │ │Generator │ │MIL   │ │
│  └────┬─────┘ └────────┘ └────┬─────┘ └────┬─────┘ └──┬───┘ │
│  ┌────┴─────┐ ┌──────────────┐│             │          │      │
│  │Diagnostic│ │  Settings    ││             │          │      │
│  └──────────┘ └──────────────┘└─────────────┴──────────┘      │
├──────────────────────────────────────────────────────────────┤
│  State Layer                                                  │
│  ┌─────────────────────────┐  ┌────────────────────────────┐ │
│  │ appStore.ts (Zustand)    │  │ waveformStore.ts (class)   │ │
│  │ - status/sessions/settings│  │ - big TypedArrays outside │ │
│  │ - decoder/measurement     │  │   React state              │ │
│  │   catalogs               │  │ - event-driven change       │ │
│  │ - toast/control mode     │  │   notifications             │ │
│  └─────────────────────────┘  └────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│  API Layer                                                    │
│  ┌────────────────────────┐  ┌────────────────────────────┐  │
│  │ api/client.ts (REST)    │  │ api/websocket.ts (WS)      │  │
│  │ - typed fetch wrappers  │  │ - ReconnectingSocket class │  │
│  │ - clientId header       │  │ - topic subscriptions      │  │
│  │ - download helpers      │  │ - message routing          │  │
│  └────────────────────────┘  └────────────────────────────┘  │
│  ┌────────────────────────┐  ┌────────────────────────────┐  │
│  │ api/types.ts (interfaces)│  │ api/binary.ts (MSAW parse) │  │
│  └────────────────────────┘  └────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  Workers                                                      │
│  ┌─────────────────────────────┐  ┌────────────────────────┐ │
│  │ waveform.worker.ts           │  │ waveformClient.ts      │ │
│  │ (Web Worker message handler)│  │ (worker proxy + cache) │ │
│  └─────────────────────────────┘  └────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

## Wiki Pages

### App Structure
- [App Shell](app-shell.md) — Navigation, header, toasts, keyboard shortcuts
- [Pages](pages.md) — All 7 page components and their layout
- [Capture Controls](capture-controls.md) — Mode/acquisition/rate/depth selectors
- [Panels](panels.md) — All side panel components (trigger, channels, decoders, measurements, markers, export, raw)

### State & Data
- [State Management](state-management.md) — Zustand appStore + waveformView class, why big data lives outside React
- [API Client](api-client.md) — REST client, type interfaces, binary MSAW parser
- [WebSocket Integration](websocket-integration.md) — ReconnectingSocket, topic subscriptions, message routing

### Waveform
- [Waveform Viewer](waveform-viewer.md) — Canvas rendering, zoom/pan, cursor/markers, transition-density shading, decoder annotations

### Build & Test
- [Workers](workers.md) — WebWorker for waveform data processing
- [Build & Test](build-and-test.md) — Package config, TypeScript, Vite, Playwright E2E tests
- [Decoder UI](decoder-ui.md) — DecoderTable, annotation overlays, packet table, severity filtering

## Key Design Points

- Waveform sample data (large TypedArrays) lives **outside React state** in a plain class (`WaveformView`) that emits change events — React subscribes only for label/metadata changes
- Canvas-based waveform rendering with transition-density shading when zoomed out
- Captures never enter React state as large objects; the API serves binary MSAW payloads parsed into zero-copy TypedArray views
- ReconnectingSocket handles backoff reconnection for all 4 WebSocket topics
- Mock mode supports full E2E testing without hardware via Playwright's `PLAYWRIGHT_USE_MOCK` env var
