// Diagnostics: live logs, sanity checks, mock captures, debug bundle, QR.
import { useEffect, useRef, useState } from 'react';
import { api, downloadDebugBundle } from '../api/client';
import { useApp } from '../state/appStore';

export function DiagnosticsPage() {
  const { logs, activeSession, toast, controlMode } = useApp();
  const [sanity, setSanity] = useState<any[]>([]);
  const [diag, setDiag] = useState<any>(null);
  const [levelFilter, setLevelFilter] = useState('');
  const logEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.diagnostics().then(setDiag).catch(() => {});
  }, []);

  useEffect(() => {
    logEnd.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs.length]);

  const runSanity = async () => {
    if (!activeSession) { toast('warning', 'Open a session first'); return; }
    try {
      const r = await api.sanity(activeSession.id);
      setSanity(r.findings);
    } catch (e: any) { toast('error', e.message); }
  };

  const mockCap = async (scenario: string, analog = false) => {
    try {
      await api.mockCapture(scenario, 1_000_000, 100_000, analog);
      toast('info', `Mock capture started: ${scenario}`);
    } catch (e: any) { toast('error', e.message); }
  };

  const shown = levelFilter ? logs.filter((l) => l.level === levelFilter) : logs;

  return (
    <div className="page">
      <h2>Diagnostics</h2>
      <div className="gen-grid">
        <div className="card">
          <h3>Tools</h3>
          <div className="button-row wrap">
            <button onClick={() => downloadDebugBundle().then(
              () => toast('success', 'Debug bundle downloaded'),
              (e) => toast('error', e.message))}>
              ⬇ Debug bundle (ZIP)
            </button>
            <button onClick={runSanity}>Run capture sanity checks</button>
          </div>
          {sanity.length > 0 && (
            <ul className="sanity-list">
              {sanity.map((f, i) => (
                <li key={i} className={`finding ${f.level}`}>
                  [{f.check}] {f.message}
                </li>
              ))}
            </ul>
          )}
          <h3>Mock captures (never touches hardware)</h3>
          <div className="button-row wrap">
            <button disabled={!controlMode} onClick={() => mockCap('demo_mixed')}>Demo mixed</button>
            <button disabled={!controlMode} onClick={() => mockCap('uart')}>UART</button>
            <button disabled={!controlMode} onClick={() => mockCap('i2c')}>I2C</button>
            <button disabled={!controlMode} onClick={() => mockCap('spi')}>SPI</button>
            <button disabled={!controlMode} onClick={() => mockCap('glitchy')}>Glitchy</button>
            <button disabled={!controlMode} onClick={() => mockCap('analog_demo', true)}>Analog demo</button>
            <button disabled={!controlMode} onClick={() => mockCap('long_stress')}>Stress test</button>
          </div>
          {diag && (
            <>
              <h3>LAN access</h3>
              <p className="hint">Open from another device on your network:</p>
              {diag.lan_urls?.map((u: string) => (
                <div key={u} className="mono">{u}</div>
              ))}
              <p><a href="/connect" target="_blank" rel="noreferrer">QR code page →</a></p>
            </>
          )}
        </div>
        <div className="card log-card">
          <div className="page-head">
            <h3>Live log</h3>
            <select value={levelFilter} onChange={(e) => setLevelFilter(e.target.value)}>
              <option value="">all levels</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          </div>
          <div className="log-view">
            {shown.map((l, i) => (
              <div key={i} className={`log-line log-${l.level}`}>
                <span className="log-time">{new Date(l.ts * 1000).toLocaleTimeString()}</span>
                <span className="log-level">{l.level}</span>
                <span>{l.message}</span>
              </div>
            ))}
            <div ref={logEnd} />
          </div>
        </div>
      </div>
    </div>
  );
}
