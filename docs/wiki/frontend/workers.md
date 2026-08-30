# Waveform Worker

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
