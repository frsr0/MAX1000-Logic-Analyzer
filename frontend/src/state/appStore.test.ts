// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { session } from '../test/session';
import { ControlledWorker } from '../test/controlledWorker';

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  localStorage.clear();
  ControlledWorker.instances = [];
  vi.stubGlobal('Worker', ControlledWorker);
});
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('keeps the most recently requested session when metadata responses race', async () => {
  let finishOld!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOld = resolve; }))
    .mockResolvedValueOnce(Response.json(session('new'))));
  const { useApp } = await import('./appStore');
  const { waveformView } = await import('./waveformStore');
  const old = useApp.getState().openSession('old');
  await useApp.getState().openSession('new');
  finishOld(Response.json(session('old')));
  await old;
  expect([useApp.getState().activeSession?.id, waveformView.sessionId]).toEqual(['new', 'new']);
});

it('does not apply old session markers when its overview finishes after another open', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(Response.json(session('old', { num_samples: 3, markers: [
      { id: 'old-cursor', sample: 1, kind: 'cursor_a', label: '', note: '' },
    ] })))
    .mockResolvedValueOnce(Response.json(session('new'))));
  const { useApp } = await import('./appStore');
  const { waveformView } = await import('./waveformStore');
  const old = useApp.getState().openSession('old');
  await vi.advanceTimersByTimeAsync(1);
  await useApp.getState().openSession('new');
  ControlledWorker.instances[0].reply(0, 'old');
  await old;
  expect([waveformView.sessionId, waveformView.cursorA, waveformView.markers]).toEqual(['new', null, []]);
});

it('does not replace a newly opened session with a late background refresh', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(Response.json(session('old')))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce(Response.json(session('new'))));
  const { useApp } = await import('./appStore');
  await useApp.getState().openSession('old');
  const refreshing = useApp.getState().refreshActiveSession();
  await useApp.getState().openSession('new');
  finish(Response.json(session('old', { name: 'refreshed old' })));
  await refreshing;
  expect(useApp.getState().activeSession?.id).toBe('new');
});

it.each(['delta', 'rle', 'raw'] as const)('normalizes legacy %s capture settings on load and save', async (compression) => {
  localStorage.setItem('msa_capture_settings', JSON.stringify({ sample_rate: 20_000, readback_compression: compression }));
  const { useApp } = await import('./appStore');
  expect(useApp.getState().captureSettings).toMatchObject({ sample_rate: 20_000,
    num_samples: 100_000, readback_compression: compression === 'raw' ? 'raw' : 'delta_rle' });
  useApp.getState().setCaptureSettings({ readback_compression: compression, num_samples: 128 });
  expect(JSON.parse(localStorage.getItem('msa_capture_settings')!)).toMatchObject({
    sample_rate: 20_000, num_samples: 128, readback_compression: compression === 'raw' ? 'raw' : 'delta_rle',
  });
});

it.each([null, '{broken', '{"theme":"light","defaultSampleRate":5000}'])('loads viewer preferences with safe JSON fallback: %s', async (raw) => {
  if (raw !== null) localStorage.setItem('msa_viewer_settings', raw);
  localStorage.setItem('msa_capture_settings', '{broken');
  const { useApp } = await import('./appStore');
  expect(useApp.getState().captureSettings.sample_rate).toBe(1_000_000);
  expect(useApp.getState().viewerSettings).toEqual(raw?.includes('light')
    ? { theme: 'light', defaultSampleRate: 5000, defaultNumSamples: 100_000 }
    : { theme: 'dark', defaultSampleRate: 1_000_000, defaultNumSamples: 100_000 });
  useApp.getState().setViewerSettings({ theme: 'light', defaultNumSamples: 256 });
  expect(document.documentElement.dataset.theme).toBe('light');
  expect(JSON.parse(localStorage.getItem('msa_viewer_settings')!)).toMatchObject({ theme: 'light', defaultNumSamples: 256 });
});

it('updates navigation, connection and control modes through public actions', async () => {
  const { useApp } = await import('./appStore');
  useApp.getState().setPage('settings');
  useApp.getState().setWsConnected(true);
  useApp.getState().setControlMode(false);
  expect(useApp.getState()).toMatchObject({ page: 'settings', wsConnected: true, controlMode: false });
});

