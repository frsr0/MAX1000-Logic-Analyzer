"""Maximum-analog capture strategy (packed MSO path: 4 real ADC lanes).

The RTL converts all 4 ADC controller channels (AIN3/ADC1, AIN1/ADC2,
AIN4/ADC3, AIN6/ADC4) only in packed mode (REG_FLAGS bit 20, mso_capture
pipeline). The legacy MODE_ANALOG_ALL frame path is a 2-channel dual frame
that the host misreads as 8 lanes, so this strategy captures the packed
16-bit word stream and decodes it with the host driver's
decode_packed_stream, yielding 4 genuinely distinct analog lanes.

Measured on hardware (hw_validation Test 5e): 4 lanes x 120 samples in a
5 ms packed capture window -> ~24 kS/s per lane (ADC round-robin split
4 ways across ~96-100 kS/s aggregate). The packed stream compresses, so
requested capture words != analog sample count; window duration is
num_samples / lane_rate, and the capture word budget is derived from it.
"""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from ...capture.session import CaptureSettings
from ...capture.sample_format import adc_to_volts
from ..base import CaptureResult, HardwareError, ProgressCb
from .base import CaptureDevice, CaptureStrategy
from driver.mso_packed import decode_packed_stream

# Measured per-lane rate in packed 4-channel mode (hw validation Test 5e:
# 120 samples/lane in a 5 ms window). The 4 ADC controller channels round-robin
# the ~96-100 kS/s aggregate conversion rate.
PACKED_LANE_RATE_HZ = 24_000.0
# Word-clock used for the packed capture request (same as Test 5e).
PACKED_WORD_RATE_HZ = 100_000_000.0
# SDRAM word budget ceiling for a single packed capture.
PACKED_MAX_WORDS = 4_194_304
# Packed analog lanes map to mux channels 1..4 (AIN3, AIN1, AIN4, AIN6).
PACKED_ADC_CHANNELS = (1, 2, 3, 4)


class AnalogAllCaptureStrategy(CaptureStrategy):
    """Maximum-analog capture — 4 real ADC lanes via the packed MSO path."""

    modes = {"analog_all", "analog_all_continuous"}

    def _pre_capture(self, dev: CaptureDevice, settings: CaptureSettings) -> None:
        dev.set_readback_compression("raw")
        # Enable the mso_capture packed pipeline (REG_FLAGS bit 20). This is
        # the only RTL path that converts all 4 ADC controller channels.
        dev.raw_flags |= 0x100000  # MODE_PACKED_MSO

    def _do_capture(
        self,
        dev: CaptureDevice,
        settings: CaptureSettings,
        trigger: Optional[int | tuple[int, int]] = None,
        progress: Optional[ProgressCb] = None,
        stop_evt: Optional[threading.Event] = None,
    ) -> CaptureResult:
        nsamp = int(settings.num_samples)
        # Window needed to gather nsamp analog samples per lane at the
        # measured packed per-lane rate. The packed word stream is compressed
        # and written at the fast clock, so the capture rate (sample counter)
        # only gates the window length: choose it so the word budget for the
        # window fits SDRAM, with a floor that keeps the window finite.
        window_s = max(nsamp, 1) / PACKED_LANE_RATE_HZ
        rate = min(PACKED_WORD_RATE_HZ,
                   max(1_000_000, PACKED_MAX_WORDS / window_s))
        word_budget = max(1024, min(PACKED_MAX_WORDS,
                                    int(window_s * rate)))

        data = dev.capture(
            rate_hz=rate,
            nsamples=word_budget,
            timeout=max(3, word_budget // 100_000 + 2),
            trigger=trigger,
            stop_evt=stop_evt,
            progress_cb=progress,
        )
        if not data:
            raise HardwareError("Analog capture returned 0 bytes — FPGA not responding")

        dec = decode_packed_stream(data)
        lanes = dec["analog"]
        if len(lanes) < 4:
            raise HardwareError(
                f"Packed analog capture decoded {len(lanes)} lanes "
                "(expected 4) — packed path not active on this image")
        if not any(len(ch) for ch in lanes):
            raise HardwareError(
                "Packed analog capture returned no complete frames")

        analog = {}
        for idx, adc_ch in enumerate(PACKED_ADC_CHANNELS):
            codes = np.asarray(lanes[idx], dtype=np.uint16)
            if codes.size:
                analog[f"a{adc_ch}"] = adc_to_volts(codes)

        return CaptureResult(
            sample_rate=PACKED_LANE_RATE_HZ,
            digital=None,
            analog=analog,
            divider=max(0, round(dev.sample_clk / rate) - 1),
        )

    def _recover(self, dev: CaptureDevice) -> None:
        super()._recover(dev)
        try:
            dev.raw_flags &= ~0x100000  # MODE_PACKED_MSO
        except Exception:
            pass
