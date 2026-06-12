"""I2C decoder — structured-annotation port of the proven mid-plateau sampling
algorithm in host/app/gui_decoders.py::decode_i2c, extended with addresses,
R/W, ACK/NACK events and transaction grouping. 7-bit addressing; the address
event carries a `ten_bit` field as the extension point for 10-bit support."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .base import (ChannelRole, DecodeContext, Decoder, DecoderResult,
                   SettingField)


def _glitch_filter(signal: np.ndarray, threshold: int) -> np.ndarray:
    """Same semantics as the legacy host glitch_filter (require `threshold`
    consecutive flipped samples before accepting a transition)."""
    if threshold <= 0 or len(signal) == 0:
        return signal
    out = signal.copy()
    stable = int(signal[0])
    cnt = 0
    sig = signal
    for i in range(len(sig)):
        if sig[i] == stable:
            cnt = 0
            out[i] = stable
        else:
            cnt += 1
            if cnt >= threshold:
                stable = int(sig[i])
                cnt = 0
            out[i] = stable
    return out


class I2cDecoder(Decoder):
    id = "i2c"
    name = "I2C"
    description = "START/STOP/repeated START, address, R/W, ACK/NACK, data"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("scl", "SCL", required=True),
                ChannelRole("sda", "SDA", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("glitch_filter", "Glitch filter (samples)", "int", 0,
                         min=0, max=64, help="0 = auto from SCL period"),
            SettingField("address_format", "Address display", "enum", "7bit",
                         options=["7bit", "8bit_rw"]),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["kind", "address", "rw", "byte", "ack"])
        scl = ctx.bits("scl").copy()
        sda = ctx.bits("sda").copy()
        n = min(len(scl), len(sda))
        if n < 2:
            return result

        # Auto glitch filter from measured SCL period (~1/8 bit) — same
        # auto-sizing rule as the legacy decoder.
        rises = np.nonzero((scl[1:] == 1) & (scl[:-1] == 0))[0] + 1
        ft = int(settings.get("glitch_filter") or 0)
        if len(rises) >= 3:
            periods = np.diff(rises)
            med = int(np.median(periods))
            ft = max(ft, max(2, med // 8))
        else:
            ft = max(ft, 2)
        scl = _glitch_filter(scl, ft)
        sda = _glitch_filter(sda, ft)

        sda_d = np.diff(sda.astype(np.int8))
        scl_high = scl[1:] == 1
        starts = np.nonzero((sda_d < 0) & scl_high)[0] + 1   # SDA falls, SCL high
        stops = np.nonzero((sda_d > 0) & scl_high)[0] + 1    # SDA rises, SCL high
        scl_rise = np.nonzero(np.diff(scl.astype(np.int8)) > 0)[0] + 1

        # Build a merged, time-ordered control stream
        ctl = ([(int(i), "start") for i in starts]
               + [(int(i), "stop") for i in stops]
               + [(int(i), "clk") for i in scl_rise])
        ctl.sort()

        addr_fmt = settings.get("address_format", "7bit")
        in_txn = False
        bits: List[int] = []
        bit_start = 0
        byte_index = 0     # 0 = address byte
        txn_addr = None
        txn_rw = None
        total = max(1, len(ctl))

        def plateau_mid(i: int) -> int:
            j = i
            while j < n and scl[j] == 1:
                j += 1
            return max(0, min((i + j) // 2, n - 1))

        for k, (pos, kind) in enumerate(ctl):
            ctx.check_cancelled()
            if k % 1024 == 0:
                ctx.report(k / total)
            if kind == "start":
                ev_type = "i2c_restart" if in_txn else "i2c_start"
                label = "Sr" if in_txn else "S"
                result.events.append(ctx.event(
                    ev_type, pos, min(pos + 2, n - 1), label,
                    fields={"kind": "restart" if in_txn else "start"}))
                in_txn = True
                bits = []
                byte_index = 0
                txn_addr = None
                continue
            if kind == "stop":
                if in_txn:
                    result.events.append(ctx.event(
                        "i2c_stop", pos, min(pos + 2, n - 1), "P",
                        fields={"kind": "stop"}))
                in_txn = False
                bits = []
                continue
            if not in_txn:
                continue
            # clock rising edge: sample SDA at mid-plateau
            mid = plateau_mid(pos)
            if not bits:
                bit_start = pos
            bits.append(int(sda[mid]))
            if len(bits) == 9:
                val = 0
                for b in bits[:8]:
                    val = (val << 1) | b
                ack = bits[8] == 0
                end = mid
                if byte_index == 0:
                    addr7 = val >> 1
                    rw = "read" if (val & 1) else "write"
                    txn_addr, txn_rw = addr7, rw
                    shown = val if addr_fmt == "8bit_rw" else addr7
                    label = (f"0x{shown:02X} {'R' if val & 1 else 'W'} "
                             f"{'ACK' if ack else 'NACK'}")
                    result.events.append(ctx.event(
                        "i2c_address", bit_start, end, label,
                        fields={"kind": "address", "address": addr7,
                                "rw": rw, "ack": ack, "ten_bit": False},
                        severity="normal" if ack else "warning"))
                else:
                    label = f"0x{val:02X} {'ACK' if ack else 'NACK'}"
                    result.events.append(ctx.event(
                        "i2c_byte", bit_start, end, label,
                        fields={"kind": "data", "byte": val, "ack": ack,
                                "address": txn_addr, "rw": txn_rw},
                        severity="normal" if ack else "warning"))
                byte_index += 1
                bits = []
        ctx.report(1.0)
        return result
