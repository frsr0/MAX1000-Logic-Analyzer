// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { MilAccelerometerConfig, MilAccelerometerStatus } from '../api/types';
import { useApp } from '../state/appStore';
import { MilAccelerometerCard } from './MilAccelerometerCard';

const POLL_INTERVAL_MS = 100;
const IDLE_POLL_INTERVAL_MS = 1000;

const sample = (x_g = 0, y_g = 0, z_g = 1): MilAccelerometerStatus => ({
  running: false, available: true, sample_rate_hz: 100, sample: { x_g, y_g, z_g, timestamp: 1 },
  samples_read: 42, last_error: null,
});
const accelerometerConfig: MilAccelerometerConfig = {
  output_data_rate_hz: 10,
  full_scale_g: 2,
  operating_mode: 'high_resolution',
  bdu: true,
  endian: 'little',
  self_test: 'normal',
  x_enabled: true,
  y_enabled: true,
  z_enabled: true,
};

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn() });
  vi.spyOn(api, 'milAccelerometerConfig').mockResolvedValue(accelerometerConfig);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

it('starts and stops tracking, polling fast while running and slowly while idle', async () => {
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

it('resynchronizes after an external start while the card was initially idle', async () => {
  vi.useFakeTimers();
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockResolvedValueOnce(sample())
    .mockResolvedValueOnce({ ...sample(), running: true, samples_read: 99 });
  render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });

  await act(async () => { await vi.advanceTimersByTimeAsync(IDLE_POLL_INTERVAL_MS); });
  expect(status).toHaveBeenCalledTimes(2);
  // Fake timers do not advance Testing Library's polling loop. Flush the
  // resolved status promise directly before checking the resynchronised UI.
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(screen.getByText('tracking')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2); });
  expect(status).toHaveBeenCalledTimes(4);
});

