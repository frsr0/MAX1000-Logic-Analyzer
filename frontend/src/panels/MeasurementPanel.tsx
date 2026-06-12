// Measurements: add typed measurements over capture/cursors/selection,
// live recompute when cursors move.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { fmtTime } from '../waveform/renderer';

function fmtValue(result: Record<string, any> | null | undefined): string {
  if (!result) return '—';
  const v = result.value;
  if (v === null || v === undefined) return result.note ?? 'n/a';
  const unit = result.unit ?? '';
  if (typeof v !== 'number') return String(v);
  if (unit === 's') return fmtTime(v);
  if (unit === 'Hz') {
    if (v >= 1e6) return `${(v / 1e6).toFixed(4)} MHz`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(4)} kHz`;
    return `${v.toFixed(2)} Hz`;
  }
  const s = Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 })
    : v.toPrecision(5);
  return `${s} ${unit}`.trim();
}

export function MeasurementPanel() {
  const { activeSession, refreshActiveSession, measurementTypes, toast } = useApp();
  const [typeId, setTypeId] = useState('dig_frequency');
  const [channel, setChannel] = useState('d0');
  const [scope, setScope] = useState<'capture' | 'cursors' | 'region'>('capture');
  const lastCursors = useRef<string>('');

  // live recalculation when cursors move
  useEffect(() => {
    if (!activeSession) return;
    const unsub = waveformView.subscribe(() => {
      const a = waveformView.cursorA;
      const b = waveformView.cursorB;
      if (a === null || b === null) return;
      const key = `${a}:${b}`;
      if (key === lastCursors.current) return;
      lastCursors.current = key;
      if (!activeSession.measurements.some((m) => m.scope === 'cursors')) return;
      debounceRecompute(activeSession.id, a, b);
    });
    return unsub;
  }, [activeSession?.id, activeSession?.measurements.length]);

  const recomputeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const debounceRecompute = (sid: string, a: number, b: number) => {
    if (recomputeTimer.current) clearTimeout(recomputeTimer.current);
    recomputeTimer.current = setTimeout(async () => {
      try {
        await api.measurementResults(sid, a, b);
        await refreshActiveSession();
      } catch { /* ignore */ }
    }, 400);
  };

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  const mt = measurementTypes.find((t) => t.id === typeId);
  const channelOptions = activeSession.channels.filter((c) =>
    mt?.category === 'analog' ? c.type === 'analog'
      : mt?.category === 'protocol' ? true
      : c.type === 'digital' || c.type === 'derived');

  const selection = waveformView.selectionStart !== null && waveformView.selectionEnd !== null
    ? [Math.floor(Math.min(waveformView.selectionStart, waveformView.selectionEnd)),
       Math.ceil(Math.max(waveformView.selectionStart, waveformView.selectionEnd))]
    : null;

  const add = async () => {
    try {
      await api.addMeasurement(activeSession.id, {
        type: typeId,
        channels: mt?.category === 'protocol' ? [] : [channel],
        scope,
        region: scope === 'region' ? selection ?? undefined : undefined,
      });
      await refreshActiveSession();
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const grouped: Record<string, typeof measurementTypes> = {};
  for (const t of measurementTypes) {
    (grouped[t.category] = grouped[t.category] ?? []).push(t);
  }

  return (
    <div className="panel-body">
      <table className="data-table">
        <thead>
          <tr><th>measurement</th><th>ch</th><th>scope</th><th>value</th><th></th></tr>
        </thead>
        <tbody>
          {activeSession.measurements.map((m) => (
            <tr key={m.id}>
              <td>{measurementTypes.find((t) => t.id === m.type)?.name ?? m.type}</td>
              <td>{m.channels.join(',') || '—'}</td>
              <td>{m.scope}</td>
              <td className="mono">{m.error ? <span className="err">{m.error}</span> : fmtValue(m.result)}</td>
              <td><button onClick={async () => {
                await api.deleteMeasurement(activeSession.id, m.id);
                await refreshActiveSession();
              }}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={async () => {
        const a = waveformView.cursorA;
        const b = waveformView.cursorB;
        await api.measurementResults(activeSession.id,
          a ?? undefined, b ?? undefined);
        await refreshActiveSession();
      }}>↻ Recompute all</button>

      <h4>Add measurement</h4>
      <label className="field">
        <span>Type</span>
        <select value={typeId} onChange={(e) => setTypeId(e.target.value)}>
          {Object.entries(grouped).map(([cat, types]) => (
            <optgroup key={cat} label={cat}>
              {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </optgroup>
          ))}
        </select>
      </label>
      {mt?.category !== 'protocol' && (
        <label className="field">
          <span>Channel</span>
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            {channelOptions.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.name})</option>)}
          </select>
        </label>
      )}
      <label className="field">
        <span>Scope</span>
        <select value={scope} onChange={(e) => setScope(e.target.value as any)}>
          <option value="capture">Whole capture</option>
          <option value="cursors">Between cursors A/B</option>
          <option value="region" disabled={!selection}>Selected region{selection ? '' : ' (none)'}</option>
        </select>
      </label>
      <button className="primary" onClick={add}>Add</button>
    </div>
  );
}
