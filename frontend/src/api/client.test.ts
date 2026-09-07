// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError, clientId, downloadDebugBundle, downloadExport } from './client';
import { buildWaveformPayload } from '../test/waveformPayload';
import { defaultCaptureSettings } from './types';

const json = (value: unknown = {}) => Response.json(value);
beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
afterEach(() => vi.unstubAllGlobals());

it('creates one stable browser control identity', () => {
  vi.spyOn(Math, 'random').mockReturnValue(0.5);
  expect(clientId()).toBe('web_i');
  expect(clientId()).toBe('web_i');
  expect(localStorage.getItem('msa_client_id')).toBe('web_i');
});

it('preserves HTTP status and message in API errors', () => {
  const error = new ApiError(409, 'held by another client');
  expect({ name: error.name, message: error.message, status: error.status })
    .toEqual({ name: 'Error', message: 'held by another client', status: 409 });
});

type Case = { name: string; call: () => Promise<unknown>; method: string; url: string; body?: unknown };
const cfg = defaultCaptureSettings();
const gen = { protocol: 'uart', data_hex: '', baud: 9600, tx_pin: 0, scl_pin: 1,
  i2c_address: 0x20, i2c_register: 0, i2c_read_len: 1, freq_hz: 1000,
  duty_pct: 50, repeat: 1, continuous: false };
