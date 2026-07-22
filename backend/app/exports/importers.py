"""Small, dependency-free CSV and VCD session importers."""
from __future__ import annotations

import csv
import io
import re
from typing import Tuple

import numpy as np

from ..capture.sample_format import WaveformData
from ..capture.session import Session, default_digital_channels, ChannelInfo


def csv_session(text: str, sample_rate: float) -> Tuple[Session, WaveformData]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("CSV contains no sample rows")
    headers = list(rows[0])
    signal_headers = [h for h in headers if h not in ("sample", "time_s")]
    digital = [h for h in signal_headers if not h.endswith(" (V)")]
    analog = [h for h in signal_headers if h.endswith(" (V)")]
    packed = np.zeros(len(rows), dtype=np.uint16)
    for index, name in enumerate(digital[:16]):
        packed |= np.array([int(float(row.get(name, 0) or 0)) for row in rows],
                           dtype=np.uint16) << index
    analog_data = {name[:-4]: np.array(
        [float(row.get(name, 0) or 0) for row in rows], dtype=np.float32)
        for name in analog}
    session = Session(name="CSV import", sample_rate=sample_rate,
                      num_samples=len(rows), device={"driver": "import",
                      "device_name": "CSV import", "mock": True})
    session.channels = default_digital_channels(16)
    for index, name in enumerate(digital[:16]):
        session.channels[index].name = name
    for index, name in enumerate(analog_data):
        session.channels.append(ChannelInfo(id=name, name=name, type="analog"))
    return session, WaveformData(sample_rate=sample_rate, digital=packed,
                                 analog=analog_data)


def vcd_session(text: str) -> Tuple[Session, WaveformData]:
    scale_match = re.search(r"\$timescale\s+(\d+)\s+(\w+)\s+\$end", text)
    if not scale_match:
        raise ValueError("VCD has no timescale")
    unit = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9,
            "ps": 1e-12, "fs": 1e-15}.get(scale_match.group(2))
    if unit is None:
        raise ValueError("Unsupported VCD timescale")
    tick_s = int(scale_match.group(1)) * unit
    variables = re.findall(r"\$var\s+wire\s+1\s+(\S+)\s+(\S+)\s+\$end", text)
    if not variables:
        raise ValueError("VCD has no one-bit wire variables")
    codes = {code: name for code, name in variables}
    changes = {name: [(0, 0)] for name in codes.values()}
    current_time = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            current_time = int(line[1:])
        elif len(line) >= 2 and line[0] in "01" and line[1:] in codes:
            changes[codes[line[1:]]].append((current_time, int(line[0])))
    max_tick = max(t for vals in changes.values() for t, _ in vals)
    sample_rate = 1.0 / tick_s
    n = max_tick + 1
    packed = np.zeros(n, dtype=np.uint16)
    session = Session(name="VCD import", sample_rate=sample_rate,
                      num_samples=n, device={"driver": "import",
                      "device_name": "VCD import", "mock": True})
    session.channels = default_digital_channels(min(16, len(codes)))
    for index, name in enumerate(codes.values()):
        if index >= 16:
            break
        session.channels[index].name = name
        arr = np.zeros(n, dtype=np.uint16)
        level = 0
        for t, value in changes[name]:
            if t < n:
                arr[t:] = value
                level = value
        packed |= arr << index
    return session, WaveformData(sample_rate=sample_rate, digital=packed)
