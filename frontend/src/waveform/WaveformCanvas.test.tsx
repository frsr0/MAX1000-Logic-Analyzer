// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const renderer = vi.hoisted(() => ({
  draw: vi.fn(), build: vi.fn(), xToSample: vi.fn(),
  layout: { axisHeight: 24, annotRows: [], annotHeight: 0,
    rows: [] as { channel: unknown; y: number; height: number }[],
    totalHeight: 300, labelWidth: 110 },
}));
vi.mock('./renderer', () => ({
  buildLayout: renderer.build,
  render: renderer.draw,
  xToSample: renderer.xToSample,
  sampleToX: vi.fn(),
  fmtTime: (value: number) => `time:${value}`,
  fmtFreq: (value: number) => `freq:${value}`,
}));
vi.mock('./minimap', () => ({ Minimap: () => <div>minimap</div> }));

import { WaveformCanvas } from './WaveformCanvas';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import type { ChannelInfo, DecoderEvent, Marker } from '../api/types';

const channel = (id: string, type: ChannelInfo['type'] = 'digital', enabled = true): ChannelInfo => ({
  id, name: id.toUpperCase(), type, enabled, units: '', volts_per_div: 1, offset: 0,
  probe_attenuation: 1, cal_gain: 1, cal_offset: 0, threshold: 0.5, coupling: '',
  members: [], display_base: 'hex',
});
const channels = [channel('d0'), channel('a0', 'analog')];
let rafs: FrameRequestCallback[];
let resize!: ResizeObserverCallback;

function flushFrame() {
  act(() => { rafs.shift()?.(0); });
}

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ activeSession: session('s', { num_samples: 1000, channels }), toast: vi.fn() });
  Object.assign(waveformView, {
    sessionId: 's', numSamples: 1000, sampleRate: 1000, trigSample: null,
    start: 100, end: 200, cursorA: null, cursorB: null, hoverSample: null,
    hoverY: 0, selectionStart: null, selectionEnd: null, payload: null,
    overview: null, annotations: [], markers: [], loading: false, error: null,
    liveFollow: true, liveRolling: false,
  });
  waveformView.heightScale.clear(); waveformView.selectedRows.clear();
  waveformView.knownRowIds = ['d0', 'a0'];
  renderer.draw.mockReset(); renderer.build.mockReset(); renderer.xToSample.mockReset();
  renderer.xToSample.mockImplementation((_view, _layout, _width, x: number) => x);
  renderer.build.mockImplementation((input: ChannelInfo[], _view, labelWidth: number) => {
    renderer.layout = { ...renderer.layout, labelWidth, rows: input.map((ch, index) => ({
      channel: ch, y: index * 50, height: 40,
    })) };
    return renderer.layout;
  });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1.25 });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 500 });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 400 });
  Object.defineProperty(Element.prototype, 'setPointerCapture', { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform: vi.fn() } as never);
  rafs = [];
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { rafs.push(callback); return rafs.length; }));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = callback; }
    observe() {}
    disconnect() {}
  });
});

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
  Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
  Reflect.deleteProperty(Element.prototype, 'setPointerCapture');
});

it('draws at integer backing dimensions and redraws on store and element changes', () => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 600 });
  const view = render(<WaveformCanvas channels={channels} />);
  const canvas = view.container.querySelector('canvas')!;
  expect(renderer.draw).not.toHaveBeenCalled();
  flushFrame();
  expect(renderer.build).toHaveBeenCalledWith(channels, waveformView, 70);
  expect([canvas.width, canvas.height, canvas.style.width, canvas.style.height])
    .toEqual([625, 420, '500px', '336px']);
  expect(renderer.draw).toHaveBeenCalledOnce();

  act(() => waveformView.notify()); flushFrame();
  resize([], {} as ResizeObserver); flushFrame();
  expect(renderer.draw).toHaveBeenCalledTimes(3);
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 0 });
  act(() => waveformView.notify()); flushFrame();
  expect(canvas.width).toBe(500);
  act(() => waveformView.notify());
  const lateFrame = rafs.shift()!;
  view.unmount();
  lateFrame(0);
  expect(cancelAnimationFrame).toHaveBeenCalled();
});

