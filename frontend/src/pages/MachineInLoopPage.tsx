// Machine-in-loop emulator: load register-map presets, run responder, inspect
// packet responses before the physical scope bridge is attached.
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { MilCaptureConfig, MilConfig, MilPresetSummary, MilRuntimeStatus, MilTransactionResponse } from '../api/types';
import { useApp } from '../state/appStore';

const READ_MODBUS_17_0100_0002 = '110301000002c767';
const READ_MODBUS_1_0000_0002 = '010300000002c40b';
const READ_UART_0001 = '030001';

function fmtAddress(value: number): string {
  return `0x${value.toString(16).padStart(4, '0')}`;
}

function cleanHex(value: string): string {
  return value.replace(/[^0-9a-fA-F]/g, '').toLowerCase();
}

function bytesFromHex(hex: string): number[] {
  const clean = cleanHex(hex);
  const out = [];
  for (let i = 0; i + 1 < clean.length; i += 2) {
    out.push(parseInt(clean.slice(i, i + 2), 16));
  }
  return out;
}

function modbusCrc(hex: string): string {
  let crc = 0xffff;
  for (const byte of bytesFromHex(hex)) {
    crc ^= byte;
    for (let i = 0; i < 8; i += 1) {
      crc = (crc & 1) ? ((crc >> 1) ^ 0xa001) : (crc >> 1);
    }
  }
  return `${(crc & 0xff).toString(16).padStart(2, '0')}${((crc >> 8) & 0xff).toString(16).padStart(2, '0')}`;
}

function modbusRead(unit: number, address: number, count: number): string {
  const body = [unit, 0x03, address >> 8, address & 0xff, count >> 8, count & 0xff]
    .map((b) => (b & 0xff).toString(16).padStart(2, '0')).join('');
  return body + modbusCrc(body);
}

function modbusWrite(unit: number, address: number, value: number): string {
  const body = [unit, 0x06, address >> 8, address & 0xff, value >> 8, value & 0xff]
    .map((b) => (b & 0xff).toString(16).padStart(2, '0')).join('');
  return body + modbusCrc(body);
}

function commandHex(kind: string, cfg: MilConfig | null): string {
  if (!cfg) return '';
  const first = cfg.registers[0];
  const writable = cfg.registers.find((r) => r.access !== 'ro') ?? first;
  if (cfg.protocol === 'uart') {
    if (kind === 'write') return `06${writable.address.toString(16).padStart(4, '0')}01`;
    return `03${first.address.toString(16).padStart(4, '0')}`;
  }
  if (kind === 'write') return modbusWrite(cfg.unit_id, writable.address, 1);
  if (kind === 'bad-crc') return modbusRead(cfg.unit_id, first.address, 1).slice(0, -2) + '00';
  return modbusRead(cfg.unit_id, first.address, Math.min(2, cfg.registers.length || 1));
}

function commandOptions(cfg: MilConfig | null) {
  if (!cfg) return [{ value: 'custom', label: 'Custom hex' }];
  return [
    { value: 'read', label: 'Read register(s)' },
    { value: 'write', label: 'Write register' },
    { value: 'bad-crc', label: 'Bad CRC / error path' },
    { value: 'custom', label: 'Custom hex' },
  ].filter((o) => cfg.protocol !== 'uart' || o.value !== 'bad-crc');
}

function uartSegments(hex: string, interByteGapUs: number, baud: number) {
  const segments: { x0: number; x1: number; value: number }[] = [];
  const bitUs = 1_000_000 / Math.max(1, baud);
  let t = 0;
  const push = (value: number, dur: number) => {
    segments.push({ x0: t, x1: t + dur, value });
    t += dur;
  };
  push(1, bitUs * 2);
  for (const byte of bytesFromHex(hex)) {
    push(0, bitUs);
    for (let i = 0; i < 8; i += 1) push((byte >> i) & 1, bitUs);
    push(1, bitUs);
    if (interByteGapUs > 0) push(1, interByteGapUs);
  }
  push(1, bitUs * 2);
  return segments;
}

