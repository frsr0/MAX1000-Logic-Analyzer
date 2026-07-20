// Signal generator: configure, send, loopback capture, and compare against the
// MAX1000 hardware routing constraints.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { GeneratorConfig, GeneratorRouteCapability } from '../api/types';
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
  extra: {},
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
  const [routes, setRoutes] = useState<GeneratorRouteCapability[]>([]);
  const [genStatus, setGenStatus] = useState<any>(null);
  const [cfg, setCfg] = useState<GeneratorConfig>(DEFAULT_CFG);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState('Hello!');
  const [expected, setExpected] = useState('');
  const [preview, setPreview] = useState<any>(null);
  const [sweepResult, setSweepResult] = useState<any>(null);
  const [bitbangPresets, setBitbangPresets] = useState<string[]>([]);

  const connected = status?.device_connected ?? false;

  useEffect(() => {
    if (!connected) return;
    api.generatorCapabilities()
      .then((r) => {
        setProtocols(r.protocols);
        setRoutes(r.routes ?? []);
        setGenStatus(r.status);
      })
      .catch(() => setProtocols([]));
    api.bitbangPresets().then((r) => setBitbangPresets(r.presets)).catch(() => {});
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
  const setExtra = (p: Record<string, any>) => set({ extra: { ...(cfg.extra ?? {}), ...p } });

  const setProtocol = (protocol: string) => {
    if (protocol === 'i2c') {
      setCfg({ ...cfg, protocol, tx_pin: 2, scl_pin: 1, baud: 400000 });
    } else if (protocol === 'rs485') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 0 : 3, scl_pin: 1, baud: 115200 });
    } else if (protocol === 'uart') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 0 : 3, baud: 115200 });
    } else if (protocol === 'spi') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 5 : 3, scl_pin: status?.device_kind === 'mock' ? 4 : 1, baud: 1000000 });
    } else if (protocol === 'swd') {
      setCfg({ ...cfg, protocol, tx_pin: status?.device_kind === 'mock' ? 1 : 3, scl_pin: status?.device_kind === 'mock' ? 0 : 1, baud: 1000000,
        extra: { requests: [{ ap: false, read: true, addr: 0, data: 0 }], jtag_to_swd: true } });
    } else if (protocol === 'pattern' || protocol === 'bitbang') {
      setCfg({ ...cfg, protocol, tx_pin: 0, baud: 9600 });
    } else {
      setCfg({ ...cfg, protocol });
    }
  };

  const setTextData = (t: string) => {
    setText(t);
    set({ data_hex: Array.from(new TextEncoder().encode(t))
      .map((b) => b.toString(16).padStart(2, '0')).join('') });
  };

  const runSweep = async () => {
    try {
      const axes: Record<string, unknown[]> = cfg.protocol === 'bitbang'
        ? { 'extra.repeat': [1, 2, 4] }
        : { baud: [Math.max(1, Math.floor(cfg.baud / 2)), cfg.baud, cfg.baud * 2] };
      setSweepResult(await api.generatorSweepPreview({ base: cfg, axes, limit: 16 }));
    } catch (e: any) { toast('error', e.message); }
  };

  const runCaptureSweep = async () => {
    try {
      const axes: Record<string, unknown[]> = cfg.protocol === 'bitbang'
        ? { 'extra.repeat': [1, 2] }
        : { baud: [Math.max(1, Math.floor(cfg.baud / 2)), cfg.baud] };
      const captureRate = 2_000_000;
      setSweepResult(await api.generatorSweepCapture({
        base: cfg, axes, limit: 8, capture_rate: captureRate,
        capture_samples: uartCaptureSamples(cfg, captureRate),
        expected_hex: expected || undefined,
      }));
      toast('success', 'Capture-backed sweep complete');
    } catch (e: any) { toast('error', e.message); }
  };

  const exportBitbangScript = () => {
    const payload = { format: 'ols-bitbang-v1', symbol_rate: cfg.baud, ...cfg.extra };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = 'bitbang-script.json'; link.click();
    URL.revokeObjectURL(url);
  };

  const importBitbangScript = async (file?: File) => {
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const extra = parsed.extra ?? parsed;
      if (!Array.isArray(extra.symbols) && !Array.isArray(extra.script)) {
        throw new Error('JSON must contain symbols or script');
      }
      setCfg({ ...cfg, protocol: 'bitbang', baud: Number(parsed.symbol_rate ?? cfg.baud), extra });
      toast('success', 'Bit Banger script imported');
    } catch (e: any) { toast('error', `Could not import script: ${e.message}`); }
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

  const needsData = ['uart', 'rs485', 'spi', 'pattern', 'i2c', 'bitbang'].includes(cfg.protocol);
  const canLoopbackCapture = ['uart', 'rs485', 'i2c', 'spi', 'swd'].includes(cfg.protocol) || status?.device_kind === 'mock';
  const canStandaloneSend = !['spi', 'pattern', 'counter', 'prbs'].includes(cfg.protocol)
    || status?.device_kind === 'mock';

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
            Hardware support on this board is UART, RS-485, I2C, SPI, SWD transaction capture, and raw two-output Bit Banger playback. Protocol exerciser workflows can be built from the raw symbol mode.
          </div>
          {routes.length > 0 && (
            <div className="hint" data-testid="generator-route-capabilities">
              {routes.map((route) => (
                <div key={route.protocol}>
                  <strong>{route.name || route.protocol.toUpperCase()}</strong>: {route.detail || route.features.join(', ') || 'basic route'}
                </div>
              ))}
            </div>
          )}

          {needsData && (
            <>
              <label className="field">
                <span>Data text - {byteLen(cfg.data_hex)}/{MAX_GENERATOR_PAYLOAD_BYTES} bytes</span>
                <input value={text} onChange={(e) => setTextData(e.target.value)} />
              </label>
              <label className="field">
                <span>Data hex</span>
                <input className="mono" value={cfg.data_hex}
                  onChange={(e) => {
                    const data_hex = e.target.value.replace(/[^0-9a-fA-F]/g, '');
                    set({ data_hex, ...(cfg.protocol === 'bitbang' && cfg.extra?.encoding
                      ? { extra: { ...(cfg.extra ?? {}), data_hex } } : {}) });
                  }} />
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

          {cfg.protocol === 'spi' && (
            <>
              <label className="field">
                <span>Clock rate (Hz)</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} />
              </label>
              <label className="field">
                <span>MOSI pin / SCLK pin</span>
                <span className="button-row">
                  <input type="number" min={0} max={15} value={cfg.tx_pin}
                    onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                  <input type="number" min={0} max={15} value={cfg.scl_pin}
                    onChange={(e) => set({ scl_pin: Number(e.target.value) })} />
                </span>
              </label>
              {status?.device_kind !== 'mock' && (
                <div className="hint">
                  Hardware SPI generator loops MOSI and SCLK only (no CS/MISO); standalone
                  Send is unsupported — use Send + capture.
                </div>
              )}
            </>
          )}

          {cfg.protocol === 'swd' && (
            <>
              <label className="field">
                <span>SWDIO pin / SWCLK pin</span>
                <span className="button-row">
                  <input type="number" min={0} max={15} value={cfg.tx_pin}
                    onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
                  <input type="number" min={0} max={15} value={cfg.scl_pin}
                    onChange={(e) => set({ scl_pin: Number(e.target.value) })} />
                </span>
              </label>
              <label className="field">
                <span>SWD requests (JSON)</span>
                <textarea className="mono" rows={4}
                  value={JSON.stringify(cfg.extra?.requests ?? [{ ap: false, read: true, addr: 0, data: 0 }], null, 2)}
                  onChange={(e) => {
                    try { setExtra({ requests: JSON.parse(e.target.value) }); } catch { /* wait for valid JSON */ }
                  }} />
              </label>
              <label className="field checkbox"><input type="checkbox" checked={cfg.extra?.jtag_to_swd !== false}
                onChange={(e) => setExtra({ jtag_to_swd: e.target.checked })} /><span>Send JTAG-to-SWD sequence</span></label>
              <div className="hint">Send + capture logs the SWCLK/SWDIO transaction and runs the SWD decoder. A target response requires an electrically connected SWD route.</div>
            </>
          )}

          {cfg.protocol === 'bitbang' && (
            <>
              <label className="field">
                <span>Protocol template</span>
                <select value={cfg.extra?.encoding ?? ''} onChange={(e) => {
                  const encoding = e.target.value;
                  const extra = { ...(cfg.extra ?? {}), data_hex: cfg.data_hex } as Record<string, any>;
                  if (encoding) extra.encoding = encoding; else delete extra.encoding;
                  set({ extra });
                }}>
                  <option value="">Raw symbols / preset</option>
                  {['uart', 'rs485', 'spi', 'i2c', 'onewire', 'pwm', 'swd', 'manchester', 'differential_manchester', 'nrz', 'ps2', 'midi', 'lin']
                    .map((p) => <option key={p} value={p}>{p.toUpperCase()}</option>)}
                </select>
              </label>
              {cfg.extra?.encoding === 'rs485' && (
                <>
                  <label className="field"><span>DE assert delay (µs)</span>
                    <input type="number" min={0} value={cfg.extra.de_assert_us ?? 0}
                      onChange={(e) => setExtra({ de_assert_us: Number(e.target.value) })} /></label>
                  <label className="field"><span>DE release delay (µs)</span>
                    <input type="number" min={0} value={cfg.extra.de_release_us ?? 0}
                      onChange={(e) => setExtra({ de_release_us: Number(e.target.value) })} /></label>
                  <label className="field"><span>Turnaround delay (µs)</span>
                    <input type="number" min={0} value={cfg.extra.turnaround_us ?? 0}
                      onChange={(e) => setExtra({ turnaround_us: Number(e.target.value) })} /></label>
                  <label className="field"><span>Direction changes</span>
                    <input type="number" min={0} value={cfg.extra.direction_changes ?? 0}
                      onChange={(e) => setExtra({ direction_changes: Number(e.target.value) })} /></label>
                </>
              )}
              {cfg.extra?.encoding === 'spi' && (
                <>
                  <label className="field"><span>SPI mode</span>
                    <select value={`${cfg.extra.cpol ?? 0}${cfg.extra.cpha ?? 0}`} onChange={(e) => setExtra({ cpol: Number(e.target.value[0]), cpha: Number(e.target.value[1]) })}>
                      {[0, 1, 2, 3].map((mode) => <option key={mode} value={`${Math.floor(mode / 2)}${mode % 2}`}>Mode {mode}</option>)}
                    </select></label>
                  <label className="field"><span>Bit order / word size</span>
                    <span className="button-row"><select value={cfg.extra.bit_order ?? 'msb'} onChange={(e) => setExtra({ bit_order: e.target.value })}><option value="msb">MSB first</option><option value="lsb">LSB first</option></select><input type="number" min={4} max={32} value={cfg.extra.word_size ?? 8} onChange={(e) => setExtra({ word_size: Number(e.target.value) })} /></span></label>
                  <label className="field"><span>Inter-word gap (symbols)</span>
                    <input type="number" min={0} value={cfg.extra.gap_symbols ?? 0} onChange={(e) => setExtra({ gap_symbols: Number(e.target.value) })} /></label>
                  <div className="hint">Bit Banger maps MOSI to bit 0 and clock to bit 1; CS/MISO require separate routing.</div>
                </>
              )}
              {cfg.extra?.encoding === 'i2c' && (
                <>
                  <label className="field"><span>7-bit address (hex)</span>
                    <input className="mono" value={(cfg.extra.address ?? 0x50).toString(16)}
                      onChange={(e) => setExtra({ address: parseInt(e.target.value, 16) || 0 })} /></label>
                  <label className="field"><span>Register (hex)</span>
                    <input className="mono" value={(cfg.extra.register ?? 0).toString(16)}
                      onChange={(e) => setExtra({ register: parseInt(e.target.value, 16) || 0 })} /></label>
                  <label className="field"><span>Read length</span>
                    <input type="number" min={0} value={cfg.extra.read_len ?? 0}
                      onChange={(e) => setExtra({ read_len: Number(e.target.value) })} /></label>
                  <label className="field checkbox"><input type="checkbox" checked={cfg.extra.repeated_start !== false}
                    onChange={(e) => setExtra({ repeated_start: e.target.checked })} /><span>Repeated start</span></label>
                  <label className="field checkbox"><input type="checkbox" checked={cfg.extra.ack !== false}
                    onChange={(e) => setExtra({ ack: e.target.checked })} /><span>ACK writes</span></label>
                  <label className="field"><span>Bus recovery clocks</span>
                    <input type="number" min={0} max={16} value={cfg.extra.recovery_clocks ?? 0}
                      onChange={(e) => setExtra({ recovery_clocks: Number(e.target.value) })} /></label>
                  <label className="field"><span>Clock stretch (µs)</span>
                    <input type="number" min={0} value={cfg.extra.clock_stretch_us ?? 0}
                      onChange={(e) => setExtra({ clock_stretch_us: Number(e.target.value) })} /></label>
                </>
              )}
              {cfg.extra?.encoding === 'onewire' && (
                <label className="field"><span>Read slots</span>
                  <input type="number" min={0} max={64} value={cfg.extra.read_slots ?? 0}
                    onChange={(e) => setExtra({ read_slots: Number(e.target.value) })} /></label>
              )}
              {cfg.extra?.encoding === 'swd' && (
                <>
                  <label className="field"><span>SWD requests (JSON)</span>
                    <textarea className="mono" rows={4} value={JSON.stringify(cfg.extra.requests ?? [{ ap: false, read: true, addr: 0, data: 0 }])}
                      onChange={(e) => { try { setExtra({ requests: JSON.parse(e.target.value) }); } catch { /* edit in progress */ } }} /></label>
                  <label className="field"><span>Line reset / idle cycles</span>
                    <span className="button-row"><input type="number" min={8} value={cfg.extra.line_reset_cycles ?? 50}
                      onChange={(e) => setExtra({ line_reset_cycles: Number(e.target.value) })} /><input type="number" min={0} value={cfg.extra.idle_cycles ?? 8}
                      onChange={(e) => setExtra({ idle_cycles: Number(e.target.value) })} /></span></label>
                  <label className="field checkbox"><input type="checkbox" checked={cfg.extra.jtag_to_swd !== false}
                    onChange={(e) => setExtra({ jtag_to_swd: e.target.checked })} /><span>JTAG-to-SWD transition</span></label>
                  <label className="field checkbox"><input type="checkbox" checked={Boolean(cfg.extra.idcode_discovery)}
                    onChange={(e) => setExtra({ idcode_discovery: e.target.checked })} /><span>Prepend IDCODE discovery</span></label>
                  <div className="hint">SWDIO is bit 0 and SWCLK is bit 1. This is a software exerciser/preview; it does not advertise a hardware SWD generator route.</div>
                </>
              )}
              {cfg.extra?.encoding === 'pwm' && (
                <>
                  <label className="field"><span>Frequency (Hz)</span>
                    <input type="number" min={1} value={cfg.extra.frequency_hz ?? 1000}
                      onChange={(e) => setExtra({ frequency_hz: Number(e.target.value) })} /></label>
                  <label className="field"><span>End frequency (Hz)</span>
                    <input type="number" min={1} value={cfg.extra.end_frequency_hz ?? cfg.extra.frequency_hz ?? 1000}
                      onChange={(e) => setExtra({ end_frequency_hz: Number(e.target.value) })} /></label>
                  <label className="field"><span>Duty / end duty (%)</span>
                    <span className="button-row"><input type="number" min={0} max={100} value={cfg.extra.duty_pct ?? 50}
                      onChange={(e) => setExtra({ duty_pct: Number(e.target.value) })} /><input type="number" min={0} max={100} value={cfg.extra.end_duty_pct ?? cfg.extra.duty_pct ?? 50}
                      onChange={(e) => setExtra({ end_duty_pct: Number(e.target.value) })} /></span></label>
                  <label className="field"><span>Sweep steps / cycles</span>
                    <span className="button-row"><input type="number" min={1} value={cfg.extra.sweep_steps ?? 1}
                      onChange={(e) => setExtra({ sweep_steps: Number(e.target.value) })} /><input type="number" min={1} value={cfg.extra.cycles ?? 8}
                      onChange={(e) => setExtra({ cycles: Number(e.target.value) })} /></span></label>
                  <label className="field"><span>Start phase (degrees)</span>
                    <input type="number" min={0} max={360} value={cfg.extra.phase_deg ?? 0}
                      onChange={(e) => setExtra({ phase_deg: Number(e.target.value) })} /></label>
                </>
              )}
              {cfg.extra?.encoding && (
                <label className="field"><span>Fault injection</span>
                  <select value={cfg.extra.fault ?? ''} onChange={(e) => {
                    const fault = e.target.value;
                    if (fault) setExtra({ fault });
                    else { const { fault: _fault, ...extra } = cfg.extra ?? {}; set({ extra }); }
                  }}>
                    <option value="">None</option>
                    <option value="wrong_parity">Wrong parity</option>
                    <option value="invalid_stop">Invalid stop bit</option>
                    <option value="malformed_checksum">Malformed checksum</option>
                    <option value="missing_ack">Missing ACK</option>
                    <option value="shortened_pulse">Shortened pulse</option>
                    <option value="illegal_transition">Illegal bus transition</option>
                  </select>
                </label>
              )}
              <label className="field">
                <span>Symbol rate (symbols/s)</span>
                <input type="number" min={1} value={cfg.baud}
                  onChange={(e) => set({ baud: Number(e.target.value) })} />
              </label>
              <label className="field">
                <span>2-bit symbols (0–3, comma separated)</span>
                <input className="mono"
                  value={(cfg.extra?.symbols ?? []).join(',')}
                  onChange={(e) => set({ extra: { ...(cfg.extra ?? {}), symbols: e.target.value.split(',').map((v) => Number(v.trim())).filter((v) => Number.isFinite(v) && v >= 0 && v <= 3) } })} />
              </label>
              <label className="field">
                <span>Preset</span>
                <select value={cfg.extra?.preset ?? ''} onChange={(e) => {
                  const preset = e.target.value;
                  if (preset) set({ extra: { ...(cfg.extra ?? {}), preset, count: cfg.extra?.count ?? 32 } });
                  else { const { preset: _preset, ...extra } = cfg.extra ?? {}; set({ extra }); }
                }}>
                  <option value="">Custom symbols</option>
                  {bitbangPresets.map((preset) => <option key={preset} value={preset}>{preset}</option>)}
                </select>
              </label>
              {cfg.extra?.preset && <label className="field">
                <span>Preset symbols</span>
                <input type="number" min={1} max={1024} value={cfg.extra.count ?? 32}
                  onChange={(e) => set({ extra: { ...(cfg.extra ?? {}), count: Number(e.target.value) } })} />
              </label>}
              <div className="hint">Bit 0 drives TX/SDA/MOSI; bit 1 drives SCL/SCLK. The hardware FIFO supports 1024 symbols per burst.</div>
              <button onClick={async () => {
                try { setPreview(await api.generatorPreview({
                  ...cfg, extra: { ...(cfg.extra ?? {}), data_hex: cfg.data_hex },
                })); }
                catch (e: any) { toast('error', e.message); }
              }}>Preview waveform</button>
              <div className="button-row">
                <button onClick={exportBitbangScript}>Export JSON</button>
                <label className="button">
                  Import JSON
                  <input type="file" accept="application/json,.json" hidden
                    onChange={(e) => { void importBitbangScript(e.target.files?.[0]); e.currentTarget.value = ''; }} />
                </label>
              </div>
              {preview && <div className="finding info">
                {preview.count} symbols · {(preview.duration_s * 1e6).toFixed(1)} µs
                <div className="mono">TX {preview.tx_levels.join('')}<br />CLK {preview.clock_levels.join('')}</div>
              </div>}
            </>
          )}

          {cfg.protocol === 'pattern' && (
            <>
              <label className="field">
                <span>Bit rate (bits/s)</span>
                <input type="number" value={cfg.baud} onChange={(e) => set({ baud: Number(e.target.value) })} />
              </label>
              <label className="field">
                <span>Output pin</span>
                <input type="number" min={0} max={15} value={cfg.tx_pin}
                  onChange={(e) => set({ tx_pin: Number(e.target.value) })} />
              </label>
              <div className="hint">
                Bit-banger playback of the data bytes above, MSB-first, one bit per
                {' '}{cfg.baud ? (1e6 / cfg.baud).toFixed(1) : '?'} µs — no protocol framing.
                {status?.device_kind !== 'mock' && ' Mock only; the Bit_Engine has no raw-pattern mode on this firmware.'}
              </div>
            </>
          )}

          {['counter', 'prbs'].includes(cfg.protocol) && (
            <div className="hint">Pattern generator: {cfg.protocol === 'counter'
              ? '16-bit counter across all channels' : 'pseudo-random bits on the output pin'}.
              {status?.device_kind !== 'mock' && ' Mock only.'}</div>
          )}

          <label className="field checkbox">
            <input type="checkbox" checked={cfg.continuous}
              onChange={(e) => set({ continuous: e.target.checked })} />
            <span>Continuous</span>
          </label>

          <div className="button-row">
            <button onClick={runSweep}>Preview parameter sweep</button>
            <button disabled={busy || !controlMode || !canLoopbackCapture}
              onClick={runCaptureSweep}>Run capture-backed sweep</button>
            {sweepResult && <span className={`badge ${sweepResult.failed ? 'badge-na' : 'badge-soft'}`}>
              {sweepResult.passed}/{sweepResult.count} variants valid
            </span>}
          </div>
          {sweepResult && <div className="sweep-results">
            {sweepResult.rows.map((row: any, index: number) => <div key={index} className="hint">
              {row.session_id && <button className="slim" onClick={async () => {
                await openSession(row.session_id);
                setPage('capture');
              }}>Open capture</button>}
              {index + 1}. {row.protocol} · {row.status}{row.error ? ` · ${row.error}` : ''}
            </div>)}
          </div>}

          <div className="button-row">
            <button className="primary" disabled={busy || !controlMode || !canStandaloneSend} onClick={() => send(false)}>Send</button>
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
            <li>SPI loops MOSI/SCLK into the capture on hardware (send + capture only, no CS/MISO); mock simulates full SCLK/MOSI/MISO/CS on CH4-7.</li>
            <li>SWD uses the existing SWCLK/SWDIO two-output route and records decoded transaction events during Send + capture.</li>
            <li>Raw Bit Banger mode drives TX/SDA/MOSI and SCL/SCLK from a bounded 2-bit symbol list.</li>
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
