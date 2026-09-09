// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const worker = vi.hoisted(() => ({ fetchWindow: vi.fn(), dispose: vi.fn() }));
vi.mock('../workers/waveformClient', () => ({
  WaveformClient: class {
    fetchWindow = worker.fetchWindow;
    dispose = worker.dispose;
  },
}));

import { AnalogPanel } from './AnalogPanel';
import { useApp } from '../state/appStore';
import { api } from '../api/client';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import type { ChannelInfo } from '../api/types';
import type { WaveformPayload } from '../api/binary';

const channel = (id: string, type: ChannelInfo['type']): ChannelInfo => ({
  id, name: id.toUpperCase(), type, enabled: true, units: type === 'analog' ? 'V' : '',
  volts_per_div: 1, offset: 0, probe_attenuation: 1, cal_gain: 1, cal_offset: 0,
  threshold: 0.5, coupling: '', members: [], display_base: 'hex',
});

const draw = vi.fn();
let resize!: ResizeObserverCallback;
const ctx = new Proxy({} as Record<string, unknown>, {
  get(object, property) {
    return object[property as string] ?? ((...args: unknown[]) => draw(String(property), ...args));
  },
  set(object, property, value) { object[property as string] = value; return true; },
}) as unknown as CanvasRenderingContext2D;

const mixedSession = (id = 'mixed') => session(id, {
  num_samples: 100,
  channels: [channel('a0', 'analog'), channel('a1', 'analog'),
    channel('d0', 'digital'), channel('derived', 'derived')],
});

function payload(entries: [string, Float32Array][]): WaveformPayload {
  return {
    header: { session_id: 'mixed', start: 0, end: 100, num_samples: 100,
      sample_rate: 1000, mode: 'raw', samples_per_bin: 1, arrays: [] },
    arrays: new Map(entries),
  };
}

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  worker.fetchWindow.mockReset(); worker.dispose.mockReset(); draw.mockClear();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx);
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 480 });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
  waveformView.selectionStart = null; waveformView.selectionEnd = null;
  waveformView.cursorA = null; waveformView.cursorB = null;
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = callback; }
    observe() {}
    disconnect() {}
  });
});

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'clientWidth');
});

it('uses a real digital channel and an accurate action label for event correlation', async () => {
  const eventCorrelation = vi.spyOn(api, 'eventCorrelation')
    .mockResolvedValueOnce({ threshold: 0.5, pairs: [{ lag_samples: 2 }, { lag_samples: -3 }] })
    .mockResolvedValueOnce({ threshold: 0 });
  useApp.setState({ activeSession: mixedSession() });
  render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));
  fireEvent.click(screen.getByRole('button', { name: 'Event correlation' }));
  expect(screen.getByLabelText('Digital channel')).toHaveProperty('value', 'd0');
  fireEvent.change(screen.getByLabelText('Digital channel'), { target: { value: 'derived' } });
  fireEvent.click(screen.getByRole('button', { name: 'Correlate events' }));
  await waitFor(() => expect(eventCorrelation).toHaveBeenCalledWith('mixed', 'a0', 'derived'));
  resize([], {} as ResizeObserver);
  expect(draw.mock.calls).toContainEqual(['fillText', 'paired edges: 2', 12, 60]);
  expect(draw.mock.calls.some(([method]) => method === 'lineTo')).toBe(true);
  const canvas = document.querySelector('canvas')!;
  Object.defineProperty(canvas, 'parentElement', { configurable: true, value: null });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 0 });
  fireEvent.click(screen.getByRole('button', { name: 'Correlate events' }));
  await waitFor(() => expect(eventCorrelation).toHaveBeenCalledTimes(2));
  expect((canvas as HTMLCanvasElement).width).toBe(480);
  expect(draw.mock.calls).toContainEqual(['fillText', 'paired edges: 0', 12, 60]);
});

it.each([
  [null, 'No session open.'],
  [session('digital', { channels: [channel('d0', 'digital')] }), 'No analog channels in this capture.'],
] as const)('renders the unavailable states and disposes the worker', (activeSession, message) => {
  useApp.setState({ activeSession });
  const view = render(<AnalogPanel />);
  expect(screen.getByText(message)).toBeTruthy();
  view.unmount();
  expect(worker.dispose).toHaveBeenCalledOnce();
});

