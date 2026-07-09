"""Standard digital capture strategy — single-shot and rolling digital-only."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy


class DigitalCaptureStrategy(CaptureStrategy):
    """Single-shot and rolling general-purpose digital capture."""

    modes = {"single", "continuous", "rolling", "triggered"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_analog_config(0)
        dev.raw_flags &= ~0x3E000  # clear narrow-digital flags
        # Use BRAM (fast mode) for small single-shot captures
        nsamp = int(settings.num_samples)
        dev.fast_mode_enabled = settings.mode == "single" and nsamp <= 1024

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int | tuple[int, int]] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        rate = float(settings.sample_rate)
        nsamp = int(settings.num_samples)
        pre = settings.trigger.pre_trigger_samples

        data = dev.capture(
            rate_hz=rate,
            nsamples=nsamp,
            timeout=max(3, nsamp // 10000 + 2),
            trigger=trigger,
            stop_evt=stop_evt,
            progress_cb=progress,
            pre_trigger=pre,
        )
        if not data:
            raise HardwareError("Capture returned 0 bytes — FPGA not responding")

        # Packed wire: contiguous 16-bit little-endian samples
        n2 = len(data) - (len(data) % 2)
        digital = np.frombuffer(data[:n2], dtype="<u2")
        warnings: list = []
        if len(digital) < nsamp:
            warnings.append(
                f"Device returned {len(digital)} effective samples "
                f"for {nsamp} requested (existing host wire format)")

        capture_divider = max(0, round(dev.sample_clk / rate) - 1)
        return CaptureResult(
            sample_rate=rate,
            digital=digital,
            trigger_sample=min(pre, len(digital)) if pre else None,
            divider=capture_divider,
            warnings=warnings,
        )
