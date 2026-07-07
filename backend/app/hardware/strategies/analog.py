"""Analog-fast capture strategy (1 ADC lane)."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ...capture.sample_format import adc_to_volts
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy
from driver.wire_format import (
    MODE_ANALOG_FAST,
    analog_frame_stride,
    decode_analog_frames,
    wire_to_payload,
)

ADC_FAST_FRAME_RATE_HZ = 1_000_000.0


class AnalogCaptureStrategy(CaptureStrategy):
    """High-speed single-channel analog capture (ADC1 → AIN3)."""

    modes = {"analog", "analog_fast", "analog_continuous"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_readback_compression("raw")
        dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=1)

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        nsamp = int(settings.num_samples)
        stride = analog_frame_stride(MODE_ANALOG_FAST)
        words_per_frame = max(1, stride // 2)
        sdram_words = nsamp * words_per_frame
        request_rate_hz = ADC_FAST_FRAME_RATE_HZ

        wire = dev.capture(
            rate_hz=request_rate_hz,
            nsamples=sdram_words,
            timeout=max(3, sdram_words // 10000 + 2),
            trigger=trigger,
            stop_evt=stop_evt,
            progress_cb=progress,
        )
        if not wire:
            raise HardwareError("Analog capture returned 0 bytes — FPGA not responding")

        payload = wire_to_payload(wire)[: nsamp * stride]
        frames = decode_analog_frames(payload, MODE_ANALOG_FAST)
        if not frames:
            raise HardwareError("Analog capture returned no complete frames")

        analog = {}
        adc = np.array([fr["adc"] for fr in frames], dtype=np.uint16)
        analog["a1"] = adc_to_volts(adc[:, 0]) if adc.ndim > 1 and adc.shape[1] > 0 else adc_to_volts(adc)

        actual_rate = float(dev.sample_clk) / float(
            max(1, round(dev.sample_clk / request_rate_hz) - 1) + 1
        )
        capture_divider = max(0, round(dev.sample_clk / request_rate_hz) - 1)

        return CaptureResult(
            sample_rate=actual_rate,
            digital=None,
            analog=analog,
            divider=capture_divider,
        )

    def _recover(self, dev: CaptureDevice) -> None:
        super()._recover(dev)
        try:
            dev.set_analog_config(MODE_ANALOG_FAST, adc_channel=1)
        except Exception:
            pass
