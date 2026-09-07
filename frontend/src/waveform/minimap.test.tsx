// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { Minimap } from './minimap';
import { waveformView } from '../state/waveformStore';
import { buildWaveformPayload } from '../test/waveformPayload';
import { parseWaveformPayload } from '../api/binary';

const calls: { name: string; args: unknown[] }[] = [];
const ctx = {
  setTransform: (...args: unknown[]) => calls.push({ name: 'setTransform', args }),
  fillRect: (...args: unknown[]) => calls.push({ name: 'fillRect', args }),
  strokeRect: (...args: unknown[]) => calls.push({ name: 'strokeRect', args }),
  beginPath: (...args: unknown[]) => calls.push({ name: 'beginPath', args }),
  moveTo: (...args: unknown[]) => calls.push({ name: 'moveTo', args }),
  lineTo: (...args: unknown[]) => calls.push({ name: 'lineTo', args }),
  stroke: (...args: unknown[]) => calls.push({ name: 'stroke', args }),
  fillStyle: '', strokeStyle: '',
};
let resizeDraw: (() => void) | undefined;
const observe = vi.fn();
const disconnect = vi.fn();

beforeEach(async () => {
  calls.length = 0;
  resizeDraw = undefined;
  observe.mockClear(); disconnect.mockClear();
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { resizeDraw = callback; }
    observe = observe; disconnect = disconnect;
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
  Object.defineProperty(HTMLCanvasElement.prototype, 'setPointerCapture', {
    configurable: true, value: vi.fn(),
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0, y: 0, width: 600, height: 36, top: 0, right: 600, bottom: 36, left: 0, toJSON: () => ({}),
  });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 600 });
  await waveformView.load('', 100, 1000, 25);
  waveformView.setView(20, 60);
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLCanvasElement.prototype, 'setPointerCapture');
  Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
});

it('draws activity, one analogue envelope, viewport and trigger at exact positions', () => {
  waveformView.overview = parseWaveformPayload(buildWaveformPayload('s', [
    { name: 'activity', dtype: 'u4', values: [0, 2, 4] },
    { name: 'analog_min_0', dtype: 'f4', values: [-1, 0, 1] },
    { name: 'analog_max_0', dtype: 'f4', values: [0, 1, 2] },
    { name: 'analog_min_1', dtype: 'f4', values: [0, 0, 0] },
    { name: 'analog_max_1', dtype: 'f4', values: [1, 1, 1] },
  ]));
  const { container, unmount } = render(<div><Minimap /></div>);
  const canvas = container.querySelector('canvas')!;
  expect([canvas.width, canvas.height, canvas.style.height]).toEqual([1200, 72, '36px']);
  expect(observe).toHaveBeenCalledWith(canvas.parentElement);
  expect(calls).toContainEqual({ name: 'strokeRect', args: [120.5, 0.5, 239, 35] });
  expect(calls).toContainEqual({ name: 'moveTo', args: [150, 0] });
  expect(calls.filter((call) => call.name === 'fillRect')).toHaveLength(8);
  calls.length = 0;
  resizeDraw?.();
  expect(calls[0]).toEqual({ name: 'setTransform', args: [2, 0, 0, 2, 0, 0] });
  unmount();
  expect(disconnect).toHaveBeenCalledOnce();
});

it('seeks on pointer down and drag, and stops seeking after pointer up', () => {
  waveformView.trigSample = null;
  waveformView.overview = null;
  const { container } = render(<Minimap />);
  const canvas = container.querySelector('canvas')!;
  fireEvent.pointerMove(canvas, { clientX: 500, pointerId: 1 });
  expect([waveformView.start, waveformView.end]).toEqual([20, 60]);
  fireEvent.pointerDown(canvas, { clientX: 300, pointerId: 1 });
  expect([waveformView.start, waveformView.end]).toEqual([30, 70]);
  fireEvent.pointerMove(canvas, { clientX: 450, pointerId: 1 });
  expect([waveformView.start, waveformView.end]).toEqual([55, 95]);
  fireEvent.pointerUp(canvas, { pointerId: 1 });
  fireEvent.pointerMove(canvas, { clientX: 0, pointerId: 1 });
  expect([waveformView.start, waveformView.end]).toEqual([55, 95]);
});

it('ignores incomplete analogue envelopes and empty activity without hiding a valid later lane', () => {
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 0 });
  waveformView.numSamples = 0;
  waveformView.overview = parseWaveformPayload(buildWaveformPayload('s', [
    { name: 'activity', dtype: 'u4', values: [] },
    { name: 'digital', dtype: 'u2', values: [1] },
    { name: 'analog_min_0', dtype: 'f4', values: [0] },
    { name: 'analog_min_1', dtype: 'f4', values: [1, 1] },
    { name: 'analog_max_1', dtype: 'f4', values: [1, 1] },
  ]));
  expect(() => render(<Minimap />)).not.toThrow();
  expect(calls).toContainEqual({ name: 'setTransform', args: [1, 0, 0, 1, 0, 0] });
  expect(calls.filter((call) => call.name === 'fillRect')).toContainEqual({
    name: 'fillRect', args: [0, 33, 300, 1],
  });
});
