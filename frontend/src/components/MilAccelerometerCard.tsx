import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import { ReconnectingSocket } from '../api/websocket';
import type { MilAccelerometerConfig, MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { hasUsableGravity, orientationFor, type Orientation } from './accelerometerPose';

const POLL_INTERVAL_MS = 100;
// The sensor stream can be several thousand samples per second. Decode and
// publish every packet; only the board's CSS paint is naturally bounded by
// the display refresh rate. The transport reports its measured decoded rate
// separately.
const BOARD_POSE_SETTLE_EPSILON_DEG = 0.01;
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
  [1, 10, 25, 50, 100, 200, 400, 1600, 5376];
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
  void current;
  void elapsedMs;
  return target;
}

export function MilAccelerometerCard() {
  const { controlMode, toast } = useApp();
  const [status, setStatus] = useState<MilAccelerometerStatus | null>(null);
  // Keep the decoded XYZ sample as the pose source. The display frame may
  // coalesce paints, but it must not alter or rate-limit the decoded value.
  const [poseSample, setPoseSample] = useState<MilAccelerometerStatus['sample']>(null);
  const [config, setConfig] = useState<MilAccelerometerConfig>(DEFAULT_CONFIG);
  const [busy, setBusy] = useState(false);
  const [configBusy, setConfigBusy] = useState(false);
  const [zero, setZero] = useState<Orientation | null>(null);
  const [requestError, setRequestError] = useState<string | null>(null);
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
  const hasFrontendWaveformSample = useRef(false);
  const pendingLiveUi = useRef<{
    sample: NonNullable<MilAccelerometerStatus['sample']>;
    data: Record<string, any>;
    timing?: Record<string, any>;
  } | null>(null);
  const activeSession = useApp((state) => state.activeSession);
  const activeSessionRef = useRef(activeSession);
  activeSessionRef.current = activeSession;

  const writeBoardPose = (next: Orientation) => {
    if (boardRef.current) boardRef.current.style.transform = boardTransform(next);
  };

  const flushPendingLiveUi = () => {
    const pending = pendingLiveUi.current;
    pendingLiveUi.current = null;
    if (!pending) return;
    if (pending.timing) {
      pending.timing.frontend_state_updated_at_ms = performance.now();
    }
    setPoseSample(pending.sample);
    setStatus((previous) => previous ? {
      ...previous,
      sample: pending.sample,
      last_sample_at: pending.data.last_sample_at ?? pending.sample.timestamp,
      samples_read: pending.data.samples_read ?? previous.samples_read,
      host_read_rate_hz: pending.data.host_read_rate_hz ?? previous.host_read_rate_hz,
      host_data_rate_bytes_per_s: pending.data.host_data_rate_bytes_per_s
        ?? previous.host_data_rate_bytes_per_s,
      measured_rate_hz: pending.data.measured_rate_hz ?? previous.measured_rate_hz,
      data_rate_bytes_per_s: pending.data.data_rate_bytes_per_s
        ?? previous.data_rate_bytes_per_s,
      last_sample_sequence: pending.data.last_sample_sequence
        ?? previous.last_sample_sequence,
      source_sample_index: pending.data.source_sample_index
        ?? previous.source_sample_index,
      source_age_samples: pending.data.source_age_samples
        ?? previous.source_age_samples,
      source_backlog_samples: pending.data.source_backlog_samples
        ?? previous.source_backlog_samples,
      waveform_packet_count: pending.data.waveform_packet_count
        ?? previous.waveform_packet_count,
      waveform_sequence_gaps: pending.data.waveform_sequence_gaps
        ?? previous.waveform_sequence_gaps,
      waveform_disconnects: pending.data.waveform_disconnects
        ?? previous.waveform_disconnects,
      frontend_decode_latency_ms: pending.data.frontend_decode_latency_ms
        ?? previous.frontend_decode_latency_ms,
      capture_to_publish_latency_ms: pending.data.capture_to_publish_latency_ms
        ?? previous.capture_to_publish_latency_ms,
      decoded_to_publish_latency_ms: pending.data.decoded_to_publish_latency_ms
        ?? previous.decoded_to_publish_latency_ms,
      live_timing: pending.timing ?? previous.live_timing,
    } : previous);
  };

  const renderBoardFrame = (timestamp: number) => {
    boardAnimationFrame.current = null;
    flushPendingLiveUi();
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
    // A zero/near-zero XYZ vector has no defined gravity orientation. Keep
    // the last valid target until the decoder supplies a usable packet; raw
    // axes remain visible below for diagnosing the sensor/input path.
    if (!hasUsableGravity(sample)) return;
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
          // A running status sample is backend-owned and may be a delayed
          // duplicate. The board waits for the frontend's raw-waveform
          // decoder instead of taking that sample as a live pose.
          if (previous || !next.sample || next.running) return previous;
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
            if (hasFrontendWaveformSample.current && previous?.sample) {
              // Once the direct waveform decoder has delivered a packet, REST
              // is status-only. It must not become a second pose producer.
              return { ...next, sample: previous.sample,
                last_sample_at: previous.last_sample_at,
                samples_read: previous.samples_read,
                last_sample_sequence: previous.last_sample_sequence,
                source_sample_index: previous.source_sample_index,
                source_backlog_samples: previous.source_backlog_samples,
                waveform_packet_count: previous.waveform_packet_count,
                waveform_sequence_gaps: previous.waveform_sequence_gaps,
                waveform_disconnects: previous.waveform_disconnects,
                frontend_decode_latency_ms: previous.frontend_decode_latency_ms,
                source_age_samples: previous.source_age_samples,
                capture_to_publish_latency_ms: previous.capture_to_publish_latency_ms,
                decoded_to_publish_latency_ms: previous.decoded_to_publish_latency_ms,
                live_timing: frontendTiming ?? previous.live_timing };
            }
            if (previous?.sample?.timestamp && next.sample?.timestamp
                && next.sample.timestamp <= previous.sample.timestamp) {
              return { ...next, sample: previous.sample,
                last_sample_at: previous.last_sample_at,
                live_timing: frontendTiming ?? previous.live_timing };
            }
            return frontendTiming ? { ...next, live_timing: frontendTiming } : next;
          });
          setPoseSample((previous) => {
            if (next.running || hasFrontendWaveformSample.current) return previous;
            const timestamp = next.sample?.timestamp;
            if (!next.sample || !timestamp || !Number.isFinite(timestamp)) return previous;
            if (lastStreamTimestamp.current !== null
                && timestamp <= lastStreamTimestamp.current) return previous;
            // REST is only a fallback/status producer while stopped. Feed its
            // value directly when no live waveform sample exists.
            latestPoseSample.current = next.sample;
            applyBoardPose(next.sample);
            return next.sample;
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
    type FrontendStreamOwner = 'worker' | 'fallback';
    // The fallback is only for browsers where the module worker fails or
    // stalls before its first packet. Once either path delivers a packet,
    // reject the other path so two independent decoders cannot fight over
    // the pose or make the packet counter move backwards.
    let streamOwner: FrontendStreamOwner | null = null;
    const handleMessage = (message: Record<string, any>, source: FrontendStreamOwner) => {
      if (!alive || message.type !== 'accelerometer_sample' || !message.data?.sample) return;
      if (streamOwner !== null && streamOwner !== source) return;
      streamOwner = source;
      hasFrontendWaveformSample.current = true;
      const sample = message.data.sample as MilAccelerometerStatus['sample'];
      if (!sample) return;
      const nextPose = sample;
      latestPoseSample.current = nextPose;
      const trace = message.data.trace && typeof message.data.trace === 'object'
        ? { ...message.data.trace, frontend_received_at_ms: performance.now() }
        : undefined;
      // The raw sample remains visible in the diagnostic readout. The board
      // target follows the decoded sample directly. Any I2C frame
      // rejection belongs in the incremental decoder, not in pose motion.
      applyBoardPose(nextPose, false, trace);

      // The decoder and pose target still receive every packet above. React
      // state is presentation only: committing 1,344+ state updates/second
      // makes the card compete with the worker and eventually causes the
      // browser socket to stop draining. Publish the newest decoded packet
      // once per paint frame while retaining the exact packet order in the
      // worker and the board target ref.
      const timing = latestFrontendTiming.current ?? trace;
      pendingLiveUi.current = { sample: nextPose, data: message.data, timing };
      // Reuse the board's existing display frame for the one presentation
      // commit; this adds no second animation queue.
      scheduleBoardRender();
    };

    let worker: Worker | null = null;
    let socket: ReconnectingSocket | null = null;
    let unsubscribe: (() => void) | null = null;
    const startFallbackSocket = () => {
      if (socket) return;
      // If a module worker is unavailable, use the backend's replaceable pose
      // topic. The main thread must not become a second raw-waveform decoder
      // or receive an archival high-rate stream while rendering React.
      socket = new ReconnectingSocket('/ws/accelerometer/pose');
      unsubscribe = socket.subscribe((message) => handleMessage(message, 'fallback'));
    };
    if (typeof Worker !== 'undefined') {
      try {
        worker = new Worker(
          new URL('../workers/accelerometerStream.worker.ts', import.meta.url),
          { type: 'module' },
        );
        worker.onmessage = (event: MessageEvent<Record<string, any>>) => {
          if (event.data.type === 'accelerometer_waveform_chunk') {
            try {
              const session = activeSessionRef.current;
              const data = event.data.data;
              const samples = data?.samples_values;
              if (session?.tags.includes('accelerometer')
                  && samples instanceof Uint16Array) {
                waveformView.setLiveRawActive(session.id, true);
                waveformView.appendLiveRaw(
                  session.id, samples, Number(data.sample_rate));
                hasFrontendWaveformSample.current = true;
              }
            } finally {
              // The worker keeps decoding the physical stream independently;
              // this acknowledgement gates only the UI paint mailbox. It
              // prevents a slow Capture canvas from accumulating transferred
              // 32K/65K-sample buffers in the main-thread message queue.
              worker?.postMessage({ type: 'accelerometer_waveform_ack' });
            }
            return;
          }
          handleMessage(event.data, 'worker');
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
      worker?.postMessage?.({ type: 'shutdown' });
      worker?.terminate();
      unsubscribe?.();
      socket?.close();
      const session = activeSessionRef.current;
      if (session?.tags.includes('accelerometer')) {
        waveformView.setLiveRawActive(session.id, false);
      }
    };
  }, []);

  const current = useMemo(() => orientationFor(poseSample ?? status?.sample ?? null), [poseSample, status?.sample]);
  const orientation = useMemo(() => relativeOrientation(current, zero), [current, zero]);
  const poseInput = poseSample ?? status?.sample ?? null;
  const hasSample = Boolean(poseInput);
  const poseUsable = hasUsableGravity(poseInput);
  const available = status?.available ?? false;
  const running = status?.running ?? false;

  const startStop = async (action: 'start' | 'stop') => {
    setBusy(true);
    try {
      // The controls can retain a pending selection while the backend has
      // restarted or another card changed the hardware. Apply the displayed
      // configuration immediately before the run edge so CTRL1/CTRL4
      // readback and the live waveform always describe the same session.
      if (action === 'start') {
        const saved = await api.milAccelerometerUpdateConfig(config);
        setConfig(saved);
      }
      const next = action === 'start'
        ? await api.milAccelerometerStart()
        : await api.milAccelerometerStop();
      setStatus(next);
      hasFrontendWaveformSample.current = false;
      lastStreamTimestamp.current = null;
      // Starting/stopping deliberately establishes a new stream epoch. The
      // raw waveform stream is authoritative once tracking starts. The start
      // response is status only; using its sample here reintroduces a second
      // pose producer and makes the board appear detached from the waveform.
      lastAppliedTimestamp.current = null;
      latestPoseSample.current = action === 'stop' ? next.sample : null;
      latestFrontendTiming.current = null;
      if (action === 'stop') {
        applyBoardPose(next.sample, true);
        setPoseSample(next.sample);
      } else {
        setPoseSample(null);
      }
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
    // Seed the initial frame if the card mounted while tracking was already
    // active, then stop accepting REST pose updates as soon as the first raw
    // waveform sample has crossed the frontend decoder seam.
    const fallback = status?.sample
      && (!status.running || !hasFrontendWaveformSample.current)
      ? status.sample : null;
    applyBoardPose(latestPoseSample.current ?? poseSample ?? fallback);
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
            <div><span className="axis-dot axis-x" />X</div><strong>{signed(poseSample?.x_g ?? status?.sample?.x_g ?? NaN)} <small>g</small></strong>
            <div><span className="axis-dot axis-y" />Y</div><strong>{signed(poseSample?.y_g ?? status?.sample?.y_g ?? NaN)} <small>g</small></strong>
            <div><span className="axis-dot axis-z" />Z</div><strong>{signed(poseSample?.z_g ?? status?.sample?.z_g ?? NaN)} <small>g</small></strong>
          </div>
          <div className="mil-accel-meta">
            <span>Requested sensor ODR</span><strong>{rate(config.output_data_rate_hz, 'Hz')}</strong>
            <span>Effective sensor ODR</span><strong>{rate(status?.effective_odr_hz ?? status?.effective_config?.output_data_rate_hz, 'Hz')}</strong>
            <span>Host XYZ read rate</span><strong>{rate(status?.host_read_rate_hz ?? status?.measured_rate_hz, 'samples/s')}</strong>
            <span>Host payload rate</span><strong>{rate(status?.host_data_rate_bytes_per_s ?? status?.data_rate_bytes_per_s, 'bytes/s')}</strong>
            <span>Samples read</span><strong>{(status?.samples_read ?? 0).toLocaleString()}</strong>
            <span>Last sample</span><strong>{sampleAge(status?.last_sample_at ?? status?.sample?.timestamp)}</strong>
            <span>Ring unread backlog</span><strong>{backlog(status?.source_backlog_samples)}</strong>
            <span>Waveform packets</span><strong>{(status?.waveform_packet_count ?? 0).toLocaleString()}</strong>
            <span>Waveform sequence gaps</span><strong>{(status?.waveform_sequence_gaps ?? 0).toLocaleString()}</strong>
            <span>Waveform disconnects</span><strong>{(status?.waveform_disconnects ?? 0).toLocaleString()}</strong>
            <span>Frontend decode latency</span><strong>{latency(status?.frontend_decode_latency_ms)}</strong>
            <span>Decoded source age</span><strong>{backlog(status?.source_age_samples)}</strong>
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
          {status?.sample && !poseUsable && !requestError && (
            <div className="hint">Pose paused: decoded XYZ vector is too small to determine gravity orientation.</div>
          )}
        </div>
      </div>
    </section>
  );
}
