import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WaveformView } from './waveformStore';
import { ControlledWorker } from '../test/controlledWorker';
import type { ChannelInfo, DecoderEvent } from '../api/types';

beforeEach(() => {
  vi.useFakeTimers();
  ControlledWorker.instances = [];
  ControlledWorker.throwNext = undefined;
  vi.stubGlobal('Worker', ControlledWorker);
  vi.stubGlobal('window', { innerWidth: 1000 });
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ events: [] })));
});

it('does not publish an old viewport while a new session is opening', async () => {
  const view = new WaveformView();
  const old = view.load('old', 100, 1000, null);
  const worker = ControlledWorker.instances[0];
  worker.reply(0, 'old');
  await old;
  await vi.advanceTimersByTimeAsync(1);
  expect(worker.messages[1]).toMatchObject({ type: 'window', sessionId: 'old' });
  const current = view.load('current', 200, 2000, null);
  worker.reply(1, 'old');
  await Promise.resolve();
  await Promise.resolve();
  expect(view.payload).toBeNull();
  worker.reply(2, 'current');
  await current;
  await vi.advanceTimersByTimeAsync(1);
  worker.reply(3, 'current');
  await vi.advanceTimersByTimeAsync(1);
  expect(view.payload?.header.session_id).toBe('current');
  expect(view.loading).toBe(false);
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('keeps the newest session overview when an earlier load finishes late', async () => {
  const view = new WaveformView();
  const old = view.load('old', 100, 1000, null);
  const worker = ControlledWorker.instances[0];
  const current = view.load('current', 200, 2000, 50);
  worker.reply(1, 'current');
  await current;
  worker.reply(0, 'old');
  await old;
  expect({ id: view.sessionId, overview: view.overview?.header.session_id,
    samples: view.numSamples, rate: view.sampleRate, trigger: view.trigSample,
  }).toEqual({ id: 'current', overview: 'current', samples: 200, rate: 2000, trigger: 50 });
});

it('discards late annotations after the user selects a different session', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  const view = new WaveformView();
  await view.load('old', 0, 1000, null);
  view.requestAnnotations(0);
  await vi.advanceTimersByTimeAsync(1);
  await view.load('current', 0, 1000, null);
  finish(Response.json({ events: [{ decoder_id: 'old-uart', text: 'old byte' }] }));
  await vi.advanceTimersByTimeAsync(1);
  expect(view.annotations).toEqual([]);
});

it('does not restore old markers or cursors after switching sessions', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal('localStorage', { getItem: () => 'test-client' });
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; })));
  const view = new WaveformView();
  await view.load('old', 0, 1000, null);
  const refreshing = view.refreshMarkers();
  await view.load('current', 0, 1000, null);
  finish(Response.json({ markers: [{ id: 'old-a', kind: 'cursor_a', sample: 99 }] }));
  await refreshing;
  expect({ markers: view.markers, cursor: view.cursorA }).toEqual({ markers: [], cursor: null });
});

it('clears previously loaded markers immediately when opening another session', async () => {
  const view = new WaveformView();
  vi.stubGlobal('localStorage', { getItem: () => 'test-client' });
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ markers: [
    { id: 'old-note', kind: 'bookmark', sample: 99 },
  ] })));
  await view.load('old', 0, 1000, null);
  await view.refreshMarkers();
  expect(view.markers).toHaveLength(1);
  await view.load('current', 0, 1000, null);
  expect(view.markers).toEqual([]);
});

it('clamps pan and zoom to the sample bounds and preserves the zoom anchor', async () => {
  const view = new WaveformView();
  await view.load('', 100, 1000, null);
  view.setView(20, 60);
  view.zoomAround(30, 0.5);
  expect([view.start, view.end]).toEqual([25, 45]);
  view.pan(-100);
  expect([view.start, view.end]).toEqual([0, 20]);
  view.pan(200);
  expect([view.start, view.end]).toEqual([80, 100]);
  view.jumpTo(50);
  expect([view.start, view.end]).toEqual([40, 60]);
  view.setView(50, 51);
  expect(view.span()).toBe(8);
  view.fit();
  expect([view.displayStart(), view.displayEnd()]).toEqual([0, 100]);
  expect(view.liveAnimating()).toBe(false);
  expect(view.liveShiftSamples(1e9)).toBe(0);
});

