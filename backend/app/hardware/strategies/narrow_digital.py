"""Narrow digital capture strategy (1-channel packed 16:1)."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy

from driver.wire_format import (
    narrow_digital_flags,
    unpack_narrow_digital_words,
)


class NarrowDigitalCaptureStrategy(CaptureStrategy):
    """Packed 1-channel high-speed digital capture.

    Each FPGA word contains 16 consecutive samples for one selected channel.
    """

    modes = {"digital_narrow"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_analog_config(0)

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        nsamp = int(settings.num_samples)
        channel = settings.enabled_digital[0] if settings.enabled_digital else 0
        word_count = max(1, (nsamp + 15) // 16)

        old_flags = dev.raw_flags
        dev.raw_flags = (old_flags & ~0x3E000) | narrow_digital_flags(channel)
        try:
            data = dev.capture(
                rate_hz=float(settings.sample_rate),
                nsamples=word_count,
                timeout=max(3, word_count // 10000 + 2),
                trigger=trigger,
                stop_evt=stop_evt,
                progress_cb=progress,
                pre_trigger=0,
            )
        finally:
            dev.raw_flags = old_flags

        if not data:
            raise HardwareError("Narrow capture returned 0 bytes — FPGA not responding")

        digital = unpack_narrow_digital_words(data, channel=channel, sample_count=nsamp)
        capture_divider = max(0, round(dev.sample_clk / float(settings.sample_rate)) - 1)

        return CaptureResult(
            sample_rate=float(settings.sample_rate),
            digital=digital,
            warnings=[f"Packed 1-channel narrow digital mode on d{channel}"],
            divider=capture_divider,
        )
