import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { ReconnectingSocket } from '../api/websocket';
import type { MilAccelerometerConfig, MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';
import { AdaptivePoseFilter, orientationFor, type Orientation } from './accelerometerPose';

const POLL_INTERVAL_MS = 100;
// The sensor stream can be several hundred samples per second. Present the
// newest pose once per display frame instead of restarting a CSS transition
// for every sample (which makes the compositor look like it is jumping).
// The transport reports its measured decoded rate separately. Keep the board
// on a display-frame target while the worker can ingest every validated sample
// from a faster sensor stream.
const BOARD_POSE_SMOOTHING_MS = 80;
const BOARD_POSE_SETTLE_EPSILON_DEG = 0.01;
// A real hand sweep can be fast, but a decoded outlier should never rotate
// the model by tens of degrees in one paint. Cap presentation motion without
// changing the raw XYZ values shown in the diagnostic readout.
const BOARD_POSE_MAX_RATE_DPS = 540;
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

const STANDARD_ODR_OPTIONS: MilAccelerometerConfig['output_data_rate_hz'][] =
  [1, 10, 25, 50, 100, 200, 400, 1344];
const LOW_POWER_ODR_OPTIONS: MilAccelerometerConfig['output_data_rate_hz'][] =
  [1, 10, 25, 50, 100, 200, 400, 1620, 5376];
const SCALE_OPTIONS: MilAccelerometerConfig['full_scale_g'][] = [2, 4, 8, 16];

function odrOptionsFor(mode: MilAccelerometerConfig['operating_mode']) {
  return mode === 'low_power' ? LOW_POWER_ODR_OPTIONS : STANDARD_ODR_OPTIONS;
}

function normalizeConfigForMode(config: MilAccelerometerConfig): MilAccelerometerConfig {
  const options = odrOptionsFor(config.operating_mode);
  if (options.includes(config.output_data_rate_hz)) return config;
  return {
    ...config,
    // Preserve the highest trustworthy ODR when a mode switch changes the
    // meaning of CTRL_REG1 code 9 (1.344 kHz ↔ 5.376 kHz).
    output_data_rate_hz: config.operating_mode === 'low_power' ? 5376 : 1344,
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

function latency(value: number | undefined): string {
  if (!Number.isFinite(value) || value === undefined) return '—';
  return `${value.toFixed(1)} ms`;
}

function backlog(value: number | undefined): string {
  if (!Number.isFinite(value) || value === undefined) return '—';
  return `${value.toLocaleString()} samples`;
}

function frontendRenderLatency(timing: Record<string, any> | null | undefined): string {
  const received = timing?.frontend_received_at_ms;
  const rendered = timing?.frontend_rendered_at_ms;
  return latency(Number.isFinite(received) && Number.isFinite(rendered)
    ? rendered - received : undefined);
}

function relativeOrientation(current: Orientation, zero: Orientation | null): Orientation {
  return {
    pitch: current.pitch - (zero?.pitch ?? 0),
    roll: current.roll - (zero?.roll ?? 0),
  };
}

function boardTransform(orientation: Orientation): string {
  // U4's pin-1 corner is at the lower-left in this USB-right top view.  From
  // the LIS3DH package axis diagram, +X therefore faces the lower board edge
  // and +Y faces USB.  `pitch` is derived from sensor X and rotates about the
  // USB axis (CSS X); `roll` is derived from sensor Y and rotates about the
  // header axis (CSS Y).
  return `perspective(520px) rotateX(${orientation.pitch}deg) rotateY(${orientation.roll}deg)`;
}

function shortestAngleDelta(target: number, current: number): number {
  let delta = target - current;
  while (delta > 180) delta -= 360;
  while (delta < -180) delta += 360;
  return delta;
}

function advanceBoardOrientation(
  current: Orientation,
  target: Orientation,
  elapsedMs: number,
): Orientation {
  const alpha = 1 - Math.exp(-elapsedMs / BOARD_POSE_SMOOTHING_MS);
  const maxStep = BOARD_POSE_MAX_RATE_DPS * elapsedMs / 1000;
  const advance = (value: number, destination: number) => {
    const desiredStep = shortestAngleDelta(destination, value) * alpha;
    const step = Math.sign(desiredStep) * Math.min(Math.abs(desiredStep), maxStep);
    return value + step;
  };
  return {
    pitch: advance(current.pitch, target.pitch),
    roll: advance(current.roll, target.roll),
  };
}

export function MilAccelerometerCard() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<MilAccelerometerStatus | null>(null);
  // Keep raw XYZ in the diagnostic readout. The separate pose sample is the
  // causal low-pass value used for the displayed orientation and 3D model.
  const [poseSample, setPoseSample] = useState<MilAccelerometerStatus['sample']>(null);
  const [config, setConfig] = useState<MilAccelerometerConfig>(DEFAULT_CONFIG);
  const [busy, setBusy] = useState(false);
  const [configBusy, setConfigBusy] = useState(false);
  const [zero, setZero] = useState<Orientation | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
  const poseFilter = useRef(new AdaptivePoseFilter());
  const lastStreamTimestamp = useRef<number | null>(null);
  // The REST status poll and the worker are independent producers. A status
  // response may have started before a newer stream sample arrived, so keep a
  // monotonic watermark at the actual DOM write seam as the final guard
  // against displaying an older pose after a fresh one.
  const lastAppliedTimestamp = useRef<number | null>(null);
  const boardRef = useRef<HTMLDivElement | null>(null);
  const latestPoseSample = useRef<MilAccelerometerStatus['sample']>(null);
  const zeroRef = useRef<Orientation | null>(null);
  const targetBoardOrientation = useRef<Orientation | null>(null);
  const renderedBoardOrientation = useRef<Orientation | null>(null);
  const boardAnimationFrame = useRef<number | null>(null);
  const lastBoardFrameAt = useRef<number | null>(null);
  const latestFrontendTiming = useRef<Record<string, any> | null>(null);

  const writeBoardPose = (next: Orientation) => {
    if (boardRef.current) boardRef.current.style.transform = boardTransform(next);
  };

  const renderBoardFrame = (timestamp: number) => {
    boardAnimationFrame.current = null;
    const target = targetBoardOrientation.current;
    if (!target || !boardRef.current) return;

    const current = renderedBoardOrientation.current ?? target;
    const elapsed = lastBoardFrameAt.current === null
      ? 16.67
      : Math.min(100, Math.max(0, timestamp - lastBoardFrameAt.current));
    lastBoardFrameAt.current = timestamp;
    const next = advanceBoardOrientation(current, target, elapsed);
    const settled = Math.abs(shortestAngleDelta(target.pitch, next.pitch))
      <= BOARD_POSE_SETTLE_EPSILON_DEG
      && Math.abs(shortestAngleDelta(target.roll, next.roll))
      <= BOARD_POSE_SETTLE_EPSILON_DEG;
    renderedBoardOrientation.current = settled ? target : next;
    writeBoardPose(renderedBoardOrientation.current);
    if (latestFrontendTiming.current
        && latestFrontendTiming.current.frontend_rendered_at_ms == null) {
      latestFrontendTiming.current.frontend_rendered_at_ms = performance.now();
    }

    if (!settled) boardAnimationFrame.current = window.requestAnimationFrame(renderBoardFrame);
  };

  const scheduleBoardRender = () => {
    if (boardAnimationFrame.current === null) {
      boardAnimationFrame.current = window.requestAnimationFrame(renderBoardFrame);
    }
  };

  const applyBoardPose = (
    sample: MilAccelerometerStatus['sample'], force = false,
    timing?: Record<string, any>,
  ) => {
    if (!sample || !boardRef.current) return;
    if (!Number.isFinite(sample.timestamp)) return;
    if (!force && lastAppliedTimestamp.current !== null
        && sample.timestamp <= lastAppliedTimestamp.current) return;
    const current = orientationFor(sample);
    const next = relativeOrientation(current, zeroRef.current);
    if (!Number.isFinite(next.pitch) || !Number.isFinite(next.roll)) return;
    targetBoardOrientation.current = next;
    lastAppliedTimestamp.current = sample.timestamp;
    if (timing) {
      latestFrontendTiming.current = {
        ...timing, frontend_target_selected_at_ms: performance.now(),
      };
    }
    if (force || renderedBoardOrientation.current === null) {
      if (boardAnimationFrame.current !== null) {
        window.cancelAnimationFrame(boardAnimationFrame.current);
        boardAnimationFrame.current = null;
      }
      renderedBoardOrientation.current = next;
      lastBoardFrameAt.current = null;
      writeBoardPose(next);
      if (latestFrontendTiming.current
          && latestFrontendTiming.current.frontend_rendered_at_ms == null) {
        latestFrontendTiming.current.frontend_rendered_at_ms = performance.now();
      }
      return;
    }
    scheduleBoardRender();
  };

  useEffect(() => () => {
    if (boardAnimationFrame.current !== null) {
      window.cancelAnimationFrame(boardAnimationFrame.current);
      boardAnimationFrame.current = null;
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api.milAccelerometerStatus()
      .then((next) => { if (alive) {
        setStatus(next);
        setPoseSample((previous) => {
          if (previous || !next.sample) return previous;
          latestPoseSample.current = next.sample;
          applyBoardPose(next.sample);
          return next.sample;
        });
        setRequestError(null);
      } })
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
            const frontendTiming = latestFrontendTiming.current;
            if (previous?.sample?.timestamp && next.sample?.timestamp
                && next.sample.timestamp <= previous.sample.timestamp) {
              return { ...next, sample: previous.sample,
                last_sample_at: previous.last_sample_at,
                live_timing: frontendTiming ?? previous.live_timing };
            }
            return frontendTiming ? { ...next, live_timing: frontendTiming } : next;
          });
          setPoseSample((previous) => {
            const timestamp = next.sample?.timestamp;
            if (!next.sample || !timestamp || !Number.isFinite(timestamp)) return previous;
            if (lastStreamTimestamp.current !== null
                && timestamp <= lastStreamTimestamp.current) return previous;
            // REST is a fallback/status producer, but it can still win the
            // timestamp race with the stream. Feed it through the exact same
            // causal filter before it can reach either React or the board;
            // otherwise one corrupt newer REST frame bypasses the stream
            // median and becomes the visible multi-degree step.
            const nextPose = poseFilter.current.update(next.sample);
            latestPoseSample.current = nextPose;
            applyBoardPose(nextPose);
            return nextPose;
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
    let pending: Record<string, any> | null = null;

    const handleMessage = (message: Record<string, any>) => {
      if (!alive || message.type !== 'accelerometer_sample' || !message.data?.sample) return;
      // This mailbox is intentionally only one item deep. The worker can
      // continue receiving and parsing every sample while React is busy; a
      // saturated display consumes the newest reading on its next paint.
      const sample = message.data.sample as MilAccelerometerStatus['sample'];
      if (!sample) return;
      if (lastStreamTimestamp.current !== null
          && sample.timestamp <= lastStreamTimestamp.current) return;
      const nextPose = poseFilter.current.update(sample);
      latestPoseSample.current = nextPose;
      const trace = message.data.trace && typeof message.data.trace === 'object'
        ? { ...message.data.trace, frontend_received_at_ms: performance.now() }
        : undefined;
      // The raw sample remains visible in the diagnostic readout, but the
      // board target must use the causal pose filter too. A corrupted I2C
      // frame can otherwise rotate the model by several degrees for one
      // display frame before the next good sample arrives.
      applyBoardPose(nextPose, false, trace);
      pending = {
        ...message.data,
        // Publish the newest filtered value for the React readout at the next
        // paint. The board itself follows the raw target above and is rendered
        // by its independent display-frame loop.
        poseSample: nextPose,
        sample,
        trace,
      };
      lastStreamTimestamp.current = sample.timestamp;
      if (animationFrame === null) {
        animationFrame = window.requestAnimationFrame(flush);
      }
    };

    const flush = () => {
      if (animationFrame !== null) {
        window.cancelAnimationFrame(animationFrame);
        animationFrame = null;
      }
      const next = pending;
      pending = null;
      if (!alive || !next) return;
      const timing = latestFrontendTiming.current ?? next.trace;
      if (timing) timing.frontend_state_updated_at_ms = performance.now();
      setPoseSample((previous) => {
        const nextPose = next.poseSample ?? next.sample;
        if (!nextPose) return previous;
        if (previous?.timestamp && nextPose.timestamp <= previous.timestamp) return previous;
        latestPoseSample.current = nextPose;
        return nextPose;
      });
      // Coalesce a burst of decoded samples into the browser's paint cadence.
      // The worker and backend retain the full validated stream; React only
      // needs to render the newest filtered target once per animation frame.
      setStatus((previous) => previous ? {
        ...previous,
        // A delayed REST response must not put the model behind the newest
        // sample already delivered by the stream. Equal timestamps are also
        // preserved because the stream sample may be filtered while REST is
        // the unfiltered duplicate.
        ...(previous.sample?.timestamp && next.sample?.timestamp
          && next.sample.timestamp <= previous.sample.timestamp
          ? {}
          : { sample: next.sample }),
        last_sample_at: next.last_sample_at ?? next.sample.timestamp,
        samples_read: next.samples_read ?? previous.samples_read,
        host_read_rate_hz: next.host_read_rate_hz ?? previous.host_read_rate_hz,
        host_data_rate_bytes_per_s: next.host_data_rate_bytes_per_s
          ?? previous.host_data_rate_bytes_per_s,
        measured_rate_hz: next.measured_rate_hz ?? previous.measured_rate_hz,
        data_rate_bytes_per_s: next.data_rate_bytes_per_s
          ?? previous.data_rate_bytes_per_s,
        last_sample_sequence: next.last_sample_sequence
          ?? previous.last_sample_sequence,
        source_sample_index: next.source_sample_index
          ?? previous.source_sample_index,
        source_backlog_samples: next.source_backlog_samples
          ?? previous.source_backlog_samples,
        capture_to_publish_latency_ms: next.capture_to_publish_latency_ms
          ?? previous.capture_to_publish_latency_ms,
        decoded_to_publish_latency_ms: next.decoded_to_publish_latency_ms
          ?? previous.decoded_to_publish_latency_ms,
        live_timing: timing ?? previous.live_timing,
      } : previous);
    };

    let worker: Worker | null = null;
    let socket: ReconnectingSocket | null = null;
    let unsubscribe: (() => void) | null = null;
    const startFallbackSocket = () => {
      if (socket) return;
      socket = new ReconnectingSocket('/ws/accelerometer/pose');
      unsubscribe = socket.subscribe(handleMessage);
    };
    if (typeof Worker !== 'undefined') {
      try {
        worker = new Worker(
          new URL('../workers/accelerometerStream.worker.ts', import.meta.url),
          { type: 'module' },
        );
        worker.onmessage = (event: MessageEvent<Record<string, any>>) => {
          handleMessage(event.data);
        };
        worker.onerror = () => {
          // Module workers can fail before their first message in embedded
          // WebViews. Keep live pose delivery working through the tested
          // direct socket path instead of silently freezing the board.
          worker?.terminate();
          worker = null;
          startFallbackSocket();
        };
      } catch {
        startFallbackSocket();
      }
    } else {
      // Keep the test/legacy-browser fallback functional. Modern browsers use
      // the worker above, so socket parsing cannot compete with React renders.
      startFallbackSocket();
    }
    return () => {
      alive = false;
      worker?.terminate();
      unsubscribe?.();
      socket?.close();
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    };
  }, []);

  const current = useMemo(() => orientationFor(poseSample ?? status?.sample ?? null), [poseSample, status?.sample]);
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
      poseFilter.current.reset();
      lastStreamTimestamp.current = next.sample?.timestamp ?? null;
      // Starting/stopping deliberately establishes a new stream epoch. The
      // command response is the authoritative pose for that transition.
      lastAppliedTimestamp.current = null;
      latestPoseSample.current = next.sample;
      latestFrontendTiming.current = null;
      applyBoardPose(next.sample, true);
      setPoseSample(next.sample);
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

  const reconnectAccelerometer = async () => {
    if (busy || !controlMode) return;
    setBusy(true);
    try {
      const next = await api.milAccelerometerReconnect();
      setStatus(next);
      if (next.effective_config) setConfig(next.effective_config);
      poseFilter.current.reset();
      lastStreamTimestamp.current = next.sample?.timestamp ?? null;
      lastAppliedTimestamp.current = null;
      latestPoseSample.current = next.sample;
      latestFrontendTiming.current = null;
      applyBoardPose(next.sample, true);
      setPoseSample(next.sample);
      if (!next.running) {
        throw new Error(next.last_error || 'Accelerometer reconnect did not start tracking');
      }
      setRequestError(null);
      toast('success', 'Accelerometer reconnected and tracking restarted');
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Accelerometer reconnect failed';
      setRequestError(message);
      toast('error', message);
    } finally {
      setBusy(false);
    }
  };

  const updateConfig = async (patch: Partial<MilAccelerometerConfig>) => {
    if (running || configBusy || !controlMode) return;
    const next = normalizeConfigForMode({ ...config, ...patch });
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
  const retryVisible = !running && Boolean(requestError || status?.last_error);

  const setZeroOrientation = () => {
    const latest = latestPoseSample.current ?? poseSample ?? status?.sample ?? null;
    const latestOrientation = orientationFor(latest);
    zeroRef.current = latestOrientation;
    setZero(latestOrientation);
    // The timestamp is intentionally unchanged when zeroing; force this one
    // write so the currently displayed board is re-based immediately.
    applyBoardPose(latest, true);
  };

  useLayoutEffect(() => {
    applyBoardPose(latestPoseSample.current ?? poseSample ?? status?.sample ?? null);
  }, [poseSample, status?.sample, zero]);

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
              ref={boardRef}
              className="mil-accel-board"
            >
              <div className="mil-accel-board-edge" />
              <div className="mil-accel-pmod" aria-hidden="true" />
              <div className="mil-accel-header mil-accel-header-top" aria-hidden="true" />
              <div className="mil-accel-header mil-accel-header-bottom" aria-hidden="true" />
              <div className="mil-accel-usb" aria-hidden="true"><span>USB</span></div>
              <div className="mil-accel-buttons" aria-hidden="true"><i /><i /></div>
              <div className="mil-accel-silk mil-accel-silk-a">MAX1000</div>
              <div className="mil-accel-sdram">SDRAM</div>
              <div className="mil-accel-fpga">MAX<br />10</div>
              <div className="mil-accel-flash">FLASH</div>
              <div className="mil-accel-ftdi">FTDI</div>
              <div className="mil-accel-osc">OSC</div>
              <div className="mil-accel-led-bank" aria-hidden="true" />
              <div className="mil-accel-sensor" aria-label="U4 LIS3DH sensor axes: positive X toward the lower board edge, positive Y toward USB, positive Z out of the board">
                <span className="mil-accel-sensor-mark" />
                <span className="mil-accel-sensor-name">U4<br />LIS3DH</span>
                <span className="mil-accel-axis mil-accel-axis-x">+X</span>
                <span className="mil-accel-axis mil-accel-axis-y">+Y</span>
                <span className="mil-accel-axis mil-accel-axis-z">+Z</span>
              </div>
            </div>
          </div>
          <div className="mil-accel-orientation" aria-live="polite">
            <span><b>U4 X / lower edge</b> {signed(orientation.pitch, 1)}°</span>
            <span><b>U4 Y / USB</b> {signed(orientation.roll, 1)}°</span>
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
            <span>Ring source backlog</span><strong>{backlog(status?.source_backlog_samples)}</strong>
            <span>Capture → publish</span><strong>{latency(status?.capture_to_publish_latency_ms)}</strong>
            <span>Decode → publish</span><strong>{latency(status?.decoded_to_publish_latency_ms)}</strong>
            <span>WebSocket → render</span><strong>{frontendRenderLatency(status?.live_timing)}</strong>
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
                {odrOptionsFor(config.operating_mode).map((value) => <option key={value} value={value}>{value.toLocaleString()} Hz</option>)}
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
            {retryVisible && (
              <button className="primary" disabled={busy || !controlMode}
                      onClick={() => void reconnectAccelerometer()}>
                {busy ? 'Reconnecting…' : 'Retry / reconnect'}
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
