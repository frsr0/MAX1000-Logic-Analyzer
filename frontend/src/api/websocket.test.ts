import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReconnectingSocket } from './websocket';

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static throwOnConstruct = false;

  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;

  constructor(readonly url: string) {
    if (FakeWebSocket.throwOnConstruct) throw new Error('constructor failed');
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closeCalls += 1;
  }

  emitOpen() { this.onopen?.(); }
  emitMessage(data: string) { this.onmessage?.({ data }); }
  emitClose() { this.onclose?.(); }
  emitError() { this.onerror?.(); }
}

describe('ReconnectingSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    FakeWebSocket.throwOnConstruct = false;
    vi.stubGlobal('location', { protocol: 'https:', host: 'scope.test' });
    vi.stubGlobal('WebSocket', FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('delivers valid messages, ignores malformed data, and reconnects', () => {
    const socket = new ReconnectingSocket('/ws/status');
    const states: boolean[] = [];
    const messages: unknown[] = [];
    socket.onStateChange = (connected) => states.push(connected);
    const unsubscribe = socket.subscribe((message) => messages.push(message));
    const first = FakeWebSocket.instances[0];

    expect(first.url).toBe('wss://scope.test/ws/status');
    first.emitOpen();
    first.emitMessage('{bad json');
    first.emitMessage(JSON.stringify({ type: 'status_snapshot', data: { ready: true } }));
    expect(states).toEqual([true]);
    expect(messages).toHaveLength(1);

    unsubscribe();
    first.emitMessage(JSON.stringify({ type: 'status_snapshot', data: {} }));
    expect(messages).toHaveLength(1);
    first.emitClose();
    expect(states).toEqual([true, false]);
    vi.advanceTimersByTime(500);
    expect(FakeWebSocket.instances).toHaveLength(2);

    const second = FakeWebSocket.instances[1];
    second.emitError();
    expect(second.closeCalls).toBe(1);
    socket.close();
    second.emitClose();
    vi.runAllTimers();
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it('retries when WebSocket construction throws', () => {
    FakeWebSocket.throwOnConstruct = true;
    const socket = new ReconnectingSocket('/ws/capture');
    expect(FakeWebSocket.instances).toHaveLength(0);
    FakeWebSocket.throwOnConstruct = false;
    vi.advanceTimersByTime(500);
    expect(FakeWebSocket.instances).toHaveLength(1);
    socket.close();
  });

  it('covers insecure URLs, capped backoff, timer replacement, and closed guards', () => {
    vi.stubGlobal('location', { protocol: 'http:', host: 'scope.test' });
    FakeWebSocket.throwOnConstruct = true;
    const socket = new ReconnectingSocket('/ws/status');

    // Repeated construction failures exercise timer replacement and the
    // 10-second retry cap without waiting in real time.
    for (let attempt = 0; attempt < 8; attempt += 1) {
      vi.runOnlyPendingTimers();
    }

    FakeWebSocket.throwOnConstruct = false;
    vi.runOnlyPendingTimers();
    const connected = FakeWebSocket.instances[0];
    expect(connected.url).toBe('ws://scope.test/ws/status');

    // All callbacks are optional, so opening/closing without a listener must
    // remain safe. Exercise the null socket side of the error callback too.
    connected.emitOpen();
    (socket as unknown as { ws: WebSocket | null }).ws = null;
    connected.emitError();
    connected.emitClose();
    socket.close();

    const internals = socket as unknown as {
      connect: () => void;
      scheduleReconnect: () => void;
    };
    internals.connect();
    internals.scheduleReconnect();
    vi.runAllTimers();
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('closes safely before a WebSocket was constructed', () => {
    FakeWebSocket.throwOnConstruct = true;
    const socket = new ReconnectingSocket('/ws/status');
    socket.close();
    vi.runAllTimers();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('closes a newly connected socket before any retry timer exists', () => {
    const socket = new ReconnectingSocket('/ws/status');
    const connected = FakeWebSocket.instances[0];
    socket.close();
    expect(connected.closeCalls).toBe(1);
  });

  it('delivers binary ArrayBuffer and Blob payloads, answers pings, and unsubscribes binary handlers', async () => {
    const socket = new ReconnectingSocket('/ws/accelerometer/waveform-binary', true);
    const ws = FakeWebSocket.instances[0];
    const binary: ArrayBuffer[] = [];
    const unsubscribe = socket.subscribeBinary((data) => binary.push(data));
    ws.emitMessage(JSON.stringify({ type: 'ping' }));
    expect(ws.closeCalls).toBe(0);

    ws.emitMessage({ type: 'not-json' } as unknown as string);
    ws.onmessage?.({ data: new ArrayBuffer(2) } as never);
    ws.onmessage?.({ data: new Blob([new Uint8Array([1, 2, 3])]) } as never);
    await vi.waitFor(() => expect(binary).toHaveLength(2));
    expect(binary).toHaveLength(2);
    unsubscribe();
    ws.onmessage?.({ data: new ArrayBuffer(1) } as never);
    expect(binary).toHaveLength(2);
    socket.close();
  });

  it('ignores Blob conversion failures and does not deliver after close', async () => {
    const socket = new ReconnectingSocket('/ws/accelerometer/waveform-binary', true);
    const ws = FakeWebSocket.instances[0];
    const binary: ArrayBuffer[] = [];
    socket.subscribeBinary((data) => binary.push(data));
    const original = Blob.prototype.arrayBuffer;
    Blob.prototype.arrayBuffer = vi.fn().mockRejectedValue(new Error('blob failed'));
    ws.onmessage?.({ data: new Blob([new Uint8Array([1, 2])]) } as never);
    socket.close();
    Blob.prototype.arrayBuffer = original;
    await Promise.resolve();
    expect(binary).toEqual([]);

    const second = new ReconnectingSocket('/ws/accelerometer/waveform-binary', true);
    const secondWs = FakeWebSocket.instances[1];
    let resolveBuffer!: (buffer: ArrayBuffer) => void;
    const originalAgain = Blob.prototype.arrayBuffer;
    Blob.prototype.arrayBuffer = vi.fn(() => new Promise<ArrayBuffer>((resolve) => {
      resolveBuffer = resolve;
    }));
    const delivered: ArrayBuffer[] = [];
    second.subscribeBinary((data) => delivered.push(data));
    secondWs.onmessage?.({ data: new Blob([new Uint8Array([1])]) } as never);
    second.close();
    resolveBuffer(new ArrayBuffer(1));
    Blob.prototype.arrayBuffer = originalAgain;
    await Promise.resolve();
    expect(delivered).toEqual([]);
  });
});
