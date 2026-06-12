// Signal generator: configure, send, loopback capture + auto-decode compare.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { GeneratorConfig } from '../api/types';
import { useApp } from '../state/appStore';

const DEFAULT_CFG: GeneratorConfig = {
  protocol: 'uart', data_hex: '48656c6c6f21', baud: 115200, tx_pin: 0,
  scl_pin: 1, i2c_address: 0x19, i2c_register: 0x0f, i2c_read_len: 1,
  freq_hz: 100000, duty_pct: 50, repeat: 1, continuous: false,
};

export function GeneratorPage() {
  const { status, toast, controlMode, openSession, setPage } = useApp();
  const [protocols, setProtocols] = useState<string[]>([]);
  const [genStatus, setGenStatus] = useState<any>(null);
  const [cfg, setCfg] = useState<GeneratorConfig>(DEFAULT_CFG);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('Hello!');
  const [expected, setExpected] = useState('');

  const connected = status?.device_connected ?? false;

  useEffect(() => {
    if (!connected) return;
    api.generatorCapabilities()
      .then((r) => { setProtocols(r.protocols); setGenStatus(r.status); })
      .catch(() => setProtocols([]));
    const t = setInterval(() => {
      api.generatorStatus().then(setGenStatus).catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [connected]);

  const set = (p: Partial<GeneratorConfig>) => setCfg({ ...cfg, ...p });

  const setTextData = (t: string) => {
    setText(t);
    set({ data_hex: Array.from(new TextEncoder().encode(t))
      .map((b) => b.toString(16).padStart(2, '0')).join('') });
  };

  const send = async (capture: boolean) => {
    setBusy(true);
    setResult(null);
    try {
      const r = await api.generatorSend({
        config: cfg, capture,
        capture_rate: 2_000_000, capture_samples: 60_000,
        expected_hex: expected || undefined,
      });
      setResult(r);
      if (capture && r.session_id) toast('success', r.detail ?? 'Loopback captured');
      else toast('success', 'Pattern sent');
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const needsData = ['uart', 'spi', 'pattern', 'i2c'].includes(cfg.protocol);
  const isPwm = ['pwm', 'square'].includes(cfg.protocol);

  if (!connected) {
    return <div className="page"><h2>Signal generator</h2>
      <div className="hint">Connect a device first (Device page).</div></div>;
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>Signal generator</h2>
        {genStatus && (
          <span className={`badge ${genStatus.busy ? 'badge-soft' : 'badge-hw'}`}>
            {genStatus.busy ? 'BUSY' : 'idle'} {genStatus.detail ? `· ${genStatus.detail}` : ''}
          </span>
        )}
      </div>
      <div className="gen-grid">
        <div className="card">
          <label className="field">
            <span>Protocol</span>
            <select value={cfg.protocol} onChange={(e) => set({ protocol: e.target.value })}>
              {protocols.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </label>
          {needsData && (
            <>
              <label className="field">
                <span>Data (text)</span>
                <input value={text} onChange={(e) => setTextData(e.target.value)} />
              </label>
              <label className="field">
                <span>Data (hex)</span>
                <input className="mono" value={cfg.data_hex}
                  onChange={(e) => set({ data_hex: e.target.value.replace(/[^0-9a-fA-F]/g, '') })} />
              </label>
            </>
          )}
          {cfg.protocol === 'uart' && (
            <>
              <label className="field"><span>Baud</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} /></label>
              <label className="field"><span>TX pin (channel)</span>
                <input type="number" min={0} max={15} value={cfg.tx_pin}
                  onChange={(e) => set({ tx_pin: Number(e.target.value) })} /></label>
            </>
          )}
          {cfg.protocol === 'i2c' && (
            <>
              <label className="field"><span>Speed (Hz)</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} /></label>
              <label className="field"><span>Address (hex)</span>
                <input className="mono" value={cfg.i2c_address.toString(16)}
                  onChange={(e) => set({ i2c_address: parseInt(e.target.value, 16) || 0 })} /></label>
              <label className="field"><span>Register (hex)</span>
                <input className="mono" value={cfg.i2c_register.toString(16)}
                  onChange={(e) => set({ i2c_register: parseInt(e.target.value, 16) || 0 })} /></label>
              <label className="field"><span>SDA pin / SCL pin</span>
                <span className="button-row">
                  <input type="number" min={0} max={15} value={cfg.tx_pin}
                    onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                  <input type="number" min={0} max={15} value={cfg.scl_pin}
                    onChange={(e) => set({ scl_pin: Number(e.target.value) })} />
                </span></label>
            </>
          )}
          {isPwm && (
            <>
              <label className="field"><span>Frequency (Hz)</span>
                <input type="number" value={cfg.freq_hz} onChange={(e) => set({ freq_hz: Number(e.target.value) })} /></label>
              <label className="field"><span>Duty (%)</span>
                <input type="number" min={1} max={99} value={cfg.duty_pct}
                  onChange={(e) => set({ duty_pct: Number(e.target.value) })} /></label>
              <label className="field"><span>Output pin</span>
                <input type="number" min={0} max={15} value={cfg.tx_pin}
                  onChange={(e) => set({ tx_pin: Number(e.target.value) })} /></label>
            </>
          )}
          {['counter', 'prbs'].includes(cfg.protocol) && (
            <div className="hint">Pattern generator: {cfg.protocol === 'counter'
              ? '16-bit counter across all channels' : 'pseudo-random bits on the output pin'}.</div>
          )}
          <label className="field checkbox">
            <input type="checkbox" checked={cfg.continuous}
              onChange={(e) => set({ continuous: e.target.checked })} />
            <span>Continuous</span>
          </label>
          <div className="button-row">
            <button className="primary" disabled={busy || !controlMode} onClick={() => send(false)}>Send</button>
            <button className="primary" disabled={busy || !controlMode} onClick={() => send(true)}>
              Send + capture (loopback)
            </button>
            <button disabled={!controlMode} onClick={() => api.generatorStop().catch(() => {})}>Stop</button>
          </div>
          <label className="field">
            <span>Expected hex (for pass/fail compare; default = sent data)</span>
            <input className="mono" value={expected} placeholder="(same as sent)"
              onChange={(e) => setExpected(e.target.value.replace(/[^0-9a-fA-F]/g, ''))} />
          </label>
          <button disabled={busy || !controlMode} onClick={async () => {
            setBusy(true);
            try { setResult(await api.generatorSelfTest()); }
            catch (e: any) { toast('error', e.message); }
            finally { setBusy(false); }
          }}>Run generator self-test (UART loopback)</button>
        </div>

        <div className="card">
          <h3>Result</h3>
          {!result && <div className="hint">Send a pattern to see the loopback result.
            Routing: generator output → capture input channels (mock: direct loopback;
            hardware: CMD_GEN_CAPTURE atomic generator+capture).</div>}
          {result && 'passed' in result && (
            <div className={`finding ${result.passed ? 'info' : 'error'}`}>
              <strong>{result.passed ? 'PASS' : 'FAIL'}</strong><br />
              sent: <span className="mono">{result.sent_hex}</span><br />
              decoded: <span className="mono">{result.decoded_hex}</span><br />
              {result.detail}
            </div>
          )}
          {result?.session_id && (
            <button onClick={async () => {
              await openSession(result.session_id);
              setPage('capture');
            }}>Open loopback capture</button>
          )}
        </div>
      </div>
    </div>
  );
}
