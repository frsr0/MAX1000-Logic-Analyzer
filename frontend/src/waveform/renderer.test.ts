import { beforeEach, expect, it } from 'vitest';
import { buildLayout, fmtFreq, fmtTime, render, sampleToX, xToSample } from './renderer';
import { WaveformView } from '../state/waveformStore';
import type { ChannelInfo } from '../api/types';
import type { WaveformPayload } from '../api/binary';

const operations: { name: string; args: unknown[] }[] = [];
const canvas = { height: 500 };
const target: Record<string, unknown> = { canvas, globalAlpha: 1 };
const ctx = new Proxy(target, {
  get(object, property) {
    if (property in object) return object[property as string];
    return (...args: unknown[]) => operations.push({ name: String(property), args });
  },
  set(object, property, value) { object[property as string] = value; return true; },
}) as unknown as CanvasRenderingContext2D;

const channel = (id: string, type: ChannelInfo['type'], changes: Partial<ChannelInfo> = {}): ChannelInfo => ({
  id, name: id.toUpperCase(), type, enabled: true, units: '', volts_per_div: 1,
  offset: 0, probe_attenuation: 1, cal_gain: 1, cal_offset: 0, threshold: 0.5,
  coupling: 'DC', members: [], display_base: 'hex', ...changes,
});
const payload = (mode: WaveformPayload['header']['mode'], arrays: WaveformPayload['arrays'], changes: Partial<WaveformPayload['header']> = {}): WaveformPayload => ({
  header: { session_id: 's', start: 0, end: 8, num_samples: 8, sample_rate: 1000,
    mode, samples_per_bin: mode === 'raw' ? 1 : 2, arrays: [], ...changes }, arrays,
});

beforeEach(() => { operations.length = 0; Object.keys(target).filter((key) => !['canvas', 'globalAlpha'].includes(key)).forEach((key) => delete target[key]); });

it('formats user-facing times and frequencies across every unit threshold', () => {
  expect([fmtTime(-2), fmtTime(0.002), fmtTime(2e-6), fmtTime(2e-9)])
    .toEqual(['-2.000 s', '2.000 ms', '2.000 µs', '2.0 ns']);
  expect([fmtFreq(2e6), fmtFreq(2000), fmtFreq(2)])
    .toEqual(['2.0000 MHz', '2.0000 kHz', '2.00 Hz']);
});

it('builds enabled row and decoder geometry and maps sample/pixel coordinates reversibly', () => {
  const view = new WaveformView();
  view.annotations = [{ decoder_id: 'uart' }] as never[];
  view.setRowScales(['a0'], 2);
  const channels = [channel('off', 'digital', { enabled: false }), channel('d0', 'digital'), channel('a0', 'analog')];
  const layout = buildLayout(channels, view, 120);
  expect(layout).toMatchObject({ axisHeight: 24, annotRows: ['uart'], annotHeight: 22, totalHeight: 288,
    rows: [{ channel: channels[1], y: 50, height: 30 }, { channel: channels[2], y: 84, height: 192 }] });
  expect(view.knownRowIds).toEqual(['d0', 'a0']);
  view.start = 10; view.end = 110;
  expect(sampleToX(view, layout, 1120, 60)).toBe(620);
  expect(xToSample(view, layout, 1120, 620)).toBe(60);
  view.end = view.start;
  expect(sampleToX(view, layout, 120, 11)).toBe(120);
  expect(xToSample(view, layout, 120, 121)).toBe(11);
});

