# Frontend API Client

**Files:** `frontend/src/api/client.ts`, `types.ts`, `binary.ts`, and
`websocket.ts`

## JSON requests and control identity

`client.ts` provides typed GET/POST/PATCH/DELETE helpers. Non-success responses
become `ApiError` with the HTTP status and backend detail. JSON requests carry
`X-Client-Id`, a stable browser-local ID (`web_<random>` stored as
`msa_client_id`) used by the backend control lock.

The `api` object groups calls by status/control, devices, capture/jobs,
sessions/import/comparison, waveform/analysis, decoders, measurements,
markers, generator/sweeps, virtual serial, MIL, and diagnostics. It mirrors
the routes listed in the [Backend API Layer](../backend/api-layer.md).

## Binary waveform calls

`waveformWindow` and `overview` fetch `ArrayBuffer` responses and parse MSAW
into zero-copy views:

```typescript
interface WaveformPayload {
  header: WaveformHeader;
  arrays: Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>;
}
```

The header declares each array's name, dtype, and element count. Raw packed
digital samples are `Uint16Array`; derived bits are `Uint8Array`; analog data
is `Float32Array`; LOD edge density uses `Uint32Array`. The parser validates
the `MSAW` magic and uses the backend's four-byte alignment.

Large live windows normally go through `WaveformClient` in a Worker rather
than these main-thread helpers. See [Workers](workers.md).

## Session and analysis coverage

Important higher-level methods include:

```text
sessions(search, offset, limit)
importSession(json_text)
importWaveform(source_text, csv|vcd, sample_rate)
compareSessions(a, b, alignmentOffset?)
triggerSearch(id, trigger, decoder_instance?, auto_scope?)
submitCaptureJob / captureJob
sessionDashboard

spectrum / spectrogram / correlation / eventCorrelation
envelope / thresholdSweep / eyeDiagram / timingSuspects / sanity
```

There is no current `xy` client or backend endpoint.

## Generator calls

The generator surface includes capabilities, configure/start/stop/status,
preview, presets, sweep preview/capture, send, and self-test. `generatorSend`
accepts optional `live`; the UI enables it only for UART, RS-485, and Bit
Banger.

## Downloads

`downloadExport(sessionId, format, body)` posts to the selected export route,
reads `Content-Disposition`, creates a temporary object URL, and triggers the
browser download. `downloadDebugBundle()` does the same for the diagnostics
ZIP.

## Types

`types.ts` mirrors the backend models for status, capabilities/routes,
capture/trigger settings, session/channel/decoder/measurement/marker records,
generator and MIL configuration, log entries, and WebSocket messages. Runtime
validation remains the backend's responsibility; TypeScript interfaces are a
compile-time client contract.

## Tests

- `binary.test.ts` covers framing, alignment, dtypes, and malformed payloads.
- `websocket.test.ts` covers URL selection, subscription, reconnect, and close.
- Backend API tests remain the request/response schema authority.
