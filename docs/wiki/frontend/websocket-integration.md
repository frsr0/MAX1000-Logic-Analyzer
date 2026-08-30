# WebSocket Integration

**Files:** `frontend/src/api/websocket.ts`, `frontend/src/App.tsx`

## ReconnectingSocket

`ReconnectingSocket` builds `ws://` or `wss://` from the current page origin,
parses typed JSON messages, and broadcasts them to a set of subscribers. A
disconnect retries after 500 ms and doubles up to a 10-second ceiling. Calling
`close()` cancels pending retries and permanently closes that instance.

```typescript
const socket = new ReconnectingSocket('/ws/status');
const unsubscribe = socket.subscribe((message) => { /* route by type */ });
socket.onStateChange = (connected) => { /* update status bar */ };
unsubscribe();
socket.close();
```

Malformed JSON is ignored. A WebSocket error closes the socket and lets the
normal reconnect path handle recovery.

## Connections owned by App

| Path | Lifetime | Frontend behavior |
|---|---|---|
| `/ws/status` | Whole app | Tracks connection; refreshes status, sessions, and capabilities on reconnect/device changes |
| `/ws/capture` | Whole app | Updates progress; refreshes status/sessions; stops live-follow on cancel; shows error/warning toasts |
| `/ws/logs` | Whole app | Appends backend log entries |
| `/ws/decoder/{sessionId}` | Active session | Refreshes session and annotations after decoder completion |
| `/ws/session/{sessionId}` | Active session | Re-fetches measurements and advances the live waveform on `waveform_ready` |

The active-session sockets are disposed and recreated when the selected
session ID changes.

## Live waveform message path

`waveform_ready` includes current `num_samples`, `sample_rate`, rolling state,
and chunk size. `App.tsx` calls `waveformView.updateLive()` immediately, then
refreshes session metadata asynchronously. Keeping the metadata request off
the drawing path prevents a slow session response from queueing live frames.

The waveform data itself is fetched over HTTP in a Web Worker; WebSockets only
announce state changes. See [Workers](workers.md).

## Message envelope

```typescript
interface WsMessage {
  type: string;
  data: unknown;
  timestamp?: string;
}
```

Backend topic and event details are documented in
[WebSocket & Diagnostics](../backend/websocket-diagnostics.md).
