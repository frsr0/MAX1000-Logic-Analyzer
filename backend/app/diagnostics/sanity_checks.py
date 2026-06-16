"""Capture sanity checks: stuck channels, floating inputs, clock plausibility."""
from __future__ import annotations

from typing import List

import numpy as np

from ..capture.sample_format import WaveformData, find_edges
from ..capture.session import Session


def run_sanity_checks(session: Session, wf: WaveformData) -> List[dict]:
    findings: List[dict] = []
    n = wf.num_samples
    if n == 0:
        return [{"level": "error", "check": "samples",
                 "message": "Capture contains no samples"}]
    findings.append({"level": "info", "check": "samples",
                     "message": f"{n:,} samples at {wf.sample_rate:,.0f} Hz "
                                f"({n / wf.sample_rate:.6g} s)"})
    if wf.digital is not None:
        for c in session.channels:
            if c.type != "digital":
                continue
            idx = int(c.id[1:])
            bits = wf.digital_channel(idx)
            edges = len(find_edges(bits, "any"))
            if edges == 0:
                level = int(bits[0]) if len(bits) else 0
                findings.append({
                    "level": "info", "check": "stuck_channel",
                    "channel": c.id,
                    "message": f"{c.name}: constant {'high' if level else 'low'} "
                               f"(all-{'one' if level else 'zero'})"})
            else:
                rate_frac = edges / n
                if rate_frac > 0.4:
                    findings.append({
                        "level": "warning", "check": "noisy_channel",
                        "channel": c.id,
                        "message": f"{c.name}: {edges:,} transitions "
                                   f"({rate_frac:.0%} of samples) — possibly "
                                   f"floating/noisy input"})
        # minimum pulse width near 1 sample suggests undersampling
        for c in session.channels[:16]:
            if c.type != "digital":
                continue
            bits = wf.digital_channel(int(c.id[1:]))
            e = find_edges(bits, "any")
            if len(e) > 4:
                min_w = int(np.min(np.diff(e)))
                if min_w <= 1:
                    findings.append({
                        "level": "warning", "check": "undersampling",
                        "channel": c.id,
                        "message": f"{c.name}: 1-sample pulses present — signal "
                                   f"may be faster than half the sample rate"})
                    break
    if session.sample_clk_hz and session.sample_rate > session.sample_clk_hz / 2:
        findings.append({
            "level": "error", "check": "clock",
            "message": f"Sample rate {session.sample_rate:,.0f} Hz exceeds "
                       f"sample_clk/2 ({session.sample_clk_hz / 2:,.0f} Hz)"})
    for name, arr in wf.analog.items():
        if len(arr) and (np.all(arr == arr[0])):
            findings.append({"level": "info", "check": "flat_analog",
                             "channel": name,
                             "message": f"{name}: flat at {arr[0]:.3f} V"})
    return findings
