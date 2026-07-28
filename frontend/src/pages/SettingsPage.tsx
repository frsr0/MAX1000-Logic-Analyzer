// Settings: theme, capture defaults, control lock, decoder presets.
import { useEffect, useState } from 'react';
import { api, clientId } from '../api/client';
import type { VirtualBridgeStatus } from '../api/types';
import { useApp } from '../state/appStore';

export function SettingsPage() {
  const { viewerSettings, setViewerSettings, status, refreshStatus,
          controlMode, setControlMode, toast, setCaptureSettings } = useApp();
  const [presets, setPresets] = useState<any[]>(
    JSON.parse(localStorage.getItem('msa_decoder_presets') ?? '[]'));
  const [virtual, setVirtual] = useState<VirtualBridgeStatus | null>(null);
  const [portA, setPortA] = useState('COM20');
  const [portB, setPortB] = useState('COM21');
  const [bridgePort, setBridgePort] = useState('');
  const [bridgeBusy, setBridgeBusy] = useState(false);

  const refreshVirtual = () => api.virtualSerialStatus().then(setVirtual).catch(() => {});
  useEffect(() => { refreshVirtual(); }, []);

  const lock = status?.control;
  const iAmHolder = lock?.holder === clientId();

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Settings</h2>
          <p className="hint">These settings only affect the web UI and the current control lock, not the underlying hardware image.</p>
        </div>
      </div>

      <div className="gen-grid">
        <div className="card">
          <h3>Appearance</h3>
          <label className="field">
            <span>Theme</span>
            <select value={viewerSettings.theme}
              onChange={(e) => setViewerSettings({ theme: e.target.value as any })}>
              <option value="dark">Dark</option>
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
          <p className="hint">Acquire the control lock before sending capture or generator commands to the hardware.</p>
          <p>
            Lock: {lock?.held
              ? <>held by <strong>{lock.holder_name}</strong>{iAmHolder ? ' (you)' : ''}</>
              : 'free'}
          </p>
          {iAmHolder && !status?.device_connected && (
            <div className="finding warning">You hold control, but no device is connected.</div>
          )}
          <label className="field checkbox">
            <input type="checkbox" checked={controlMode}
              onChange={(e) => setControlMode(e.target.checked)} />
            <span>Control mode</span>
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
              toast('success', 'Control taken. Connect a device on the Device page.');
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
              }}>x</button>
            </div>
          ))}

          <h3>Keyboard shortcuts</h3>
          <table className="data-table kv">
            <tbody>
              <tr><th>space</th><td>start / stop capture</td></tr>
              <tr><th>f</th><td>fit capture</td></tr>
              <tr><th>t</th><td>jump to trigger</td></tr>
              <tr><th>a / b</th><td>place cursor A / B at pointer</td></tr>
              <tr><th>left / right</th><td>pan</td></tr>
              <tr><th>+ / -</th><td>zoom</td></tr>
              <tr><th>n / p</th><td>next / previous decoder event</td></tr>
              <tr><th>e / E</th><td>next / previous digital edge</td></tr>
              <tr><th>r / R</th><td>next / previous decoder error</td></tr>
              <tr><th>ctrl+s</th><td>save session (JSON download)</td></tr>
              <tr><th>ctrl+k</th><td>open command palette</td></tr>
              <tr><th>shift+drag</th><td>select region</td></tr>
              <tr><th>double-click</th><td>cursor A snapped to edge (alt: B)</td></tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Virtual debug ports</h3>
            <span className={`badge ${virtual?.running ? 'badge-hw' : 'badge-soft'}`}>
              {virtual?.running ? 'bridge running' : 'app-only'}
            </span>
          </div>
          <p className="hint">
            This creates a software endpoint for debugger utilities and forwards
            JSON-lines SWD requests through the existing generator path. It does
            not reconfigure JTAG, MPSSE, FTDI EEPROM, or board wiring.
          </p>
          {!virtual && <div className="hint">Checking virtual-port support…</div>}
          {virtual && !virtual.driver.available && (
            <div className="finding warning">
              No com0com driver is installed. The TCP bridge works now; paired
              Windows COMx ports require a separately installed signed virtual-COM driver.
            </div>
          )}
          {virtual?.driver.available && (
            <>
              <div className="hint">Driver: <span className="mono">{virtual.driver.setup_path}</span></div>
              <label className="field">
                <span>New COM pair (utility side is the second port)</span>
                <div className="button-row">
                  <input value={portA} onChange={(e) => setPortA(e.target.value)} placeholder="COM20" />
                  <span>↔</span>
                  <input value={portB} onChange={(e) => setPortB(e.target.value)} placeholder="COM21" />
                </div>
              </label>
              <button disabled={bridgeBusy} onClick={async () => {
                setBridgeBusy(true);
                try {
                  const result = await api.createVirtualComPair(portA, portB);
                  setBridgePort(result.port_a);
                  toast('success', `Created ${result.port_a} ↔ ${result.port_b}`);
                  refreshVirtual();
                } catch (e: any) { toast('error', e.message); }
                finally { setBridgeBusy(false); }
              }}>Make COM pair</button>
            </>
          )}
          <label className="field">
            <span>Bridge transport</span>
            <select value={virtual?.driver.available && bridgePort ? 'com' : 'tcp'}
              onChange={(e) => setBridgePort(e.target.value === 'com' ? portA : '')}>
              <option value="tcp">TCP localhost (driver-free)</option>
              <option value="com" disabled={!virtual?.driver.available}>Virtual COM pair</option>
            </select>
          </label>
          {virtual?.driver.available && (
            <label className="field">
              <span>App-side COM endpoint</span>
              <input value={bridgePort} onChange={(e) => setBridgePort(e.target.value)} placeholder={portA} />
            </label>
          )}
          <div className="button-row">
            {!virtual?.running ? (
              <button className="primary" disabled={bridgeBusy} onClick={async () => {
                setBridgeBusy(true);
                try {
                  const transport = virtual?.driver.available && bridgePort ? 'com' : 'tcp';
                  const result = await api.startVirtualBridge({ transport, app_port: bridgePort });
                  setVirtual(result);
                  toast('success', result.tcp_endpoint ? `Bridge listening at ${result.tcp_endpoint}` : `Bridge opened ${result.app_port}`);
                } catch (e: any) { toast('error', e.message); }
                finally { setBridgeBusy(false); }
              }}>Start SWD bridge</button>
            ) : (
              <button className="danger" disabled={bridgeBusy} onClick={async () => {
                setBridgeBusy(true);
                try { setVirtual(await api.stopVirtualBridge()); toast('success', 'SWD bridge stopped'); }
                catch (e: any) { toast('error', e.message); }
                finally { setBridgeBusy(false); }
              }}>Stop bridge</button>
            )}
            <button onClick={refreshVirtual}>Refresh</button>
          </div>
          {virtual?.running && virtual.transport === 'tcp' && (
            <div className="finding info">
              Endpoint: <span className="mono">{virtual.tcp_endpoint}</span>. Send one JSON object per line;
              use <span className="mono">PING</span> or <span className="mono">STATUS</span> to check it.
            </div>
          )}
          <p className="hint">
            SWD bridge request shape: <span className="mono">{'{"op":"swd","config":{"extra":{"requests":[...]}}}'}</span>.
            This is an app protocol, not CMSIS-DAP; standard debuggers still need a CMSIS-DAP/OpenOCD adapter.
          </p>
        </div>
      </div>
    </div>
  );
}
