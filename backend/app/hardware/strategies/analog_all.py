"""Maximum-analog capture strategy (8 decoded ADC lanes in the raw frames)."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ...capture.sample_format import adc_to_volts
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy
from driver.wire_format import (
    MODE_ANALOG_ALL,
    analog_frame_stride,
    decode_analog_frames,
    wire_to_payload,
)

ADC_SCAN_FRAME_RATE_HZ = 125_000.0


class AnalogAllCaptureStrategy(CaptureStrategy):
    """Maximum-analog capture — all decoded analog inputs."""

    modes = {"analog_all", "analog_all_continuous"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_readback_compression("raw")
        dev.set_analog_config(MODE_ANALOG_ALL, adc_channel=1)

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        nsamp = int(settings.num_samples)
        stride = analog_frame_stride(MODE_ANALOG_ALL)
        words_per_frame = max(1, stride // 2)
        sdram_words = nsamp * words_per_frame
        request_rate_hz = ADC_SCAN_FRAME_RATE_HZ * words_per_frame

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
        frames = decode_analog_frames(payload, MODE_ANALOG_ALL)
        if not frames:
            raise HardwareError("Analog capture returned no complete frames")

        analog = {}
        adc = np.array([fr["adc"] for fr in frames], dtype=np.uint16)
        adc_channels = [1, 2, 3, 4]
        for idx, adc_ch in enumerate(adc_channels[: adc.shape[1]]):
            analog[f"a{adc_ch}"] = adc_to_volts(adc[:, idx])

        actual_rate = float(dev.sample_clk) / float(
            max(1, round(dev.sample_clk / request_rate_hz) - 1) + 1
        )
        rate = actual_rate / words_per_frame
        capture_divider = max(0, round(dev.sample_clk / request_rate_hz) - 1)

        return CaptureResult(
            sample_rate=rate,
            digital=None,
            analog=analog,
            divider=capture_divider,
        )

    def _recover(self, dev: CaptureDevice) -> None:
        super()._recover(dev)
        try:
            dev.set_analog_config(MODE_ANALOG_ALL, adc_channel=1)
        except Exception:
            pass
