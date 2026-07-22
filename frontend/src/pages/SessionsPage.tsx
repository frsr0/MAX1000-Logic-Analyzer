// Session library: open, rename, duplicate, delete, tag, compare, import.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

function formatRate(rate: number) {
  return rate >= 1e6 ? `${(rate / 1e6).toFixed(1)} MHz` : `${(rate / 1e3).toFixed(1)} kHz`;
}

export function SessionsPage() {
  const { sessions, refreshSessions, openSession, setPage, toast, activeSession } = useApp();
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [compareResult, setCompareResult] = useState<any>(null);
  const [alignmentOffset, setAlignmentOffset] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    refreshSessions();
  }, []);

  const open = async (id: string) => {
    try {
      await openSession(id);
      setPage('capture');
    } catch (e: any) {
      toast('error', e.message);
    }
  };

  const rename = async (id: string, name: string) => {
    await api.patchSession(id, { name });
    refreshSessions();
  };

  const setTags = async (id: string, raw: string) => {
    await api.patchSession(id, { tags: raw.split(',').map((t) => t.trim()).filter(Boolean) });
    refreshSessions();
  };

  const compare = async (a: string, b: string, offset = alignmentOffset) => {
    try {
      setCompareResult(await api.compareSessions(a, b, offset === '' ? undefined : Number(offset)));
    } catch (e: any) {
      toast('error', e.message);
    }
    setCompareWith(null);
  };

  const importJson = async (file: File) => {
    try {
      const text = await file.text();
      const lower = file.name.toLowerCase();
      const s = lower.endsWith('.csv')
        ? await api.importWaveform(text, 'csv')
        : lower.endsWith('.vcd')
          ? await api.importWaveform(text, 'vcd')
          : await api.importSession(text);
      toast('success', `Imported ${s.name}`);
      refreshSessions();
    } catch (e: any) {
      toast('error', `Import failed: ${e.message}`);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Sessions</h2>
          <p className="hint">Saved captures stay tied to the hardware metadata that produced them, including analog channels when present.</p>
        </div>
        <button onClick={() => fileRef.current?.click()}>Import JSON / CSV / VCD</button>
        <input ref={fileRef} type="file" accept=".json,.csv,.vcd" hidden
          onChange={(e) => e.target.files?.[0] && importJson(e.target.files[0])} />
      </div>

      <table className="data-table sessions-table">
        <thead>
          <tr>
            <th>name</th><th>created</th><th>samples</th><th>rate</th>
            <th>duration</th><th>decoders</th><th>tags</th><th>device</th><th>actions</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id} className={s.id === activeSession?.id ? 'selected' : ''}>
              <td>
                <input className="ch-name" defaultValue={s.name}
                  onBlur={(e) => e.target.value !== s.name && rename(s.id, e.target.value)} />
              </td>
              <td>{new Date(s.created_at * 1000).toLocaleString()}</td>
              <td className="mono">{s.num_samples.toLocaleString()}</td>
              <td className="mono">{formatRate(s.sample_rate)}</td>
              <td className="mono">{s.duration_s >= 1 ? `${s.duration_s.toFixed(2)} s` : `${(s.duration_s * 1e3).toFixed(1)} ms`}</td>
              <td>{s.decoder_count}</td>
              <td>
                <input className="ch-name" defaultValue={s.tags.join(', ')} placeholder="tags..."
                  onBlur={(e) => setTags(s.id, e.target.value)} />
              </td>
              <td>{s.device}{s.mock ? ' (mock)' : ''}{s.has_analog ? ' ∿' : ''}</td>
              <td className="button-row">
                <button className="primary slim" onClick={() => open(s.id)}>Open</button>
                <button className="slim" onClick={async () => {
                  await api.duplicateSession(s.id);
                  refreshSessions();
                }}>Dup</button>
                {compareWith === null ? (
                  <button className="slim" onClick={() => setCompareWith(s.id)}>Cmp...</button>
                ) : compareWith === s.id ? (
                  <button className="slim" onClick={() => setCompareWith(null)}>x</button>
                ) : (
                  <button className="slim warning" onClick={() => compare(compareWith, s.id)}>Cmp!</button>
                )}
                <button className="danger slim" onClick={async () => {
                  if (!confirm(`Delete session "${s.name}"?`)) return;
                  await api.deleteSession(s.id);
                  refreshSessions();
                }}>Del</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {compareWith && <div className="hint">Pick the second session to compare with...</div>}
      {compareResult && (
        <div className="compare-result">
          <h3>
            Compare: {compareResult.a.name} vs {compareResult.b.name}
            <button className="slim" onClick={() => setCompareResult(null)} style={{ marginLeft: 12 }}>x</button>
          </h3>
          <p>
            Digital data identical: <strong>{compareResult.identical_digital ? 'yes' : 'no'}</strong>
            {' · '}sample count delta {compareResult.sample_count_diff}
          </p>
          <div className="button-row">
            <label className="field compact">
              <span>Alignment offset</span>
              <input
                type="number"
                value={alignmentOffset}
                onChange={(e) => setAlignmentOffset(e.target.value)}
                placeholder="auto"
              />
            </label>
            <button className="slim" onClick={() => compare(compareResult.a.id, compareResult.b.id)}>
              Recompare
            </button>
          </div>
          <p className="hint">
            Applied alignment: {compareResult.alignment_offset} samples ·{' '}
            {compareResult.first_divergence
              ? `first divergence A ${compareResult.first_divergence.a} / B ${compareResult.first_divergence.b}`
              : 'no digital divergence in the overlapping region'}
          </p>
          {compareResult.timing_deltas?.length > 0 && <>
            <h4>Timing deltas</h4>
            <table className="data-table"><thead><tr><th>channel</th><th>first edge Δ</th><th>mean period Δ</th><th>median period Δ</th></tr></thead>
              <tbody>{compareResult.timing_deltas.map((d: any) => <tr key={d.channel}>
                <td>{d.channel}</td><td className="mono">{d.first_edge_delta_samples ?? '—'}</td>
                <td className="mono">{Number(d.mean_period_delta_samples).toFixed(2)}</td>
                <td className="mono">{Number(d.median_period_delta_samples).toFixed(2)}</td>
              </tr>)}</tbody></table>
          </>}
          {Object.keys(compareResult.settings_diff).length > 0 && (
            <>
              <h4>Settings differences</h4>
              <table className="data-table">
                <thead><tr><th>setting</th><th>A</th><th>B</th></tr></thead>
                <tbody>
                  {Object.entries(compareResult.settings_diff).map(([k, v]: [string, any]) => (
                    <tr key={k}><td>{k}</td>
                      <td className="mono">{JSON.stringify(v.a)}</td>
                      <td className="mono">{JSON.stringify(v.b)}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {compareResult.channel_diffs.length > 0 && (
            <>
              <h4>Channel differences</h4>
              <table className="data-table">
                <thead><tr><th>channel</th><th>A</th><th>B</th></tr></thead>
                <tbody>
                  {compareResult.channel_diffs.map((d: any) => (
                    <tr key={d.channel}>
                      <td>{d.channel}</td>
                      <td className="mono">{d.a ? `${d.a.edges} / ${(d.a.duty * 100).toFixed(1)}%` : '—'}</td>
                      <td className="mono">{d.b ? `${d.b.edges} / ${(d.b.duty * 100).toFixed(1)}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
