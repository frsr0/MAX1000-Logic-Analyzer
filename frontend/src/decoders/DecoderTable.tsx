// Packet table: paginated, searchable, severity filter; row click jumps the
// waveform to the packet (and vice versa via annotation click).
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { DecoderEvent } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { fmtTime } from '../waveform/renderer';

const PAGE = 100;

export function DecoderTable() {
  const { activeSession } = useApp();
  const [decId, setDecId] = useState<string>('');
  const [events, setEvents] = useState<DecoderEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState('');
  const [severity, setSeverity] = useState('');
  const [selected, setSelected] = useState<string>('');

  const decoders = activeSession?.decoders.filter((d) => d.status === 'done') ?? [];
  const effective = decId || decoders[0]?.id || '';

  useEffect(() => {
    if (!activeSession || !effective) { setEvents([]); setTotal(0); return; }
    let stale = false;
    api.decoderTable(activeSession.id, effective, offset, PAGE, search, severity)
      .then((r) => { if (!stale) { setEvents(r.events); setTotal(r.total); } })
      .catch(() => { if (!stale) setEvents([]); });
    return () => { stale = true; };
  }, [activeSession?.id, effective, offset, search, severity,
      activeSession?.decoders.map((d) => `${d.id}:${d.status}:${d.event_count}`).join(',')]);

  if (!activeSession || !decoders.length) {
    return <div className="decoder-table empty hint">Run a decoder to see the packet table.</div>;
  }

  const fieldKeys: string[] = [];
  for (const e of events.slice(0, 30)) {
    for (const k of Object.keys(e.fields)) {
      if (!fieldKeys.includes(k)) fieldKeys.push(k);
    }
  }

  return (
    <div className="decoder-table">
      <div className="table-toolbar">
        <select value={effective} onChange={(e) => { setDecId(e.target.value); setOffset(0); }}>
          {decoders.map((d) => (
            <option key={d.id} value={d.id}>{d.name || d.decoder_id} ({d.event_count})</option>
          ))}
        </select>
        <input placeholder="search packets…" value={search}
          onChange={(e) => { setSearch(e.target.value); setOffset(0); }} />
        <select value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}>
          <option value="">all</option>
          <option value="normal">normal</option>
          <option value="warning">warnings</option>
          <option value="error">errors</option>
        </select>
        <span className="hint">{total.toLocaleString()} packets</span>
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>⟨</button>
        <span className="hint">{offset + 1}–{Math.min(offset + PAGE, total)}</span>
        <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>⟩</button>
      </div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th><th>time</th><th>type</th><th>label</th>
              {fieldKeys.map((k) => <th key={k}>{k}</th>)}
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={e.id}
                className={`clickable sev-${e.severity} ${selected === e.id ? 'selected' : ''}`}
                onClick={() => {
                  setSelected(e.id);
                  waveformView.jumpTo((e.start_sample + e.end_sample) / 2);
                }}>
                <td className="mono">{offset + i + 1}</td>
                <td className="mono">{fmtTime(e.start_time)}</td>
                <td>{e.type}</td>
                <td className="mono">{e.label}</td>
                {fieldKeys.map((k) => (
                  <td key={k} className="mono">{formatField(e.fields[k])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatField(v: any): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? '✓' : '✗';
  if (typeof v === 'number' && Number.isInteger(v) && v > 9) {
    return `${v} (0x${v.toString(16).toUpperCase()})`;
  }
  if (typeof v === 'number' && !Number.isInteger(v)) return v.toPrecision(5);
  return String(v);
}
