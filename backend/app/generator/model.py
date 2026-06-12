"""Generator request/response models (the wire-facing GeneratorConfig lives in
hardware/device_models.py so devices and API share one schema)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..hardware.device_models import GeneratorConfig


class GeneratorSendRequest(BaseModel):
    config: Optional[GeneratorConfig] = None     # None = use last configured
    capture: bool = False                        # loopback: capture while sending
    capture_rate: float = 1_000_000.0
    capture_samples: int = 10_000
    expected_hex: Optional[str] = None           # auto-compare decoded output
    decoder_id: Optional[str] = None             # decoder for auto-compare


class GeneratorSelfTestResult(BaseModel):
    passed: bool
    sent_hex: str = ""
    decoded_hex: str = ""
    detail: str = ""
    session_id: Optional[str] = None
    mismatches: List[int] = Field(default_factory=list)
