"""Session assertion endpoint for automated sweeps and CI."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..state import store
from ..validation import junit_xml, validate_events
from .deps import get_session_or_404

router = APIRouter(tags=["validation"])


class ValidationRequest(BaseModel):
    spec: dict = {}
    decoder_instance: str | None = None
    junit: bool = False


@router.post("/api/sessions/{session_id}/validate")
def validate_session(session_id: str, req: ValidationRequest):
    session = get_session_or_404(session_id)
    if req.decoder_instance:
        match = next((d for d in session.decoders
                      if d.id == req.decoder_instance), None)
        if match is None:
            raise HTTPException(
                404, f"Decoder instance not found: {req.decoder_instance}")
        decoders = [match]
    else:
        decoders = session.decoders
    events = []
    for decoder in decoders:
        if decoder.status == "done":
            events.extend(store.load_decoder_events(session_id, decoder.id))
    result = validate_events(events, req.spec)
    if req.junit:
        result["junit_xml"] = junit_xml(result, session.name)
    return result
