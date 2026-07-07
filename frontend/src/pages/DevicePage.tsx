// Device view: discovery, connect/disconnect, hardware overview, pin maps,
// raw debug inspector, self-test.
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { DeviceDescriptor } from '../api/types';
import { useApp } from '../state/appStore';

function formatRate(rate: number) {
  return rate >= 1e6 ? `${(rate / 1e6).toFixed(1)} MHz` : `${(rate / 1e3).toFixed(0)} kHz`;
}

function formatSamples(samples: number) {
  return samples.toLocaleString();
}

export function DevicePage() {
  const { status, refreshStatus, refreshCapabilities, capabilities, toast, controlMode } = useApp();
  const [devices, setDevices] = useState<DeviceDescriptor[]>([]);
  const [debug, setDebug] = useState<any>(null);
  const [selfTest, setSelfTest] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const scan = () => api.devices().then((r) => setDevices(r.devices)).catch(() => {});
  useEffect(() => {
    scan();
  }, []);

  const connect = async (id: string) => {
    setBusy(true);
    try {
      await api.connect(id);
      await refreshStatus();
      await refreshCapabilities();
      toast('success', 'Device connected');
    } catch (e: any) {
      toast('error', e.message);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    await api.disconnect().catch(() => {});
    await refreshStatus();
    setDebug(null);
    setSelfTest(null);
  };

  const loadDebug = () => api.deviceDebug().then(setDebug).catch((e) => toast('error', e.message));
  const runSelfTest = async () => {
    setBusy(true);
    try {
      setSelfTest(await api.selfTest());
    } catch (e: any) {
      toast('error', e.message);
    } finally {
      setBusy(false);
    }
  };

  const meta = status?.device;
  const connected = Boolean(status?.device_connected);

  const keyFacts = useMemo(() => {
    if (!capabilities) return [];
    return [
      { label: 'Digital inputs', value: `${capabilities.digital_channels}` },
      { label: 'Analog inputs', value: `${capabilities.analog_channels}` },
      { label: 'Max sample rate', value: formatRate(capabilities.max_sample_rate) },
      { label: 'Capture depth', value: `${formatSamples(capabilities.max_samples)} samples` },
      { label: 'BRAM fast depth', value: formatSamples(capabilities.bram_samples) },
      { label: 'Generator', value: capabilities.generator_protocols.join(', ') || 'none' },
    ];
  }, [capabilities]);

  return (
    <div className="page device-page">
      <div className="page-head">
        <div>
          <h2>Device</h2>
          <p className="hint">What is actually on the bench: MAX1000 pin map, ADC inputs, generator support, and live capability data.</p>
        </div>
        <button onClick={scan}>Rescan</button>
      </div>

      <div className="card-grid device-grid">
        {devices.map((d) => (
          <div key={d.id} className={`card device-card ${d.available ? '' : 'unavailable'}`}>
            <div className="card-head">
              <div>
                <h3>{d.name}{d.mock ? ' (mock)' : ''}</h3>
                <p className="hint">{d.connection}</p>
              </div>
              <span className={`badge ${d.available ? 'badge-hw' : 'badge-na'}`}>
                {d.available ? 'available' : 'offline'}
              </span>
            </div>
            {d.detail && <p className="hint">{d.detail}</p>}
            {connected && status?.device_kind === d.id ? (
              <button className="danger" onClick={disconnect} disabled={!controlMode}>Disconnect</button>
            ) : (
              <button className="primary" disabled={!d.available || busy || !controlMode}
                onClick={() => connect(d.id)}>Connect</button>
            )}
          </div>
        ))}
      </div>

      {meta && (
        <div className="card device-hero">
          <div className="hero-copy">
            <h3>{meta.device_name}{meta.mock ? ' (mock)' : ''}</h3>
            <p className="hint">{meta.connection} · {meta.port}</p>
            <div className="hero-badges">
              <span className="badge badge-hw">{formatRate(meta.sample_clk_hz)} sample clock</span>
              <span className="badge badge-soft">{formatRate(meta.sys_clk_hz)} system clock</span>
              <span className="badge badge-soft">{meta.protocol_version === 'unknown' ? 'protocol unknown' : `protocol v${meta.protocol_version}`}</span>
            </div>
          </div>
          <div className="hero-meta">
            <div><span>Firmware</span><strong className="mono">{meta.firmware_version}</strong></div>
            <div><span>Backend uptime</span><strong className="mono">{Math.floor(status!.uptime_s)} s</strong></div>
            <div><span>Connected clients</span><strong className="mono">{status!.ws_clients}</strong></div>
            <div><span>Control</span><strong>{status?.control?.held ? `held by ${status.control.holder_name}` : 'free'}</strong></div>
          </div>
        </div>
      )}

      {capabilities && (
        <div className="device-stack">
          <div className="card">
            <div className="card-head">
              <h3>Hardware summary</h3>
              <span className="badge badge-hw">MAX1000 board map</span>
            </div>
            <div className="fact-grid">
              {keyFacts.map((fact) => (
                <div key={fact.label} className="fact">
                  <span>{fact.label}</span>
                  <strong>{fact.value}</strong>
                </div>
              ))}
            </div>
            {capabilities.notes.length > 0 && (
              <div className="note-list">
                {capabilities.notes.map((note) => <div key={note} className="finding info">{note}</div>)}
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Capture matrix</h3>
              <span className="badge badge-soft">hardware + software triggers</span>
            </div>
            <div className="trigger-matrix">
              {capabilities.triggers.map((t) => (
                <span key={t.type}
                  className={`badge ${t.execution === 'hardware' ? 'badge-hw'
                    : t.execution === 'post_capture' ? 'badge-soft' : 'badge-na'}`}
                  title={t.description}>
                  {t.type.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Analog inputs</h3>
              <span className="badge badge-soft">{capabilities.analog_rate_note || 'Analog capture available'}</span>
            </div>
            <table className="data-table">
              <thead>
                <tr><th>ADC</th><th>Board input</th><th>Header</th><th>FPGA pin</th><th>Status</th></tr>
              </thead>
              <tbody>
                {capabilities.analog_pin_map.map((p, idx) => (
                  <tr key={`analog-${p.board_label}-${idx}`} title={p.note || ''}>
                    <td className="mono">{p.adc_channel !== undefined ? `ADC${p.adc_channel}` : '—'}</td>
                    <td>{p.board_label}</td>
                    <td>{p.header || '—'}</td>
                    <td className="mono">{p.fpga_pin || '—'}</td>
                    <td>{p.current_rtl_stream ? 'captured' : p.available ? 'board pin' : 'not input'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Digital pin pool</h3>
              <span className="badge badge-soft">D0-D14, PMOD, sensor bus</span>
            </div>
            <table className="data-table">
              <thead>
                <tr><th>Pin</th><th>Board input</th><th>Header</th><th>FPGA pin</th></tr>
              </thead>
              <tbody>
                {capabilities.digital_pin_map.map((p) => (
                  <tr key={`pin-${p.pin_index}`}>
                    <td className="mono">{p.pin_index}</td>
                    <td>{p.board_label}</td>
                    <td>{p.header || '—'}</td>
                    <td className="mono">{p.fpga_pin || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {connected && (
        <div className="card">
          <div className="card-head">
            <h3>Diagnostics</h3>
            <span className="badge badge-soft">live driver access</span>
          </div>
          <div className="button-row">
            <button onClick={loadDebug}>Raw debug inspector</button>
            <button onClick={runSelfTest} disabled={busy || !controlMode}>Run self-test</button>
          </div>
          {selfTest && (
            <div className={`finding ${selfTest.passed ? 'info' : 'error'}`}>
              <strong>{selfTest.passed ? 'PASS' : 'FAIL'}</strong> - {selfTest.message}
              <ul>
                {selfTest.checks.map((c: any, i: number) => (
                  <li key={i}>{c.passed ? 'OK' : 'FAIL'} {c.name}: {c.detail}</li>
                ))}
              </ul>
            </div>
          )}
          {debug && <pre className="debug-pre">{JSON.stringify(debug, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}
