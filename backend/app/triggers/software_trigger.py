"""Post-capture (software) trigger search.

Hardware-supported triggers run on the FPGA; everything else is located
after the capture by scanning the recorded samples. The capture itself is
never altered — we only report the matching sample position."""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..capture.sample_format import WaveformData, find_edges
from ..capture.session import TriggerConfig


def _event_value(event: dict) -> Optional[int]:
    fields = event.get("fields", {})
    for key in ("byte", "mosi", "miso", "address", "value", "word"):
        if fields.get(key) is not None:
            try:
                return int(fields[key])
            except (TypeError, ValueError):
                pass
    return None


def _event_matches(event: dict, event_type: str, value: Optional[int]) -> bool:
    if event.get("type") != event_type:
        return False
    return value is None or _event_value(event) == int(value)


def _protocol_event_trigger(trig: TriggerConfig,
                            decoder_events: list[dict]) -> Optional[int]:
    event_type = {
        "uart_byte": "uart_byte",
        "i2c_address": "i2c_address",
        "i2c_nack": None,
        "spi_byte": "spi_word",
        "decoder_error": None,
    }.get(trig.type)
    matches = []
    for event in sorted(decoder_events, key=lambda e: e.get("start_sample", 0)):
        if trig.type == "i2c_nack":
            fields = event.get("fields", {})
            hit = fields.get("ack") is False
        elif trig.type == "decoder_error":
            hit = event.get("severity") == "error"
        else:
            hit = _event_matches(event, event_type or "", trig.value)
            if trig.type == "spi_byte":
                hit = hit and (trig.value is None or
                               _event_value(event) == int(trig.value))
        if hit:
            matches.append(event)
    occurrence = max(1, int(trig.occurrence or 1))
    if len(matches) < occurrence:
        return None
    return int(matches[occurrence - 1].get("start_sample", 0))


def _reverse_pattern_bits(value: int, width: int) -> int:
    result = 0
    for bit in range(width):
        result |= ((int(value) >> bit) & 1) << (width - 1 - bit)
    return result


def _normalized_pattern(trig: TriggerConfig) -> tuple[int, int, int]:
    width = max(1, min(32, int(trig.frame_width or 8)))
    width_mask = (1 << width) - 1 if width < 32 else 0xFFFFFFFF
    value = int(trig.value or 0) & width_mask
    mask = int(trig.match_mask if trig.match_mask is not None else width_mask) & width_mask
    if trig.bit_order == "lsb_first":
        value = _reverse_pattern_bits(value, width)
        mask = _reverse_pattern_bits(mask, width)
    return value, mask, width


def project_generic_pattern_for_hardware(trig: TriggerConfig) -> TriggerConfig:
    """Project a multi-channel pattern onto the first coarse trigger channel."""
    channels = [int(c) for c in trig.channels]
    if len(channels) <= 1:
        return trig
    value, mask, width = _normalized_pattern(trig)
    lane_count = len(channels)
    positions = list(range(0, width, lane_count))
    coarse_value = 0
    coarse_mask = 0
    for position in positions:
        source_bit = width - 1 - position
        coarse_value = (coarse_value << 1) | ((value >> source_bit) & 1)
        coarse_mask = (coarse_mask << 1) | ((mask >> source_bit) & 1)
    return trig.model_copy(update={
        "channels": [channels[0]],
        "frame_width": len(positions),
        "value": coarse_value,
        "match_mask": coarse_mask,
        "bit_order": "msb_first",
        "execution": "hardware",
    })