function pathForSegments(
  segments: { x0: number; x1: number; value: number }[],
  scale: number, offsetUs: number, highY: number, lowY: number,
): string {
  if (!segments.length) return '';
  let path = `M ${(segments[0].x0 + offsetUs) * scale} ${segments[0].value ? highY : lowY}`;
  segments.forEach((seg) => {
    const x0 = (seg.x0 + offsetUs) * scale;
    const x1 = (seg.x1 + offsetUs) * scale;
    const y = seg.value ? highY : lowY;
    path += ` L ${x0} ${y} L ${x1} ${y}`;
  });
  return path;
}

function TimelineTrace({ event, cfg }: { event: Record<string, any>; cfg: MilConfig | null }) {
  const width = 720;
  const baud = Number(event.baud ?? cfg?.trigger.baud ?? 115200);
  const interByteGapUs = Number(event.inter_byte_gap_us ?? cfg?.timing.inter_byte_gap_us ?? 0);
  const responseDelayUs = Number(event.response_delay_us ?? cfg?.timing.response_delay_us ?? 0);
  const rx = uartSegments(event.request_hex ?? '', interByteGapUs, baud);
  const tx = uartSegments(event.response_hex ?? '', interByteGapUs, baud);
  const rxEnd = rx.length ? rx[rx.length - 1].x1 : 0;
  const txEnd = tx.length ? rxEnd + responseDelayUs + tx[tx.length - 1].x1 : rxEnd;
  const totalUs = Math.max(txEnd, 1);
  const scale = width / totalUs;
  const txOffset = rxEnd + responseDelayUs;
  const ticks = Array.from({ length: 6 }, (_, i) => i * totalUs / 5);
  const extraChannels = (event.extra_digital_channels ?? cfg?.capture.extra_digital_channels ?? []) as number[];
  return (
    <>
      <div className="mil-timeline">
        <div className="mil-wave-label"><strong>RX</strong><span>CH{event.rx_pin ?? cfg?.trigger.rx_pin ?? 0}</span></div>
        <svg className="mil-wave mil-wave-shared" viewBox={`0 0 ${width} 118`} preserveAspectRatio="none">
          {ticks.map((t) => <line key={t} x1={t * scale} y1="10" x2={t * scale} y2="106" className="grid" />)}
          <path className="rx" d={pathForSegments(rx, scale, 0, 26, 50)} />
          <path className="tx" d={pathForSegments(tx, scale, txOffset, 76, 100)} />
          <line x1={rxEnd * scale} y1="12" x2={rxEnd * scale} y2="108" className="marker" />
          <line x1={txOffset * scale} y1="12" x2={txOffset * scale} y2="108" className="marker response" />
        </svg>
        <div className="mil-wave-label"><strong>TX</strong><span>CH{event.tx_pin ?? cfg?.trigger.tx_pin ?? 1}</span></div>
      </div>
      <div className="mil-timeline-meta">
        <span>RX <span className="mono">{event.request_hex}</span></span>
        <span>TX <span className="mono">{event.response_hex || '(none)'}</span></span>
        <span>delay {responseDelayUs.toLocaleString()} us</span>
        <span>window {event.capture_mode ?? cfg?.capture.mode} · max {event.max_response_bytes ?? cfg?.capture.max_response_bytes} bytes</span>
      </div>
      {!!extraChannels.length && (
        <div className="mil-extra-group">
          <span>Extra signals</span>
          {extraChannels.map((ch) => <span key={ch} className="chip">CH{ch}</span>)}
        </div>
      )}
    </>
  );
}

function defaultRequest(status: MilRuntimeStatus | null): string {
  const protocol = status?.config?.protocol;
  if (protocol === 'rs485_modbus') return READ_MODBUS_17_0100_0002;
  if (protocol === 'modbus_uart') return READ_MODBUS_1_0000_0002;
  return READ_UART_0001;
}