it('operates the toolbar, live following, row presets and cursor readout', () => {
  Object.assign(waveformView, { trigSample: 150, cursorA: 120, cursorB: 120,
    error: 'render failed', liveRolling: true, annotations: [
      { start_sample: 120, severity: 'normal' }, { start_sample: 180, severity: 'normal' },
    ] as DecoderEvent[] });
  waveformView.selectedRows.add('d0');
  const fit = vi.spyOn(waveformView, 'fit');
  const zoom = vi.spyOn(waveformView, 'zoomAround');
  const jump = vi.spyOn(waveformView, 'jumpTo');
  const all = vi.spyOn(waveformView, 'setAllScales');
  const selected = vi.spyOn(waveformView, 'setRowScales');
  const commit = vi.spyOn(waveformView, 'commitRowHeights').mockResolvedValue();
  render(<WaveformCanvas channels={channels} />); flushFrame();

  expect(screen.getByText('render failed')).toBeTruthy();
  expect(screen.getByText(/\|A−B\| time:0 \(0 smp\)/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Fit' }));
  fireEvent.click(screen.getByRole('button', { name: '+' }));
  fireEvent.click(screen.getByRole('button', { name: '−' }));
  fireEvent.click(screen.getByRole('button', { name: '⭢T' }));
  fireEvent.click(screen.getByRole('button', { name: '⭢A' }));
  fireEvent.click(screen.getByRole('button', { name: '⭢B' }));
  fireEvent.click(screen.getByRole('button', { name: '⟨ ev' }));
  fireEvent.click(screen.getByRole('button', { name: 'ev ⟩' }));
  expect(fit).toHaveBeenCalled(); expect(zoom).toHaveBeenCalledTimes(2);
  expect(jump.mock.calls.map(([sample]) => sample)).toEqual(expect.arrayContaining([150, 120, 180]));

  fireEvent.click(screen.getByRole('button', { name: 'Live' }));
  expect(waveformView.liveFollow).toBe(false);
  fireEvent.click(screen.getByTitle('Row height presets'));
  fireEvent.click(screen.getByRole('button', { name: 'Default (1×)' }));
  expect(all).toHaveBeenCalledWith(1);
  fireEvent.click(screen.getByTitle('Row height presets'));
  fireEvent.click(screen.getAllByRole('button', { name: '2×' })[0]);
  expect(all).toHaveBeenCalledWith(2);
  fireEvent.click(screen.getByTitle('Row height presets'));
  fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
  expect(selected).toHaveBeenCalledWith(['d0'], 1);
  fireEvent.click(screen.getByTitle('Row height presets'));
  fireEvent.pointerLeave(screen.getByText('Snap all rows').parentElement!);
  expect(screen.queryByText('Snap all rows')).toBeNull();
  expect(commit).toHaveBeenCalled();
});

it('supports label selection, grouped resizing, pan, selection and pinch gestures', () => {
  const region = vi.fn();
  const select = vi.spyOn(waveformView, 'selectRow');
  const commit = vi.spyOn(waveformView, 'commitRowHeights').mockResolvedValue();
  const zoom = vi.spyOn(waveformView, 'zoomAround');
  const setView = vi.spyOn(waveformView, 'setView');
  const { container } = render(<WaveformCanvas channels={channels} onSelectRegion={region} />);
  const canvas = container.querySelector('canvas')!;
  fireEvent.pointerDown(canvas, { pointerId: 0, clientX: 20, clientY: 200 });
  fireEvent.pointerMove(canvas, { pointerId: 9, clientX: 200, clientY: 20 });
  flushFrame();

  fireEvent.pointerMove(canvas, { pointerId: 0, clientX: 20, clientY: 20 });
  expect(canvas.style.cursor).toBe('ns-resize');

  fireEvent.pointerDown(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 1, clientX: 20, clientY: 20 });
  expect(select).toHaveBeenCalledWith('d0', false);
  fireEvent.pointerDown(canvas, { pointerId: 11, clientX: 20, clientY: 60, ctrlKey: true });
  fireEvent.pointerMove(canvas, { pointerId: 11, clientX: 20, clientY: 62 });
  fireEvent.pointerUp(canvas, { pointerId: 11, clientX: 20, clientY: 62 });
  expect(select).toHaveBeenCalledWith('a0', true);

  waveformView.selectedRows.add('d0'); waveformView.selectedRows.add('a0');
  fireEvent.pointerDown(canvas, { pointerId: 2, clientX: 20, clientY: 20 });
  fireEvent.pointerMove(canvas, { pointerId: 2, clientX: 20, clientY: 35 });
  fireEvent.pointerUp(canvas, { pointerId: 2, clientX: 20, clientY: 35 });
  expect(commit).toHaveBeenCalledWith(['d0', 'a0']);

  fireEvent.pointerDown(canvas, { pointerId: 3, clientX: 200, clientY: 20 });
  fireEvent.pointerMove(canvas, { pointerId: 3, clientX: 250, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 3, clientX: 250, clientY: 20 });
  expect(setView).toHaveBeenCalled();

  fireEvent.pointerDown(canvas, { pointerId: 4, clientX: 200, clientY: 20, shiftKey: true });
  fireEvent.pointerMove(canvas, { pointerId: 4, clientX: 250, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 4, clientX: 250, clientY: 20 });
  expect(region).toHaveBeenCalledWith(200, 250);

  fireEvent.pointerDown(canvas, { pointerId: 14, clientX: 200, clientY: 20, shiftKey: true });
  fireEvent.pointerMove(canvas, { pointerId: 14, clientX: 201, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 14, clientX: 201, clientY: 20 });
  expect(waveformView.selectionStart).toBeNull();

  fireEvent.pointerDown(canvas, { pointerId: 5, clientX: 200, clientY: 20 });
  fireEvent.pointerDown(canvas, { pointerId: 6, clientX: 300, clientY: 20 });
  fireEvent.pointerMove(canvas, { pointerId: 6, clientX: 350, clientY: 20 });
  expect(zoom).toHaveBeenCalled();
  fireEvent.pointerCancel(canvas, { pointerId: 6, clientX: 350, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 5, clientX: 200, clientY: 20 });
  fireEvent.pointerDown(canvas, { pointerId: 7, clientX: 250, clientY: 20 });
  fireEvent.pointerDown(canvas, { pointerId: 8, clientX: 250, clientY: 20 });
  fireEvent.pointerMove(canvas, { pointerId: 8, clientX: 250, clientY: 20 });
  fireEvent.pointerDown(canvas, { pointerId: 10, clientX: 280, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 10, clientX: 280, clientY: 20 });
});

it('zooms and pans with the wheel and exposes hover-relative timing', () => {
  waveformView.cursorA = 100;
  const zoom = vi.spyOn(waveformView, 'zoomAround');
  const pan = vi.spyOn(waveformView, 'pan');
  const { container } = render(<WaveformCanvas channels={channels} />);
  const canvas = container.querySelector('canvas')!;
  fireEvent.wheel(canvas, { clientX: 200, deltaY: 1 });
  flushFrame();
  fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 130, clientY: 20 });
  expect(screen.getByText('#130 time:0.13 ΔA time:0.03')).toBeTruthy();
  fireEvent.wheel(canvas, { clientX: 200, deltaY: 1 });
  fireEvent.wheel(canvas, { clientX: 200, deltaY: -1, ctrlKey: true });
  fireEvent.wheel(canvas, { clientX: 200, deltaY: -1, shiftKey: true });
  expect(zoom.mock.calls.map((call) => call[1])).toEqual([1.25, 0.8]);
  expect(pan).toHaveBeenCalledWith(-10);
});

