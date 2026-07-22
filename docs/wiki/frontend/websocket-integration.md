# WebSocket Integration

**File:** `frontend/src/api/websocket.ts`

## ReconnectingSocket

```typescript
class ReconnectingSocket {
  constructor(path: string)
  subscribe(cb: (msg: WsMessage) => void): () => void  // returns unsubscribe
  close(): void
  onStateChange: ((connected: boolean) => void) | null
}
```

Auto-reconnecting WebSocket with exponential backoff. Cleanly handles connection drops during network interruptions.

### Topics

| Path | Purpose |
|---|---|
| `/ws/status` | Device connection, capture state, session created |
| `/ws/capture` | Capture progress (read/total/phase) and completion |
| `/ws/logs` | Log entries streamed in real-time |
| `/ws/decoder/{sessionId}` | Decoder progress/completion per session |
| `/ws/session/{sessionId}` | Waveform ready, session updated |

### Message Routing

In `App.tsx`:
```typescript
const statusWs = new ReconnectingSocket('/ws/status');
statusWs.subscribe((msg) => {
  switch(msg.type) {
    case 'device_connected': refreshStatus(); toast('success', 'Device connected'); break;
    case 'capture_state': refreshStatus(); break;
    case 'session_created': refreshSessions(); break;
  }
});

const captureWs = new ReconnectingSocket('/ws/capture');
captureWs.subscribe((msg) => {
  // Update capture progress in state
  // On capture_complete: refresh session, load waveform
});

const decoderWs = new ReconnectingSocket(`/ws/decoder/${sessionId}`);
decoderWs.subscribe((msg) => {
  // Update decoder progress, load events on complete
});
```

### Message Format

```typescript
interface WsMessage {
  type: string;       // event type
  data: any;          // event-specific payload
  timestamp?: string; // ISO 8601
}
```

# Workers

**Files:** `frontend/src/workers/waveform.worker.ts`, `frontend/src/workers/waveformClient.ts`

## Purpose

WebWorker for offloading waveform data processing from the main thread.

## waveform.worker.ts

```typescript
// Message handler for processing waveform data
self.onmessage = (e) => {
  const { type, payload } = e.data;
  switch(type) {
    case 'process_waveform':
      // Unpack MSAW binary → structured arrays
      // Build channel buffers
      self.postMessage({ type: 'waveform_ready', payload: result });
      break;
    case 'process_overview':
      // Build overview data
      break;
  }
};
```

## waveformClient.ts

Proxy that hides the worker message passing behind a clean async API:

```typescript
class WaveformClient {
  private worker: Worker;

  async processWaveform(buffer: ArrayBuffer): Promise<WaveformPayload> {
    // Post message to worker, return promise that resolves on response
  }

  async processOverview(sessionId: string): Promise<OverviewPayload> {
    // Post overview request, return promise
  }

  terminate(): void {
    this.worker.terminate();
  }
}
```

The `WaveformView` class uses `WaveformClient` to process binary data without blocking the UI thread during large capture loads.
