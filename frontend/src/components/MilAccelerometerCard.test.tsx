// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';
import { MilAccelerometerCard } from './MilAccelerometerCard';

const sample = (x_g = 0, y_g = 0, z_g = 1): MilAccelerometerStatus => ({
  running: false, available: true, sample_rate_hz: 100, sample: { x_g, y_g, z_g, timestamp: 1 },
  samples_read: 42, last_error: null,
});

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('starts and stops tracking, and polls at 10 Hz only while running', async () => {
  vi.useFakeTimers();
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockResolvedValueOnce(sample())
    .mockResolvedValue({ ...sample(), running: true });
  const start = vi.spyOn(api, 'milAccelerometerStart').mockResolvedValue({ ...sample(), running: true });
  const stop = vi.spyOn(api, 'milAccelerometerStop').mockResolvedValue(sample());
  render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });
  expect(status).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Start tracking' }));
  await act(async () => { await Promise.resolve(); });
  expect(start).toHaveBeenCalledOnce();
  const callsAfterStart = status.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(350); });
  expect(status.mock.calls.length - callsAfterStart).toBe(3);
  fireEvent.click(screen.getByRole('button', { name: 'Stop tracking' }));
  await act(async () => { await Promise.resolve(); });
  const callsAfterStop = status.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(status.mock.calls.length).toBe(callsAfterStop);
  expect(stop).toHaveBeenCalledOnce();
});

it('does not overlap a slow poll and cleans up its pending schedule on unmount', async () => {
  vi.useFakeTimers();
  let resolvePoll: ((value: MilAccelerometerStatus) => void) | undefined;
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockResolvedValueOnce({ ...sample(), running: true })
    .mockImplementation(() => new Promise((resolve) => { resolvePoll = resolve; }));
  const view = render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });

  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(status).toHaveBeenCalledTimes(2);
  // The first poll is intentionally unresolved; no second request may start.
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(status).toHaveBeenCalledTimes(2);

  resolvePoll?.({ ...sample(), running: true, samples_read: 43 });
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(status).toHaveBeenCalledTimes(3);

  view.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(status).toHaveBeenCalledTimes(3);
});

it('reports a structured start failure instead of claiming tracking began', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue(sample());
  vi.spyOn(api, 'milAccelerometerStart').mockResolvedValue({
    ...sample(), running: false, last_error: 'accelerometer is not connected',
  });
  render(<MilAccelerometerCard />);
  fireEvent.click(await screen.findByRole('button', { name: 'Start tracking' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'accelerometer is not connected'));
  expect(screen.getAllByRole('alert').some((alert) => alert.textContent === 'accelerometer is not connected')).toBe(true);
  expect(screen.queryByText('Accelerometer tracking started')).toBeNull();
});

it('uses clear fallback messages when a structured command cannot complete', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue(sample());
  const start = vi.spyOn(api, 'milAccelerometerStart').mockResolvedValue({ ...sample(), last_error: null });
  render(<MilAccelerometerCard />);
  fireEvent.click(await screen.findByRole('button', { name: 'Start tracking' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'Accelerometer tracking did not start'));
  expect(screen.getAllByRole('alert').some((alert) => alert.textContent === 'Accelerometer tracking did not start')).toBe(true);
  expect(start).toHaveBeenCalledOnce();

  cleanup();
  const stopToast = vi.fn();
  useApp.setState({ toast: stopToast });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ ...sample(), running: true });
  vi.spyOn(api, 'milAccelerometerStop').mockResolvedValue({ ...sample(), running: true, last_error: null });
  render(<MilAccelerometerCard />);
  fireEvent.click(await screen.findByRole('button', { name: 'Stop tracking' }));
  await waitFor(() => expect(stopToast).toHaveBeenCalledWith('error', 'Accelerometer tracking did not stop'));
});

