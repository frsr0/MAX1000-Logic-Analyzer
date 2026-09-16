import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const busWord = (scl: number, sda: number) =>
  ((scl & 1) << 14) | ((sda & 1) << 13);

function hold(scl: number, sda: number, count = 3): number[] {
  return Array.from({ length: count }, () => busWord(scl, sda));
}

function byteTrace(value: number, ack: number): number[] {
  const result: number[] = [];
  for (let bit = 7; bit >= 0; bit -= 1) {
    const sda = (value >> bit) & 1;
    result.push(...hold(0, sda, 2), ...hold(1, sda, 4));
  }
  result.push(...hold(0, ack, 2), ...hold(1, ack, 4));
  return result;
}

function transaction(payload: number[]): number[] {
  const result = [...hold(1, 1), ...hold(1, 0)];
  result.push(...byteTrace(0x32, 0), ...byteTrace(0xA8, 0));
  result.push(...hold(0, 1, 2), ...hold(1, 1), ...hold(1, 0));
  result.push(...byteTrace(0x33, 0));
  payload.forEach((value, index) => {
    result.push(...byteTrace(value, index === payload.length - 1 ? 1 : 0));
  });
  result.push(...hold(0, 1, 2), ...hold(1, 1));
  return result;
}

function sampleBytes(x: number, y: number, z: number): number[] {
  const words = [x << 4, y << 4, z << 4];
  return words.flatMap((word) => [word & 0xff, (word >> 8) & 0xff]);
}

