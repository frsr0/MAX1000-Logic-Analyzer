"""Mixed digital+analog capture strategy — time-correlated frames."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ...capture.sample_format import adc_to_volts
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy
# Import wire-format functions (pure) from the host driver package.
# The host path is already on sys.path via hardware/protocol.py.
from driver.wire_format import (
    MODE_MIXED,
    analog_frame_stride,
    analog_wire_stride,
    decode_analog_frames,
    wire_to_payload,
)

ADC_SCAN_FRAME_RATE_HZ = 125_000.0


class MixedCaptureStrategy(CaptureStrategy):
    """Time-correlated mixed digital+analog capture (16 digital + ADC0..ADC3)."""

    modes = {"mixed", "mixed_continuous"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_readback_compression("raw")
        dev.set_analog_config(MODE_MIXED)

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int | tuple[int, int]] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        nsamp = int(settings.num_samples)
        fstride = analog_frame_stride(MODE_MIXED)
        wstride = analog_wire_stride(MODE_MIXED)
        # SDRAM words per frame must match the HDL burst length, which is
        # (Analog_Frame_Len + 1) / 2.  For mixed mode Analog_Frame_Len=5
        # this gives 3 words per frame.
        words_per_frame = wstride // 2
        sdram_words = nsamp * words_per_frame
        # The ADC scan rate is fixed (~125 kHz); words_per_frame scales it
        # up to the SDRAM word request rate the capture hardware expects.
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
            raise HardwareError("Mixed capture returned 0 bytes — FPGA not responding")

        payload = wire_to_payload(wire)[: nsamp * fstride]
        frames = decode_analog_frames(payload, MODE_MIXED)
        if not frames:
            raise HardwareError("Mixed capture returned no complete frames")

        digital = np.array([fr["digital"] for fr in frames], dtype=np.uint16)
        analog = {}
        adc = np.array([fr["adc"] for fr in frames], dtype=np.uint16)
        for ch in range(adc.shape[1]):
            analog[f"a{ch}"] = adc_to_volts(adc[:, ch])

        actual_rate = float(dev.sample_clk) / float(
            max(1, round(dev.sample_clk / request_rate_hz) - 1) + 1
        )
        rate = actual_rate / words_per_frame
    def _recover(self, dev: CaptureDevice) -> None:
        super()._recover(dev)
        try:
            dev.set_analog_config(MODE_MIXED)
        except Exception:
            pass
