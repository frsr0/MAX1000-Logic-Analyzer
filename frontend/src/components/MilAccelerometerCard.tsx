import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { ReconnectingSocket } from '../api/websocket';
import type { MilAccelerometerConfig, MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';

const POLL_INTERVAL_MS = 100;
const SAMPLE_FLUSH_DEADLINE_MS = 8;
// Keep a low-rate health poll while stopped so reconnects, another panel's
// Start action, or a backend restart can resynchronize this card. Once the
// service reports tracking, switch back to the responsive live cadence.
const IDLE_POLL_INTERVAL_MS = 1000;

const DEFAULT_CONFIG: MilAccelerometerConfig = {
  output_data_rate_hz: 10,
  full_scale_g: 2,
  operating_mode: 'high_resolution',
  bdu: true,
  endian: 'little',
  self_test: 'normal',
  x_enabled: true,
  y_enabled: true,
  z_enabled: true,
};

const ODR_OPTIONS: MilAccelerometerConfig['output_data_rate_hz'][] =
  [1, 10, 25, 50, 100, 200, 400];
const SCALE_OPTIONS: MilAccelerometerConfig['full_scale_g'][] = [2, 4, 8, 16];

interface Orientation {
  pitch: number;
  roll: number;
}

function orientationFor(sample: MilAccelerometerStatus['sample']): Orientation {
  if (!sample) return { pitch: 0, roll: 0 };
  // A stationary accelerometer measures gravity. Keep the board level when
  // gravity is +Z, and use X/Y to tilt it around its two screen axes.
  return {
    pitch: Math.atan2(-sample.x_g, Math.hypot(sample.y_g, sample.z_g)) * 180 / Math.PI,
    roll: Math.atan2(sample.y_g, sample.z_g) * 180 / Math.PI,
  };
}

function signed(value: number, digits = 3): string {
  if (!Number.isFinite(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

function rate(value: number | undefined, unit: string): string {
  if (!Number.isFinite(value) || !value) return '—';
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
}

function sampleAge(timestamp: number | null | undefined): string {
  if (!timestamp || !Number.isFinite(timestamp)) return '—';
  const age = Math.max(0, (Date.now() / 1000) - timestamp);
  return age < 1 ? `${Math.round(age * 1000)} ms ago` : `${age.toFixed(1)} s ago`;
}

function relativeOrientation(current: Orientation, zero: Orientation | null): Orientation {
  return {
    pitch: current.pitch - (zero?.pitch ?? 0),
    roll: current.roll - (zero?.roll ?? 0),
  };
}

export function MilAccelerometerCard() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<MilAccelerometerStatus | null>(null);
  const [config, setConfig] = useState<MilAccelerometerConfig>(DEFAULT_CONFIG);
  const [busy, setBusy] = useState(false);
  const [configBusy, setConfigBusy] = useState(false);
  const [zero, setZero] = useState<Orientation | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.milAccelerometerStatus()
      .then((next) => { if (alive) { setStatus(next); setRequestError(null); } })
      .catch((error: unknown) => {
        if (alive) setRequestError(error instanceof Error ? error.message : 'Unable to read accelerometer status');
      });
    // Configuration is a separate resource so status polling remains cheap;
    // older backends may not expose it, in which case the documented defaults
    // keep the controls usable.
    api.milAccelerometerConfig()
      .then((next) => { if (alive) setConfig(next); })
      .catch(() => { /* configuration is optional on older backends */ });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    let inFlight = false;

    const poll = () => {
      // Keep a steady UI cadence without allowing a slow request to pile up
      // stale responses. A completion-chained timeout adds request latency to
      // every interval (and is especially visible through the dev proxy).
      if (inFlight) return;
      inFlight = true;
      api.milAccelerometerStatus()
        .then((next) => { if (alive) {
          setStatus((previous) => {
            // A REST response can have started before a newer websocket sample
            // arrived. Never move the model backwards to that older sample.
            if (previous?.sample?.timestamp && next.sample?.timestamp
                && next.sample.timestamp < previous.sample.timestamp) {
              return { ...next, sample: previous.sample,
                last_sample_at: previous.last_sample_at };
            }
            return next;
          });
          setRequestError(null);
        } })
        .catch((error: unknown) => {
          if (alive) setRequestError(error instanceof Error ? error.message : 'Accelerometer update failed');
        })
        .finally(() => {
          inFlight = false;
        });
    };

    const timer = window.setInterval(
      poll, status?.running ? POLL_INTERVAL_MS : IDLE_POLL_INTERVAL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [status?.running]);

  useEffect(() => {
    let alive = true;
    let animationFrame: number | null = null;
    let flushTimer: number | null = null;
    let pending: Record<string, any> | null = null;

    const handleMessage = (message: Record<string, any>) => {
      if (!alive || message.type !== 'accelerometer_sample' || !message.data?.sample) return;
      // This mailbox is intentionally only one item deep. The worker can
      // continue receiving and parsing every sample while React is busy; a
      // saturated display consumes the newest reading on its next paint.
      pending = message.data;
      if (animationFrame === null) {
        animationFrame = window.requestAnimationFrame(flush);
        // A 60 Hz display can leave RAF waiting almost one full frame when a
        // packet lands just after paint. Bound that tail latency while still
        // coalescing bursts to at most one React update per 8 ms.
        flushTimer = window.setTimeout(flush, SAMPLE_FLUSH_DEADLINE_MS);
      }
    };

    const flush = () => {
      if (animationFrame !== null) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
      }
      if (flushTimer !== null) {
        window.clearTimeout(flushTimer);
        flushTimer = null;
      }
      const next = pending;
      pending = null;
      if (!alive || !next) return;
      // Coalesce a burst of decoded samples into the browser's paint cadence.
      // The socket remains lossless enough for status (the newest sample wins)
      // while React only renders once per animation frame.
      setStatus((previous) => previous ? {
        ...previous,
        sample: next.sample,
        last_sample_at: next.last_sample_at ?? next.sample.timestamp,
        samples_read: next.samples_read ?? previous.samples_read,
        host_read_rate_hz: next.host_read_rate_hz ?? previous.host_read_rate_hz,
        host_data_rate_bytes_per_s: next.host_data_rate_bytes_per_s
          ?? previous.host_data_rate_bytes_per_s,
        measured_rate_hz: next.measured_rate_hz ?? previous.measured_rate_hz,
        data_rate_bytes_per_s: next.data_rate_bytes_per_s
          ?? previous.data_rate_bytes_per_s,
      } : previous);
    };

    let cleanupStream: () => void;
    if (typeof Worker !== 'undefined') {
      const worker = new Worker(
        new URL('../workers/accelerometerStream.worker.ts', import.meta.url),
        { type: 'module' },
      );
      worker.onmessage = (event: MessageEvent<Record<string, any>>) => {
        handleMessage(event.data);
      };
      cleanupStream = () => worker.terminate();
    } else {
      // Keep the test/legacy-browser fallback functional. Modern browsers use
      // the worker above, so socket parsing cannot compete with React renders.
      const socket = new ReconnectingSocket('/ws/accelerometer');
      const unsubscribe = socket.subscribe(handleMessage);
      cleanupStream = () => { unsubscribe(); socket.close(); };
    }
    return () => {
      alive = false;
      cleanupStream();
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      if (flushTimer !== null) window.clearTimeout(flushTimer);
    };
  }, []);

  const current = useMemo(() => orientationFor(status?.sample ?? null), [status?.sample]);
  const orientation = useMemo(() => relativeOrientation(current, zero), [current, zero]);
  const hasSample = Boolean(status?.sample);
  const available = status?.available ?? false;
  const running = status?.running ?? false;

  const startStop = async (action: 'start' | 'stop') => {
    setBusy(true);
    try {
      const next = action === 'start'
        ? await api.milAccelerometerStart()
        : await api.milAccelerometerStop();
      setStatus(next);
      const completed = action === 'start' ? next.running : !next.running;
      if (!completed) {
        const message = next.last_error
          || (action === 'start' ? 'Accelerometer tracking did not start' : 'Accelerometer tracking did not stop');
        setRequestError(message);
        toast('error', message);
      } else {
        setRequestError(null);
        toast('success', action === 'start' ? 'Accelerometer tracking started' : 'Accelerometer tracking stopped');
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Accelerometer command failed';
      setRequestError(message);
      toast('error', message);
    } finally {
      setBusy(false);
    }
  };

  const updateConfig = async (patch: Partial<MilAccelerometerConfig>) => {
    if (running || configBusy || !controlMode) return;
    const next = { ...config, ...patch };
    setConfigBusy(true);
    try {
      const saved = await api.milAccelerometerUpdateConfig(next);
      setConfig(saved);
      setStatus((currentStatus) => currentStatus
        ? { ...currentStatus, sample_rate_hz: saved.output_data_rate_hz }
        : currentStatus);
      setRequestError(null);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Unable to update accelerometer configuration';
      setRequestError(message);
      toast('error', message);
    } finally {
      setConfigBusy(false);
    }
  };

  const controlsDisabled = running || busy || configBusy || !controlMode;

  const setZeroOrientation = () => {
    setZero(current);
  };

  return (
    <section className="card mil-accelerometer-card" aria-labelledby="mil-accelerometer-title">
      <div className="card-head">
        <div>
          <h3 id="mil-accelerometer-title">Live accelerometer</h3>
          <div className="hint">Gravity orientation · live decoded samples</div>
        </div>
        <span className={`badge ${running ? 'badge-hw' : 'badge-soft'}`}>
          {status === null && !requestError ? 'checking' : running ? 'tracking' : available ? 'ready' : 'unavailable'}
        </span>
      </div>

      <div className="mil-accelerometer-body">
        <div className="mil-accelerometer-visual">
          <div
            className="mil-accel-stage"
            role="img"
            aria-label={`Accelerometer board, pitch ${orientation.pitch.toFixed(1)} degrees, roll ${orientation.roll.toFixed(1)} degrees`}
          >
            <div
              className="mil-accel-board"
              style={{ transform: `perspective(520px) rotateX(${orientation.pitch}deg) rotateY(${orientation.roll}deg)` }}
            >
              <div className="mil-accel-board-edge" />
              <div className="mil-accel-silk mil-accel-silk-a">MAX1000</div>
              <div className="mil-accel-chip"><span>ACCEL</span><i /></div>
              <div className="mil-accel-axis mil-accel-axis-x">X</div>
              <div className="mil-accel-axis mil-accel-axis-y">Y</div>
              <div className="mil-accel-axis mil-accel-axis-z">Z</div>
              <div className="mil-accel-led" />
            </div>
          </div>
          <div className="mil-accel-orientation" aria-live="polite">
            <span><b>Pitch</b> {signed(orientation.pitch, 1)}°</span>
            <span><b>Roll</b> {signed(orientation.roll, 1)}°</span>
          </div>
        </div>

        <div className="mil-accelerometer-readings">
          <div className="mil-accel-axis-readout">
            <div><span className="axis-dot axis-x" />X</div><strong>{signed(status?.sample?.x_g ?? NaN)} <small>g</small></strong>
            <div><span className="axis-dot axis-y" />Y</div><strong>{signed(status?.sample?.y_g ?? NaN)} <small>g</small></strong>
            <div><span className="axis-dot axis-z" />Z</div><strong>{signed(status?.sample?.z_g ?? NaN)} <small>g</small></strong>
          </div>
          <div className="mil-accel-meta">
            <span>Requested sensor ODR</span><strong>{rate(config.output_data_rate_hz, 'Hz')}</strong>
            <span>Effective sensor ODR</span><strong>{rate(status?.effective_odr_hz ?? status?.effective_config?.output_data_rate_hz, 'Hz')}</strong>
            <span>Host XYZ read rate</span><strong>{rate(status?.host_read_rate_hz ?? status?.measured_rate_hz, 'samples/s')}</strong>
            <span>Host payload rate</span><strong>{rate(status?.host_data_rate_bytes_per_s ?? status?.data_rate_bytes_per_s, 'bytes/s')}</strong>
            <span>Samples read</span><strong>{(status?.samples_read ?? 0).toLocaleString()}</strong>
            <span>Last sample</span><strong>{sampleAge(status?.last_sample_at ?? status?.sample?.timestamp)}</strong>
            <small className="hint mil-accel-rate-note">Host rates measure completed USB/I²C reads and can be lower than the sensor ODR.</small>
          </div>
          <div className="mil-accel-readback" aria-label="Accelerometer hardware readback">
            <div className="mil-accel-readback-head">
              <span>Hardware readback</span>
              <small>{status?.register_readback
                ? `CTRL1 0x${status.register_readback.ctrl1.toString(16).padStart(2, '0').toUpperCase()} · CTRL4 0x${status.register_readback.ctrl4.toString(16).padStart(2, '0').toUpperCase()}`
                : 'not available until tracking starts'}</small>
            </div>
            {status?.effective_config ? (
              <div className="mil-accel-readback-values">
                <span>ODR <b>{rate(status.effective_config.output_data_rate_hz, 'Hz')}</b></span>
                <span>Scale <b>±{status.effective_config.full_scale_g} g</b></span>
                <span>Mode <b>{status.effective_config.operating_mode.replace('_', ' ')}</b></span>
                <span>Bytes <b>{status.effective_config.endian}-endian</b></span>
                <span>BDU <b>{status.effective_config.bdu ? 'on' : 'off'}</b></span>
                <span>Axes <b>{['x', 'y', 'z'].filter((axis) => status.effective_config?.[`${axis}_enabled` as 'x_enabled' | 'y_enabled' | 'z_enabled']).join('').toUpperCase() || 'none'}</b></span>
              </div>
            ) : <div className="hint">Start tracking to verify the settings applied to the sensor.</div>}
          </div>
          <div className="mil-accel-config" aria-label="Accelerometer settings">
            <label className="field"><span>Output data rate</span>
              <select aria-label="Output data rate" value={config.output_data_rate_hz}
                      disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ output_data_rate_hz: Number(e.target.value) as MilAccelerometerConfig['output_data_rate_hz'] })}>
                {ODR_OPTIONS.map((value) => <option key={value} value={value}>{value.toLocaleString()} Hz</option>)}
              </select>
            </label>
            <label className="field"><span>Full-scale range</span>
              <select aria-label="Full-scale range" value={config.full_scale_g}
                      disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ full_scale_g: Number(e.target.value) as MilAccelerometerConfig['full_scale_g'] })}>
                {SCALE_OPTIONS.map((value) => <option key={value} value={value}>±{value} g</option>)}
              </select>
            </label>
            <label className="field"><span>Operating mode</span>
              <select aria-label="Operating mode" value={config.operating_mode}
                      disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ operating_mode: e.target.value as MilAccelerometerConfig['operating_mode'] })}>
                <option value="normal">Normal</option>
                <option value="high_resolution">High resolution</option>
                <option value="low_power">Low power</option>
              </select>
            </label>
            <label className="field"><span>Byte order</span>
              <select aria-label="Byte order" value={config.endian}
                      disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ endian: e.target.value as MilAccelerometerConfig['endian'] })}>
                <option value="little">Little-endian</option>
                <option value="big">Big-endian</option>
              </select>
            </label>
            <label className="field"><span>Self-test</span>
              <select aria-label="Self-test" value={config.self_test}
                      disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ self_test: e.target.value as MilAccelerometerConfig['self_test'] })}>
                <option value="normal">Disabled</option>
                <option value="positive">Positive</option>
                <option value="negative">Negative</option>
              </select>
            </label>
            <label className="field checkbox"><input type="checkbox" aria-label="Block data update (BDU)"
                      checked={config.bdu} disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ bdu: e.target.checked })} /><span>Block data update (BDU)</span></label>
            <label className="field checkbox"><input type="checkbox" aria-label="Enable X axis"
                      checked={config.x_enabled} disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ x_enabled: e.target.checked })} /><span>Enable X axis</span></label>
            <label className="field checkbox"><input type="checkbox" aria-label="Enable Y axis"
                      checked={config.y_enabled} disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ y_enabled: e.target.checked })} /><span>Enable Y axis</span></label>
            <label className="field checkbox"><input type="checkbox" aria-label="Enable Z axis"
                      checked={config.z_enabled} disabled={controlsDisabled}
                      onChange={(e) => void updateConfig({ z_enabled: e.target.checked })} /><span>Enable Z axis</span></label>
          </div>
          <div className="button-row wrap">
            {!running ? (
              <button className="primary" disabled={!available || busy || !controlMode} onClick={() => void startStop('start')}>
                Start tracking
              </button>
            ) : (
              <button className="danger" disabled={busy || !controlMode} onClick={() => void startStop('stop')}>
                Stop tracking
              </button>
            )}
            <button disabled={!hasSample} onClick={setZeroOrientation}>
              {zero ? 'Re-zero board' : 'Set current as zero'}
            </button>
            {zero && <button className="slim" onClick={() => setZero(null)}>Clear zero</button>}
          </div>
          {!controlMode && <div className="hint">Read-only mode: request control to start or stop tracking.</div>}
          {requestError && <div className="finding warning" role="alert">{requestError}</div>}
          {status?.last_error && status.last_error !== requestError && (
            <div className="finding warning" role="alert">{status.last_error}</div>
          )}
          {!status?.sample && !requestError && <div className="hint">No sample yet. Start tracking with the board connected.</div>}
        </div>
      </div>
    </section>
  );
}