it('retains only the newest 500 log entries in arrival order', async () => {
  const { useApp } = await import('./appStore');
  for (let i = 0; i < 502; i++) useApp.getState().pushLog({ ts: i, level: 'INFO', logger: 'test', message: `event-${i}` });
  expect(useApp.getState().logs).toHaveLength(500);
  expect(useApp.getState().logs[0].message).toBe('event-2');
  expect(useApp.getState().logs[499].message).toBe('event-501');
});

it('expires ordinary toasts at four seconds and errors at eight, with manual dismissal', async () => {
  const { useApp } = await import('./appStore');
  useApp.getState().toast('success', 'saved');
  useApp.getState().toast('error', 'capture failed');
  useApp.getState().toast('info', 'dismiss me');
  const toasts = useApp.getState().toasts;
  expect(new Set(toasts.map((t) => t.id)).size).toBe(3);
  useApp.getState().dismissToast(toasts[2].id);
  await vi.advanceTimersByTimeAsync(3999);
  expect(useApp.getState().toasts.map((t) => t.message)).toEqual(['saved', 'capture failed']);
  await vi.advanceTimersByTimeAsync(1);
  expect(useApp.getState().toasts.map((t) => t.message)).toEqual(['capture failed']);
  await vi.advanceTimersByTimeAsync(3999);
  expect(useApp.getState().toasts).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(useApp.getState().toasts).toEqual([]);
});

it('refreshes backend state through HTTP and keeps useful state across transient failures', async () => {
  const { useApp } = await import('./appStore');
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ device_connected: true }))
    .mockResolvedValueOnce(Response.json({ digital_channels: 16 }))
    .mockResolvedValueOnce(Response.json({ sessions: [{ id: 's' }] }))
    .mockRejectedValue(new Error('offline'));
  vi.stubGlobal('fetch', fetcher);
  await useApp.getState().refreshStatus();
  await useApp.getState().refreshCapabilities();
  await useApp.getState().refreshSessions();
  expect(useApp.getState()).toMatchObject({ status: { device_connected: true },
    capabilities: { digital_channels: 16 }, sessions: [{ id: 's' }] });
  await useApp.getState().refreshStatus();
  await useApp.getState().refreshCapabilities();
  await useApp.getState().refreshSessions();
  expect(useApp.getState()).toMatchObject({ status: { device_connected: true },
    capabilities: null, sessions: [{ id: 's' }] });
});

it('loads decoder and measurement catalogs atomically and retains them on failure', async () => {
  const { useApp } = await import('./appStore');
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(Response.json({ decoders: [{ id: 'uart' }] }))
    .mockResolvedValueOnce(Response.json({ types: [{ id: 'frequency' }] }))
    .mockResolvedValueOnce(Response.json({ decoders: [{ id: 'spi' }] }))
    .mockRejectedValueOnce(new Error('offline')));
  await useApp.getState().loadCatalogs();
  expect(useApp.getState()).toMatchObject({ decoderTypes: [{ id: 'uart' }], measurementTypes: [{ id: 'frequency' }] });
  await useApp.getState().loadCatalogs();
  expect(useApp.getState()).toMatchObject({ decoderTypes: [{ id: 'uart' }], measurementTypes: [{ id: 'frequency' }] });
});

it('opens a session with both cursors and refreshes its metadata without disturbing the viewport', async () => {
  const { useApp } = await import('./appStore');
  const { waveformView } = await import('./waveformStore');
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json(session('s', { trigger_sample: 3, markers: [
      { id: 'a', kind: 'cursor_a', sample: 10, label: 'A', note: '' },
      { id: 'b', kind: 'cursor_b', sample: 20, label: 'B', note: '' },
    ] })))
    .mockResolvedValueOnce(Response.json(session('s', { name: 'renamed' })))
    .mockRejectedValueOnce(new Error('deleted'));
  vi.stubGlobal('fetch', fetcher);
  await useApp.getState().refreshActiveSession();
  expect(fetcher).not.toHaveBeenCalled();
  await useApp.getState().openSession('s');
  expect([waveformView.cursorA, waveformView.cursorB, waveformView.trigSample]).toEqual([10, 20, 3]);
  await useApp.getState().refreshActiveSession();
  expect(useApp.getState().activeSession?.name).toBe('renamed');
  await useApp.getState().refreshActiveSession();
  expect(useApp.getState().activeSession?.name).toBe('renamed');
});
