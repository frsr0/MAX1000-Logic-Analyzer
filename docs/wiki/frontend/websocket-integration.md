# WebSocket Integration

**Files:** `frontend/src/api/websocket.ts`, `frontend/src/App.tsx`

## ReconnectingSocket

`ReconnectingSocket` builds `ws://` or `wss://` from the current page origin,
parses typed JSON messages, and broadcasts them to a set of subscribers. A
disconnect retries after 500 ms and doubles up to a 10-second ceiling. Calling
`close()` cancels pending retries and permanently closes that instance.
The accelerometer worker applies the same reconnect expectation to its binary
and legacy text waveform sockets.

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
| `/ws/accelerometer/waveform-binary` | Worker lifetime | Ordered binary waveform packets; every packet is decoded |
| `/ws/accelerometer/waveform` | Worker compatibility path | Ordered JSON/base64 waveform packets for legacy backends |
| `/ws/accelerometer/pose` | Fallback lifetime | Replaceable latest pose when the module worker is unavailable |

The active-session sockets are disposed and recreated when the selected
session ID changes.

## Live waveform message path

`waveform_ready` includes current `num_samples`, `sample_rate`, rolling state,
and chunk size. `App.tsx` calls `waveformView.updateLive()` immediately, then
refreshes session metadata asynchronously. Keeping the metadata request off
the drawing path prevents a slow session response from queueing live frames.

The normal capture waveform data is fetched over HTTP in a Web Worker; its
WebSockets only announce state changes. The accelerometer is different: its
ordered raw waveform is delivered over a dedicated WebSocket to the
accelerometer worker, because the worker must decode every packet without
blocking the main thread. See [Workers](workers.md).

## Backend queue and slow-client contract

The binary waveform and legacy JSON/base64 waveform topics use one bounded
queue per client. Each healthy client is lossless, with at most two pending
chunks. A client that cannot send within the 10 ms live-send deadline is
removed from the topic and its socket is explicitly closed with retryable code
1013 (`slow websocket consumer`). This explicit close matters: the frontend
reconnects on `onclose`; merely removing a socket would leave a connected
client receiving nothing.

The `/ws/accelerometer` history topic is also lossless per client. The
`/ws/accelerometer/pose` topic is intentionally different: it is a latest-value
mailbox, so stale poses may be replaced. No queue policy should be inferred
from `source_backlog_samples`; that value belongs to the FPGA/ring source.

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