const cases: Case[] = [
  { name: 'status', call: api.status, method: 'GET', url: '/api/status' },
  { name: 'acquire control', call: () => api.acquireControl('browser', true), method: 'POST', url: '/api/control/acquire', body: { name: 'browser', force: true } },
  { name: 'acquire defaults', call: () => api.acquireControl('browser'), method: 'POST', url: '/api/control/acquire', body: { name: 'browser', force: false } },
  { name: 'release control', call: api.releaseControl, method: 'POST', url: '/api/control/release' },
  { name: 'devices', call: api.devices, method: 'GET', url: '/api/devices' },
  { name: 'connect', call: () => api.connect('hardware'), method: 'POST', url: '/api/connect', body: { device_id: 'hardware' } },
  { name: 'disconnect', call: api.disconnect, method: 'POST', url: '/api/disconnect' },
  { name: 'device metadata', call: api.deviceMetadata, method: 'GET', url: '/api/device/metadata' },
  { name: 'capabilities', call: api.capabilities, method: 'GET', url: '/api/device/capabilities' },
  { name: 'device debug', call: api.deviceDebug, method: 'GET', url: '/api/device/debug' },
  { name: 'self test', call: api.selfTest, method: 'POST', url: '/api/device/self-test' },
  { name: 'start capture', call: () => api.startCapture(cfg, 'strict'), method: 'POST', url: '/api/capture/start', body: { settings: cfg, name: 'strict' } },
  { name: 'stop capture', call: api.stopCapture, method: 'POST', url: '/api/capture/stop' },
  { name: 'capture state', call: api.captureState, method: 'GET', url: '/api/capture/state' },
  { name: 'validate settings', call: () => api.validateSettings(cfg), method: 'POST', url: '/api/capture/settings/validate', body: cfg },
  { name: 'mock scenarios', call: api.mockScenarios, method: 'GET', url: '/api/capture/scenarios' },
  { name: 'sessions', call: () => api.sessions('a & b', 20, 5), method: 'GET', url: '/api/sessions?search=a%20%26%20b&offset=20&limit=5' },
  { name: 'sessions defaults', call: () => api.sessions(), method: 'GET', url: '/api/sessions?search=&offset=0&limit=100' },
  { name: 'session', call: () => api.session('s'), method: 'GET', url: '/api/sessions/s' },
  { name: 'patch session', call: () => api.patchSession('s', { name: 'N' }), method: 'PATCH', url: '/api/sessions/s', body: { name: 'N' } },
  { name: 'delete session', call: () => api.deleteSession('s'), method: 'DELETE', url: '/api/sessions/s' },
  { name: 'duplicate session', call: () => api.duplicateSession('s'), method: 'POST', url: '/api/sessions/s/duplicate' },
  { name: 'compare sessions', call: () => api.compareSessions('a', 'b', 12), method: 'POST', url: '/api/sessions/a/compare/b?alignment_offset=12' },
  { name: 'compare defaults', call: () => api.compareSessions('a', 'b'), method: 'POST', url: '/api/sessions/a/compare/b' },
  { name: 'trigger search', call: () => api.triggerSearch('s', { type: 'uart' }, 'dec', true), method: 'POST', url: '/api/sessions/s/trigger-search', body: { trigger: { type: 'uart' }, decoder_instance: 'dec', auto_scope: true } },
  { name: 'capture job', call: () => api.submitCaptureJob(cfg, 'job'), method: 'POST', url: '/api/capture/jobs', body: { settings: cfg, name: 'job' } },
  { name: 'capture job state', call: () => api.captureJob('j'), method: 'GET', url: '/api/capture/jobs/j' },
  { name: 'dashboard', call: () => api.sessionDashboard('s'), method: 'GET', url: '/api/sessions/s/dashboard?bins=32' },
  { name: 'import json', call: () => api.importSession('{}'), method: 'POST', url: '/api/sessions', body: { json_text: '{}' } },
  { name: 'import waveform', call: () => api.importWaveform('0,1', 'csv'), method: 'POST', url: '/api/sessions', body: { source_text: '0,1', source_format: 'csv', sample_rate: 1_000_000 } },
  { name: 'waveform metadata', call: () => api.waveformMeta('s'), method: 'GET', url: '/api/sessions/s/metadata' },
  { name: 'edges', call: () => api.edges('s', 'd0', 'rising'), method: 'GET', url: '/api/sessions/s/edges?channel=d0&kind=rising&start=0&end=-1&limit=5000' },
  { name: 'value at', call: () => api.valueAt('s', 3, ['d0', 'a0']), method: 'GET', url: '/api/sessions/s/value-at?sample=3&channels=d0,a0' },
  { name: 'raw window', call: () => api.rawWindow('s', 2, 9), method: 'GET', url: '/api/sessions/s/raw?start=2&end=9' },
  { name: 'sanity', call: () => api.sanity('s'), method: 'GET', url: '/api/sessions/s/sanity' },
  { name: 'bus', call: () => api.addBus('s', 'data', ['d0'], 'bin'), method: 'POST', url: '/api/sessions/s/buses', body: { name: 'data', members: ['d0'], display_base: 'bin' } },
  { name: 'derived', call: () => api.addDerivedChannel('s', 'd0', { op: 'not' }, 'not d0'), method: 'POST', url: '/api/sessions/s/derived-channels', body: { source: 'd0', derive: { op: 'not' }, name: 'not d0' } },
  { name: 'spectrum', call: () => api.spectrum('s', 'a0'), method: 'GET', url: '/api/sessions/s/spectrum?channel=a0&start=0&end=-1' },
  { name: 'spectrogram', call: () => api.spectrogram('s', 'a0'), method: 'GET', url: '/api/sessions/s/spectrogram?channel=a0&start=0&end=-1' },
  { name: 'correlation', call: () => api.correlation('s', 'a 0', 'd&0'), method: 'GET', url: '/api/sessions/s/correlation?channel_a=a%200&channel_b=d%260&start=0&end=-1' },
  { name: 'envelope', call: () => api.envelope('s', 'a 0'), method: 'GET', url: '/api/sessions/s/envelope?channel=a%200&bins=512' },
  { name: 'threshold', call: () => api.thresholdSweep('s', 'a 0'), method: 'GET', url: '/api/sessions/s/threshold-sweep?channel=a%200&levels=16' },
  { name: 'event correlation', call: () => api.eventCorrelation('s', 'a 0', 'd&0', 2.5, 'falling'), method: 'GET', url: '/api/sessions/s/event-correlation?analog_channel=a+0&digital_channel=d%260&edge=falling&threshold=2.5' },
  { name: 'event correlation default threshold', call: () => api.eventCorrelation('s', 'a0', 'd0'), method: 'GET', url: '/api/sessions/s/event-correlation?analog_channel=a0&digital_channel=d0&edge=rising' },
  { name: 'eye', call: () => api.eyeDiagram('s', 'd 0', 9600), method: 'GET', url: '/api/sessions/s/eye?channel=d%200&baud=9600' },
  { name: 'timing', call: () => api.timingSuspects('s', 'd 0'), method: 'GET', url: '/api/sessions/s/timing-suspects?channel=d%200' },
  { name: 'decoder catalog', call: api.decoderTypes, method: 'GET', url: '/api/decoders' },
  { name: 'add decoder', call: () => api.addDecoder('s', { decoder_id: 'uart', channels: { rx: 'd0' } }), method: 'POST', url: '/api/sessions/s/decoders', body: { decoder_id: 'uart', channels: { rx: 'd0' } } },
  { name: 'patch decoder', call: () => api.patchDecoder('s', 'd', { enabled: false }), method: 'PATCH', url: '/api/sessions/s/decoders/d', body: { enabled: false } },
  { name: 'delete decoder', call: () => api.deleteDecoder('s', 'd'), method: 'DELETE', url: '/api/sessions/s/decoders/d' },
  { name: 'run decoder', call: () => api.runDecoder('s', 'd'), method: 'POST', url: '/api/sessions/s/decoders/d/run', body: { region: null } },
  { name: 'cancel decoder', call: () => api.cancelDecoder('s', 'd'), method: 'POST', url: '/api/sessions/s/decoders/d/cancel' },
  { name: 'decoder annotations', call: () => api.decoderAnnotations('s', 'd', 1.9, 9.1), method: 'GET', url: '/api/sessions/s/decoders/d/annotations?start=1&end=10&limit=3000' },
  { name: 'decoder table', call: () => api.decoderTable('s', 'd', 0, 10, 'a&b', 'warning'), method: 'GET', url: '/api/sessions/s/decoders/d/table?offset=0&limit=10&search=a%26b&severity=warning' },
  { name: 'measurement catalog', call: api.measurementTypes, method: 'GET', url: '/api/measurements/types' },
  { name: 'add measurement', call: () => api.addMeasurement('s', { type: 'frequency', channels: ['d0'] }), method: 'POST', url: '/api/sessions/s/measurements', body: { type: 'frequency', channels: ['d0'] } },
  { name: 'delete measurement', call: () => api.deleteMeasurement('s', 'm'), method: 'DELETE', url: '/api/sessions/s/measurements/m' },
  { name: 'measurement results', call: () => api.measurementResults('s', 1.9, 9.1), method: 'GET', url: '/api/sessions/s/measurements/results?cursor_a=1&cursor_b=9' },
  { name: 'measurement defaults', call: () => api.measurementResults('s'), method: 'GET', url: '/api/sessions/s/measurements/results' },
  { name: 'markers', call: () => api.markers('s'), method: 'GET', url: '/api/sessions/s/markers' },
  { name: 'add marker', call: () => api.addMarker('s', { sample: 2 }), method: 'POST', url: '/api/sessions/s/markers', body: { sample: 2 } },
  { name: 'patch marker', call: () => api.patchMarker('s', 'm', { note: 'n' }), method: 'PATCH', url: '/api/sessions/s/markers/m', body: { note: 'n' } },
  { name: 'delete marker', call: () => api.deleteMarker('s', 'm'), method: 'DELETE', url: '/api/sessions/s/markers/m' },
  { name: 'generator capabilities', call: api.generatorCapabilities, method: 'GET', url: '/api/generator/capabilities' },
  { name: 'generator configure', call: () => api.generatorConfigure(gen), method: 'POST', url: '/api/generator/configure', body: gen },
  { name: 'generator start', call: api.generatorStart, method: 'POST', url: '/api/generator/start' },
  { name: 'generator stop', call: api.generatorStop, method: 'POST', url: '/api/generator/stop' },
  { name: 'generator status', call: api.generatorStatus, method: 'GET', url: '/api/generator/status' },
  { name: 'generator preview', call: () => api.generatorPreview(gen), method: 'POST', url: '/api/generator/preview', body: gen },
  { name: 'sweep preview', call: () => api.generatorSweepPreview({ base: gen, axes: {} }), method: 'POST', url: '/api/generator/sweep-preview', body: { base: gen, axes: {} } },
  { name: 'sweep capture', call: () => api.generatorSweepCapture({ base: gen, axes: {} }), method: 'POST', url: '/api/generator/sweep-capture', body: { base: gen, axes: {} } },
  { name: 'bitbang presets', call: api.bitbangPresets, method: 'GET', url: '/api/generator/bitbang/presets' },
  { name: 'generator send', call: () => api.generatorSend({ config: gen, capture: true }), method: 'POST', url: '/api/generator/send', body: { config: gen, capture: true } },
  { name: 'generator self test', call: api.generatorSelfTest, method: 'POST', url: '/api/generator/self-test' },
  { name: 'serial status', call: api.virtualSerialStatus, method: 'GET', url: '/api/serial/virtual' },
  { name: 'com pair', call: () => api.createVirtualComPair('COM20', 'COM21'), method: 'POST', url: '/api/serial/virtual/com-pair', body: { port_a: 'COM20', port_b: 'COM21' } },
  { name: 'bridge start', call: () => api.startVirtualBridge({ transport: 'tcp' }), method: 'POST', url: '/api/serial/virtual/start', body: { transport: 'tcp' } },
  { name: 'bridge stop', call: api.stopVirtualBridge, method: 'POST', url: '/api/serial/virtual/stop' },
  { name: 'mil presets', call: api.milPresets, method: 'GET', url: '/api/mil/presets' },
  { name: 'mil status', call: api.milStatus, method: 'GET', url: '/api/mil/status' },
  { name: 'mil load', call: () => api.milLoad({ preset_id: 'p' }), method: 'POST', url: '/api/mil/load', body: { preset_id: 'p' } },
  { name: 'mil start', call: api.milStart, method: 'POST', url: '/api/mil/start' },
  { name: 'mil stop', call: api.milStop, method: 'POST', url: '/api/mil/stop' },
  { name: 'mil transaction', call: () => api.milTransaction({ request_hex: '55' }), method: 'POST', url: '/api/mil/transaction', body: { request_hex: '55' } },
  { name: 'logs', call: () => api.logs(), method: 'GET', url: '/api/logs?limit=500' },
  { name: 'diagnostics', call: api.diagnostics, method: 'GET', url: '/api/diagnostics' },
  { name: 'mock capture', call: () => api.mockCapture('demo'), method: 'POST', url: '/api/diagnostics/mock-capture', body: { scenario: 'demo', sample_rate: 1_000_000, num_samples: 50_000, analog: false } },
];