it('updates the orientation from each pushed decoded sample', async () => {
  class FakeAccelerometerWebSocket {
    static instances: FakeAccelerometerWebSocket[] = [];
    onopen: (() => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onclose: (() => void) | null = null;
    onerror: (() => void) | null = null;
    readonly url: string;

    constructor(url: string) {
      this.url = url;
      FakeAccelerometerWebSocket.instances.push(this);
    }

    close() { this.onclose?.(); }

    emit(data: unknown) {
      this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
  vi.stubGlobal('WebSocket', FakeAccelerometerWebSocket);
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ ...sample(), running: true });
  render(<MilAccelerometerCard />);
  await waitFor(() => expect(screen.getAllByText('+0.000', { exact: true })).toHaveLength(2));
  expect((document.querySelector('.mil-accel-board') as HTMLElement).style.transition).toBe('none');
  expect(FakeAccelerometerWebSocket.instances).toHaveLength(1);
  expect(FakeAccelerometerWebSocket.instances[0].url).toContain('/ws/accelerometer');

  const push = (x_g: number, samples_read: number) => {
    FakeAccelerometerWebSocket.instances[0].emit({
      type: 'accelerometer_sample',
      ts: Date.now() / 1000,
      data: {
        sample: { x_g, y_g: 0, z_g: 0.866, timestamp: Date.now() / 1000 },
        last_sample_at: Date.now() / 1000,
        samples_read,
      },
    });
  };
  await act(async () => {
    push(0.5, 43);
    push(0.75, 44);
  });
  expect(frames).toHaveLength(1);
  await act(async () => { frames[0](0); });
  expect(screen.getByText('+0.750', { exact: true })).toBeTruthy();
  expect(screen.queryByText('+0.500', { exact: true })).toBeNull();
  expect(document.querySelector('.mil-accelerometer-card')?.textContent).toContain('44');
});

it('keeps high-rate worker ingestion independent from a slow render', async () => {
  class FakeAccelerometerWorker {
    static instances: FakeAccelerometerWorker[] = [];
    onmessage: ((event: MessageEvent) => void) | null = null;
    readonly url: string;
    received = 0;

    constructor(url: URL | string) {
      this.url = String(url);
      FakeAccelerometerWorker.instances.push(this);
    }

    emit(data: unknown) {
      this.received += 1;
      this.onmessage?.({ data } as MessageEvent);
    }

    terminate() {}
  }

  vi.stubGlobal('Worker', FakeAccelerometerWorker);
  const frames: FrameRequestCallback[] = [];
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ ...sample(), running: true });

  render(<MilAccelerometerCard />);
  await waitFor(() => expect(FakeAccelerometerWorker.instances).toHaveLength(1));

  const worker = FakeAccelerometerWorker.instances[0];
  for (let i = 0; i < 120; i += 1) {
    worker.emit({
      type: 'accelerometer_sample',
      data: {
        sample: { x_g: i / 100, y_g: 0, z_g: 0.866, timestamp: i },
        last_sample_at: i,
        samples_read: i + 1,
      },
    });
  }

  // All 120 messages were accepted without waiting for React, while the
  // bounded mailbox schedules only one paint and keeps the newest reading.
  expect(worker.received).toBe(120);
  expect(frames).toHaveLength(1);
  await act(async () => { frames[0](0); });
  expect(screen.getByText('+1.190', { exact: true })).toBeTruthy();
  expect(document.querySelector('.mil-accelerometer-card')?.textContent).toContain('120');
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

it('keeps the live poll cadence independent of response latency', async () => {
  vi.useFakeTimers();
  const delayed = () => new Promise<MilAccelerometerStatus>((resolve) => {
    window.setTimeout(() => resolve({ ...sample(), running: true, samples_read: 43 }), 80);
  });
  const status = vi.spyOn(api, 'milAccelerometerStatus')
    .mockResolvedValueOnce({ ...sample(), running: true })
    .mockImplementation(delayed);
  render(<MilAccelerometerCard />);
  await act(async () => { await Promise.resolve(); });

  await act(async () => { await vi.advanceTimersByTimeAsync(350); });

  // A fixed 100 ms cadence gets three opportunities by 350 ms even though
  // each request takes 80 ms. The implementation must still be single-flight.
  expect(status).toHaveBeenCalledTimes(4);
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

it('shows measured sample/data rates and hardware configuration readback', async () => {
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({
    ...sample(), running: true, samples_read: 1234, effective_odr_hz: 100,
    host_read_rate_hz: 97.5, host_data_rate_bytes_per_s: 585,
    last_sample_at: Date.now() / 1000,
    effective_config: { ...accelerometerConfig, output_data_rate_hz: 100, full_scale_g: 8,
      operating_mode: 'low_power', endian: 'big', bdu: false, x_enabled: true,
      y_enabled: false, z_enabled: true },
    register_readback: { ctrl1: 0x5d, ctrl4: 0x62 },
  });
  render(<MilAccelerometerCard />);
  expect(await screen.findByText('Effective sensor ODR')).toBeTruthy();
  expect(await screen.findByText('97.5 samples/s')).toBeTruthy();
  expect(screen.getByText('585 bytes/s')).toBeTruthy();
  expect(screen.getByText(/CTRL1 0x5D · CTRL4 0x62/)).toBeTruthy();
  expect(screen.getByLabelText('Accelerometer hardware readback').textContent).toContain('±8 g');
  expect(screen.getByText('low power')).toBeTruthy();
});

it('loads and saves the complete accelerometer configuration while stopped', async () => {
  const update = vi.spyOn(api, 'milAccelerometerUpdateConfig')
    .mockImplementation(async (next) => next);
  render(<MilAccelerometerCard />);

  const odr = await screen.findByRole('combobox', { name: 'Output data rate' });
  expect(odr).toHaveProperty('value', '10');
  expect(screen.queryByRole('option', { name: '5,376 Hz' })).toBeNull();
  fireEvent.change(odr, { target: { value: '100' } });
  await waitFor(() => expect(update).toHaveBeenNthCalledWith(1, {
    ...accelerometerConfig, output_data_rate_hz: 100,
  }));

  fireEvent.click(screen.getByRole('checkbox', { name: 'Block data update (BDU)' }));
  await waitFor(() => expect(update).toHaveBeenNthCalledWith(2, {
    ...accelerometerConfig, output_data_rate_hz: 100, bdu: false,
  }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Enable X axis' }));
  await waitFor(() => expect(update).toHaveBeenNthCalledWith(3, {
    ...accelerometerConfig, output_data_rate_hz: 100, bdu: false, x_enabled: false,
  }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Enable Y axis' }));
  await waitFor(() => expect(update).toHaveBeenNthCalledWith(4, {
    ...accelerometerConfig, output_data_rate_hz: 100, bdu: false, x_enabled: false, y_enabled: false,
  }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Enable Z axis' }));
  await waitFor(() => expect(update).toHaveBeenNthCalledWith(5, {
    ...accelerometerConfig, output_data_rate_hz: 100, bdu: false,
    x_enabled: false, y_enabled: false, z_enabled: false,
  }));
});

it('disables accelerometer configuration controls while tracking', async () => {
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ ...sample(), running: true });
  render(<MilAccelerometerCard />);
  const controls = await screen.findAllByRole('combobox');
  expect(controls.length).toBeGreaterThanOrEqual(5);
  controls.forEach((control) => expect(control).toHaveProperty('disabled', true));
  ['Block data update (BDU)', 'Enable X axis', 'Enable Y axis', 'Enable Z axis']
    .forEach((name) => expect(screen.getByRole('checkbox', { name })).toHaveProperty('disabled', true));
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
