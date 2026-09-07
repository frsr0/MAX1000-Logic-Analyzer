import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { buildWaveformPayload } from '../test/waveformPayload';
import { parseWaveformPayload } from '../api/binary';

let receive: (event: MessageEvent) => Promise<void>;
let post: ReturnType<typeof vi.fn>;
beforeEach(async () => {
  vi.resetModules();
  post = vi.fn();
  const scope = { onmessage: null, postMessage: post };
  vi.stubGlobal('self', scope);
  await import('./waveform.worker');
  receive = scope.onmessage!;
});
afterEach(() => vi.unstubAllGlobals());

it.each([
  [{ type: 'window', start: 1.9, end: 9.1, resolution: 512, channels: ['d0', 'a0'] }, '/api/sessions/s/waveform?start=1&end=10&resolution=512&channels=d0,a0'],
  [{ type: 'window', start: 0, end: 3, resolution: 1024 }, '/api/sessions/s/waveform?start=0&end=3&resolution=1024'],
  [{ type: 'window', start: 0, end: 3, resolution: 1024, channels: [] }, '/api/sessions/s/waveform?start=0&end=3&resolution=1024'],
  [{ type: 'overview' }, '/api/sessions/s/overview?bins=1024'],
  [{ type: 'overview', bins: 256 }, '/api/sessions/s/overview?bins=256'],
])('returns parsed, transferable waveform data for %j', async (request, url) => {
  const buf = buildWaveformPayload('s');
  const fetcher = vi.fn(async () => new Response(buf));
  vi.stubGlobal('fetch', fetcher);
  await receive(new MessageEvent('message', { data: { ...request, id: 'job-1', sessionId: 's' } }));
  expect(fetcher).toHaveBeenCalledWith(url);
  const [result, transfer] = post.mock.calls[0];
  expect(result.id).toBe('job-1');
  expect(result.header).toMatchObject({ session_id: 's', num_samples: 3 });
  expect(parseWaveformPayload(result.buf).arrays.get('digital')).toEqual(new Uint16Array([1, 2, 3]));
  expect(transfer).toEqual([result.buf]);
});

it('reports HTTP failures with the request id, even if the response body cannot be read', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response('unavailable', { status: 503 }))
    .mockResolvedValueOnce({ ok: false, status: 502, text: async () => { throw new Error('broken stream'); } });
  vi.stubGlobal('fetch', fetcher);
  await receive(new MessageEvent('message', { data: { type: 'overview', id: 'a', sessionId: 's' } }));
  await receive(new MessageEvent('message', { data: { type: 'overview', id: 'b', sessionId: 's' } }));
  expect(post.mock.calls).toEqual([[{ id: 'a', error: 'fetch 503: unavailable' }], [{ id: 'b', error: 'fetch 502: ' }]]);
});

it.each([new Error('offline'), 'offline'])('reports transport failures without leaving the caller pending', async (error) => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));
  await receive(new MessageEvent('message', { data: { type: 'overview', id: 'a', sessionId: 's' } }));
  expect(post.mock.calls).toEqual([[{ id: 'a', error: 'offline' }]]);
});

it('rejects a malformed binary payload instead of transferring corrupt samples', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new Uint8Array(8))));
  await receive(new MessageEvent('message', { data: { type: 'overview', id: 'bad', sessionId: 's' } }));
  expect(post.mock.calls).toEqual([[{ id: 'bad', error: 'Bad waveform payload magic' }]]);
});