function binaryPacket(sequence: number): ArrayBuffer {
  const samples = transaction(sampleBytes(sequence, 0, 1000));
  let header = JSON.stringify({
    sequence,
    sample_rate: 2_000_000,
    sample_count: samples.length,
    source_index: sequence * samples.length,
    config: { operating_mode: 'high_resolution', full_scale_g: 2, endian: 'little' },
    address: 0x19,
    read_length: 6,
  });
  if (header.length % 2) header += ' ';
  const headerBytes = new TextEncoder().encode(header);
  const buffer = new ArrayBuffer(4 + headerBytes.length + samples.length * 2);
  const view = new DataView(buffer);
  view.setUint32(0, headerBytes.length, true);
  new Uint8Array(buffer, 4, headerBytes.length).set(headerBytes);
  new Uint16Array(buffer, 4 + headerBytes.length).set(samples);
  return buffer;
}

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  binaryType = '';
  sent: string[] = [];

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(message: string) { this.sent.push(message); }

  close() {
    this.onclose?.();
  }

  emitBinary(data: ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  emit(data: unknown) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

describe('accelerometerStream worker binary transport', () => {
  let post: ReturnType<typeof vi.fn>;
  let workerScope: {
    postMessage: ReturnType<typeof vi.fn>;
    addEventListener: (type: string, listener: (event: MessageEvent) => void) => void;
    dispatch: (event: MessageEvent) => void;
  };

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    post = vi.fn();
    const listeners = new Map<string, (event: MessageEvent) => void>();
    workerScope = {
      postMessage: post,
      addEventListener(type, listener) { listeners.set(type, listener); },
      dispatch(event) { listeners.get(event.type)?.(event); },
    };
    vi.stubGlobal('self', workerScope);
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', {
      protocol: 'http:', hostname: '127.0.0.1', port: '8002', host: '127.0.0.1:8002',
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('decodes every binary packet across a reconnect while coalescing only worker output', async () => {
    await import('./accelerometerStream.worker');
    const firstBinarySocket = FakeWebSocket.instances[0];
    expect(FakeWebSocket.instances).toHaveLength(2); // binary + legacy text

    for (let sequence = 1; sequence <= 48; sequence += 1) {
      firstBinarySocket.emitBinary(binaryPacket(sequence));
    }
    firstBinarySocket.onclose?.();
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeWebSocket.instances).toHaveLength(3);
    const reconnectedBinarySocket = FakeWebSocket.instances[2];

    for (let sequence = 49; sequence <= 96; sequence += 1) {
      reconnectedBinarySocket.emitBinary(binaryPacket(sequence));
    }
    await vi.advanceTimersByTimeAsync(32);

    const poseMessages = post.mock.calls
      .map(([message]) => message)
      .filter((message) => message.type === 'accelerometer_sample');
    const lastPoseMessage = poseMessages[poseMessages.length - 1];
    expect(lastPoseMessage.data.samples_read).toBe(96);
    expect(lastPoseMessage.data.trace.waveform_sequence).toBe(96);
    expect(lastPoseMessage.data.waveform_packet_count).toBe(96);
    expect(lastPoseMessage.data.waveform_sequence_gaps).toBe(0);
    expect(lastPoseMessage.data.waveform_disconnects).toBe(1);
    expect(lastPoseMessage.data.frontend_decode_latency_ms).toBeGreaterThanOrEqual(0);

    workerScope.dispatch(new MessageEvent('message', { data: { type: 'shutdown' } }));
  });

  it('handles legacy text frames, malformed binary frames, acknowledgements, and unload', async () => {
    const timerSpy = vi.spyOn(globalThis, 'setTimeout');
    await import('./accelerometerStream.worker');
    const binarySocket = FakeWebSocket.instances[0];
    const textSocket = FakeWebSocket.instances[1];
    textSocket.emit(JSON.stringify({
      type: 'accelerometer_waveform',
      ts: 10,
      data: {
        sequence: 1, sample_rate: 2_000_000,
        sample_count: transaction(sampleBytes(64, 0, 1000)).length,
        samples_b64: btoa(String.fromCharCode(...transaction(sampleBytes(64, 0, 1000)).flatMap((value) => [value & 0xff, value >> 8]))),
      },
    }));
    textSocket.emit(JSON.stringify({
      type: 'accelerometer_waveform',
      data: {
        sample_rate: 2_000_000,
        samples_b64: btoa(String.fromCharCode(...transaction(sampleBytes(32, 0, 1000)).flatMap((value) => [value & 0xff, value >> 8]))),
      },
    }));
    textSocket.emit(JSON.stringify({
      type: 'accelerometer_waveform',
      ts: 10,
      data: {
        sequence: 1, sample_rate: 2_000_000, sample_count: 0,
        samples_b64: btoa(String.fromCharCode(0, 0)),
      },
    }));
    textSocket.emit(JSON.stringify({ type: 'accelerometer_waveform', data: {} }));
    binarySocket.emit(JSON.stringify({ type: 'accelerometer_sample', data: { sample: { x_g: 0 } } }));
    binarySocket.emit(JSON.stringify({ type: 'accelerometer_sample', data: {
      sample: { x_g: 0, y_g: 0, z_g: 1 }, trace: { source: 'legacy' },
    } }));
    binarySocket.emit(JSON.stringify({
      type: 'accelerometer_waveform',
      data: { sequence: 1, sample_rate: 2_000_000,
        samples_b64: btoa(String.fromCharCode(0, 0)) },
    }));
    binarySocket.emit(JSON.stringify({ type: 'other' }));
    textSocket.emit(JSON.stringify({ type: 'other' }));
    binarySocket.emitBinary(new ArrayBuffer(2));
    const malformed = new ArrayBuffer(6);
    new DataView(malformed).setUint32(0, 8, true);
    binarySocket.emitBinary(malformed);
    const invalidJson = new ArrayBuffer(8);
    new DataView(invalidJson).setUint32(0, 2, true);
    new Uint8Array(invalidJson, 4, 2).set([0x7b, 0x2d]);
    binarySocket.emitBinary(invalidJson);

    binarySocket.emitBinary(binaryPacket(2));
    const waveformTimer = timerSpy.mock.calls
      .find(([, delay]) => delay === 32)?.[0];
    if (typeof waveformTimer === 'function') {
      waveformTimer();
      waveformTimer();
      binarySocket.emitBinary(binaryPacket(3));
      workerScope.dispatch(new MessageEvent('message', {
        data: { type: 'accelerometer_waveform_ack' },
      }));
      waveformTimer();
    }
    const poseTimer = timerSpy.mock.calls
      .find(([, delay]) => delay === 16)?.[0];
    if (typeof poseTimer === 'function') {
      poseTimer();
      poseTimer();
    }
    // The acknowledgement above schedules the queued packet without creating
    // a second unbounded display queue.
    workerScope.dispatch(new MessageEvent('message', {
      data: { type: 'accelerometer_waveform_ack' },
    }));
    await vi.advanceTimersByTimeAsync(32);
    workerScope.dispatch(new MessageEvent('message', {
      data: { type: 'accelerometer_waveform_ack' },
    }));
    binarySocket.emitBinary(binaryPacket(5));
    workerScope.dispatch(new MessageEvent('message', { data: { type: 'noop' } }));
    workerScope.dispatch(new MessageEvent('message', { data: { type: 'shutdown' } }));
    workerScope.dispatch(new MessageEvent('unload'));
    expect(binarySocket.sent).toEqual([]);
  });
});
