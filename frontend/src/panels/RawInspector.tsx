// Raw sample inspector: hex/bit dump of a small window around a position.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

export function RawInspector() {
  const { activeSession } = useApp();
  const [start, setStart] = useState(0);
  const [rows, setRows] = useState<{ sample: number; value: number }[]>([]);
  const count = 64;

  const load = async (s: number) => {
    if (!activeSession) return;
    const clamped = Math.max(0, Math.min(s, waveformView.numSamples - count));
    setStart(clamped);
    try {
      const r = await api.rawWindow(activeSession.id, clamped, clamped + count);
      const packed: number[] = r.digital_packed ?? [];
      setRows(packed.map((v, i) => ({ sample: clamped + i, value: v })));
    } catch {
      setRows([]);
    }
  };

  useEffect(() => {
    load(waveformView.cursorA ?? Math.floor(waveformView.start));
  }, [activeSession?.id]);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  return (
    <div className="panel-body">
      <div className="button-row">
        <button onClick={() => load(start - count)}>⟨</button>
        <input type="number" value={start} className="mono"
          onChange={(e) => load(Number(e.target.value))} />
        <button onClick={() => load(start + count)}>⟩</button>
        <button onClick={() => load(waveformView.cursorA ?? 0)}>@ cursor A</button>
        <button onClick={() => load(Math.floor(waveformView.start))}>@ view</button>
      </div>
      <div className="table-scroll" style={{ maxHeight: 320 }}>
        <table className="data-table mono">
          <thead><tr><th>sample</th><th>hex</th><th>bits 15..0</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.sample} className="clickable"
                onClick={() => waveformView.jumpTo(r.sample)}>
                <td>{r.sample}</td>
                <td>0x{r.value.toString(16).toUpperCase().padStart(4, '0')}</td>
                <td>{r.value.toString(2).padStart(16, '0').replace(/(.{4})/g, '$1 ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