def _generic_pattern_trigger(wf: WaveformData,
                             trig: TriggerConfig) -> Optional[int]:
    if wf.digital is None or not trig.channels:
        return None
    channels = [int(c) for c in trig.channels]
    if any(c < 0 or c >= 16 for c in channels):
        return None
    value, mask, width = _normalized_pattern(trig)
    lane_count = len(channels)
    sample_count = wf.num_samples

    if trig.clock_source == "internal_baud":
        baud = max(1, int(trig.baud or 115200))
        divisor = max(1, round(wf.sample_rate / baud))
        if trig.start_mode == "edge_on_channel":
            start_bits = wf.digital_channel(int(trig.start_channel))
            starts = find_edges(start_bits,
                                "rising" if trig.start_polarity else "falling")
            first_offset = (2 if divisor < 2 else divisor + divisor // 2)
            sample_groups = [range(int(start) + first_offset, sample_count, divisor)
                             for start in starts]
        else:
            sample_groups = [range(divisor, sample_count, divisor)]
    else:
        clock_bits = wf.digital_channel(int(trig.clock_channel))
        clock_edges = find_edges(clock_bits,
                                 "falling" if trig.clock_edge == "falling" else "rising")
        if trig.start_mode == "edge_on_channel":
            start_bits = wf.digital_channel(int(trig.start_channel))
            starts = find_edges(start_bits,
                                "rising" if trig.start_polarity else "falling")
            sample_groups = [clock_edges[clock_edges >= start] for start in starts]
        else:
            sample_groups = [clock_edges]

    matches = []
    for samples in sample_groups:
        frame = 0
        bit_count = 0
        for sample in samples:
            sample = int(sample)
            if sample >= sample_count:
                break
            lanes_this_sample = min(lane_count, width - bit_count)
            packed = 0
            for channel in channels[:lanes_this_sample]:
                packed = (packed << 1) | int((int(wf.digital[sample]) >> channel) & 1)
            frame = (frame << lanes_this_sample) | packed
            bit_count += lanes_this_sample
            if bit_count < width:
                continue
            if ((frame ^ value) & mask) == 0:
                matches.append(sample)
                if trig.start_mode == "edge_on_channel":
                    break
            frame = 0
            bit_count = 0
    occurrence = max(1, int(trig.occurrence or 1))
    return matches[occurrence - 1] if len(matches) >= occurrence else None


def _sequence_trigger(trig: TriggerConfig,
                      decoder_events: list[dict]) -> Optional[int]:
    if not trig.sequence_steps:
        return None
    steps = trig.sequence_steps
    events = sorted(decoder_events, key=lambda e: e.get("start_sample", 0))
    window = float(trig.window_s or 0)
    first_step = steps[0]
    for first_i, first in enumerate(events):
        if not _event_matches(first, str(first_step.get("type", "")),
                              first_step.get("value")):
            continue
        cursor = first
        ok = True
        for step in steps[1:]:
            typ = str(step.get("type", ""))
            value = step.get("value")
            found = next((e for e in events[first_i:]
                          if e.get("start_sample", 0) >= cursor.get("start_sample", 0)
                          and _event_matches(e, typ, value)), None)
            if found is None:
                ok = False
                break
            if window and (found.get("start_time", 0) -
                           first.get("start_time", 0)) > window:
                ok = False
                break
            cursor = found
        if ok:
            return int(first.get("start_sample", 0))
    return None


def find_software_trigger(wf: WaveformData, trig: TriggerConfig,
                          decoder_events: Optional[list[dict]] = None) -> Optional[int]:
    if trig.type == "none":
        return None
    decoder_events = decoder_events or []
    if trig.type == "generic_pattern":
        return _generic_pattern_trigger(wf, trig)
    if trig.type in ("uart_byte", "i2c_address", "i2c_nack", "spi_byte",
                     "decoder_error"):
        return _protocol_event_trigger(trig, decoder_events)
    if trig.type == "sequence":
        return _sequence_trigger(trig, decoder_events)
    if wf.digital is None and not trig.channel_refs:
        return None
    t = trig.type
    chans = trig.channels or [0]
    refs = list(trig.channel_refs or [f"d{c}" for c in (trig.channels or [0])])
    occurrence = max(1, int(trig.occurrence or 1))
    consecutive = max(1, int(trig.consecutive or 1))

    def nth(values):
        values = sorted(int(v) for v in values)
        if trig.holdoff_s:
            holdoff = max(1, int(float(trig.holdoff_s) * wf.sample_rate))
            filtered = []
            for value in values:
                if not filtered or value - filtered[-1] >= holdoff:
                    filtered.append(value)
            values = filtered
        if consecutive > 1:
            grouped = []
            for value in values:
                if not grouped or value > grouped[-1][-1] + 1:
                    grouped.append([value])
                else:
                    grouped[-1].append(value)
            values = [group[0] for group in grouped if len(group) >= consecutive]
        return values[occurrence - 1] if len(values) >= occurrence else None

    def width_ok(width_samples: int) -> bool:
        width_s = width_samples / wf.sample_rate
        return ((trig.min_duration_s is None or width_s >= trig.min_duration_s) and
                (trig.max_duration_s is None or width_s <= trig.max_duration_s))

    if t in ("rising", "falling", "any_edge"):
        kind = {"rising": "rising", "falling": "falling", "any_edge": "any"}[t]
        matches = []
        for ref in refs:
            e = find_edges(wf.channel_bits(ref), kind)
            bounds = np.concatenate(([0], e, [wf.num_samples]))
            matches.extend(int(x) for j, x in enumerate(e)
                           if j + 2 < len(bounds) and width_ok(int(bounds[j + 2] - x)))
        return nth(matches)

    if t in ("high", "low"):
        want = 1 if t == "high" else 0
        matches = []
        for ref in refs:
            matches.extend(int(x) for x in np.nonzero(wf.channel_bits(ref) == want)[0])
        return nth(matches)

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
        return nth(hits)

    if t == "bus_value" and trig.value is not None:
        mask = 0
        for c in chans:
            mask |= 1 << c
        value = 0
        for i, c in enumerate(chans):
            if (trig.value >> i) & 1:
                value |= 1 << c
        hits = np.nonzero((wf.digital & mask) == value)[0]
        return nth(hits)

    if t in ("pulse_wider", "pulse_narrower") and trig.width_s:
        width_samples = trig.width_s * wf.sample_rate
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        matches = []
        for i in range(1, len(bounds) - 1):
            w = bounds[i + 1] - bounds[i]
            if width_ok(int(w)) and ((t == "pulse_wider" and w > width_samples) or \
               (t == "pulse_narrower" and w < width_samples)):
                matches.append(int(bounds[i]))
        return nth(matches)

    if t == "timeout" and trig.width_s:
        width_samples = int(trig.width_s * wf.sample_rate)
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        matches = []
        for i in range(len(bounds) - 1):
            if bounds[i + 1] - bounds[i] >= width_samples:
                matches.append(int(bounds[i] + width_samples))
        return nth(matches)

    if t == "glitch":
        max_w = max(1, int((trig.width_s or 3 / wf.sample_rate) * wf.sample_rate))
        bits = wf.digital_channel(chans[0])
        edges = find_edges(bits, "any")
        bounds = np.concatenate(([0], edges, [len(bits)]))
        matches = []
        for i in range(1, len(bounds) - 1):
            if bounds[i + 1] - bounds[i] <= max_w:
                matches.append(int(bounds[i]))
        return nth(matches)

    return None