it('renders raw digital, derived, analogue and all bus display modes with overlays', () => {
  const view = new WaveformView();
  view.start = 0; view.end = 8; view.sampleRate = 1000; view.loading = true;
  view.selectionStart = 6; view.selectionEnd = 2;
  view.trigSample = 1; view.cursorA = 2; view.cursorB = 3; view.hoverSample = 4;
  view.markers = [
    { id: 'ignored', kind: 'cursor_a', sample: 0, label: '', note: '' },
    { id: 'error', kind: 'error', sample: 5, label: '', note: '' },
    { id: 'custom', kind: 'bookmark', sample: 6, label: '', note: '', color: '#123' },
    { id: 'default', kind: 'bookmark', sample: 7, label: '', note: '' },
    { id: 'outside', kind: 'bookmark', sample: 99, label: '', note: '' },
  ];
  view.annotations = [
    { decoder_id: 'uart', start_sample: 0, end_sample: 4, severity: 'normal', label: 'a long decoder label' },
    { decoder_id: 'spi', start_sample: 1, end_sample: 2, severity: 'warning', label: 'warn' },
    { decoder_id: 'i2c', start_sample: 2, end_sample: 3, severity: 'error', label: 'err' },
    { decoder_id: 'hidden', start_sample: 0, end_sample: 1, severity: 'normal', label: 'hidden' },
  ] as never[];
  const channels = [
    channel('d0', 'digital'), channel('d1', 'digital', { color: '#abc' }),
    channel('f0', 'derived'), channel('a0', 'analog', { color: null, volts_per_div: 0, cal_gain: 2, cal_offset: 1, offset: 1 }),
    channel('hex', 'bus', { members: ['d0', 'd1'], display_base: 'hex' }),
    channel('bin', 'bus', { members: ['d0', 'd1'], display_base: 'bin' }),
    channel('dec', 'bus', { members: ['d0', 'd1'], display_base: 'dec' }),
    channel('ascii', 'bus', { members: ['d0', 'd1', 'd2', 'd3', 'd4', 'd5', 'd6'], display_base: 'ascii' }),
  ];
  view.setRowScales(['a0'], 1.5); view.selectRow('d0', false);
  view.payload = payload('raw', new Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>([
    ['digital', new Uint16Array([0, 1, 3, 2, 65, 31, 0, 1])],
    ['derived:f0', new Uint8Array([0, 1, 1, 0, 1, 0, 0, 1])],
    ['analog:a0', new Float32Array([-99, 0, 0.5, 99, 0, 1, -1, 0])],
  ]));
  const layout = buildLayout(channels, view, 120);
  render(ctx, view, layout, 920, 500);
  const labels = operations.filter((op) => op.name === 'fillText').map((op) => op.args[0]);
  expect(labels).toEqual(expect.arrayContaining(['loading…', '⎍ D0', 'ƒ F0', '∿ A0', '1.5×', '0x0', '00', '0', 'A']));
  expect(operations.some((op) => op.name === 'roundRect')).toBe(true);
  expect(operations.some((op) => op.name === 'setLineDash')).toBe(true);
  expect(target.globalAlpha).toBe(1);
});

it('renders LOD density, stable levels, analogue envelopes and bus zoom guidance', () => {
  const view = new WaveformView();
  view.start = 2; view.end = 10; view.sampleRate = 1_000_000;
  const channels = [channel('d0', 'digital'), channel('d1', 'digital'), channel('f0', 'derived'),
    channel('a0', 'analog'), channel('bus', 'bus', { members: ['d0'] })];
  view.payload = payload('lod', new Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>([
    ['digital_and', new Uint16Array([0, 2, 1, 0, 0, 0])],
    ['digital_or', new Uint16Array([1, 2, 1, 0, 1, 0])],
    ['digital_edges', new Uint32Array([1, 0, 4, 0, 2, 0, 0, 0, 3, 0, 0, 0])],
    ['derived_and:f0', new Uint16Array([0, 1, 1, 0, 0, 0])],
    ['derived_or:f0', new Uint16Array([1, 1, 1, 0, 1, 0])],
    ['analog_min:a0', new Float32Array([-1, 0, 1, 2, -1, 0])],
    ['analog_max:a0', new Float32Array([1, 1, 2, 3, 1, 0])],
  ]), { bin_start: -2, edges_channels: 2 });
  const layout = buildLayout(channels, view, 120);
  render(ctx, view, layout, 920, 500);
  const labels = operations.filter((op) => op.name === 'fillText').map((op) => op.args[0]);
  expect(labels).toContain('zoom in for bus values');
  expect(operations.filter((op) => op.name === 'fillRect').length).toBeGreaterThan(4);
  expect(target.globalAlpha).toBe(1);
});

