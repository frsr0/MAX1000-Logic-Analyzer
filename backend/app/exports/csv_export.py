"""CSV exports: raw samples and decoded packets."""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from ..capture.sample_format import WaveformData
from ..capture.session import Session


def samples_csv(session: Session, wf: WaveformData,
                start: int = 0, end: Optional[int] = None,
                channels: Optional[List[str]] = None) -> str:
    end = wf.num_samples if end is None else min(end, wf.num_samples)
    dig = [c for c in session.channels
           if c.type == "digital" and (channels is None or c.id in channels)]
    ana = [c for c in session.channels
           if c.type == "analog" and (channels is None or c.id in channels)
           and c.id in wf.analog]
    der = [c for c in session.channels
           if c.type == "derived" and (channels is None or c.id in channels)
           and c.id in wf.derived_digital]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["sample", "time_s"]
               + [c.name for c in dig] + [c.name for c in der]
               + [f"{c.name} (V)" for c in ana])
    rate = wf.sample_rate
    dig_bits = [wf.digital_channel(int(c.id[1:]))[start:end] for c in dig]
    der_bits = [wf.derived_digital[c.id][start:end] for c in der]
    ana_arr = [wf.analog[c.id][start:end] for c in ana]
    for i in range(end - start):
        row = [start + i, (start + i) / rate]
        row += [int(b[i]) for b in dig_bits]
        row += [int(b[i]) for b in der_bits]
        row += [f"{a[i]:.6f}" for a in ana_arr]
        w.writerow(row)
    return out.getvalue()


def decoder_csv(events: List[dict], columns: Optional[List[str]] = None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    field_keys: List[str] = []
    for e in events:
        for k in e.get("fields", {}):
            if k not in field_keys:
                field_keys.append(k)
    if columns:
        field_keys = [k for k in columns if k in field_keys] + \
                     [k for k in field_keys if k not in columns]
    w.writerow(["start_sample", "end_sample", "start_time", "end_time",
                "type", "label", "severity"] + field_keys)
    for e in events:
        w.writerow([e["start_sample"], e["end_sample"],
                    f"{e['start_time']:.9f}", f"{e['end_time']:.9f}",
                    e["type"], e["label"], e["severity"]]
                   + [e.get("fields", {}).get(k, "") for k in field_keys])
    return out.getvalue()