it('places snapped cursors, persists new/existing markers and reports save failures', async () => {
  const edges = vi.spyOn(api, 'edges')
    .mockResolvedValueOnce({ edges: [208, 201, 205], times: [], count: 3, truncated: false })
    .mockResolvedValueOnce({ edges: [], times: [], count: 0, truncated: false })
    .mockRejectedValueOnce(new Error('edge lookup failed'));
  const add = vi.spyOn(api, 'addMarker').mockResolvedValue({ id: 'a', kind: 'cursor_a', sample: 201 } as Marker);
  const patch = vi.spyOn(api, 'patchMarker').mockResolvedValue({} as Marker);
  const toast = vi.fn(); useApp.setState({ toast });
  const { container } = render(<WaveformCanvas channels={channels} />); flushFrame();
  const canvas = container.querySelector('canvas')!;

  fireEvent.doubleClick(canvas, { clientX: 20 });
  fireEvent.doubleClick(canvas, { clientX: 200 });
  await waitFor(() => expect(add).toHaveBeenCalledWith('s', { sample: 201, kind: 'cursor_a', label: 'A' }));
  expect(waveformView.cursorA).toBe(201);
  waveformView.markers = [{ id: 'b-existing', kind: 'cursor_b', sample: 1 } as Marker];
  fireEvent.doubleClick(canvas, { clientX: 220, altKey: true });
  await waitFor(() => expect(patch).toHaveBeenCalledWith('s', 'b-existing', { sample: 220 }));

  add.mockRejectedValueOnce(new Error('disk full'));
  fireEvent.doubleClick(canvas, { clientX: 230 });
  await waitFor(() => expect(toast).toHaveBeenCalledWith('warning', 'Cursor not saved: disk full'));
  expect(edges).toHaveBeenCalledTimes(3);
});

