"""ARM SWD decoder (SWCLK/SWDIO), ported from the proven bit-level parser in
host/app/gui_decoders.decode_swd onto the new Decoder plugin framework."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import ChannelRole, DecodeContext, Decoder, DecoderResult, SettingField

_ACK_LABEL = {1: "OK", 2: "WAIT", 4: "FAULT", 7: "no target"}


def _sample_bits(swclk: List[int], swdio: List[int]) -> Tuple[List[int], List[int]]:
    """Sample SWDIO at the middle of every SWCLK-high plateau.

    Returns (bits, positions); positions are the sample index of the SWCLK
    rising edge that starts each plateau (mid-plateau sampling avoids the
    settling glitch right at the edge, same rationale as decode_spi/i2c).
    """
    n = min(len(swclk), len(swdio))
    bits: List[int] = []
    pos: List[int] = []
    i = 1
    while i < n:
        if swclk[i - 1] == 0 and swclk[i] == 1:
            j = i
            while j < n and swclk[j] == 1:
                j += 1
            mid = min((i + j) // 2, n - 1)
            bits.append(1 if swdio[mid] else 0)
            pos.append(i)
            i = j
        else:
            i += 1
    return bits, pos


def _glitch_filter(signal: List[int], threshold: int) -> List[int]:
    if not signal:
        return []
    out = list(signal)
    stable = signal[0]
    cnt = 0
    for i, v in enumerate(signal):
        if v == stable:
            cnt = 0
            out[i] = stable
        else:
            cnt += 1
            if cnt >= threshold:
                stable = v
                cnt = 0
            out[i] = stable
    return out


class SwdDecoder(Decoder):
    id = "swd"
    name = "SWD"
    description = "ARM Serial Wire Debug: line reset, JTAG-to-SWD, request/ack/data transfers"

    def channel_roles(self) -> List[ChannelRole]:
        return [ChannelRole("swclk", "SWCLK", required=True),
                ChannelRole("swdio", "SWDIO", required=True)]

    def settings_schema(self) -> List[SettingField]:
        return [
            SettingField("glitch_filter", "Glitch filter (samples)", "int",
                         0, min=0, max=64,
                         help="Debounce SWCLK/SWDIO before decoding; 0 disables"),
            SettingField(
                "expected_no_target", "Expected no target", "bool", False,
                help="Treat released-line ACK/data as an open-loop fixture result"),
        ]

    def decode(self, ctx: DecodeContext, settings: Dict[str, Any]) -> DecoderResult:
        result = DecoderResult(columns=["apndp", "rnw", "addr", "ack", "data"])
        swclk = ctx.bits("swclk").tolist()
        swdio = ctx.bits("swdio").tolist()
        threshold = int(settings.get("glitch_filter", 0) or 0)
        expected_no_target = bool(settings.get("expected_no_target", False))
        if threshold > 0:
            swclk = _glitch_filter(swclk, threshold)
            swdio = _glitch_filter(swdio, threshold)

        bits, pos = _sample_bits(swclk, swdio)
        n = len(bits)
        i = 0
        total = max(1, n)
        while i < n:
            ctx.check_cancelled()
            if i % 512 == 0:
                ctx.report(i / total)

            if bits[i] == 1:
                run = 0
                while i + run < n and bits[i + run] == 1:
                    run += 1
                if run >= 50:
                    result.events.append(ctx.event(
                        "swd_linereset", pos[i], pos[min(i + run, n - 1)],
                        "Line reset"))
                    i += run
                    if i + 16 <= n:
                        val = 0
                        for k in range(16):
                            val |= bits[i + k] << k
                        if val == 0xE79E:
                            result.events.append(ctx.event(
                                "swd_jtag2swd", pos[i], pos[i + 15],
                                "JTAG-to-SWD select"))
                            i += 16
                    continue
                # else: short run of 1s — fall through, try a request header
            elif bits[i] == 0:
                i += 1
                continue

            # Start bit: try to parse a request header (start=1, park=1,
            # stop=0 land at offsets 0/6/7; parity at 5 over apndp/rnw/a2/a3).
            if i + 12 > n:
                break
            hdr = bits[i:i + 8]
            apndp, rnw, a2, a3 = hdr[1], hdr[2], hdr[3], hdr[4]
            req_ok = (hdr[6] == 0 and hdr[7] == 1 and
                      ((apndp ^ rnw ^ a2 ^ a3) & 1) == hdr[5])
            if not req_ok:
                i += 1
                continue
            ack = bits[i + 9] | (bits[i + 10] << 1) | (bits[i + 11] << 2)
            j = i + 12
            if not rnw:
                j += 1  # turnaround back to host before a write's data phase
            data: Optional[int] = None
            parity_ok: Optional[bool] = None
            if j + 33 <= n:
                data = 0
                for k in range(32):
                    data |= bits[j + k] << k
                parity_ok = (bin(data).count("1") & 1) == bits[j + 32]
                j += 33
                if rnw:
                    j += 1  # turnaround back to host after a read's data phase
            addr = (a3 << 3) | (a2 << 2)
            ack_label = _ACK_LABEL.get(ack, f"0x{ack:x}")
            label = (f"{'AP' if apndp else 'DP'} {'RD' if rnw else 'WR'} "
                     f"@0x{addr:X} {ack_label}")
            no_target = (ack == 7 and data == 0xFFFFFFFF
                         and parity_ok is False)
            if data is not None:
                label += f" data=0x{data:08X}"
                if parity_ok is False:
                    label += (" (expected no target; parity not driven)"
                              if expected_no_target and no_target
                              else " (parity error)")
            end = pos[min(j, n - 1)] if j < n else pos[i] + 1
            result.events.append(ctx.event(
                "swd_xfer", pos[i], end, label,
                fields={"apndp": apndp, "rnw": rnw, "addr": addr, "ack": ack,
                        "data": data, "parity_ok": parity_ok},
                severity=("normal" if ack == 1
                          or (expected_no_target and no_target)
                          else "warning")))
            i = j
        ctx.report(1.0)
        return result
