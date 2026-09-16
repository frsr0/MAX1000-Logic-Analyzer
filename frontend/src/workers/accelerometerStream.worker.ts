import { ReconnectingSocket } from '../api/websocket';
import { decodeBase64, inferLiveReadLength, LiveI2cDecoder } from './liveI2cDecoder';

// Keep the direct waveform worker asset fingerprinted when its transport
// contract changes; a running lab tab must not retain an older decoder bundle.

// Keep the socket, waveform parsing, and protocol decoding off the React/main
// thread. The board must be driven by this raw ordered stream; the backend's
// decoded pose topic remains available for legacy consumers only.
const socket = new ReconnectingSocket('/ws/accelerometer/waveform-binary', true);
// The backend keeps the legacy text topic as the raw-waveform transport when
// direct FIFO delivery falls back to the SDRAM ring. Subscribe to both topics;
// direct mode publishes only binary, while ring mode publishes only text.
const textSocket = new ReconnectingSocket('/ws/accelerometer/waveform');
let decoder = new LiveI2cDecoder();
let decoderConfigKey = '';
let lastWaveformSequence: number | null = null;
let decodedSamples = 0;
let lastTimestamp = 0;
const LIVE_POSE_POST_INTERVAL_MS = 16;
// The decoder consumes every waveform packet immediately, but the Capture
// canvas only needs a fresh paint window at display cadence. Keeping this
// mailbox a little below 30 fps prevents a 65K-sample canvas redraw from
// monopolising the browser's websocket receive path at multi-kHz ODRs.
const LIVE_WAVEFORM_POST_INTERVAL_MS = 32;
let pendingWaveformChunk: {
  sequence: number;
  sample_rate: number;
  sample_count: number;
  samples_values: Uint16Array;
} | null = null;
let waveformPostTimer: ReturnType<typeof setTimeout> | null = null;
let waveformPostInFlight = false;
let pendingPoseMessage: Record<string, any> | null = null;
let posePostTimer: ReturnType<typeof setTimeout> | null = null;

function postLatestWaveformChunk(): void {
  waveformPostTimer = null;
  if (waveformPostInFlight) return;
  const pending = pendingWaveformChunk;
  pendingWaveformChunk = null;
  if (!pending) return;
  waveformPostInFlight = true;
  (self.postMessage as (message: unknown, transfer: Transferable[]) => void)({
    type: 'accelerometer_waveform_chunk',
    data: pending,
  }, [pending.samples_values.buffer]);
}

function queueWaveformChunk(chunk: {
  sequence: number;
  sample_rate: number;
  sample_count: number;
  samples_values: Uint16Array;
}): void {
  // The worker has already decoded every source chunk. The main thread only
  // needs the newest paint payload; keeping older display payloads queued
  // makes the browser a second waveform backlog and can starve the socket.
  pendingWaveformChunk = chunk;
  if (waveformPostTimer === null && !waveformPostInFlight) {
    waveformPostTimer = setTimeout(
      postLatestWaveformChunk, LIVE_WAVEFORM_POST_INTERVAL_MS);
  }
}

function postLatestPose(): void {
  posePostTimer = null;
  const pending = pendingPoseMessage;
  pendingPoseMessage = null;
  if (pending) self.postMessage(pending);
}

function queuePoseMessage(message: Record<string, any>): void {
  // The worker has already accepted and decoded every packet in order. The
  // main thread only needs the newest pose target; queueing thousands of
  // individual React/DOM messages per second eventually starves the browser
  // websocket receive path and makes the FPGA FIFO overrun upstream.
  pendingPoseMessage = message;
  if (posePostTimer === null) {
    posePostTimer = setTimeout(postLatestPose, LIVE_POSE_POST_INTERVAL_MS);
  }
}

function configKey(config: Record<string, unknown> | undefined): string {
  return JSON.stringify(config ?? {});
}

