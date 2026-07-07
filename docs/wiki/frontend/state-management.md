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

Key state: `session`, `waveform`, `channels`, `zoom.start/end`, `maxZoom`, `scrollOffset`, `markers`, `decoderEvents`.

Change notifications via `onChange(listener)` / `notifyChange()` for label/tooltip updates. Canvas reads data directly from this class.

## Why Split

- Sample arrays can be hundreds of MB (4M × 16 channels × 2 bytes = 128 MB)
- Zoom/pan at 60 fps — React re-render at that rate is prohibitive
- Canvas rendering reads TypedArrays directly
