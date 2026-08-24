"""Hardware abstraction. Every backend (real FPGA via the existing host
driver, mock device, future hardware) implements HardwareDevice.

The capture call is synchronous and blocking — the CaptureManager runs it on a
worker thread and handles progress/cancellation via the callbacks/event.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from typing import Any, Callable, Dict, Optional

import numpy as np

from ..capture.session import CaptureSettings, DeviceMetadata
from .device_models import (DebugInfo, DeviceCapabilities, GeneratorConfig,
                            GeneratorStatus)

ProgressCb = Callable[[int, int, str], None]   # (read, total, phase)


@dataclass
class CaptureResult:
    """Canonical result returned by every hardware adapter.

    The result is the seam between a hardware implementation and the capture
    manager.  Digital samples are packed 16-channel words; analog arrays are
    voltage samples.  All populated arrays must describe the same time axis.
    """
    sample_rate: float
    digital: Optional[np.ndarray] = None         # packed uint16
    analog: Dict[str, np.ndarray] = field(default_factory=dict)  # volts f32
    trigger_sample: Optional[int] = None
    divider: Optional[int] = None
    warnings: list = field(default_factory=list)


class HardwareError(Exception):
    pass


def validate_capture_result(result: CaptureResult) -> int:
    """Validate the capture contract and return the sample count.

    Hardware adapters translate device-specific payloads before returning a
    ``CaptureResult``.  Keeping this check at the seam prevents malformed
    digital/analog lengths or trigger positions from leaking into session
    storage, WebSocket events, and decoder input.
    """
    if not math.isfinite(float(result.sample_rate)) or result.sample_rate <= 0:
        raise HardwareError("Capture result sample rate must be finite and positive")

    arrays = []
    if result.digital is not None:
        digital = np.asarray(result.digital)
        if digital.ndim != 1 or not np.issubdtype(digital.dtype, np.integer):
            raise HardwareError("Capture result digital samples must be a 1-D integer array")
        if digital.size and (int(digital.min()) < 0 or int(digital.max()) > 0xFFFF):
            raise HardwareError("Capture result digital samples must fit in uint16 words")
        arrays.append(("digital", digital))

    for name, values in result.analog.items():
        analog = np.asarray(values)
        if analog.ndim != 1 or not np.issubdtype(analog.dtype, np.number):
            raise HardwareError(f"Capture result analog channel '{name}' must be a 1-D numeric array")
        arrays.append((f"analog channel '{name}'", analog))

    if not arrays:
        raise HardwareError("Capture result contains no samples")

    sample_count = len(arrays[0][1])
    mismatched = [(name, len(values)) for name, values in arrays
                  if len(values) != sample_count]
    if mismatched:
        details = ", ".join(f"{name}={length}" for name, length in mismatched)
        raise HardwareError(
            f"Capture result channels must share one sample count ({details}, expected {sample_count})")

    if result.trigger_sample is not None and not 0 <= int(result.trigger_sample) <= sample_count:
        raise HardwareError("Capture result trigger sample is outside the capture")
    if result.divider is not None and int(result.divider) < 0:
        raise HardwareError("Capture result clock divider cannot be negative")
    return sample_count


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
        if (settings.analog_enabled or settings.mode in (
                "analog", "analog_fast", "analog_all", "analog_continuous",
                "analog_all_continuous", "mixed", "mixed_continuous")) and not caps.supports_analog:
            findings.append({"level": "error",
                             "message": "Analog capture is not available on this device"})
        if settings.mode in (
                "mixed", "mixed_continuous", "analog", "analog_fast",
                "analog_all", "analog_continuous", "analog_all_continuous"
        ) and settings.readback_compression != "raw":
            findings.append({"level": "info",
                             "message": "Delta/RLE readback compression is digital-only; "
                                        "mixed and analog captures use raw readback"})
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

    def validate_generator_config(self, cfg: GeneratorConfig) -> None:
        """Reject requests for wires/features absent from the device route.

        Older/custom adapters may not publish route descriptors yet, so an
        empty descriptor list preserves their existing protocol validation.
        Once descriptors are present, optional physical wires become an
        explicit capability boundary instead of silently being ignored.
        """
        caps = self.get_capabilities()
        routes = caps.generator_routes
        if not routes:
            return
        route = next((r for r in routes if r.protocol == cfg.protocol), None)
        if route is None:
            # A protocol may be mock-only or preview-only and therefore have
            # no physical route descriptor. Preserve the protocol list as the
            # source of truth for those software-only generators.
            if cfg.protocol in caps.generator_protocols:
                return
            raise HardwareError(
                f"Generator route '{cfg.protocol}' is unavailable on this device")
        if not route.available:
            raise HardwareError(
                f"Generator route '{cfg.protocol}' is unavailable on this device")

        extra = cfg.extra or {}
        def check_pin(name: str, value: Any) -> None:
            if value is not None and not 0 <= int(value) <= 25:
                raise HardwareError(f"{name} must be a physical pin in range 0..25")

        check_pin("RS-485 DE pin", extra.get("de_pin"))
        check_pin("SPI CS pin", extra.get("cs_pin"))
        check_pin("SPI MISO pin", extra.get("miso_pin"))
        for channel_name in ("miso_capture_channel", "cs_capture_channel"):
            if extra.get(channel_name) is not None and not 0 <= int(extra[channel_name]) <= 15:
                raise HardwareError(f"SPI {channel_name} must be in range 0..15")
        if cfg.protocol == "rs485" and extra.get("de_pin") is not None:
            if int(extra["de_pin"]) in (int(cfg.tx_pin), int(cfg.scl_pin)):
                raise HardwareError("RS-485 DE pin must differ from A and B pins")
        if cfg.protocol == "spi":
            aux = [p for p in (extra.get("cs_pin"), extra.get("miso_pin"))
                   if p is not None]
            if any(int(p) in (int(cfg.tx_pin), int(cfg.scl_pin)) for p in aux):
                raise HardwareError("SPI CS/MISO pins must differ from MOSI and SCLK pins")
            channels = [c for c in (extra.get("cs_capture_channel"),
                                    extra.get("miso_capture_channel"))
                        if c is not None]
            if len(channels) != len(set(map(int, channels))):
                raise HardwareError("SPI CS/MISO capture channels must differ")
        requested = []
        if cfg.protocol == "rs485" and extra.get("de_pin") is not None:
            requested.append(("de_pin", "a separate RS-485 DE pin"))
        if cfg.protocol == "spi":
            if extra.get("cs_pin") is not None:
                requested.append(("cs", "SPI CS output"))
            if extra.get("miso_pin") is not None:
                requested.append(("miso", "SPI MISO output"))
        if cfg.protocol == "swd" and extra.get("capture_target_response", True):
            requested.append(("transaction_capture", "SWD transaction capture"))
        for feature, label in requested:
            if feature not in route.features:
                raise HardwareError(
                    f"{label} is not routed by the connected device firmware")

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
