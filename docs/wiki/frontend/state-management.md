# State Management

**Files:** `frontend/src/state/appStore.ts`, `frontend/src/state/waveformStore.ts`

## Split State Architecture

Two separate state layers:

1. **Zustand `useApp` store** — small reactive metadata (status, sessions, settings, toasts)
2. **`WaveformView` class** — large TypedArrays outside React (waveform data, view state, decoder events)

```
React Components ←→ useApp() [reactive, triggers re-render]
                        |
                  WaveformView [event-driven, no re-render]
                        |
                  REST/WS API
```

## `useApp` (Zustand)

Key state: `page`, `wsConnected`, `status`, `capabilities`, `sessions`, `activeSession`, `captureSettings`, `decoderTypes`, `measurementTypes`, `logs`, `toasts`, `controlMode`, `viewerSettings`.

Key actions: `refreshStatus()`, `refreshSessions()`, `refreshCapabilities()`, `openSession(id)`, `loadCatalogs()`, `setCaptureSettings()`, `toast()`, `dismissToast()`.

Settings persisted to `localStorage`.

## WaveformView (Plain Class)

Key state: session ID/sample metadata, viewport `start`/`end`, current and
overview MSAW payloads, annotations, markers, selected rows, cursor position,
loading/error state, and live-follow/chunk metadata.

Change notifications use `subscribe(listener)`/internal `notify()`. The canvas
reads TypedArray views directly. Window requests are debounced; live requests
are coalesced and overview updates are throttled.

## Why Split

- A full digital session is one packed `uint16` per sample (about 8 MiB at
  4,194,304 samples), with additional analog/LOD buffers as required
- Zoom/pan at 60 fps — React re-render at that rate is prohibitive
- Canvas rendering reads TypedArrays directly
