"""Post-capture (software) trigger search.

Hardware-supported triggers run on the FPGA; everything else is located
after the capture by scanning the recorded samples. The capture itself is
never altered — we only report the matching sample position."""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..capture.sample_format import WaveformData, find_edges
from ..capture.session import TriggerConfig


def find_software_trigger(wf: WaveformData, trig: TriggerConfig) -> Optional[int]:
    if wf.digital is None or trig.type == "none":
        return None
    t = trig.type
    chans = trig.channels or [0]

    if t in ("rising", "falling", "any_edge"):
        kind = {"rising": "rising", "falling": "falling", "any_edge": "any"}[t]
        best = None
        for c in chans:
            e = find_edges(wf.digital_channel(c), kind)
            if len(e):
                best = int(e[0]) if best is None else min(best, int(e[0]))
        return best

    if t in ("high", "low"):
        want = 1 if t == "high" else 0
        for c in chans:
            idx = np.nonzero(wf.digital_channel(c) == want)[0]
            if len(idx):
                return int(idx[0])
        return None

    if t == "pattern" and trig.pattern:
        # pattern like "1x0" — index i = channel chans[i] (or i if not given)
        mask = 0
        value = 0
        for i, ch in enumerate(trig.pattern.strip().lower()):
            c = chans[i] if i < len(chans) else i
            if ch == "x":
                continue
            mask |= 1 << c
            if ch == "1":
                value |= 1 << c
        hits = np.nonzero((wf.digital & mask) == value)[0]
        return int(hits[0]) if len(hits) else None

    if t == "bus_value" and trig.value is not None:
        mask = 0
        for c in chans:
            mask |= 1 << c
        value = 0
        for i, c in enumerate(chans):
            if (trig.value >> i) & 1:
                value |= 1 << c
        hits = np.nonzero((wf.digital & mask) == value)[0]
        return int(hits[0]) if len(hits) else None

    if t in ("pulse_wider", "pulse_narrower") and trig.width_s:
        width_samples = trig.width_s * wf.sample_rate
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        for i in range(1, len(bounds) - 1):
            w = bounds[i + 1] - bounds[i]
            if (t == "pulse_wider" and w > width_samples) or \
               (t == "pulse_narrower" and w < width_samples):
                return int(bounds[i])
        return None

    if t == "timeout" and trig.width_s:
        width_samples = int(trig.width_s * wf.sample_rate)
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        for i in range(len(bounds) - 1):
            if bounds[i + 1] - bounds[i] >= width_samples:
                return int(bounds[i] + width_samples)
        return None

    if t == "glitch":
        max_w = max(1, int((trig.width_s or 3 / wf.sample_rate) * wf.sample_rate))
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        for i in range(1, len(bounds) - 1):
            if bounds[i + 1] - bounds[i] <= max_w:
                return int(bounds[i])
        return None

    # uart_byte/i2c_address/i2c_nack/spi_byte/sequence/decoder_error need a
    # decoder pass — handled at the API layer by running the decoder and
    # picking the first matching event.
    return None
