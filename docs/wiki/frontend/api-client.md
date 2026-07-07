# API Client

**Files:** `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/api/binary.ts`, `frontend/src/api/websocket.ts`

## Purpose

Type-safe REST client, WebSocket abstraction, TypeScript type interfaces mirroring backend Pydantic models, and binary MSAW waveform parser.

## REST Client (`client.ts`)

```typescript
class ApiError extends Error {
  status: number;
  constructor(status: number, message: string)
}
```

Generic request helpers:
```typescript
const get = <T>(p: string) => req<T>('GET', p);
const post = <T>(p: string, b?: unknown) => req<T>('POST', p, b);
const patch = <T>(p: string, b?: unknown) => req<T>('PATCH', p, b);
const del = <T>(p: string) => req<T>('DELETE', p);
```

### Client ID

Every request carries a `Client-Id` header with a stable per-browser UUID stored in `localStorage`:
```typescript
export function clientId(): string {
  let id = localStorage.getItem('msa_client_id');
  if (!id) { id = crypto.randomUUID(); localStorage.setItem('msa_client_id', id); }
  return id;
}
```

This is used by the backend's `ControlLock` to identify which browser owns the hardware.

### API Methods (`api` object)

```typescript
export const api = {
  // Status
  status: () => get<BackendStatus>('/api/status'),
  // Devices
  devices: () => get<{devices: DeviceDescriptor[]}>('/api/devices'),
  connect: (device_id: string) => post('/api/connect', {device_id}),
  disconnect: () => post('/api/disconnect'),
  deviceDebug: () => get('/api/device/debug'),
  deviceSelfTest: () => post('/api/device/self-test'),
  // Capture
  startCapture: (settings: CaptureSettings, name?: string) => post('/api/capture/start', {settings, name}),
  stopCapture: () => post('/api/capture/stop'),
  captureState: () => get('/api/capture/state'),
  // Sessions
  sessions: () => get<{sessions: SessionSummary[]}>('/api/sessions'),
  session: (id: string) => get<Session>(`/api/sessions/${id}`),
  deleteSession: (id: string) => del(`/api/sessions/${id}`),
  duplicateSession: (id: string) => post(`/api/sessions/${id}/duplicate`),
  importSession: (json: string) => post('/api/sessions', {json_text: json}),
  // Waveform
  waveform: (id: string, start: number, end: number, channels?: string) =>
    req<ArrayBuffer>('GET', `/api/sessions/${id}/waveform?start=${start}&end=${end}&channels=${channels ?? ''}`),
  // Decoders
  decoderTypes: () => get<{types: DecoderDescription[]}>('/api/decoders'),
  addDecoder: (sessionId: string, inst: Partial<DecoderInstance>) =>
    post(`/api/sessions/${sessionId}/decoders`, inst),
  deleteDecoder: (sessionId: string, decId: string) =>
    del(`/api/sessions/${sessionId}/decoders/${decId}`),
  // ... and more
};
```

### Download Export

```typescript
export async function downloadExport(sessionId: string, format: string, body: unknown = {}) {
  // POST to /api/sessions/{id}/export/{format}, handles Content-Disposition
  // Triggers browser download via hidden anchor click
}
```

## TypeScript Types (`types.ts`)

Every Pydantic model has a corresponding TypeScript interface:

| Interface | Key Fields |
|---|---|
| `BackendStatus` | connected, device, mock, capture_state, last_session_id |
| `DeviceDescriptor` | id, name, mock, available |
| `DeviceCapabilities` | modes, max_sample_rate, max_depth, analog_channels |
| `CaptureSettings` | sample_rate, num_samples, mode, compression, trigger |
| `ChannelInfo` | id, label, type, index, enabled, color, physical_pin |
| `Session` | id, name, created, device, settings, channels, decoders |
| `SessionSummary` | id, name, created, num_samples, sample_rate, mode |
| `DecoderInstance` | id, decoder_type, label, channel_map, settings, status |
| `TriggerConfig` | type, channel_mask, value, execution |
| `Marker` | id, sample, label, color |
| `MeasurementInstance` | id, measurement_type, channel_id, result |
| `DecoderEvent` | id, decoder_id, type, start_sample, end_sample, label, severity |
| `GeneratorConfig` | protocol, baud_rate, data, pin_tx, pin_scl |
| `MilConfig` | protocol, registers, timing |

## Binary Parser (`binary.ts`)

```typescript
export interface WaveformPayload {
  num_samples: number;
  sample_rate: number;
  channels: { id: string; type: string; data: Uint8Array | Float32Array }[];
}

export function parseWaveformPayload(buffer: ArrayBuffer): WaveformPayload {
  // Parse MSAW format: magic(4) + jsonLen(4) + json + typed arrays
  // Returns zero-copy TypedArray views
}
```

## WebSocket (`websocket.ts`)

See [WebSocket Integration](websocket-integration.md) for `ReconnectingSocket` class.
