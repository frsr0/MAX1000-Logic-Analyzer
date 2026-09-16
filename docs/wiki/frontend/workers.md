# Frontend Workers

The frontend uses separate workers for large waveform transport and the live
accelerometer stream. Both keep high-rate work out of React; their contracts
are different and must not be collapsed into one queue.

## Waveform Worker

**Files:** `frontend/src/workers/waveform.worker.ts` and
`waveformClient.ts`

Waveform HTTP fetch and MSAW parsing run in a module Web Worker. The parsed
header and raw `ArrayBuffer` return to the main thread with ownership
transferred, allowing `parseWaveformPayload` to recreate TypedArray views
without copying multi-megabyte sample arrays.

## Request protocol

| Request | Fields | Backend resource |
|---|---|---|
| `window` | `id`, session, start/end, resolution, optional channels | `/api/sessions/{id}/waveform` |
| `overview` | `id`, session, optional bins | `/api/sessions/{id}/overview` |

Every response echoes the request `id`:

```typescript
type WorkerResult = { id: string; header: WaveformHeader; buf: ArrayBuffer };
type WorkerError = { id: string; error: string };
```

Request correlation is essential because viewport and overview fetches may be
in flight together. The previous FIFO resolver could deliver an overview to a
window promise, leaving live captures blank. `WaveformClient` now stores
pending resolvers in a map keyed by the echoed ID.

## WaveformClient

```typescript
class WaveformClient {
  fetchWindow(sessionId, start, end, resolution, channels?): Promise<WaveformPayload>
  fetchOverview(sessionId, bins = 1024): Promise<WaveformPayload>
  dispose(): void
}
```

Window resolution is clamped to 512-4,096 points. `dispose()` terminates the
worker and rejects every outstanding promise.

## Live-fetch policy

`WaveformView` adds scheduling around the worker:

- normal viewport changes may abort superseded main-thread API work;
- live window fetches are not aborted for every new chunk;
- if a live fetch is already running, one re-fetch is queued and coalesces any
  additional chunk announcements;
- overview refresh is fire-and-forget and throttled to roughly 400 ms;
- annotations use their own debounce path.

This guarantees that slow LOD rebuilding can eventually return a viewport
instead of being perpetually cancelled by a faster chunk cadence.

## Tests

`src/workers/waveformClient.test.ts` uses a fake Worker to cover concurrent
window/overview response ordering, request IDs, error routing, buffer parsing,
and disposal. Binary framing is covered separately by
`src/api/binary.test.ts`.

## Accelerometer Stream Worker

**Files:** `frontend/src/workers/accelerometerStream.worker.ts` and
`liveI2cDecoder.ts`

The accelerometer worker owns the ordered live waveform stream. It subscribes
to the binary endpoint first and keeps the legacy JSON/base64 waveform topic
as a compatibility fallback. It decodes every packet in arrival order,
retains decoder state across packet boundaries, and emits one pose message for
each complete LIS3DH XYZ transaction. Waveform packets are never replaced by
a latest-value policy: dropping one creates a sequence gap and could join
unrelated I²C transactions.

Only presentation work is coalesced. The worker maintains a one-item pending
waveform paint mailbox and waits for the UI ACK before posting the next chunk;
the React card coalesces state updates into `requestAnimationFrame`. The
decoder and packet counters continue to advance for every packet, even when
the canvas is not repainting.

The worker publishes separate diagnostics with each decoded point:

| Metric | Meaning |
|---|---|
| `waveform_packet_count` | Ordered waveform packets received |
| `waveform_sequence_gaps` | Missing sequence boundaries; decoder resets after a gap |
| `waveform_disconnects` | Binary/text socket disconnects observed by the worker |
| `frontend_decode_latency_ms` | Browser receive-to-decode latency |
| `capture_to_publish_latency_ms` | Backend capture-to-pose publication latency |
| `decoded_to_publish_latency_ms` | Backend decode-to-pose publication latency |

`source_backlog_samples` is not a browser queue metric. It describes unread
data in the FPGA/ring source and is reported separately from the worker
counters and WebSocket-to-render timing.

If the module worker cannot start, `MilAccelerometerCard` falls back to
`/ws/accelerometer/pose`, which is a replaceable latest-pose stream. This
fallback intentionally does not duplicate the raw waveform decoder on the
main thread. Once either worker or fallback owns the pose stream, messages
from the other owner are ignored until the component is recreated.

## Live worker tests

`src/workers/accelerometerStream.worker.test.ts` runs the worker with fake
WebSockets and covers binary preference, legacy text fallback, complete packet
order, sequence-gap recovery, reconnect behavior, per-packet pose output, and
the UI waveform ACK mailbox. `liveI2cDecoder.test.ts` covers incremental
decoder state and includes an opt-in real-backend binary packet test under
`LIVE_HW_TEST=1`.
