import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';

const POLL_INTERVAL_MS = 100;

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

function relativeOrientation(current: Orientation, zero: Orientation | null): Orientation {
  return {
    pitch: current.pitch - (zero?.pitch ?? 0),
    roll: current.roll - (zero?.roll ?? 0),
  };
}

export function MilAccelerometerCard() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<MilAccelerometerStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [zero, setZero] = useState<Orientation | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.milAccelerometerStatus()
      .then((next) => { if (alive) { setStatus(next); setRequestError(null); } })
      .catch((error: unknown) => {
        if (alive) setRequestError(error instanceof Error ? error.message : 'Unable to read accelerometer status');
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!status?.running) return undefined;
    let alive = true;
    let timer: number | undefined;

    const poll = () => {
      api.milAccelerometerStatus()
        .then((next) => { if (alive) { setStatus(next); setRequestError(null); } })
        .catch((error: unknown) => {
          if (alive) setRequestError(error instanceof Error ? error.message : 'Accelerometer update failed');
        })
        .finally(() => {
          // Queue from completion rather than using setInterval so a slow
          // hardware read can never overlap the next status request.
          if (alive) timer = window.setTimeout(poll, POLL_INTERVAL_MS);
        });
    };

    timer = window.setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [status?.running]);

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

  const setZeroOrientation = () => {
    setZero(current);
  };

  return (
    <section className="card mil-accelerometer-card" aria-labelledby="mil-accelerometer-title">
      <div className="card-head">
        <div>
          <h3 id="mil-accelerometer-title">Live accelerometer</h3>
          <div className="hint">Gravity orientation · updates at 10 Hz</div>
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
            <span>Sample rate</span><strong>{(status?.sample_rate_hz ?? 0).toLocaleString()} Hz</strong>
            <span>Samples read</span><strong>{(status?.samples_read ?? 0).toLocaleString()}</strong>
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
