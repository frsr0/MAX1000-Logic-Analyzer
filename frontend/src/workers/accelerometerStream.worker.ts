import { ReconnectingSocket } from '../api/websocket';
import { LiveI2cDecoder } from './liveI2cDecoder';

// Keep the socket, waveform parsing, and protocol decoding off the React/main
// thread. The board must be driven by this raw ordered stream; the backend's
// decoded pose topic remains available for legacy consumers only.
const socket = new ReconnectingSocket('/ws/accelerometer/waveform');
let decoder = new LiveI2cDecoder();
let decoderConfigKey = '';
let lastWaveformSequence: number | null = null;
let decodedSamples = 0;
let lastTimestamp = 0;

function configKey(config: Record<string, unknown> | undefined): string {
  return JSON.stringify(config ?? {});
}

function decodeWaveform(message: any) {
  const data = message.data;
  if (!data || typeof data.samples_b64 !== 'string') return;

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
      Number(data.read_length ?? 6),
    );
    decoderConfigKey = nextConfigKey;
  }

  const receivedAtMs = performance.now();
  const sourceIndex = Number.isFinite(Number(data.source_index))
    ? Number(data.source_index) : null;
  const decoded = decoder.feedBase64(data.samples_b64, sourceIndex);
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
    self.postMessage({
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

self.addEventListener('unload', () => socket.close());