it.each(cases)('$name maps to its documented HTTP contract', async ({ call, method, url, body }) => {
  localStorage.setItem('msa_client_id', 'strict-client');
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({ ok: true }));
  vi.stubGlobal('fetch', fetcher);
  await call();
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe(url);
  expect(fetcher.mock.calls[0][1]).toEqual({ method, headers: {
    'Content-Type': 'application/json', 'X-Client-Id': 'strict-client',
  }, body: body === undefined ? undefined : JSON.stringify(body) });
});

it('parses text success and all structured HTTP error forms', async () => {
  localStorage.setItem('msa_client_id', 'strict-client');
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(null))
    .mockResolvedValueOnce(new Response('plain response'))
    .mockResolvedValueOnce(Response.json({ detail: 'invalid settings' }, { status: 422 }))
    .mockResolvedValueOnce(Response.json({ reason: 'busy' }, { status: 409 }))
    .mockResolvedValueOnce(Response.json({}, { status: 418, statusText: 'Teapot' }))
    .mockResolvedValueOnce(new Response('not json', { status: 503, statusText: 'Unavailable' }));
  vi.stubGlobal('fetch', fetcher);
  await expect(api.releaseControl()).resolves.toBe('');
  await expect(api.releaseControl()).resolves.toBe('plain response');
  await expect(api.status()).rejects.toMatchObject({ status: 422, message: 'invalid settings' });
  await expect(api.status()).rejects.toMatchObject({ status: 409, message: '{"reason":"busy"}' });
  await expect(api.status()).rejects.toMatchObject({ status: 418, message: '{}' });
  await expect(api.status()).rejects.toMatchObject({ status: 503, message: 'Unavailable' });
});

