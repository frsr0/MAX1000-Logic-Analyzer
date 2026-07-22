"""Decoder lifecycle service — owns run orchestration and dependency ordering.

Consolidates the decoder lifecycle logic that was split across
capture_manager.py (run_decoder, cancel_decoder), api/decoders.py (dispatch),
and session_store.py (event persistence).
"""
from __future__ import annotations

import threading
from typing import List, Optional

from ..capture.session import DecoderInstance, Session
from ..decoders import registry as decoder_registry
from ..decoders.base import DecodeCancelled, DecodeContext
from ..state import capture_manager, store
from ..websocket.manager import manager


class DecoderService:
    """Manages decoder run lifecycle for sessions.

    Handles dependency ordering (stacked decoders) and provides
    batch operations like rerun_all.
    """

    def run(
        self,
        session: Session,
        inst: DecoderInstance,
        region: Optional[List[int]] = None,
    ) -> None:
        """Run *inst*, first ensuring all upstream decoders are complete.

        If *inst* declares a ``consumes`` dependency (e.g. modbus on uart),
        the upstream decoder is run first if not already done.
        """
        decoder = decoder_registry.get(inst.decoder_id)
        if decoder is None:
            raise ValueError(f"Unknown decoder: {inst.decoder_id}")

        # Ensure upstream dependency runs first
        if decoder.consumes:
            src = next((d for d in session.decoders
                        if d.decoder_id == decoder.consumes
                        and d.id != inst.id), None)
            if src is None:
                raise ValueError(
                    f"Stacked decoder '{decoder.id}' needs a "
                    f"'{decoder.consumes}' decoder configured on this session")
            if src.status not in ("done", "running"):
                self.run(session, src)
            # Wait for the upstream to finish (caller should hold a reference
            # if they need synchronous completion; otherwise the downstream
            # will find the events once the upstream finishes).

        if region is not None:
            inst.region = region
            store.save(session)

        # Delegate async execution to the capture manager's proven worker
        capture_manager.run_decoder(session, inst)

    def rerun_all(self, session_id: str) -> None:
        """Re-run all enabled decoders on a session, in dependency order.

        Cancels any running decoders first, clears stored events,
        then runs each enabled decoder in topological order.
        """
        session = store.get(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Cancel any running decoders
        for inst in session.decoders:
            if inst.status == "running":
                capture_manager.cancel_decoder(inst.id)

        # Determine run order: decoders that consume others come after
        ordered = self._topological_order(session.decoders)
        for inst in ordered:
            if not inst.enabled:
                continue
            # Reset state
            inst.status = "idle"
            inst.event_count = 0
            inst.warning_count = 0
            inst.error = None
            store.delete_decoder_events(session_id, inst.id)
            store.save(session)
            self.run(session, inst)

    def cancel(self, decoder_id: str) -> bool:
        """Cancel a running decoder instance."""
        return capture_manager.cancel_decoder(decoder_id)

    def events(
        self,
        session_id: str,
        decoder_id: str,
        start: int = 0,
        end: int = -1,
        limit: int = 5000,
    ) -> List[dict]:
        """Events overlapping *[start, end)* for annotation rendering."""
        all_events = store.load_decoder_events(session_id, decoder_id)
        if not all_events:
            return []
        if end >= 0:
            filtered = [
                e for e in all_events
                if e.get("start_sample", 0) < end
                and e.get("end_sample", 0) >= start
            ]
        else:
            filtered = list(all_events)
        if len(filtered) > limit:
            filtered = filtered[:limit]
        return filtered

    # ── internal ──────────────────────────────────────────────────────

    @staticmethod
    def _topological_order(
        instances: List[DecoderInstance],
    ) -> List[DecoderInstance]:
        """Return decoders in dependency order: consumers after their source.

        Uses the ``consumes`` field from each decoder's registry entry.
        """
        by_id = {d.id: d for d in instances}
        # Build dependency graph
        order: List[DecoderInstance] = []
        visited: set = set()

        def visit(inst: DecoderInstance) -> None:
            if inst.id in visited:
                return
            visited.add(inst.id)
            decoder = decoder_registry.get(inst.decoder_id)
            if decoder and decoder.consumes:
                # Find the upstream instance
                src = next(
                    (d for d in instances if d.decoder_id == decoder.consumes),
                    None,
                )
                if src and src.id != inst.id:
                    visit(src)
            order.append(inst)

        for inst in instances:
            visit(inst)
        return order


# Module-level singleton
decoder_service = DecoderService()
