import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { buildWaveformPayload } from '../test/waveformPayload';
import { WaveformClient } from './waveformClient';

class FakeWorker {
  static instances: FakeWorker[] = [];

  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  posted: Record<string, unknown>[] = [];
  terminated = false;

  constructor() {
    FakeWorker.instances.push(this);
  }

  postMessage(message: Record<string, unknown>) {
    this.posted.push(message);
  }

  terminate() {
    this.terminated = true;
  }

  emit(data: Record<string, unknown>) {
    this.onmessage?.({ data } as MessageEvent);
  }

  emitError(message: string) {
    this.onerror?.({ message } as ErrorEvent);
  }
}

describe('WaveformClient', () => {
  beforeEach(() => {
    FakeWorker.instances = [];
    vi.stubGlobal('Worker', FakeWorker);
  });

  afterEach(() => vi.unstubAllGlobals());

  it('correlates concurrent worker replies by id and clamps resolution', async () => {
    const client = new WaveformClient();
    const windowPromise = client.fetchWindow('window-session', 1, 20, 10, ['d0']);
    const overviewPromise = client.fetchOverview('overview-session', 32);
    const worker = FakeWorker.instances[0];

    expect(worker.posted).toEqual([
      { id: '0', type: 'window', sessionId: 'window-session', start: 1, end: 20, resolution: 512, channels: ['d0'] },
      { id: '1', type: 'overview', sessionId: 'overview-session', bins: 32 },
    ]);

    worker.emit({ id: '1', header: {}, buf: buildWaveformPayload('overview-session') });
    worker.emit({ id: '0', header: {}, buf: buildWaveformPayload('window-session') });
    await expect(overviewPromise).resolves.toMatchObject({ header: { session_id: 'overview-session' } });
    await expect(windowPromise).resolves.toMatchObject({ header: { session_id: 'window-session' } });
  });

  it('routes worker errors to the matching request', async () => {
    const client = new WaveformClient();
    const request = client.fetchOverview('broken');
    FakeWorker.instances[0].emit({ id: '0', error: 'fetch 500: broken' });
    await expect(request).rejects.toThrow('fetch 500: broken');
  });

  it('rejects all pending work on worker failure or disposal', async () => {
    const failedClient = new WaveformClient();
    const first = failedClient.fetchOverview('one');
    const second = failedClient.fetchWindow('two', 0, 1, 9_999);
    FakeWorker.instances[0].emitError('worker crashed');
    await expect(first).rejects.toThrow('worker crashed');
    await expect(second).rejects.toThrow('worker crashed');

    const disposedClient = new WaveformClient();
    const pending = disposedClient.fetchOverview('pending');
    const disposedWorker = FakeWorker.instances[1];
    disposedClient.dispose();
    expect(disposedWorker.terminated).toBe(true);
    await expect(pending).rejects.toThrow('WaveformClient disposed');
  });

  it('uses request defaults and ignores malformed or uncorrelated replies', async () => {
    const client = new WaveformClient();
    const request = client.fetchOverview('default-bins');
    const worker = FakeWorker.instances[0];
    expect(worker.posted[0]).toMatchObject({ bins: 1024 });

    worker.emit({ error: 500 });
    worker.emit({ error: 'missing id' });
    worker.emit({ id: 'unknown', error: 'unknown request' });
    worker.emit({ id: '0', header: {} });
    worker.emit({ id: '0', buf: buildWaveformPayload('missing-header') });
    worker.emit({ id: 99, header: {}, buf: buildWaveformPayload('uncorrelated') });
    worker.emit({ id: '0', header: {}, buf: buildWaveformPayload('default-bins') });

    await expect(request).resolves.toMatchObject({
      header: { session_id: 'default-bins' },
    });

    // A duplicate success has no pending request and must be ignored.
    worker.emit({ id: '0', header: {}, buf: buildWaveformPayload('duplicate') });
    client.dispose();
  });

  it('uses the fallback worker error and disposes an unused client safely', async () => {
    const failedClient = new WaveformClient();
    const pending = failedClient.fetchWindow('maximum', 0, 1, 50_000);
    expect(FakeWorker.instances[0].posted[0]).toMatchObject({ resolution: 4096 });
    FakeWorker.instances[0].onerror?.({ message: null } as unknown as ErrorEvent);
    await expect(pending).rejects.toThrow('Worker error');

    new WaveformClient().dispose();
  });
});