it('parses waveform and overview binary data and rejects their HTTP errors', async () => {
  localStorage.setItem('msa_client_id', 'strict-client');
  const buf = buildWaveformPayload('s');
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response(buf))
    .mockResolvedValueOnce(new Response(buf))
    .mockResolvedValueOnce(new Response('window bad', { status: 400 }))
    .mockResolvedValueOnce(new Response('overview bad', { status: 500 }));
  vi.stubGlobal('fetch', fetcher);
  const signal = new AbortController().signal;
  await expect(api.waveformWindow('s', 1.9, 9.1, 512, ['d0'], signal))
    .resolves.toMatchObject({ header: { session_id: 's' } });
  expect(fetcher).toHaveBeenNthCalledWith(1,
    '/api/sessions/s/waveform?start=1&end=10&resolution=512&channels=d0',
    { signal, headers: { 'X-Client-Id': 'strict-client' } });
  await expect(api.overview('s')).resolves.toMatchObject({ header: { session_id: 's' } });
  await expect(api.waveformWindow('s', 0, 1, 512)).rejects.toMatchObject({ status: 400, message: 'window bad' });
  await expect(api.overview('s', 3)).rejects.toMatchObject({ status: 500, message: 'overview bad' });
});

it('downloads server-selected and fallback export filenames and always revokes object URLs', async () => {
  localStorage.setItem('msa_client_id', 'strict-client');
  const clicked: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.download); });
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:test'), revokeObjectURL: vi.fn() });
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response('data', { headers: { 'content-disposition': 'attachment; filename="capture.vcd"' } }))
    .mockResolvedValueOnce(new Response('data'))
    .mockResolvedValueOnce(Response.json({ detail: 'export failed' }, { status: 500 }))
    .mockResolvedValueOnce(Response.json({}, { status: 502, statusText: 'Bad Gateway' }))
    .mockResolvedValueOnce(new Response('zip'))
    .mockResolvedValueOnce(new Response('', { status: 503, statusText: 'Unavailable' }));
  vi.stubGlobal('fetch', fetcher);
  await downloadExport('s', 'vcd', { start: 2 });
  await downloadExport('s', 'csv');
  await expect(downloadExport('s', 'pdf')).rejects.toMatchObject({ status: 500, message: 'export failed' });
  await expect(downloadExport('s', 'json')).rejects.toMatchObject({ status: 502, message: 'Bad Gateway' });
  await downloadDebugBundle();
  await expect(downloadDebugBundle()).rejects.toMatchObject({ status: 503, message: 'Unavailable' });
  expect(clicked).toEqual(['capture.vcd', 's.csv', 'debug_bundle.zip']);
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(3);
});