it('computes spectra for the whole capture and a reversed selection', async () => {
  const spectrum = vi.spyOn(api, 'spectrum')
    .mockResolvedValueOnce({ freqs: [0, 1000], magnitude: [0, 2], peaks: [{ frequency_hz: 1500, magnitude: 2 }] })
    .mockResolvedValueOnce({ freqs: [0], magnitude: [0] })
    .mockResolvedValueOnce({ freqs: [], magnitude: [] });
  useApp.setState({ activeSession: mixedSession() });
  render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));

  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  expect(await screen.findByText('Peaks: 1.50 kHz')).toBeTruthy();
  expect(spectrum).toHaveBeenNthCalledWith(1, 'mixed', 'a0', 0, -1);
  expect(draw.mock.calls).toContainEqual(['fillText', '1.0 kHz', 410, 256]);

  waveformView.selectionStart = 9.2; waveformView.selectionEnd = 2.8;
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.change(screen.getByLabelText('Analog channel'), { target: { value: 'a1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  await waitFor(() => expect(spectrum).toHaveBeenNthCalledWith(2, 'mixed', 'a1', 2, 10));
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  await waitFor(() => expect(spectrum).toHaveBeenCalledTimes(3));
  fireEvent.click(screen.getByRole('button', { name: 'Spectrogram' }));
  fireEvent.click(screen.getByRole('button', { name: 'Spectrum' }));
  expect(screen.getByRole('button', { name: 'Compute spectrum' })).toBeTruthy();
});

it('draws spectrogram, correlation, envelope, and threshold results', async () => {
  vi.spyOn(api, 'spectrogram')
    .mockResolvedValueOnce({ freqs: [0, 2000], times: [0, 1], magnitude: [[0, 2], [1, 4]] })
    .mockResolvedValueOnce({ freqs: [], times: [], magnitude: [] })
    .mockResolvedValueOnce({ freqs: [1000], times: [0], magnitude: [[1]] });
  vi.spyOn(api, 'correlation')
    .mockResolvedValueOnce({ delay_s: null })
    .mockResolvedValueOnce({ delay_s: 0.000002, correlation: 0.75 })
    .mockResolvedValueOnce({ delay_s: 0, correlation: 0 });
  vi.spyOn(api, 'envelope')
    .mockResolvedValueOnce({ channel: 'a0', min: [1], max: [1] })
    .mockResolvedValueOnce({ channel: 'a0', min: [-2, -1], max: [1, 3] });
  vi.spyOn(api, 'thresholdSweep')
    .mockResolvedValueOnce({ channel: 'a0', levels: [] })
    .mockResolvedValueOnce({ channel: 'a0', levels: [{ level: 0.5, rising_edges: 3, frequency_hz: 12.5 }] });
  useApp.setState({ activeSession: mixedSession() });
  render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));

  fireEvent.click(screen.getByRole('button', { name: 'Spectrogram' }));
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrogram' }));
  await waitFor(() => expect(api.spectrogram).toHaveBeenCalledOnce());
  expect(draw.mock.calls).toContainEqual(['fillText', '2.0 kHz', 4, 12]);
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrogram' }));
  await waitFor(() => expect(api.spectrogram).toHaveBeenCalledTimes(2));
  waveformView.cursorA = 4; waveformView.cursorB = null;
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrogram' }));
  await waitFor(() => expect(api.spectrogram).toHaveBeenNthCalledWith(3, 'mixed', 'a0', 0, -1));

  waveformView.cursorA = 80; waveformView.cursorB = 20;
  fireEvent.click(screen.getByRole('button', { name: 'Correlation' }));
  fireEvent.click(screen.getByRole('button', { name: 'Correlate channels' }));
  await waitFor(() => expect(api.correlation).toHaveBeenNthCalledWith(1, 'mixed', 'a0', 'a1', 20, 80));
  expect(draw.mock.calls).toContainEqual(['fillText', 'delay: n/a', 12, 80]);
  fireEvent.click(screen.getByRole('button', { name: 'Correlate channels' }));
  await waitFor(() => expect(api.correlation).toHaveBeenCalledTimes(2));
  expect(draw.mock.calls).toContainEqual(['fillText', 'delay: 2.000 µs', 12, 80]);
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Correlate channels' }));
  await waitFor(() => expect(api.correlation).toHaveBeenNthCalledWith(3, 'mixed', 'a0', 'a1', 0, -1));

  fireEvent.click(screen.getByRole('button', { name: 'Envelope' }));
  fireEvent.click(screen.getByRole('button', { name: 'Compute envelope' }));
  await waitFor(() => expect(api.envelope).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Compute envelope' }));
  await waitFor(() => expect(api.envelope).toHaveBeenCalledTimes(2));

  fireEvent.click(screen.getByRole('button', { name: 'Threshold sweep' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sweep thresholds' }));
  await waitFor(() => expect(api.thresholdSweep).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Sweep thresholds' }));
  await waitFor(() => expect(api.thresholdSweep).toHaveBeenCalledTimes(2));
  expect(draw.mock.calls.some((call) => String(call[1]).includes('12.50 Hz'))).toBe(true);
});

it('plots XY data for whole/cursor ranges and rejects missing or empty arrays', async () => {
  worker.fetchWindow
    .mockResolvedValueOnce(payload([['analog:a0', new Float32Array([2, -1, 3])], ['analog:a1', new Float32Array([0, 2, -2])]]))
    .mockResolvedValueOnce(payload([['analog:a0', new Float32Array([1])], ['analog:a1', new Float32Array([1])]]))
    .mockResolvedValueOnce(payload([]))
    .mockResolvedValueOnce(payload([['analog:a0', new Float32Array([1])]]))
    .mockResolvedValueOnce(payload([['analog:a0', new Float32Array()], ['analog:a1', new Float32Array([1])]]))
    .mockResolvedValueOnce(payload([['analog:a0', new Float32Array([1])], ['analog:a1', new Float32Array()]]));
  useApp.setState({ activeSession: mixedSession() });
  render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));
  fireEvent.click(screen.getByRole('button', { name: 'XY scope' }));

  fireEvent.click(screen.getByRole('button', { name: 'Plot XY' }));
  await waitFor(() => expect(worker.fetchWindow).toHaveBeenNthCalledWith(1, 'mixed', 0, 100, 2000, ['a0', 'a1']));
  expect(draw.mock.calls.some(([method]) => method === 'stroke')).toBe(true);

  waveformView.cursorA = 70; waveformView.cursorB = 10;
  fireEvent.click(screen.getByRole('checkbox'));
  for (let call = 2; call <= 6; call++) {
    fireEvent.click(screen.getByRole('button', { name: 'Plot XY' }));
    await waitFor(() => expect(worker.fetchWindow).toHaveBeenCalledTimes(call));
  }
  expect(worker.fetchWindow).toHaveBeenNthCalledWith(2, 'mixed', 10, 70, 2000, ['a0', 'a1']);
  // Repeated analysis failures share one user-facing message and are
  // intentionally deduplicated by the toast store.
  expect(useApp.getState().toasts.filter((toast) => toast.message.includes('No analog samples'))).toHaveLength(1);

  fireEvent.change(screen.getByLabelText('Y channel'), { target: { value: 'a0' } });
  expect(screen.getByText('Pick two different channels for an XY plot.')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Plot XY' }).hasAttribute('disabled')).toBe(true);
});

it('shows busy/errors and suppresses stale success and failure after a session change', async () => {
  let resolveOld!: (value: { freqs: number[]; magnitude: number[] }) => void;
  let rejectOld!: (error: Error) => void;
  const spectrum = vi.spyOn(api, 'spectrum')
    .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }))
    .mockRejectedValueOnce(new Error('current failure'));
  useApp.setState({ activeSession: mixedSession('old') });
  const { rerender } = render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));

  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  expect(screen.getByRole('button', { name: 'Working…' }).hasAttribute('disabled')).toBe(true);
  useApp.setState({ activeSession: mixedSession('new') }); rerender(<AnalogPanel />);
  await act(async () => resolveOld({ freqs: [1000], magnitude: [1] }));
  expect(screen.queryByText(/Peaks:/)).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  useApp.setState({ activeSession: mixedSession('newer') }); rerender(<AnalogPanel />);
  await act(async () => rejectOld(new Error('stale failure')));
  expect(useApp.getState().toasts.some((toast) => toast.message === 'stale failure')).toBe(false);

  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'current failure')).toBe(true));
  expect(screen.getByRole('button', { name: 'Compute spectrum' })).toBeTruthy();
  expect(spectrum).toHaveBeenCalledTimes(3);
});