it('surfaces unknown polling and command failures with safe messages', async () => {
  vi.useFakeTimers();
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockResolvedValueOnce({ ...sample(), running: true })
    .mockRejectedValueOnce('poll failed')
    .mockRejectedValueOnce(new Error('poll exception'))
    .mockResolvedValue({ ...sample(), running: false });
  render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getAllByRole('alert').some((alert) => alert.textContent === 'Accelerometer update failed')).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getAllByRole('alert').some((alert) => alert.textContent === 'poll exception')).toBe(true);
  // Allow the recovery response to stop the polling chain before the command case.
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(status).toHaveBeenCalledTimes(4);
  cleanup();
  vi.useRealTimers();

  const toast = vi.fn();
  useApp.setState({ toast });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue(sample());
  const start = vi.spyOn(api, 'milAccelerometerStart')
    .mockRejectedValueOnce(new Error('start failed'))
    .mockRejectedValueOnce('unknown start failure');
  render(<MilAccelerometerCard />);
  fireEvent.click(await screen.findByRole('button', { name: 'Start tracking' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'start failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Start tracking' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'Accelerometer command failed'));
  expect(start).toHaveBeenCalledTimes(2);
});

it('shows a safe message when the initial status request rejects with an unknown value', async () => {
  vi.spyOn(api, 'milAccelerometerStatus').mockRejectedValue('status unavailable');
  render(<MilAccelerometerCard />);
  expect(await screen.findByText('Unable to read accelerometer status')).toBeTruthy();
});

it('ignores initial and in-flight polling responses after unmount', async () => {
  vi.useFakeTimers();
  let resolveInitial: ((value: MilAccelerometerStatus) => void) | undefined;
  let rejectInitial: ((reason?: unknown) => void) | undefined;
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve; }))
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectInitial = reject; }));

  const first = render(<MilAccelerometerCard />);
  first.unmount();
  resolveInitial?.(sample());
  await act(async () => { await Promise.resolve(); });

  const second = render(<MilAccelerometerCard />);
  second.unmount();
  rejectInitial?.('late status failure');
  await act(async () => { await Promise.resolve(); });
  expect(status).toHaveBeenCalledTimes(2);

  let resolvePoll: ((value: MilAccelerometerStatus) => void) | undefined;
  status.mockReset()
    .mockResolvedValueOnce({ ...sample(), running: true })
    .mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve; }));
  const third = render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  third.unmount();
  resolvePoll?.({ ...sample(), running: true, samples_read: 99 });
  await act(async () => { await Promise.resolve(); });
  expect(status).toHaveBeenCalledTimes(2);

  let rejectPoll: ((reason?: unknown) => void) | undefined;
  status.mockReset()
    .mockResolvedValueOnce({ ...sample(), running: true })
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectPoll = reject; }));
  const fourth = render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  fourth.unmount();
  rejectPoll?.('late poll failure');
  await act(async () => { await Promise.resolve(); });
});

it('shows axis readings and lets the operator establish a zero orientation', async () => {
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue(sample(0.5, 0, 0.866));
  render(<MilAccelerometerCard />);
  expect(await screen.findByText('+0.500', { exact: true })).toBeTruthy();
  expect(document.querySelector('.mil-accel-orientation span')?.textContent).toContain('-30.0°');
  fireEvent.click(screen.getByRole('button', { name: 'Set current as zero' }));
  expect(document.querySelector('.mil-accel-orientation span')?.textContent).toContain('+0.0°');
  fireEvent.click(screen.getByRole('button', { name: 'Re-zero board' }));
  fireEvent.click(screen.getByRole('button', { name: 'Clear zero' }));
  expect(screen.getByRole('button', { name: 'Set current as zero' })).toBeTruthy();
});

it('disables hardware controls in read-only mode and reports backend errors', async () => {
  const toast = vi.fn();
  useApp.setState({ controlMode: false, toast });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ ...sample(), available: false, last_error: 'sensor missing' });
  const start = vi.spyOn(api, 'milAccelerometerStart');
  render(<MilAccelerometerCard />);
  expect(await screen.findByText('sensor missing')).toBeTruthy();
  const button = screen.getByRole('button', { name: 'Start tracking' }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  expect(screen.getByText(/Read-only mode/)).toBeTruthy();
  fireEvent.click(button);
  expect(start).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByText('unavailable')).toBeTruthy());
});
