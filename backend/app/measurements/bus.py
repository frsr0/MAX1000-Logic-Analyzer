"""Protocol/bus measurements — operate on decoder events."""
from __future__ import annotations

import numpy as np

from .base import MeasurementContext, MeasurementType, register


def _events_in_region(ctx: MeasurementContext):
    t0 = ctx.start / ctx.sample_rate
    t1 = ctx.end / ctx.sample_rate
    return [e for e in ctx.decoder_events
            if e["start_time"] >= t0 and e["end_time"] <= t1]


def m_packet_count(ctx, channels):
    ev = _events_in_region(ctx)
    return {"value": len(ev)}


def m_error_count(ctx, channels):
    ev = _events_in_region(ctx)
    return {"value": sum(1 for e in ev if e["severity"] == "error"),
            "warnings": sum(1 for e in ev if e["severity"] == "warning")}


def m_nack_count(ctx, channels):
    ev = _events_in_region(ctx)
    return {"value": sum(1 for e in ev
                         if e["fields"].get("ack") is False)}


def m_uart_frame_errors(ctx, channels):
    ev = _events_in_region(ctx)
    return {"value": sum(1 for e in ev if e["fields"].get("framing_error"))}


def m_uart_parity_errors(ctx, channels):
    ev = _events_in_region(ctx)
    return {"value": sum(1 for e in ev if e["fields"].get("parity_error"))}


def m_byte_rate(ctx, channels):
    ev = [e for e in _events_in_region(ctx)
          if "byte" in e["fields"] or "mosi" in e["fields"]]
    d = ctx.duration_s
    return {"value": len(ev) / d if d > 0 else None, "bytes": len(ev)}


def m_bus_utilisation(ctx, channels):
    ev = _events_in_region(ctx)
    if not ev:
        return {"value": 0.0}
    busy = sum(e["end_time"] - e["start_time"] for e in ev)
    d = ctx.duration_s
    return {"value": min(100.0, busy / d * 100.0) if d > 0 else None}


def m_inter_packet(ctx, channels):
    ev = sorted(_events_in_region(ctx), key=lambda e: e["start_time"])
    gaps = [ev[i + 1]["start_time"] - ev[i]["end_time"]
            for i in range(len(ev) - 1)]
    gaps = [g for g in gaps if g >= 0]
    if not gaps:
        return {"value": None}
    return {"value": float(np.mean(gaps)), "min": float(np.min(gaps)),
            "max": float(np.max(gaps))}


for mt in [
    MeasurementType("proto_packet_count", "Packet count", "protocol", "",
                    needs_decoder=True, fn=m_packet_count),
    MeasurementType("proto_error_count", "Error count", "protocol", "",
                    needs_decoder=True, fn=m_error_count),
    MeasurementType("proto_nack_count", "I2C NACK count", "protocol", "",
                    needs_decoder=True, fn=m_nack_count),
    MeasurementType("proto_uart_framing", "UART framing errors", "protocol", "",
                    needs_decoder=True, fn=m_uart_frame_errors),
    MeasurementType("proto_uart_parity", "UART parity errors", "protocol", "",
                    needs_decoder=True, fn=m_uart_parity_errors),
    MeasurementType("proto_byte_rate", "Average byte rate", "protocol", "B/s",
                    needs_decoder=True, fn=m_byte_rate),
    MeasurementType("proto_utilisation", "Bus utilisation", "protocol", "%",
                    needs_decoder=True, fn=m_bus_utilisation),
    MeasurementType("proto_inter_packet", "Time between packets", "protocol", "s",
                    needs_decoder=True, fn=m_inter_packet),
]:
    register(mt)
