# Session Stores

**Files:** `backend/app/capture/session_store.py` (6.9 KB), `backend/app/capture/waveform_store.py` (6.9 KB), `backend/app/capture/chunk_store.py` (1.6 KB)

## Purpose

Persistent storage for capture sessions. Sessions are stored as directories on disk containing JSON metadata, NPZ waveform data, and decoder event files.

## SessionStore

```python
class SessionStore:
    BASE_DIR = Path("data/sessions")
```

Manages session CRUD:

| Method | Description |
|---|---|
| `create(session, waveform)` | Create session directory, write JSON + NPZ |
| `get(session_id)` | Load session from JSON file |
| `list_sessions()` | List all sessions sorted by creation date |
| `update(session)` | Write updated session JSON |
| `delete(session_id)` | Remove session directory and all files |
| `duplicate(session_id)` | Copy session with new ID |

### Directory Layout

```
data/sessions/
  <session_id>/
    session.json          # Session model (Pydantic → JSON)
    waveform.npz           # Raw sample data (immutable)
    decoders/
      <decoder_id>.json   # Decoder events
```

## WaveformStore

```python
class WaveformStore:
    def save(session_id, wf: WaveformData, lod: LodPyramid = None)
    def load(session_id) -> WaveformData
    def load_lod(session_id) -> LodPyramid
    def delete(session_id)
```

- Waveform saved as compressed NPZ with `digital` (uint8 bit-packed) and `analog` (float32) arrays
- LOD pyramid saved alongside for fast zoomed-out rendering
- NPZ format enables numpy-based loading without session model parsing

## ChunkStore

```python
def clamp_window(start: int, end: int, num_samples: int) -> Tuple[int, int]:
    """Clamp query window to valid sample range."""
```

Utility for waveform queries ensuring requested windows stay within capture bounds.

## Data Lifecycle

1. **Capture complete**: `CaptureManager` calls `store.create(session, waveform)`
2. **Query**: API calls `waveform_store.load(session_id)` then `WaveformQuery` reads data
3. **Update**: Session metadata (decoders, markers) saved via `session_store.update()`
4. **Delete**: Entire session directory removed
5. **Duplicate**: `session_store.duplicate()` copies with new ID, preserving all data and events

## Dependencies

| Module | File |
|---|---|
| `Session` model | `capture/session.py` |
| `WaveformData` | `capture/sample_format.py` |
| `LodPyramid` | `capture/lod.py` |
