// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MeasurementPanel } from './MeasurementPanel';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import type { ChannelInfo } from '../api/types';

const ch = (id: string, type: ChannelInfo['type']): ChannelInfo => ({ id, name: id, type, enabled: true,
  units: '', volts_per_div: 1, offset: 0, probe_attenuation: 1, cal_gain: 1,
  cal_offset: 0, threshold: 0, coupling: '', members: [], display_base: 'hex' });
const types = [
  { id: 'dig_frequency', name: 'Frequency', category: 'digital' },
  { id: 'dig_setup_hold', name: 'Setup/hold', category: 'digital' },
  { id: 'dig_channel_skew', name: 'Skew', category: 'digital' },
  { id: 'analog_mean', name: 'Mean', category: 'analog' },
  { id: 'protocol_count', name: 'Protocol count', category: 'protocol' },
] as never[];

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true }); useApp.setState(useApp.getInitialState(), true);
  await waveformView.load('', 100, 1000, null); waveformView.selectionStart = waveformView.selectionEnd = null;
});
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('requires an open session', () => {
  render(<MeasurementPanel />);
  expect(screen.getByText('No session open.')).toBeTruthy();
});

it('renders all result formats and deletes and recomputes through the API', async () => {
  const current = session('s', { channels: [ch('d0', 'digital')], measurements: [
    { id: 'error', type: 'missing', channels: [], scope: 'capture', settings: {}, error: 'bad input' },
    { id: 'none', type: 'missing', channels: [], scope: 'capture', settings: {}, result: null },
    { id: 'note', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: null, note: 'quiet' } },
    { id: 'na', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: undefined } },
    { id: 'text', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 'locked' } },
    { id: 'seconds', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 0.002, unit: 's' } },
    { id: 'mhz', type: 'dig_frequency', channels: ['d0'], scope: 'capture', settings: {}, result: { value: 2e6, unit: 'Hz' } },
    { id: 'khz', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 2000, unit: 'Hz' } },
    { id: 'hz', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 2, unit: 'Hz' } },
    { id: 'large', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 1234, unit: 'V' } },
    { id: 'small', type: 'missing', channels: [], scope: 'capture', settings: {}, result: { value: 1.23456, unit: '' } },
  ] as never[] });
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).includes('/api/sessions/s')
    && !String(input).includes('/measurements') ? Response.json(current) : Response.json({ measurements: current.measurements }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: current, measurementTypes: types });
  render(<MeasurementPanel />);
  expect(screen.getAllByText('—').length).toBeGreaterThan(1);
  for (const value of ['bad input', 'quiet', 'n/a', 'locked', '2.000 ms', '2.0000 MHz', '2.0000 kHz', '2.00 Hz', '1,234 V', '1.2346']) {
    expect(screen.getByText(value)).toBeTruthy();
  }
  waveformView.cursorA = 4; waveformView.cursorB = 8; waveformView.notify();
  waveformView.cursorA = null; waveformView.cursorB = null;
  fireEvent.click(screen.getByRole('button', { name: '↻ Recompute all' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/measurements/results'))).toBe(true));
  waveformView.cursorA = 2; waveformView.cursorB = 9;
  fireEvent.click(screen.getByRole('button', { name: '↻ Recompute all' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/measurements/results?cursor_a=2&cursor_b=9'))).toBe(true));
  fireEvent.click(screen.getAllByRole('button', { name: '✕' })[0]);
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/measurements/error'))).toBe(true));
});

it('adds digital pairs, analogue, protocol and selected-region measurements with exact contracts', async () => {
  const current = session('s', { channels: [ch('d0', 'digital'), ch('d1', 'derived'), ch('a0', 'analog'), ch('bus', 'bus')] });
  const requests: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith('/measurements') && init?.method === 'POST') requests.push(JSON.parse(String(init.body)));
    return String(input) === '/api/sessions/s' ? Response.json(current) : Response.json({});
  }));
  useApp.setState({ activeSession: current, measurementTypes: types });
  waveformView.selectionStart = 8.8; waveformView.selectionEnd = 2.2;
  const { rerender } = render(<MeasurementPanel />);
  const selects = screen.getAllByRole('combobox');
  fireEvent.change(selects[0], { target: { value: 'dig_setup_hold' } });
  fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'd1' } });
  fireEvent.change(screen.getAllByRole('combobox')[2], { target: { value: 'd0' } });
  fireEvent.change(screen.getAllByRole('combobox')[3], { target: { value: 'region' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toEqual({ type: 'dig_setup_hold', channels: ['d1', 'd0'], scope: 'region', region: [2, 9] });
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'analog_mean' } });
  expect(screen.getByText('a0 (a0)')).toBeTruthy();
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'protocol_count' } });
  expect(screen.queryByText('a0 (a0)')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual({ type: 'protocol_count', channels: [], scope: 'region', region: [2, 9] });
  waveformView.selectionStart = waveformView.selectionEnd = null; rerender(<MeasurementPanel />);
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(requests).toHaveLength(3));
  expect(requests[2]).toEqual({ type: 'protocol_count', channels: [], scope: 'region' });
});

it('reports add failures and debounces cursor-scoped recomputation', async () => {
  const current = session('s', { channels: [ch('d0', 'digital')], measurements: [
    { id: 'cursor', type: 'dig_frequency', channels: ['d0'], scope: 'cursors', settings: {} },
  ] as never[] });
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: 'unsupported' }, { status: 422 }))
    .mockRejectedValueOnce(new Error('offline'));
  vi.stubGlobal('fetch', fetcher);
  const refreshActiveSession = vi.fn().mockResolvedValue(undefined);
  useApp.setState({ activeSession: current, measurementTypes: types, refreshActiveSession });
  render(<MeasurementPanel />);
  fireEvent.click(screen.getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'unsupported')).toBe(true));
  waveformView.cursorA = null; waveformView.cursorB = 2; waveformView.notify();
  waveformView.cursorA = 1; waveformView.notify(); waveformView.notify();
  waveformView.cursorA = 2; waveformView.notify();
  await vi.advanceTimersByTimeAsync(399);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(fetcher).toHaveBeenCalledTimes(2);
  fetcher.mockResolvedValueOnce(Response.json({ measurements: [] }));
  waveformView.cursorA = 3; waveformView.notify();
  await vi.advanceTimersByTimeAsync(400);
  expect(refreshActiveSession).toHaveBeenCalledOnce();
});