it('notifies only subscribed observers and supports row selection toggles', () => {
  const view = new WaveformView();
  const snapshots: string[][] = [];
  const unsubscribe = view.subscribe(() => snapshots.push([...view.selectedRows]));
  view.clearRowSelection();
  view.selectRow('d0', false);
  view.selectRow('d0', false);
  view.selectRow('d0', true);
  view.selectRow('d1', true);
  view.selectRow('d0', true);
  view.clearRowSelection();
  unsubscribe();
  view.selectRow('d2', false);
  expect(snapshots).toEqual([['d0'], [], ['d0'], ['d0', 'd1'], ['d1'], []]);
});

it('bounds row heights and applies presets only to known visible rows', () => {
  const view = new WaveformView();
  expect(view.rowScale('d0')).toBe(1);
  view.setRowScales(['d0', 'a0'], -3);
  expect(view.rowScale('d0')).toBe(0.5);
  view.scaleRowsBy(['d0', 'd1'], 4);
  expect([view.rowScale('d0'), view.rowScale('d1')]).toEqual([2, 4]);
  view.setRowScales(['a0'], 99);
  expect(view.rowScale('a0')).toBe(8);
  view.knownRowIds = ['d0', 'a0'];
  view.setAllScales(2);
  expect([...view.heightScale]).toEqual([['d0', 2], ['a0', 2]]);
  view.setAllScales(1);
  expect([...view.heightScale]).toEqual([]);
});

it('loads persisted row heights and resets selections on opening a session', async () => {
  const view = new WaveformView();
  view.selectRow('old', false);
  await view.load('', 0, 1000, null, [
    { id: 'd0', display_height_scale: 2 }, { id: 'd1' }, { id: 'd2', display_height_scale: 1 },
  ] as ChannelInfo[]);
  expect([...view.heightScale]).toEqual([['d0', 2]]);
  expect([...view.selectedRows]).toEqual([]);
  expect(view.end).toBe(1);
});

it('persists row heights through the session API and tolerates storage failure', async () => {
  const view = new WaveformView();
  vi.stubGlobal('localStorage', { getItem: () => 'viewer' });
  const fetcher = vi.fn(async () => Response.json({}));
  vi.stubGlobal('fetch', fetcher);
  await view.commitRowHeights(['d0']);
  await view.load('s', 0, 1000, null);
  await view.commitRowHeights([]);
  expect(fetcher).not.toHaveBeenCalled();
  view.setRowScales(['d0'], 2);
  await view.commitRowHeights(['d0', 'd1']);
  expect(fetcher).toHaveBeenCalledWith('/api/sessions/s', expect.objectContaining({
    method: 'PATCH', body: '{"channels":[{"id":"d0","display_height_scale":2},{"id":"d1","display_height_scale":1}]}',
  }));
  fetcher.mockRejectedValueOnce(new Error('offline'));
  await expect(view.commitRowHeights(['d0'])).resolves.toBeUndefined();
  expect(view.rowScale('d0')).toBe(2);
});

it('uses first-seen decoder order, deduplicates rows, and limits them to six', () => {
  const view = new WaveformView();
  expect(view.decoderRows()).toEqual([]);
  view.annotations = ['uart', 'uart', 'spi', 'i2c', 'can', 'lin', 'swd', 'extra']
    .map((decoder_id) => ({ decoder_id })) as DecoderEvent[];
  expect(view.decoderRows()).toEqual(['uart', 'spi', 'i2c', 'can', 'lin', 'swd']);
});