it('ignores actions whose required channel selection is empty', async () => {
  const spectrum = vi.spyOn(api, 'spectrum');
  const spectrogram = vi.spyOn(api, 'spectrogram');
  const correlation = vi.spyOn(api, 'correlation');
  const envelope = vi.spyOn(api, 'envelope');
  const threshold = vi.spyOn(api, 'thresholdSweep');
  useApp.setState({ activeSession: mixedSession() });
  render(<AnalogPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analog channel')).toHaveProperty('value', 'a0'));

  fireEvent.change(screen.getByLabelText('Analog channel'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrum' }));
  expect(spectrum).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Spectrogram' }));
  fireEvent.click(screen.getByRole('button', { name: 'Compute spectrogram' }));
  expect(spectrogram).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Envelope' }));
  fireEvent.click(screen.getByRole('button', { name: 'Compute envelope' }));
  expect(envelope).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Threshold sweep' }));
  fireEvent.click(screen.getByRole('button', { name: 'Sweep thresholds' }));
  expect(threshold).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'XY scope' }));
  fireEvent.click(screen.getByRole('button', { name: 'Plot XY' }));
  expect(worker.fetchWindow).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('X channel'), { target: { value: 'a0' } });
  fireEvent.change(screen.getByLabelText('Y channel'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Plot XY' }));
  expect(worker.fetchWindow).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: 'Correlation' }));
  fireEvent.change(screen.getByLabelText('X channel'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Correlate channels' }));
  fireEvent.change(screen.getByLabelText('X channel'), { target: { value: 'a0' } });
  fireEvent.change(screen.getByLabelText('Y channel'), { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Correlate channels' }));
  expect(correlation).not.toHaveBeenCalled();
});