function decodeWaveform(message: any) {
  const data = message.data;
  if (!data
      || (typeof data.samples_b64 !== 'string'
          && !(data.samples_values instanceof Uint16Array))) return;

  const samples = data.samples_values instanceof Uint16Array
    ? data.samples_values
    : decodeBase64(data.samples_b64);

  const sequence = Number(data.sequence);
  if (Number.isFinite(sequence)
      && lastWaveformSequence !== null
      && sequence !== lastWaveformSequence + 1) {
    // Never decode a partial chunk after transport loss. Waiting for the next
    // valid START keeps a missing waveform visible as a gap rather than as a
    // fabricated board position.
    decoder.reset();
  }
  if (Number.isFinite(sequence)) lastWaveformSequence = sequence;

  const config = data.config && typeof data.config === 'object'
    ? data.config as Record<string, unknown> : undefined;
  const nextConfigKey = configKey(config);
  if (nextConfigKey !== decoderConfigKey) {
    decoder = new LiveI2cDecoder(
      Number(data.address ?? 0x19),
      config as any,
      inferLiveReadLength(data),
    );
    decoderConfigKey = nextConfigKey;
  }

  const receivedAtMs = performance.now();
  const sourceIndex = Number.isFinite(Number(data.source_index))
    ? Number(data.source_index) : null;
  const decoded = decoder.feed(samples, sourceIndex);
  // The decoder and live waveform view consume the same physical ordered
  // chunk. The binary socket owns a fresh ArrayBuffer for each message, so
  // retain that view and transfer it at the next paint tick instead of
  // allocating a second full copy for every 2 MHz packet.
  queueWaveformChunk({
    sequence,
    sample_rate: Number(data.sample_rate),
    sample_count: Number(data.sample_count),
    samples_values: samples,
  });
  const sampleRate = Number(data.sample_rate);
  const sampleCount = Number(data.sample_count);
  const messageTimestamp = Number(message.ts);
  for (const sample of decoded) {
    decodedSamples += 1;
    const offset = Number(sample.sample_offset);
    let timestamp = Number.isFinite(messageTimestamp) && sampleRate > 0
      ? messageTimestamp - Math.max(0, sampleCount - offset) / sampleRate
      : performance.timeOrigin / 1000 + receivedAtMs / 1000;
    // The pose consumer has a monotonic watermark. Preserve that contract if
    // a transport timestamp is coarse or a ring cursor wraps.
    if (!Number.isFinite(timestamp) || timestamp <= lastTimestamp) {
      timestamp = lastTimestamp + 1e-6;
    }
    lastTimestamp = timestamp;
    // A decoded packet is the pose update. Publish every packet in decoder
    // order; any display-frame throttling belongs to painting, never to the
    // waveform/decoder seam.
    queuePoseMessage({
      type: 'accelerometer_sample',
      ts: message.ts,
      data: {
        sample: { ...sample, timestamp },
        last_sample_at: timestamp,
        samples_read: decodedSamples,
        last_sample_sequence: decodedSamples,
        source_sample_index: sample.source_index,
        source_backlog_samples: 0,
        source_age_samples: 0,
        trace: {
          decoder_source: 'frontend_waveform',
          waveform_sequence: sequence,
          waveform_source_index: sourceIndex,
          waveform_sample_count: sampleCount,
          waveform_sample_rate_hz: sampleRate,
          frontend_decoded_at_ms: performance.now(),
          frontend_received_at_ms: receivedAtMs,
        },
      },
    });
  }
}

socket.subscribe((message) => {
  if (message.type === 'accelerometer_waveform') {
    decodeWaveform(message);
    return;
  }
  // Accept the old decoded topic while an older backend is being upgraded.
  // The normal path above is the only source used by the current UI.
  if (message.type === 'accelerometer_sample' && message.data?.sample) {
    const trace = message.data.trace;
    self.postMessage(trace && typeof trace === 'object'
      ? { ...message, data: {
        ...message.data,
        trace: { ...trace, worker_received_at_ms: performance.now() },
      } }
      : message);
  }
});

textSocket.subscribe((message) => {
  if (message.type === 'accelerometer_waveform') decodeWaveform(message);
});

socket.subscribeBinary((buffer) => {
  if (buffer.byteLength < 4) return;
  const view = new DataView(buffer);
  const headerLength = view.getUint32(0, true);
  const headerStart = 4;
  const payloadStart = headerStart + headerLength;
  if (payloadStart > buffer.byteLength || (buffer.byteLength - payloadStart) % 2) return;
  try {
    const header = JSON.parse(new TextDecoder().decode(
      new Uint8Array(buffer, headerStart, headerLength)));
    const samples = new Uint16Array(buffer, payloadStart);
    decodeWaveform({ data: { ...header, samples_values: samples } });
  } catch { /* ignore malformed binary frames */ }
});

self.addEventListener('message', (event: MessageEvent) => {
  if (event.data?.type === 'accelerometer_waveform_ack') {
    waveformPostInFlight = false;
    if (pendingWaveformChunk && waveformPostTimer === null) {
      waveformPostTimer = setTimeout(
        postLatestWaveformChunk, LIVE_WAVEFORM_POST_INTERVAL_MS);
    }
    return;
  }
  if (event.data?.type !== 'shutdown') return;
  if (waveformPostTimer !== null) {
    clearTimeout(waveformPostTimer);
    waveformPostTimer = null;
  }
  if (posePostTimer !== null) {
    clearTimeout(posePostTimer);
    posePostTimer = null;
  }
  pendingWaveformChunk = null;
  waveformPostInFlight = false;
  pendingPoseMessage = null;
  socket.close();
  textSocket.close();
});

self.addEventListener('unload', () => {
  socket.close();
  textSocket.close();
});