export function MachineInLoopPage() {
  const { toast, controlMode, openSession, setPage } = useApp();
  const [presets, setPresets] = useState<MilPresetSummary[]>([]);
  const [status, setStatus] = useState<MilRuntimeStatus | null>(null);
  const [selected, setSelected] = useState('modbus-rtu-demo');
  const [path, setPath] = useState('');
  const [requestHex, setRequestHex] = useState(READ_MODBUS_1_0000_0002);
  const [command, setCommand] = useState('read');
  const [params, setParams] = useState({ response_delay_us: 1000, inter_byte_gap_us: 0, jitter_us: 0 });
  const [captureCfg, setCaptureCfg] = useState<MilCaptureConfig>({
    mode: 'auto',
    sample_rate: 14_000_000,
    max_response_bytes: 64,
    manual_post_packet_us: 1000,
    extra_digital_channels: [] as number[],
  });
  const [extraChannels, setExtraChannels] = useState('');
  const [stressCount, setStressCount] = useState(25);
  const [stressResult, setStressResult] = useState('');
  const [result, setResult] = useState<MilTransactionResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const cfg = status?.config ?? null;
  const running = status?.running ?? false;
  const registerRows = useMemo(() => cfg?.registers ?? [], [cfg]);
  const transactions = useMemo(() => (status?.events ?? [])
    .filter((e) => e.kind === 'transaction')
    .slice()
    .reverse(), [status]);
  const captureEstimate = useMemo(() => {
    const baud = Math.max(1, cfg?.trigger.baud ?? 115200);
    const byteUs = 10_000_000 / baud;
    const rxBytes = bytesFromHex(requestHex).length;
    const rxUs = rxBytes * byteUs;
    const responseUs = captureCfg.max_response_bytes * byteUs;
    const tailUs = captureCfg.manual_post_packet_us;
    const totalUs = rxUs + params.response_delay_us + responseUs + tailUs;
    const samples = Math.ceil(totalUs * captureCfg.sample_rate / 1_000_000);
    return { byteUs, totalUs, samples };
  }, [cfg, requestHex, captureCfg, params.response_delay_us]);

  const refresh = async () => {
    const [presetRes, statusRes] = await Promise.all([api.milPresets(), api.milStatus()]);
    setPresets(presetRes.presets);
    setStatus(statusRes);
    if (statusRes.config) {
      setParams(statusRes.config.timing);
      setCaptureCfg(statusRes.config.capture);
      setExtraChannels(statusRes.config.capture.extra_digital_channels.join(','));
      setRequestHex(commandHex(command, statusRes.config) || defaultRequest(statusRes));
    }
  };

  useEffect(() => {
    refresh().catch(() => {});
    const t = setInterval(() => api.milStatus().then(setStatus).catch(() => {}), 2000);
    return () => clearInterval(t);
  }, []);

  const loadPreset = async () => {
    setBusy(true);
    try {
      const next = await api.milLoad(path ? { path } : { preset_id: selected });
      setStatus(next);
      setParams(next.config?.timing ?? params);
      if (next.config?.capture) {
        setCaptureCfg(next.config.capture);
        setExtraChannels(next.config.capture.extra_digital_channels.join(','));
      }
      setRequestHex(commandHex(command, next.config ?? null) || defaultRequest(next));
      setResult(null);
      toast('success', 'MIL preset loaded');
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const startStop = async () => {
    setBusy(true);
    try {
      const next = running ? await api.milStop() : await api.milStart();
      setStatus(next);
      toast('success', running ? 'MIL emulator stopped' : 'MIL emulator running');
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const sendProbe = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await api.milTransaction({ request_hex: cleanHex(requestHex) }));
      setStatus(await api.milStatus());
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const applyParams = async () => {
    if (!cfg) return;
    setBusy(true);
    try {
      const extra = extraChannels.split(',')
        .map((v) => Number(v.trim()))
        .filter((v) => Number.isInteger(v) && v >= 0 && v <= 15);
      const nextCfg: MilConfig = {
        ...cfg,
        timing: params,
        capture: { ...captureCfg, extra_digital_channels: extra },
      };
      const next = await api.milLoad({ config: nextCfg });
      setStatus(await api.milStart().catch(() => next));
      setCaptureCfg(nextCfg.capture);
      toast('success', 'MIL capture settings applied');
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const runStress = async () => {
    setBusy(true);
    let passed = 0;
    let failed = 0;
    try {
      for (let i = 0; i < stressCount; i += 1) {
        const r = await api.milTransaction({
          request_hex: cleanHex(requestHex),
          capture_evidence: false,
        });
        if (r.action === 'exception' || !r.response_hex) failed += 1;
        else passed += 1;
      }
      setStatus(await api.milStatus());
      setStressResult(`${passed}/${stressCount} passed, ${failed} failed`);
      toast(failed ? 'warning' : 'success', `Stress test: ${passed}/${stressCount} passed`);
    } catch (e: any) {
      toast('error', e.message);
    } finally { setBusy(false); }
  };

  const openEvidence = async (sessionId?: string | null) => {
    if (!sessionId) return;
    await openSession(sessionId);
    setPage('capture');
  };

  return (
    <div className="page">
      <div className="page-head">
        <h2>Machine in loop</h2>
        <span className={`badge ${running ? 'badge-hw' : 'badge-soft'}`}>
          {running ? 'listening' : 'stopped'}
        </span>
        {cfg && <span className="badge badge-soft">{cfg.protocol.replace('_', ' ')}</span>}
      </div>

      <div className="mil-layout">
        <div className="card">
          <h3>Preset</h3>
          <label className="field">
            <span>Device / protocol file</span>
            <select value={selected} onChange={(e) => {
              setSelected(e.target.value);
              setPath('');
            }}>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} - {p.protocol.replace('_', ' ')} [{p.source}]
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Preset path (.json)</span>
            <input value={path} placeholder="data/mil/device.json or absolute path"
              onChange={(e) => setPath(e.target.value)} />
          </label>
          <div className="button-row wrap">
            <button className="primary" disabled={busy || !controlMode} onClick={loadPreset}>Load</button>
            <button className="primary" disabled={busy || !controlMode} onClick={startStop}>
              {running ? 'Stop emulator' : 'Start emulator'}
            </button>
          </div>
          {cfg && (
            <div className="mil-summary">
              <span>Name</span><strong>{cfg.name}</strong>
              <span>Trigger</span><strong>{cfg.trigger.mode.replace('_', ' ')}</strong>
              <span>RX / TX</span><strong>CH{cfg.trigger.rx_pin} / CH{cfg.trigger.tx_pin}</strong>
              <span>Baud</span><strong>{cfg.trigger.baud.toLocaleString()}</strong>
              <span>Unit</span><strong>{cfg.unit_id}</strong>
              <span>RS485 DE</span><strong>{cfg.trigger.rs485_de_pin ?? '-'}</strong>
            </div>
          )}
          {cfg?.description && <div className="hint">{cfg.description}</div>}
        </div>

        <div className="card">
          <h3>Commands</h3>
          <label className="field">
            <span>Command</span>
            <select value={command} onChange={(e) => {
              const next = e.target.value;
              setCommand(next);
              if (next !== 'custom') setRequestHex(commandHex(next, cfg));
            }}>
              {commandOptions(cfg).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="field">
            <span>Request hex</span>
            <input className="mono" value={requestHex}
              onChange={(e) => {
                setCommand('custom');
                setRequestHex(cleanHex(e.target.value));
              }} />
          </label>
          <div className="button-row wrap">
            <button disabled={!running || busy || !controlMode} onClick={sendProbe}>Send to emulator</button>
            <button disabled={!cfg} onClick={() => setRequestHex(commandHex('read', cfg))}>Example read</button>
          </div>
          {result && (
            <div className={`finding ${result.action === 'exception' ? 'warning' : 'info'}`}>
              <strong>{result.action.toUpperCase()}</strong> {result.detail}<br />
              request: <span className="mono">{result.request_hex}</span><br />
              response: <span className="mono">{result.response_hex || '(none)'}</span>
            </div>
          )}
          {result?.session_id && (
            <button onClick={() => openEvidence(result.session_id)}>
              Open TX/RX capture
            </button>
          )}
          <div className="hint">
            The control plane is ready for the scope/generator bridge: decode RX packets,
            call the MIL responder, then transmit the returned bytes on TX.
          </div>
        </div>

        <div className="card">
          <h3>MIL parameters</h3>
          <label className="field"><span>Response delay (us)</span>
            <input type="number" min={0} value={params.response_delay_us}
              onChange={(e) => setParams({ ...params, response_delay_us: Number(e.target.value) })} /></label>
          <label className="field"><span>Inter-byte spacing (us)</span>
            <input type="number" min={0} value={params.inter_byte_gap_us}
              onChange={(e) => setParams({ ...params, inter_byte_gap_us: Number(e.target.value) })} /></label>
          <label className="field"><span>Response jitter budget (us)</span>
            <input type="number" min={0} value={params.jitter_us}
              onChange={(e) => setParams({ ...params, jitter_us: Number(e.target.value) })} /></label>
          <h3>Deep capture window</h3>
          <label className="field"><span>Capture sizing</span>
            <select value={captureCfg.mode}
              onChange={(e) => setCaptureCfg({ ...captureCfg, mode: e.target.value as 'auto' | 'manual' })}>
              <option value="auto">Auto from packet + max response</option>
              <option value="manual">Manual post-packet tail</option>
            </select></label>
          <label className="field"><span>Deep digital capture rate</span>
            <input type="number" min={1_000_000} value={captureCfg.sample_rate}
              onChange={(e) => setCaptureCfg({ ...captureCfg, sample_rate: Number(e.target.value) })} /></label>
          <label className="field"><span>Max response bytes</span>
            <input type="number" min={0} max={4096} value={captureCfg.max_response_bytes}
              onChange={(e) => setCaptureCfg({ ...captureCfg, max_response_bytes: Number(e.target.value) })} /></label>
          <label className="field"><span>Capture after packet (us)</span>
            <input type="number" min={0} value={captureCfg.manual_post_packet_us}
              onChange={(e) => setCaptureCfg({ ...captureCfg, manual_post_packet_us: Number(e.target.value) })} /></label>
          <label className="field"><span>Extra digital channels</span>
            <input value={extraChannels} placeholder="4,5,6"
              onChange={(e) => setExtraChannels(e.target.value.replace(/[^0-9, ]/g, ''))} /></label>
          <div className={`finding ${captureEstimate.samples > 1_000_000 ? 'warning' : 'info'}`}>
            Window {Math.ceil(captureEstimate.totalUs).toLocaleString()} us · {captureEstimate.samples.toLocaleString()} samples
            {captureEstimate.samples > 1_000_000 ? ' · exceeds 1,000,000 sample budget' : ''}
          </div>
          <button disabled={!cfg || busy || !controlMode} onClick={applyParams}>Apply MIL settings</button>
          <h3>Tests</h3>
          <label className="field"><span>Stress count</span>
            <input type="number" min={1} max={500} value={stressCount}
              onChange={(e) => setStressCount(Number(e.target.value))} /></label>
          <button disabled={!running || busy || !controlMode} onClick={runStress}>Run stress test</button>
          {stressResult && <div className="finding info">{stressResult}</div>}
        </div>
      </div>

      <div className="card mil-registers">
        <h3>Register map</h3>
        {!registerRows.length && <div className="hint">Load a preset to inspect registers.</div>}
        {!!registerRows.length && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Address</th>
                  <th>Name</th>
                  <th>Access</th>
                  <th>Width</th>
                  <th>Value</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {registerRows.map((r) => (
                  <tr key={`${r.address}-${r.name}`}>
                    <td className="mono">{fmtAddress(r.address)}</td>
                    <td>{r.name}</td>
                    <td>{r.access.toUpperCase()}</td>
                    <td>{r.width}</td>
                    <td className="mono">0x{r.value.toString(16)}</td>
                    <td>{r.description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card mil-registers">
        <h3>TX / RX waveforms</h3>
        {!transactions.length && <div className="hint">Run a probe transaction to see request and response waveforms.</div>}
        {transactions.map((e, i) => (
          <div className="mil-transaction" key={`${e.ts}-${i}`}>
            <div className="decoder-head">
              <strong>{e.message}</strong>
              <span className="badge badge-soft">{e.protocol}</span>
              <span className="hint">{Number(e.baud ?? 0).toLocaleString()} baud</span>
              {e.rs485_de_pin !== null && e.rs485_de_pin !== undefined && (
                <span className="hint">DE CH{e.rs485_de_pin}</span>
              )}
            </div>
            <TimelineTrace event={e} cfg={cfg} />
            {e.session_id && (
              <button className="slim" onClick={() => openEvidence(e.session_id)}>
                Open capture session
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="card log-card">
        <h3>Emulator events</h3>
        <div className="log-view">
          {(status?.events ?? []).slice().reverse().map((e, i) => (
            <div key={`${e.ts}-${i}`} className="log-line">
              <span className="log-time">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
              <span className="log-level">{e.kind}</span>
              <span>{e.message}</span>
              {e.response_hex && <span className="mono">{e.response_hex}</span>}
            </div>
          ))}
          {!status?.events?.length && <div className="hint">No emulator events yet.</div>}
        </div>
      </div>
    </div>
  );
}