it.each([1, -1] as const)('jumps to the nearest edge in direction %s', async (direction) => {
  const view = new WaveformView();
  await view.load('s', 0, 1000, null);
  view.numSamples = 100;
  view.setView(40, 60);
  vi.stubGlobal('localStorage', { getItem: () => 'viewer' });
  const fetcher = vi.fn(async (_url: string) => Response.json({ edges: direction === 1 ? [70, 90] : [10, 30] }));
  vi.stubGlobal('fetch', fetcher);
  expect(await view.jumpToEdge('d0', 50.9, direction)).toBe(direction === 1 ? 70 : 30);
  expect([view.start, view.end]).toEqual(direction === 1 ? [60, 80] : [20, 40]);
  expect(fetcher.mock.calls[0][0]).toBe(direction === 1
    ? '/api/sessions/s/edges?channel=d0&kind=any&start=51&end=-1&limit=50000'
    : '/api/sessions/s/edges?channel=d0&kind=any&start=0&end=50&limit=50000');
  fetcher.mockResolvedValueOnce(Response.json({ edges: [] }));
  expect(await view.jumpToEdge('d0', 50, direction)).toBeNull();
  fetcher.mockRejectedValueOnce(new Error('offline'));
  expect(await view.jumpToEdge('d0', 50, direction)).toBeNull();
});

it('coalesces rapid live chunks and fetches the newest window without starvation', async () => {
  vi.stubGlobal('performance', { now: () => 1000 });
  const view = new WaveformView();
  const loading = view.load('live', 100, 1000, null);
  const worker = ControlledWorker.instances[0];
  worker.reply(0, 'live');
  await loading;
  await vi.advanceTimersByTimeAsync(1);
  await view.updateLive(200, 1000, 20, true, 25);
  await vi.advanceTimersByTimeAsync(1);
  await view.updateLive(300, 1000, 30, true, 999);
  await vi.advanceTimersByTimeAsync(1);
  expect(worker.messages.filter((m) => m.type === 'window')).toHaveLength(1);
  expect([view.start, view.end, view.liveChunkSamples]).toEqual([200, 300, 300]);
  worker.reply(2, 'live');
  worker.reply(1, 'live');
  await vi.advanceTimersByTimeAsync(2);
  expect(worker.messages[3]).toMatchObject({ type: 'window', start: 200, end: 300 });
  worker.reply(3, 'live');
  await vi.advanceTimersByTimeAsync(2);
  expect(view.payload?.header.session_id).toBe('live');
  expect(view.loading).toBe(false);
  expect(view.error).toBeNull();
  expect(view.liveUpdatedAt).toBe(1000);
  view.setLiveFollow(false);
  expect(view.liveFollow).toBe(false);
  await view.updateLive(400, 1000, null, true, -1);
  await vi.advanceTimersByTimeAsync(2);
  worker.reply(4, 'live');
  await vi.advanceTimersByTimeAsync(2);
  expect(view.liveChunkSamples).toBe(0);
});

it('preserves a manually panned live window but follows the previous right edge', async () => {
  const view = new WaveformView();
  await view.load('s', 0, 1000, null);
  view.numSamples = 100;
  view.setView(20, 40);
  await view.updateLive(120, 1000, null, false);
  expect([view.start, view.end, view.payload, view.liveRolling]).toEqual([20, 40, null, false]);
  view.setView(100, 120);
  await view.updateLive(150, 1000, null, false);
  expect([view.start, view.end]).toEqual([130, 150]);
  ControlledWorker.instances[0].fail(0, 'overview unavailable');
  await vi.advanceTimersByTimeAsync(1);
  expect(view.error).toBeNull();
});

it('ignores a live overview that arrives after a session change', async () => {
  const view = new WaveformView();
  await view.load('old', 0, 1000, null);
  await view.updateLive(20, 1000, null);
  await view.load('new', 0, 1000, null);
  ControlledWorker.instances[0].reply(0, 'old');
  await vi.advanceTimersByTimeAsync(1);
  expect(view.overview).toBeNull();
});

