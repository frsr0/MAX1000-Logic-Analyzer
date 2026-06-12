// Markers/bookmarks: list, add at cursor/hover, notes, jump navigation.
import { useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { fmtTime } from '../waveform/renderer';

export function MarkerPanel() {
  const { activeSession, toast } = useApp();
  const [label, setLabel] = useState('');
  const [, force] = useState(0);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;

  const refresh = async () => {
    await waveformView.refreshMarkers();
    force((n) => n + 1);
  };

  const addAt = async (sample: number | null) => {
    if (sample === null) { toast('warning', 'Place cursor A or hover the waveform first'); return; }
    try {
      await api.addMarker(activeSession.id, {
        sample: Math.round(sample), label: label || `M${waveformView.markers.length + 1}`,
      });
      setLabel('');
      await refresh();
    } catch (e: any) { toast('error', e.message); }
  };

  const bookmarks = waveformView.markers
    .filter((m) => m.kind !== 'cursor_a' && m.kind !== 'cursor_b')
    .sort((a, b) => a.sample - b.sample);

  const jumpRelative = (dir: 1 | -1) => {
    const centre = waveformView.start + waveformView.span() / 2;
    const next = dir > 0
      ? bookmarks.find((m) => m.sample > centre + 1)
      : [...bookmarks].reverse().find((m) => m.sample < centre - 1);
    if (next) waveformView.jumpTo(next.sample);
  };

  return (
    <div className="panel-body">
      <div className="button-row">
        <input value={label} placeholder="marker label"
          onChange={(e) => setLabel(e.target.value)} style={{ flex: 1 }} />
        <button onClick={() => addAt(waveformView.cursorA)}>@ cursor A</button>
        <button onClick={() => addAt(waveformView.hoverSample)}>@ hover</button>
      </div>
      <div className="button-row">
        <button onClick={() => jumpRelative(-1)}>⟨ prev marker</button>
        <button onClick={() => jumpRelative(1)}>next marker ⟩</button>
      </div>
      <table className="data-table">
        <thead><tr><th>label</th><th>sample</th><th>time</th><th>note</th><th></th></tr></thead>
        <tbody>
          {bookmarks.map((m) => (
            <tr key={m.id} className="clickable" onClick={() => waveformView.jumpTo(m.sample)}>
              <td>{m.label || m.kind}</td>
              <td className="mono">{m.sample}</td>
              <td className="mono">{fmtTime(m.sample / waveformView.sampleRate)}</td>
              <td>
                <input value={m.note} placeholder="note…"
                  onClick={(e) => e.stopPropagation()}
                  onChange={async (e) => {
                    await api.patchMarker(activeSession.id, m.id, { note: e.target.value });
                    m.note = e.target.value;
                    force((n) => n + 1);
                  }} />
              </td>
              <td><button onClick={async (e) => {
                e.stopPropagation();
                await api.deleteMarker(activeSession.id, m.id);
                await refresh();
              }}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {!bookmarks.length && <div className="hint">No markers yet. Double-click the waveform to place cursor A (alt = B); add named markers above.</div>}
    </div>
  );
}
