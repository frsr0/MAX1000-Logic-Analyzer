// Session library: open/rename/duplicate/delete/tag/compare/import.
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

export function SessionsPage() {
  const { sessions, refreshSessions, openSession, setPage, toast, activeSession } = useApp();
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [compareResult, setCompareResult] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { refreshSessions(); }, []);

  const open = async (id: string) => {
    try {
      await openSession(id);
      setPage('capture');
    } catch (e: any) { toast('error', e.message); }
  };

  const rename = async (id: string, name: string) => {
    await api.patchSession(id, { name });
    refreshSessions();
  };

  const setTags = async (id: string, raw: string) => {
    await api.patchSession(id, { tags: raw.split(',').map((t) => t.trim()).filter(Boolean) });
    refreshSessions();
  };

  const compare = async (a: string, b: string) => {
    try {
      setCompareResult(await api.compareSessions(a, b));
    } catch (e: any) { toast('error', e.message); }
    setCompareWith(null);
  };

  const importJson = async (file: File) => {
    try {
      const text = await file.text();
      const s = await api.importSession(text);
      toast('success', `Imported ${s.name}`);
      refreshSessions();
    } catch (e: any) { toast('error', `Import failed: ${e.message}`); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <h2>Sessions</h2>
        <button onClick={() => fileRef.current?.click()}>Import JSON session</button>
        <input ref={fileRef} type="file" accept=".json" hidden
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
              <td className="mono">{s.sample_rate >= 1e6 ? `${s.sample_rate / 1e6}M` : `${s.sample_rate / 1e3}k`}</td>
              <td className="mono">{s.duration_s >= 1 ? `${s.duration_s.toFixed(2)}s` : `${(s.duration_s * 1e3).toFixed(1)}ms`}</td>
              <td>{s.decoder_count}</td>
              <td>
                <input className="ch-name" defaultValue={s.tags.join(', ')} placeholder="tags…"
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
                  <button className="slim" onClick={() => setCompareWith(s.id)}>Cmp…</button>
                ) : compareWith === s.id ? (
                  <button className="slim" onClick={() => setCompareWith(null)}>✕</button>
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
      {compareWith && <div className="hint">Pick the second session to compare with…</div>}
      {compareResult && (
        <div className="compare-result">
          <h3>Compare: {compareResult.a.name} vs {compareResult.b.name}
            <button className="slim" onClick={() => setCompareResult(null)} style={{ marginLeft: 12 }}>✕</button>
          </h3>
          <p>
            Digital data identical: <strong>{compareResult.identical_digital ? 'yes' : 'no'}</strong>
            {' · '}sample count Δ {compareResult.sample_count_diff}
          </p>
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
              <h4>Channel differences (edges / duty)</h4>
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
