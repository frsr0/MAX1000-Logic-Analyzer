"""On-disk session persistence: data/sessions/<id>/session.json + waveform.npz."""
from __future__ import annotations

import json
import shutil
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from ..config import SESSION_DIR
from .lod import LodPyramid
from .sample_format import WaveformData
from .session import Session, new_id


class SessionStore:
    """Owns session metadata persistence and an LRU cache of waveform data."""

    def __init__(self, root: Path = SESSION_DIR, cache_size: int = 4):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sessions: Dict[str, Session] = {}
        self._wf_cache: "OrderedDict[str, WaveformData]" = OrderedDict()
        self._lod_cache: "OrderedDict[str, LodPyramid]" = OrderedDict()
        self._cache_size = cache_size
        self._load_all()

    # ── metadata ─────────────────────────────────────────────────────

    def _dir(self, session_id: str) -> Path:
        return self.root / session_id

    def _load_all(self) -> None:
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            f = d / "session.json"
            if f.exists():
                try:
                    self._sessions[d.name] = Session.model_validate_json(
                        f.read_text(encoding="utf-8"))
                except Exception:
                    continue

    def list_sessions(self) -> List[Session]:
        with self._lock:
            return sorted(self._sessions.values(),
                          key=lambda s: s.created_at, reverse=True)

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def save(self, session: Session) -> None:
        with self._lock:
            session.touch()
            self._sessions[session.id] = session
            d = self._dir(session.id)
            d.mkdir(parents=True, exist_ok=True)
            (d / "session.json").write_text(
                session.model_dump_json(indent=2), encoding="utf-8")

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
            self._wf_cache.pop(session_id, None)
            self._lod_cache.pop(session_id, None)
            shutil.rmtree(self._dir(session_id), ignore_errors=True)
            return True

    def duplicate(self, session_id: str) -> Optional[Session]:
        with self._lock:
            src = self._sessions.get(session_id)
            if src is None:
                return None
            copy = src.model_copy(deep=True)
            copy.id = new_id("ses")
            copy.name = f"{src.name} (copy)"
            import time
            copy.created_at = time.time()
            copy.exports = []
            self.save(copy)
            src_npz = self._dir(session_id) / "waveform.npz"
            if src_npz.exists():
                shutil.copy(src_npz, self._dir(copy.id) / "waveform.npz")
            return copy

    # ── waveform data ────────────────────────────────────────────────

    def save_waveform(self, session_id: str, wf: WaveformData) -> None:
        d = self._dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        arrays = {"sample_rate": np.array([wf.sample_rate])}
        if wf.digital is not None:
            arrays["digital"] = wf.digital
        for name, arr in wf.analog.items():
            arrays[f"analog__{name}"] = arr
        for name, arr in wf.derived_digital.items():
            arrays[f"derived__{name}"] = arr
        np.savez_compressed(d / "waveform.npz", **arrays)
        with self._lock:
            self._cache_put(self._wf_cache, session_id, wf)
            self._lod_cache.pop(session_id, None)

    def load_waveform(self, session_id: str) -> Optional[WaveformData]:
        with self._lock:
            if session_id in self._wf_cache:
                self._wf_cache.move_to_end(session_id)
                return self._wf_cache[session_id]
        f = self._dir(session_id) / "waveform.npz"
        if not f.exists():
            return None
        with np.load(f) as z:
            wf = WaveformData(sample_rate=float(z["sample_rate"][0]))
            if "digital" in z:
                wf.digital = z["digital"]
            for key in z.files:
                if key.startswith("analog__"):
                    wf.analog[key[len("analog__"):]] = z[key]
                elif key.startswith("derived__"):
                    wf.derived_digital[key[len("derived__"):]] = z[key]
        with self._lock:
            self._cache_put(self._wf_cache, session_id, wf)
        return wf

    def get_lod(self, session_id: str) -> Optional[LodPyramid]:
        with self._lock:
            if session_id in self._lod_cache:
                self._lod_cache.move_to_end(session_id)
                return self._lod_cache[session_id]
        wf = self.load_waveform(session_id)
        if wf is None:
            return None
        lod = LodPyramid(wf)
        with self._lock:
            self._cache_put(self._lod_cache, session_id, lod)
        return lod

    def invalidate_lod(self, session_id: str) -> None:
        with self._lock:
            self._lod_cache.pop(session_id, None)

    def _cache_put(self, cache: OrderedDict, key: str, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._cache_size:
            cache.popitem(last=False)

    # ── decoder results (kept out of session.json — can be large) ────

    def save_decoder_events(self, session_id: str, decoder_id: str,
                            events: List[dict]) -> None:
        d = self._dir(session_id) / "decoders"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{decoder_id}.json").write_text(
            json.dumps(events), encoding="utf-8")

    def load_decoder_events(self, session_id: str, decoder_id: str) -> List[dict]:
        f = self._dir(session_id) / "decoders" / f"{decoder_id}.json"
        if not f.exists():
            return []
        return json.loads(f.read_text(encoding="utf-8"))

    def delete_decoder_events(self, session_id: str, decoder_id: str) -> None:
        f = self._dir(session_id) / "decoders" / f"{decoder_id}.json"
        if f.exists():
            f.unlink()

    def export_dir(self, session_id: str) -> Path:
        d = self._dir(session_id) / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d