it('draws row frames but no samples when data arrays are unavailable', () => {
  const view = new WaveformView(); view.start = 0; view.end = 8;
  const channels = [channel('d0', 'digital'), channel('f0', 'derived'), channel('a0', 'analog'), channel('bus', 'bus')];
  let layout = buildLayout(channels, view, 120);
  render(ctx, view, layout, 920, 500);
  view.payload = payload('raw', new Map());
  render(ctx, view, layout, 920, 500);
  view.payload = payload('raw', new Map([['digital', new Uint16Array([])]]));
  render(ctx, view, layout, 920, 500);
  view.payload = payload('lod', new Map());
  render(ctx, view, layout, 920, 500);
  expect(operations.filter((op) => op.name === 'strokeRect').length).toBeGreaterThanOrEqual(12);
});

it('clips offscreen geometry and uses safe axis and LOD defaults', () => {
  const view = new WaveformView(); view.sampleRate = 1; view.start = view.end = 0;
  let layout = buildLayout([], view, 120);
  render(ctx, view, layout, 230, 200);
  for (const span of [2, 5, 8]) {
    view.start = 0; view.end = span;
    layout = buildLayout([], view, 120);
    render(ctx, view, layout, 230, 200);
  }

  view.start = 0; view.end = 2; view.sampleRate = 1000; view.hoverSample = -10;
  view.annotations = [
    { decoder_id: 'uart', start_sample: -100, end_sample: -90, severity: 'normal', label: 'left' },
    { decoder_id: 'uart', start_sample: 100, end_sample: 110, severity: 'normal', label: 'right' },
    { decoder_id: 'uart', start_sample: 0, end_sample: 0.1, severity: 'normal', label: 'tiny' },
    { decoder_id: 'uart', start_sample: 0, end_sample: 1, severity: 'normal', label: 'ok' },
  ] as never[];
  const channels = [channel('d0', 'digital'), channel('a0', 'analog'),
    channel('bus', 'bus', { members: ['d0'], display_base: 'ascii' })];
  view.setRowScales(['d0'], 2);
  view.payload = payload('raw', new Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>([
    ['digital', new Uint16Array([0, 0, 1, 1, 0, 1, 0, 1])],
    ['analog:a0', new Float32Array([0, 1, 2, 3, 4, 5, 6, 7])],
  ]));
  layout = buildLayout(channels, view, 120);
  render(ctx, view, layout, 220, 400);
  expect(operations.filter((op) => op.name === 'fillText').map((op) => op.args[0])).toContain('2×');

  view.start = 0; view.end = 100;
  view.annotations = [{ decoder_id: 'layout', start_sample: 0, end_sample: 1, severity: 'normal', label: 'layout' }] as never[];
  layout = buildLayout(channels, view, 120);
  view.annotations = [
    { decoder_id: 'missing', start_sample: 0, end_sample: 1, severity: 'normal', label: 'ignored' },
    { decoder_id: 'layout', start_sample: 0, end_sample: 80, severity: 'normal', label: 'a decoder label that is deliberately much longer than the available annotation width' },
  ] as never[];
  view.payload = payload('raw', new Map([['digital', new Uint16Array([0, 1, 0, 1])]]));
  render(ctx, view, layout, 220, 400);

  view.payload = payload('lod', new Map<string, Uint8Array | Uint16Array | Uint32Array | Float32Array>([
    ['digital_and', new Uint16Array([0, 1])], ['digital_or', new Uint16Array([1, 1])],
    ['digital_edges', new Uint32Array(32)],
    ['analog_min:a0', new Float32Array([0, 1])], ['analog_max:a0', new Float32Array([1, 2])],
  ]));
  layout = buildLayout(channels, view, 120);
  render(ctx, view, layout, 220, 400);
  expect(operations.some((op) => op.name === 'stroke')).toBe(true);
});
