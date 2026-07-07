# Workers

**Files:** `frontend/src/workers/waveform.worker.ts`, `frontend/src/workers/waveformClient.ts`

## Purpose

WebWorker for offloading waveform data processing from the main thread, keeping the UI responsive during large capture loads.

## waveform.worker.ts

Dedicated WebWorker that processes binary MSAW waveform payloads:

```typescript
self.onmessage = (e) => {
  const { type, payload } = e.data;
  switch(type) {
    case 'process_waveform':
      // Unpack MSAW binary → structured TypedArray views
      self.postMessage({ type: 'waveform_ready', payload: result },
        [result.buffers]);  // transfer ownership
      break;
    case 'process_overview':
      // Build overview/minimap data from waveform
      break;
  }
};
```

## waveformClient.ts

Proxy class that provides a clean async API over the worker:

```typescript
class WaveformClient {
  private worker: Worker;

  async processWaveform(buffer: ArrayBuffer): Promise<WaveformPayload>
  async processOverview(sessionId: string): Promise<OverviewPayload>
  terminate(): void
}
```

## Message Protocol

| Direction | Type | Payload |
|---|---|---|
| Main → Worker | `process_waveform` | `ArrayBuffer` (MSAW) |
| Worker → Main | `waveform_ready` | `WaveformPayload` (transferred) |
| Main → Worker | `process_overview` | session ID + bins |
| Worker → Main | `overview_ready` | overview data |

## Dependencies

| File | Purpose |
|---|---|
| `waveform.worker.ts` | Worker message handler |
| `waveformClient.ts` | Worker proxy class |
| `WaveformView` | `state/waveformStore.ts` |
