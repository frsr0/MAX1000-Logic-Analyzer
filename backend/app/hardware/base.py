"""Hardware abstraction. Every backend (real FPGA via the existing host
driver, mock device, future hardware) implements HardwareDevice.

The capture call is synchronous and blocking — the CaptureManager runs it on a
worker thread and handles progress/cancellation via the callbacks/event.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import numpy as np

from ..capture.session import CaptureSettings, DeviceMetadata
from .device_models import (DebugInfo, DeviceCapabilities, GeneratorConfig,
                            GeneratorStatus)

ProgressCb = Callable[[int, int, str], None]   # (read, total, phase)


@dataclass
class CaptureResult:
    sample_rate: float
    digital: Optional[np.ndarray] = None         # packed uint16
    analog: Dict[str, np.ndarray] = field(default_factory=dict)  # volts f32
    trigger_sample: Optional[int] = None
    divider: Optional[int] = None
    warnings: list = field(default_factory=list)


class HardwareError(Exception):
    pass


class HardwareDevice(ABC):
    """Interface owned by the backend server; browsers never touch hardware."""

    @abstractmethod
    def connect(self) -> DeviceMetadata: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    def get_metadata(self) -> DeviceMetadata: ...

    @abstractmethod
    def get_capabilities(self) -> DeviceCapabilities: ...

    @abstractmethod
    def capture(self, settings: CaptureSettings,
                progress: Optional[ProgressCb] = None,
                stop_evt: Optional[threading.Event] = None) -> CaptureResult: ...

    @abstractmethod
    def get_debug_info(self) -> DebugInfo: ...

    def validate_settings(self, settings: CaptureSettings) -> list:
        """Return a list of {'level','message'} validation findings."""
        caps = self.get_capabilities()
        findings = []
        if settings.sample_rate > caps.max_sample_rate:
            findings.append({"level": "error",
                             "message": f"Sample rate {settings.sample_rate:.0f} Hz exceeds "
                                        f"device maximum {caps.max_sample_rate:.0f} Hz"})
        if settings.num_samples > caps.max_samples:
            findings.append({"level": "error",
                             "message": f"{settings.num_samples} samples exceeds capture "
                                        f"depth {caps.max_samples}"})
        if settings.analog_enabled and not caps.supports_analog:
            findings.append({"level": "error",
                             "message": "Analog capture is not available on this device"})
        trig = settings.trigger
        cap_map = {t.type: t.execution for t in caps.triggers}
        if trig.type != "none":
            execu = cap_map.get(trig.type, "unavailable")
            if execu == "unavailable":
                findings.append({"level": "error",
                                 "message": f"Trigger type '{trig.type}' is unavailable on this device"})
            elif execu == "post_capture":
                findings.append({"level": "info",
                                 "message": f"Trigger '{trig.type}' runs post-capture in software"})
        return findings

    # Generator — optional; default reports unsupported
    def generator_status(self) -> GeneratorStatus:
        return GeneratorStatus(supported=False, detail="No generator on this device")

    def generator_configure(self, cfg: GeneratorConfig) -> None:
        raise HardwareError("Signal generator not supported on this device")

    def generator_start(self) -> None:
        raise HardwareError("Signal generator not supported on this device")

    def generator_stop(self) -> None:
        raise HardwareError("Signal generator not supported on this device")

    def capture_with_generator(self, settings: CaptureSettings, cfg: GeneratorConfig,
                               progress: Optional[ProgressCb] = None,
                               stop_evt: Optional[threading.Event] = None) -> CaptureResult:
        raise HardwareError("Generator loopback capture not supported on this device")

    def self_test(self) -> dict:
        return {"passed": False, "checks": [],
                "message": "Self-test not implemented for this device"}
