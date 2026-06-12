import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

const RATES = [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 20e6, 50e6, 100e6];
const DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000];

export function CaptureControls() {
  const { status, captureSettings, setCaptureSettings, toast, controlMode } = useApp();
  const [scenarios, setScenarios] = useState<{ id: string; name: string }[]>([]);
  const [findings, setFindings] = useState<{ level: string; message: string }[]>([]);
  const [name, setName] = useState('');

  const connected = status?.device_connected ?? false;
  const capturing = status?.capture_state === 'capturing' || status?.capture_state === 'armed';
  const isMock = status?.device_kind === 'mock';

  useEffect(() => {
    if (isMock) api.mockScenarios().then((r) => setScenarios(r.scenarios)).catch(() => {});
    else setScenarios([]);
  }, [isMock, connected]);

  useEffect(() => {
    if (!connected) return;
    const t = setTimeout(() => {
      api.validateSettings(captureSettings)
        .then((r) => setFindings(r.findings))
        .catch(() => setFindings([]));
    }, 300);
    return () => clearTimeout(t);
  }, [captureSettings, connected]);

  const start = async () => {
    try {
      await api.startCapture(captureSettings, name);
    } catch (e: any) {
      toast('error', e.message);
    }
  };
  const stop = async () => {
    try { await api.stopCapture(); } catch (e: any) { toast('error', e.message); }
  };

  const duration = captureSettings.num_samples / captureSettings.sample_rate;

  return (
    <div className="panel-body">
      <label className="field">
        <span>Capture name</span>
        <input value={name} placeholder="(auto)" onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Sample rate</span>
        <select value={captureSettings.sample_rate}
          onChange={(e) => setCaptureSettings({ sample_rate: Number(e.target.value) })}>
          {RATES.map((r) => (
            <option key={r} value={r}>{r >= 1e6 ? `${r / 1e6} MHz` : `${r / 1e3} kHz`}</option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Samples</span>
        <select value={captureSettings.num_samples}
          onChange={(e) => setCaptureSettings({ num_samples: Number(e.target.value) })}>
          {DEPTHS.map((d) => <option key={d} value={d}>{d.toLocaleString()}</option>)}
        </select>
      </label>
      <div className="hint">duration ≈ {duration >= 1 ? `${duration.toFixed(2)} s` : `${(duration * 1e3).toFixed(2)} ms`}</div>
      <label className="field">
        <span>Mode</span>
        <select value={captureSettings.mode}
          onChange={(e) => setCaptureSettings({ mode: e.target.value as any })}>
          <option value="single">Single</option>
          <option value="continuous">Continuous (auto-rearm)</option>
          <option value="rolling">Rolling</option>
        </select>
      </label>
      {captureSettings.mode === 'single' && (
        <label className="field">
          <span>Repeat N</span>
          <input type="number" min={1} max={100} value={captureSettings.repeat_count}
            onChange={(e) => setCaptureSettings({ repeat_count: Math.max(1, Number(e.target.value)) })} />
        </label>
      )}
      <label className="field checkbox">
        <input type="checkbox" checked={captureSettings.analog_enabled}
          onChange={(e) => setCaptureSettings({ analog_enabled: e.target.checked })} />
        <span>Analog channels (mixed mode)</span>
      </label>
      {isMock && (
        <label className="field">
          <span>Mock scenario</span>
          <select value={captureSettings.mock_scenario ?? 'demo_mixed'}
            onChange={(e) => setCaptureSettings({ mock_scenario: e.target.value })}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      )}
      {findings.map((f, i) => (
        <div key={i} className={`finding ${f.level}`}>{f.message}</div>
      ))}
      <div className="button-row">
        {!capturing ? (
          <button className="primary big" disabled={!connected || !controlMode} onClick={start}>
            ▶ Capture
          </button>
        ) : (
          <button className="danger big" onClick={stop} disabled={!controlMode}>■ Stop</button>
        )}
      </div>
      {capturing && status && (
        <div className="progress">
          <div className="progress-bar" style={{
            width: `${(status.capture_progress.samples_read /
              Math.max(1, status.capture_progress.samples_total)) * 100}%`,
          }} />
          <span>{status.capture_progress.samples_read.toLocaleString()} / {status.capture_progress.samples_total.toLocaleString()} ({status.capture_progress.message})</span>
        </div>
      )}
      {!connected && <div className="hint">Connect a device on the Device page first.</div>}
      {!controlMode && <div className="finding warning">Read-only viewer mode — controls disabled.</div>}
    </div>
  );
}
