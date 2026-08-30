# Session Storage

**Files:** `backend/app/capture/session_store.py`, `waveform_store.py`, and
`chunk_store.py`

`SessionStore` owns persistent session metadata, waveform arrays, decoder
events, and small in-memory LRU caches. `waveform_store.py` encodes binary MSAW
responses; it is not a second persistence class.

## Directory layout

```text
data/sessions/<session_id>/
  session.json
  waveform.npz
  decoders/
    <decoder_instance_id>.json
  exports/
```

`session.json` holds the Pydantic session model. Large decoder event lists are
separate so fetching session metadata remains bounded.

## SessionStore responsibilities

| Method group | Behavior |
|---|---|
| `list_sessions`, `get`, `save` | Metadata CRUD and startup reload |
| `delete`, `duplicate` | Whole-session lifecycle |
| `save_waveform`, `load_waveform` | NumPy waveform persistence with an LRU cache |
| `get_lod`, `invalidate_lod` | Build/cache a resolution pyramid from current arrays |
| `save_decoder_events`, `load_decoder_events`, `delete_decoder_events` | Per-instance event files |
| `export_dir` | Per-session export workspace |

Session IDs are validated before they are resolved below the configured
storage root.

## Waveform persistence

`waveform.npz` contains:

- packed `uint16` digital samples;
- one float array per analog channel;
- one array per derived channel;
- sample-rate metadata.

Persistence intentionally uses plain `numpy.savez`, not
`numpy.savez_compressed`. Live capture rewrites a multi-million-sample rolling
window frequently; compression previously took about 150 ms per write and
could exceed the chunk cadence, starving LOD and browser window requests.
Exported NPZ files remain a separate user-facing format.

## MSAW transport

`waveform_store.py` converts a raw or LOD window into the binary `MSAW`
protocol used by the frontend. `overview_payload` creates minimap data.
`chunk_store.py` clamps raw windows and provides digital, analog, derived, and
value-at accessors.

## Live update lifecycle

1. CaptureManager appends/replaces the in-memory rolling window.
2. `save_waveform` writes the current uncompressed NPZ and invalidates cached
   LOD state.
3. The session WebSocket announces `waveform_ready` with sample/chunk metadata.
4. Browser window requests are served from current data while overview updates
   are throttled.
5. Metadata refresh happens outside the critical waveform drawing path.

See [Waveform Service](waveform-service.md), [Workers](../frontend/workers.md),
and [Session Model](session-model.md).
