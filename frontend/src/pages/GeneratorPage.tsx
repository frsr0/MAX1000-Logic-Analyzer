// Signal generator: configure, send, loopback capture, and compare against the
// MAX1000 hardware routing constraints.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { GeneratorConfig } from '../api/types';
import { useApp } from '../state/appStore';

const DEFAULT_CFG: GeneratorConfig = {
  protocol: 'uart',
  data_hex: '48656c6c6f21',
  baud: 115200,
  tx_pin: 0,
  scl_pin: 1,
  i2c_address: 0x19,
  i2c_register: 0x0f,
  i2c_read_len: 1,
  freq_hz: 100000,
  duty_pct: 50,
  repeat: 1,
  continuous: false,
};
const MAX_GENERATOR_PAYLOAD_BYTES = 256;
const UART_BITS_PER_BYTE = 10;
const UART_CAPTURE_GUARD_SAMPLES = 2000;
const UART_CAPTURE_MARGIN = 1.2;

function byteLen(hex: string): number {
  return Math.floor(hex.length / 2);
}

function uartCaptureSamples(cfg: GeneratorConfig, captureRate: number): number {
  if (!['uart', 'rs485'].includes(cfg.protocol)) return 60_000;
  const baud = Math.max(1, cfg.baud || 1);
  const samples = byteLen(cfg.data_hex) * UART_BITS_PER_BYTE * captureRate / baud;
  return Math.max(4_000, Math.ceil(samples * UART_CAPTURE_MARGIN) + UART_CAPTURE_GUARD_SAMPLES);
}

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
      .then((r) => {
        setProtocols(r.protocols);
        setGenStatus(r.status);
      })
      .catch(() => setProtocols([]));
    const t = setInterval(() => {
      api.generatorStatus().then(setGenStatus).catch(() => {});
    }, 2000);
    return () => clearInterval(t);
  }, [connected]);

  useEffect(() => {
    if (!connected || status?.device_kind !== 'hardware') return;
    if (cfg.protocol === 'uart' || cfg.protocol === 'rs485') {
      setCfg((prev) => (prev.tx_pin === 0 ? { ...prev, tx_pin: 3 } : prev));
    }
  }, [connected, status?.device_kind]);

  const set = (p: Partial<GeneratorConfig>) => setCfg({ ...cfg, ...p });

  const setProtocol = (protocol: string) => {
    if (protocol === 'i2c') {
      setCfg({ ...cfg, protocol, tx_pin: 2, scl_pin: 1, baud: 400000 });
    } else if (protocol === 'rs485') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 0 : 3, scl_pin: 1, baud: 115200 });
    } else if (protocol === 'uart') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 0 : 3, baud: 115200 });
    } else {
      setCfg({ ...cfg, protocol });
    }
  };

  const setTextData = (t: string) => {
    setText(t);
    set({ data_hex: Array.from(new TextEncoder().encode(t))
      .map((b) => b.toString(16).padStart(2, '0')).join('') });
  };

  const send = async (capture: boolean) => {
    setBusy(true);
    setResult(null);
    try {
      if (byteLen(cfg.data_hex) > MAX_GENERATOR_PAYLOAD_BYTES) {
        throw new Error(`Generator FIFO holds ${MAX_GENERATOR_PAYLOAD_BYTES} bytes; this payload is ${byteLen(cfg.data_hex)} bytes.`);
      }
      const captureRate = 2_000_000;
      const r = await api.generatorSend({
        config: cfg,
        capture,
        capture_rate: captureRate,
        capture_samples: uartCaptureSamples(cfg, captureRate),
        expected_hex: expected || undefined,
      });
      setResult(r);
      if (capture && r.session_id) toast('success', r.detail ?? 'Loopback captured');
      else toast('success', 'Pattern sent');
    } catch (e: any) {
      toast('error', e.message);
    } finally {
      setBusy(false);
    }
  };

  const needsData = ['uart', 'rs485', 'spi', 'pattern', 'i2c'].includes(cfg.protocol);
  const canLoopbackCapture = ['uart', 'rs485', 'i2c'].includes(cfg.protocol) || status?.device_kind === 'mock';

  if (!connected) {
    return (
      <div className="page">
        <h2>Signal generator</h2>
        <div className="hint">Connect a device first on the Device page.</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Signal generator</h2>
          <p className="hint">Routes are constrained by the MAX1000 board map, so the control copy stays honest about what the hardware can do.</p>
        </div>
        {genStatus && (
          <span className={`badge ${genStatus.busy ? 'badge-soft' : 'badge-hw'}`}>
            {genStatus.busy ? 'BUSY' : 'idle'}{genStatus.detail ? ` · ${genStatus.detail}` : ''}
          </span>
        )}
      </div>

      <div className="gen-grid">
        <div className="card">
          <div className="card-head">
            <h3>Protocol</h3>
            <span className="badge badge-soft">{protocols.length ? `${protocols.length} supported` : 'loading'}</span>
          </div>
          <label className="field">
            <span>Generator protocol</span>
            <select value={cfg.protocol} onChange={(e) => setProtocol(e.target.value)}>
              {protocols.map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
            </select>
          </label>
          <div className="finding info">
            Hardware support on this board is UART, RS-485, and I2C. Bit-banger-driven pin exercise lives on the MIL page.
          </div>

          {needsData && (
            <>
              <label className="field">
                <span>Data text - {byteLen(cfg.data_hex)}/{MAX_GENERATOR_PAYLOAD_BYTES} bytes</span>
                <input value={text} onChange={(e) => setTextData(e.target.value)} />
              </label>
              <label className="field">
                <span>Data hex</span>
                <input className="mono" value={cfg.data_hex}
                  onChange={(e) => set({ data_hex: e.target.value.replace(/[^0-9a-fA-F]/g, '') })} />
              </label>
            </>
          )}

          {['uart', 'rs485'].includes(cfg.protocol) && (
            <>
              <label className="field">
                <span>Baud</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} />
              </label>
              {cfg.protocol === 'rs485' ? (
                <>
                  <label className="field">
                    <span>B / + pin</span>
                    <input type="number" min={0} max={15} value={cfg.tx_pin}
                      onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                  </label>
                  <label className="field">
                    <span>A / - pin</span>
                    <input type="number" min={0} max={15} value={cfg.scl_pin}
                      onChange={(e) => set({ scl_pin: Number(e.target.value) })} />
                  </label>
                  <div className="hint">B carries the UART logic level; A carries the inverted complement.</div>
                </>
              ) : (
                <label className="field">
                  <span>TX pin</span>
                  <input type="number" min={0} max={15} value={cfg.tx_pin}
                    onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                </label>
              )}
            </>
          )}

          {cfg.protocol === 'i2c' && (
            <>
              <label className="field">
                <span>Speed (Hz)</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} />
              </label>
              <label className="field">
                <span>Address (hex)</span>
                <input className="mono" value={cfg.i2c_address.toString(16)}
                  onChange={(e) => set({ i2c_address: parseInt(e.target.value, 16) || 0 })} />
              </label>
              <label className="field">
                <span>Register (hex)</span>
                <input className="mono" value={cfg.i2c_register.toString(16)}
                  onChange={(e) => set({ i2c_register: parseInt(e.target.value, 16) || 0 })} />
              </label>
              <label className="field">
                <span>SDA channel / SCL channel</span>
                <span className="button-row">
                  <input type="number" min={0} max={15} value={cfg.tx_pin}
                    onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                  <input type="number" min={0} max={15} value={cfg.scl_pin}
                    onChange={(e) => set({ scl_pin: Number(e.target.value) })} />
                </span>
              </label>
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
            <button className="primary" disabled={busy || !controlMode || !canLoopbackCapture} onClick={() => send(true)}>
              Send + capture
            </button>
            <button disabled={!controlMode} onClick={() => api.generatorStop().catch(() => {})}>Stop</button>
          </div>

          <label className="field">
            <span>Expected hex for compare</span>
            <input className="mono" value={expected} placeholder="(same as sent)"
              onChange={(e) => setExpected(e.target.value.replace(/[^0-9a-fA-F]/g, ''))} />
          </label>

          <button disabled={busy || !controlMode} onClick={async () => {
            setBusy(true);
            try {
              setResult(await api.generatorSelfTest());
            } catch (e: any) {
              toast('error', e.message);
            } finally {
              setBusy(false);
            }
          }}>Run generator self-test</button>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Routing</h3>
            <span className="badge badge-soft">MAX1000 capture path</span>
          </div>
          <div className="finding info">
            Generator output is captured through the same board pins and the same backend driver path as the analyzer.
          </div>
          <ul className="sanity-list">
            <li>UART and RS-485 support loopback capture in one action.</li>
            <li>I2C uses the configured SDA and SCL capture channels.</li>
            <li>Bit-banger-driven pin activity is exercised from the MIL page rather than the generator page.</li>
          </ul>
          <div className="divider" />
          <h3>Result</h3>
          {!result && (
            <div className="hint">
              Send a pattern to see the loopback result.
            </div>
          )}
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
