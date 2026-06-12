"""Process-wide singletons: session store + capture manager."""
from .capture.capture_manager import CaptureManager
from .capture.session_store import SessionStore

store = SessionStore()
capture_manager = CaptureManager(store)