it('debounces window changes and sends the selected channels and bounded pixel resolution', async () => {
  vi.stubGlobal('window', { innerWidth: 0 });
  const view = new WaveformView();
  const loading = view.load('s', 1000, 1000, null);
  const worker = ControlledWorker.instances[0];
  worker.reply(0, 's');
  await loading;
  view.setView(20.2, 60.8);
  view.setChannelFilter(['d0', 'a0']);
  await vi.advanceTimersByTimeAsync(1);
  expect(worker.messages[1]).toMatchObject({ start: 20, end: 61, resolution: 1800, channels: ['d0', 'a0'] });
  worker.fail(1, 'window unavailable');
  await vi.advanceTimersByTimeAsync(1);
  expect([view.error, view.loading]).toEqual(['window unavailable', false]);
  view.requestFetch();
  await vi.advanceTimersByTimeAsync(59);
  expect(worker.messages).toHaveLength(2);
  await vi.advanceTimersByTimeAsync(1);
  worker.reply(2, 's');
  await vi.advanceTimersByTimeAsync(1);
  expect([view.error, view.loading]).toEqual([null, false]);
});

it('reports overview failures but still requests the viewport, ignoring obsolete errors', async () => {
  const view = new WaveformView();
  const old = view.load('old', 10, 1000, null);
  const worker = ControlledWorker.instances[0];
  const current = view.load('new', 10, 1000, null);
  worker.fail(0, 'obsolete');
  await old;
  expect(view.error).toBeNull();
  worker.fail(1, 'overview unavailable');
  await current;
  expect(view.error).toBe('overview unavailable');
  await vi.advanceTimersByTimeAsync(1);
  expect(worker.messages[2]).toMatchObject({ type: 'window', sessionId: 'new' });
});

it('ignores obsolete viewport errors and does not fetch after the session is cleared', async () => {
  const view = new WaveformView();
  const loaded = view.load('old', 10, 1000, null);
  const worker = ControlledWorker.instances[0];
  worker.reply(0, 'old');
  await loaded;
  await vi.advanceTimersByTimeAsync(1);
  await view.load('', 0, 1000, null);
  worker.fail(1, 'obsolete');
  await vi.advanceTimersByTimeAsync(120);
  expect(view.error).toBeNull();
  await view.load('empty', 0, 1000, null);
  view.requestFetch(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(worker.messages).toHaveLength(2);
});

it('reports non-Error failures from the browser worker boundary', async () => {
  ControlledWorker.throwNext = 'worker unavailable';
  const view = new WaveformView();
  await view.load('s', 10, 1000, null);
  expect(view.error).toBe('worker unavailable');
  ControlledWorker.throwNext = 'viewport unavailable';
  view.requestFetch(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(view.error).toBe('viewport unavailable');
});

it('updates annotations and cursors, retaining them when optional endpoints fail', async () => {
  const view = new WaveformView();
  vi.stubGlobal('localStorage', { getItem: () => 'viewer' });
  const fetcher = vi.fn(async () => Response.json({ events: [], markers: [] }));
  vi.stubGlobal('fetch', fetcher);
  await view.refreshMarkers();
  view.requestAnnotations(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(fetcher).not.toHaveBeenCalled();
  await view.load('s', 0, 1000, null);
  fetcher.mockResolvedValueOnce(Response.json({ events: [{ decoder_id: 'uart' }] }));
  view.requestAnnotations(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(view.decoderRows()).toEqual(['uart']);
  fetcher.mockResolvedValueOnce(new Response('unavailable', { status: 503 }));
  view.requestAnnotations(0);
  await vi.advanceTimersByTimeAsync(1);
  fetcher.mockRejectedValueOnce(new Error('offline'));
  view.requestAnnotations(0);
  await vi.advanceTimersByTimeAsync(1);
  expect(view.decoderRows()).toEqual(['uart']);
  fetcher.mockResolvedValueOnce(Response.json({ markers: [
    { id: 'a', kind: 'cursor_a', sample: 10 }, { id: 'b', kind: 'cursor_b', sample: 20 },
  ] }));
  await view.refreshMarkers();
  expect([view.cursorA, view.cursorB]).toEqual([10, 20]);
  fetcher.mockRejectedValueOnce(new Error('offline'));
  await view.refreshMarkers();
  expect(view.markers).toHaveLength(2);
  await view.refreshMarkers();
  expect(view.markers).toEqual([]);
});
