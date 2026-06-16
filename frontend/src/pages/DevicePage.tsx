// Device view: discovery, connect/disconnect, metadata, capabilities,
// raw debug inspector, self-test.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { DeviceDescriptor } from '../api/types';
import { useApp } from '../state/appStore';

export function DevicePage() {
  const { status, refreshStatus, refreshCapabilities, capabilities, toast, controlMode } = useApp();
  const [devices, setDevices] = useState<DeviceDescriptor[]>([]);
  const [debug, setDebug] = useState<any>(null);
  const [selfTest, setSelfTest] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const scan = () => api.devices().then((r) => setDevices(r.devices)).catch(() => {});
  useEffect(() => { scan(); }, []);

  const connect = async (id: string) => {
    setBusy(true);
    try {
      await api.connect(id);
      await refreshStatus();
      await refreshCapabilities();
      toast('success', 'Device connected');
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const disconnect = async () => {
    await api.disconnect().catch(() => {});
    await refreshStatus();
    setDebug(null);
  };

  const loadDebug = () => api.deviceDebug().then(setDebug).catch((e) => toast('error', e.message));
  const runSelfTest = async () => {
    setBusy(true);
    try { setSelfTest(await api.selfTest()); }
    catch (e: any) { toast('error', e.message); }
    finally { setBusy(false); }
  };

  const meta = status?.device;

  return (
    <div className="page">
      <div className="page-head">
        <h2>Device</h2>
        <button onClick={scan}>↻ Rescan</button>
      </div>

      <div className="card-grid">
        {devices.map((d) => (
          <div key={d.id} className={`card ${d.available ? '' : 'unavailable'}`}>
            <h3>{d.name}{d.mock ? ' 🧪' : ''}</h3>
            <p className="hint">{d.connection}</p>
            {d.detail && <p className="hint">{d.detail}</p>}
            {status?.device_connected && status.device_kind === d.id ? (
              <button className="danger" onClick={disconnect} disabled={!controlMode}>Disconnect</button>
            ) : (
              <button className="primary" disabled={!d.available || busy || !controlMode}
                onClick={() => connect(d.id)}>Connect</button>
            )}
          </div>
        ))}
      </div>

      {meta && (
        <>
          <h3>Hardware metadata</h3>
          <table className="data-table kv">
            <tbody>
              <tr><th>Device</th><td>{meta.device_name}{meta.mock ? ' (MOCK)' : ''}</td></tr>
              <tr><th>Connection</th><td>{meta.connection} · {meta.port}</td></tr>
              <tr><th>Firmware / protocol</th><td className="mono">{meta.firmware_version} / v{meta.protocol_version}</td></tr>
              <tr><th>Sys clock</th><td className="mono">{(meta.sys_clk_hz / 1e6).toFixed(1)} MHz</td></tr>
              <tr><th>Sample clock</th><td className="mono">{(meta.sample_clk_hz / 1e6).toFixed(1)} MHz</td></tr>
              <tr><th>Backend uptime</th><td className="mono">{Math.floor(status!.uptime_s)} s</td></tr>
              <tr><th>Connected clients</th><td className="mono">{status!.ws_clients}</td></tr>
            </tbody>
          </table>
        </>
      )}

      {capabilities && (
        <>
          <h3>Capabilities</h3>
          <table className="data-table kv">
            <tbody>
              <tr><th>Digital channels</th><td>{capabilities.digital_channels}</td></tr>
              <tr><th>Analog channels</th><td>{capabilities.analog_channels} <span className="hint">{capabilities.analog_rate_note}</span></td></tr>
              <tr><th>Max sample rate</th><td className="mono">{(capabilities.max_sample_rate / 1e6).toFixed(0)} MHz</td></tr>
              <tr><th>Capture depth</th><td className="mono">{capabilities.max_samples.toLocaleString()} (SDRAM) / {capabilities.bram_samples} (BRAM fast)</td></tr>
              <tr><th>Generator</th><td>{capabilities.generator_protocols.join(', ') || 'none'}</td></tr>
              <tr><th>Notes</th><td>{capabilities.notes.join('; ')}</td></tr>
            </tbody>
          </table>
          <h3>Trigger support matrix</h3>
          <div className="trigger-matrix">
            {capabilities.trigger_matrix.map((t) => (
              <span key={t.type}
                className={`badge ${t.execution === 'hardware' ? 'badge-hw'
                  : t.execution === 'post_capture' ? 'badge-soft' : 'badge-na'}`}
                title={t.description}>
                {t.type.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </>
      )}

      {status?.device_connected && (
        <>
          <h3>Diagnostics</h3>
          <div className="button-row">
            <button onClick={loadDebug}>Raw debug inspector</button>
            <button onClick={runSelfTest} disabled={busy || !controlMode}>Run self-test</button>
          </div>
          {selfTest && (
            <div className={`finding ${selfTest.passed ? 'info' : 'error'}`}>
              <strong>{selfTest.passed ? 'PASS' : 'FAIL'}</strong> — {selfTest.message}
              <ul>
                {selfTest.checks.map((c: any, i: number) => (
                  <li key={i}>{c.passed ? '✓' : '✗'} {c.name}: {c.detail}</li>
                ))}
              </ul>
            </div>
          )}
          {debug && <pre className="debug-pre">{JSON.stringify(debug, null, 2)}</pre>}
        </>
      )}
    </div>
  );
}