it('handles navigation shortcuts, edge/error jumps and ignores form typing', async () => {
  const edges = vi.spyOn(api, 'edges')
    .mockResolvedValueOnce({ edges: [170], times: [], count: 1, truncated: false })
    .mockResolvedValueOnce({ edges: [130], times: [], count: 1, truncated: false })
    .mockRejectedValueOnce(new Error('offline'));
  const toast = vi.fn(); useApp.setState({ toast });
  waveformView.annotations = [
    { start_sample: 120, severity: 'error' }, { start_sample: 180, severity: 'error' },
  ] as DecoderEvent[];
  waveformView.hoverSample = 145; waveformView.trigSample = 150;
  const fit = vi.spyOn(waveformView, 'fit'); const jump = vi.spyOn(waveformView, 'jumpTo');
  const pan = vi.spyOn(waveformView, 'pan'); const zoom = vi.spyOn(waveformView, 'zoomAround');
  render(<><input aria-label="typing" /><WaveformCanvas channels={channels} /></>); flushFrame();

  fireEvent.keyDown(screen.getByLabelText('typing'), { key: 'f' });
  expect(fit).not.toHaveBeenCalled();
  for (const key of ['f', 't', 'a', 'b', 'ArrowLeft', 'ArrowRight', '+', '=', '-', 'n', 'p', 'e', 'E', 'r', 'R', 'x']) {
    fireEvent.keyDown(window, { key });
  }
  expect(fit).toHaveBeenCalled(); expect(pan).toHaveBeenCalledTimes(2);
  expect(zoom).toHaveBeenCalledTimes(3);
  await waitFor(() => expect(edges).toHaveBeenCalledTimes(2));
  fireEvent.keyDown(window, { key: 'e' });
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'offline'));
  expect(jump).toHaveBeenCalled();
});

it('keeps optional navigation and persistence paths safe when data is absent', async () => {
  const add = vi.spyOn(api, 'addMarker').mockResolvedValue({ id: 'a', kind: 'cursor_a', sample: 200 } as Marker);
  const analogOnly = [channel('a0', 'analog')];
  waveformView.cursorA = 100; waveformView.cursorB = 110;
  const { container, rerender } = render(<WaveformCanvas channels={analogOnly} />); flushFrame();
  expect(screen.getByText(/freq:100/)).toBeTruthy();
  const canvas = container.querySelector('canvas')!;
  fireEvent.doubleClick(canvas, { clientX: 200 });
  await waitFor(() => expect(add).toHaveBeenCalled());
  fireEvent.keyDown(window, { key: 'e' });

  waveformView.annotations = [];
  waveformView.trigSample = null; waveformView.hoverSample = null;
  fireEvent.keyDown(window, { key: 't' });
  fireEvent.keyDown(window, { key: 'a' });
  fireEvent.keyDown(window, { key: 'b' });
  fireEvent.keyDown(window, { key: 'n' });

  useApp.setState({ activeSession: null });
  rerender(<WaveformCanvas channels={analogOnly} />); flushFrame();
  waveformView.hoverSample = 42;
  fireEvent.keyDown(window, { key: 'a' });
  fireEvent.keyDown(window, { key: 'e' });
  fireEvent.doubleClick(canvas, { clientX: 200 });
  expect(waveformView.cursorA).toBe(42);

  fireEvent.pointerDown(canvas, { pointerId: 20, clientX: 200, clientY: 20, shiftKey: true });
  fireEvent.pointerMove(canvas, { pointerId: 20, clientX: 230, clientY: 20 });
  fireEvent.pointerUp(canvas, { pointerId: 20, clientX: 230, clientY: 20 });
});
