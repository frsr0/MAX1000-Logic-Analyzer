// Settings: theme, capture defaults, control lock, decoder presets.
import { useState } from 'react';
import { api, clientId } from '../api/client';
import { useApp } from '../state/appStore';

export function SettingsPage() {
  const { viewerSettings, setViewerSettings, status, refreshStatus,
          controlMode, setControlMode, toast, setCaptureSettings } = useApp();
  const [presets, setPresets] = useState<any[]>(
    JSON.parse(localStorage.getItem('msa_decoder_presets') ?? '[]'));

  const lock = status?.control;
  const iAmHolder = lock?.holder === clientId();

  return (
    <div className="page">
      <h2>Settings</h2>
      <div className="gen-grid">
        <div className="card">
          <h3>Appearance</h3>
          <label className="field">
            <span>Theme</span>
            <select value={viewerSettings.theme}
              onChange={(e) => setViewerSettings({ theme: e.target.value as any })}>
              <option value="dark">Dark (default)</option>
              <option value="light">Light</option>
            </select>
          </label>
          <h3>Capture defaults</h3>
          <label className="field">
            <span>Default sample rate (Hz)</span>
            <input type="number" value={viewerSettings.defaultSampleRate}
              onChange={(e) => setViewerSettings({ defaultSampleRate: Number(e.target.value) })} />
          </label>
          <label className="field">
            <span>Default samples</span>
            <input type="number" value={viewerSettings.defaultNumSamples}
              onChange={(e) => setViewerSettings({ defaultNumSamples: Number(e.target.value) })} />
          </label>
          <button onClick={() => {
            setCaptureSettings({
              sample_rate: viewerSettings.defaultSampleRate,
              num_samples: viewerSettings.defaultNumSamples,
            });
            toast('success', 'Applied defaults to capture settings');
          }}>Apply now</button>
        </div>

        <div className="card">
          <h3>Hardware control</h3>
          <p className="hint">Client id: <span className="mono">{clientId()}</span></p>
          <p>
            Lock: {lock?.held
              ? <>held by <strong>{lock.holder_name}</strong>{iAmHolder ? ' (you)' : ''}</>
              : 'free'}
          </p>
          <label className="field checkbox">
            <input type="checkbox" checked={controlMode}
              onChange={(e) => setControlMode(e.target.checked)} />
            <span>Control mode (uncheck for read-only viewer)</span>
          </label>
          <div className="button-row">
            <button onClick={async () => {
              const r = await api.acquireControl('me');
              toast(r.acquired ? 'success' : 'warning',
                r.acquired ? 'Control acquired' : 'Another client holds control');
              refreshStatus();
            }}>Acquire control</button>
            <button className="warning" onClick={async () => {
              await api.acquireControl('me', true);
              toast('success', 'Control taken (forced)');
              refreshStatus();
            }}>Force take</button>
            <button onClick={async () => {
              await api.releaseControl();
              refreshStatus();
            }}>Release</button>
          </div>

          <h3>Decoder presets</h3>
          {!presets.length && <div className="hint">Save presets from the Decoders panel.</div>}
          {presets.map((p, i) => (
            <div key={i} className="button-row">
              <span style={{ flex: 1 }}>{p.name}</span>
              <button className="danger slim" onClick={() => {
                const next = presets.filter((_, j) => j !== i);
                setPresets(next);
                localStorage.setItem('msa_decoder_presets', JSON.stringify(next));
              }}>✕</button>
            </div>
          ))}

          <h3>Keyboard shortcuts</h3>
          <table className="data-table kv">
            <tbody>
              <tr><th>space</th><td>start / stop capture</td></tr>
              <tr><th>f</th><td>fit capture</td></tr>
              <tr><th>t</th><td>jump to trigger</td></tr>
              <tr><th>a / b</th><td>place cursor A / B at pointer</td></tr>
              <tr><th>← / →</th><td>pan</td></tr>
              <tr><th>+ / −</th><td>zoom</td></tr>
              <tr><th>n / p</th><td>next / previous decoder event</td></tr>
              <tr><th>ctrl+s</th><td>save session (JSON download)</td></tr>
              <tr><th>shift+drag</th><td>select region</td></tr>
              <tr><th>double-click</th><td>cursor A snapped to edge (alt: B)</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
