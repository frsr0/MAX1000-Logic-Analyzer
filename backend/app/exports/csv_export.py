"""CSV exports: raw samples and decoded packets."""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from ..capture.sample_format import WaveformData
from ..capture.session import Session


def samples_csv_iter(session: Session, wf: WaveformData,
                     start: int = 0, end: Optional[int] = None,
                     channels: Optional[List[str]] = None, chunk_rows: int = 4096):
    """Yield CSV rows as strings, chunked to limit per-yield size.

    Yields header row first, then data rows in ``chunk_rows``-sized blocks.
    Keeps only one chunk in memory at a time instead of building the entire
    CSV string before returning it.
    """
    end = wf.num_samples if end is None else min(end, wf.num_samples)
    dig = [c for c in session.channels
           if c.type == "digital" and (channels is None or c.id in channels)]
    ana = [c for c in session.channels
           if c.type == "analog" and (channels is None or c.id in channels)
           and c.id in wf.analog]
    der = [c for c in session.channels
           if c.type == "derived" and (channels is None or c.id in channels)
           and c.id in wf.derived_digital]

    import io, csv
    rate = wf.sample_rate
    dig_bits = [wf.digital_channel(int(c.id[1:]))[start:end] for c in dig]
    der_bits = [wf.derived_digital[c.id][start:end] for c in der]
    ana_arr = [wf.analog[c.id][start:end] for c in ana]

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["sample", "time_s"]
               + [c.name for c in dig] + [c.name for c in der]
               + [f"{c.name} (V)" for c in ana])
    yield out.getvalue()

    buf = io.StringIO()
    w = csv.writer(buf)
    for idx, i in enumerate(range(start, end)):
        row = [i, i / rate]
        row += [int(b[idx]) for b in dig_bits]
        row += [int(b[idx]) for b in der_bits]
        row += [f"{a[idx]:.6f}" for a in ana_arr]
        w.writerow(row)
        if (idx + 1) % chunk_rows == 0:
            yield buf.getvalue()
            buf = io.StringIO()
            w = csv.writer(buf)
    leftover = buf.getvalue()
    if leftover:
        yield leftover


def samples_csv(session: Session, wf: WaveformData,
                start: int = 0, end: Optional[int] = None,
                channels: Optional[List[str]] = None) -> str:
    return "".join(samples_csv_iter(session, wf, start, end, channels))


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
