// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { CaptureControls } from './CaptureControls';
import { api } from '../api/client';
import { defaultCaptureSettings, type CaptureSettings } from '../api/types';
import { useApp } from '../state/appStore';

const status = (overrides = {}) => ({
  device_connected: true, device_kind: 'hardware', capture_state: 'idle',
  capture_progress: { samples_read: 0, samples_total: 0, message: '' }, ...overrides,
});

function settings(overrides: Partial<CaptureSettings> = {}): CaptureSettings {
  return { ...defaultCaptureSettings(), ...overrides };
}

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ status: status() as never, captureSettings: settings(), controlMode: true });
  vi.spyOn(api, 'validateSettings').mockResolvedValue({ valid: true, findings: [] } as never);
  vi.spyOn(api, 'milAccelerometerStatus').mockResolvedValue({ running: false } as never);
});

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.useRealTimers();
});

it('maps every hardware source across single and live acquisition', () => {
  render(<CaptureControls />);
  expect(screen.getByText(/full 16-channel probe pool/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Continuous view' }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'rolling', auto_rearm: true, sample_rate: 50e6 });
  expect(screen.getByText(/SDRAM ring/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'One capture' }));
  expect(useApp.getState().captureSettings.mode).toBe('single');

  fireEvent.click(screen.getByRole('button', { name: /High-speed single channel/ }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'digital_narrow', enabled_digital: [0], sample_rate: 200e6 });
  expect(screen.getByText(/High-speed single-channel capture runs continuously/)).toBeTruthy();
  expect(screen.getByRole('button', { name: 'One capture' }).hasAttribute('disabled')).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: /Digital \+ analog/ }));
  expect(useApp.getState().captureSettings.mode).toBe('mixed_continuous');
  expect(screen.getByText(/16 digital bits plus ADC1\/AIN3 and ADC2\/AIN1/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'One capture' }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'mixed', analog_enabled: true, readback_compression: 'raw' });

  fireEvent.click(screen.getByRole('button', { name: /Analog — one channel/ }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'analog_fast', enabled_digital: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Continuous view' }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_continuous');

  fireEvent.click(screen.getByRole('button', { name: /Analog — four channels/ }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_all_continuous');
  fireEvent.click(screen.getByRole('button', { name: 'One capture' }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_all');
  expect(screen.getByText(/physical MAX1000 analog profile/)).toBeTruthy();
});

it('offers RLE, then delta RLE, then uncompressed digital readback', () => {
  useApp.setState({ captureSettings: settings({ mode: 'single', readback_compression: 'rle' }) });
  render(<CaptureControls />);
  fireEvent.click(screen.getByText('Advanced transfer options'));
  const buttons = within(screen.getByRole('group', { name: 'Digital readback compression' }))
    .getAllByRole('button');
  expect(buttons.map((button) => button.textContent)).toEqual(['RLE', 'Delta + RLE', 'Uncompressed']);
  expect(buttons[0].getAttribute('title')).toBe('Run-length encoding: fastest for logic signals (recommended)');
  expect(buttons[1].getAttribute('title')).toBe('Delta + RLE: only for numeric buses that change in small steps, e.g. counters or parallel ADC data');
  expect(buttons[2].getAttribute('title')).toBe('No compression');
  expect(buttons[0].className).toContain('active');
});

it('updates rates, live windows, depth, compression, packing, repeat and name', () => {
  useApp.setState({ captureSettings: settings({ mode: 'rolling', sample_rate: 10e3,
    num_samples: 1, auto_rearm: true, readback_compression: 'delta_rle' }) });
  render(<CaptureControls />);
  fireEvent.click(screen.getByText('Advanced transfer options'));
  expect(screen.getByText(/Live compression buffer: ready/)).toBeTruthy();
  expect(screen.getByRole('option', { name: /^5 s / })).toBeTruthy();
  expect(screen.getByRole('option', { name: /100 us/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Sample rate'), { target: { value: '100000' } });
  expect(useApp.getState().captureSettings.sample_rate).toBe(100e3);
  fireEvent.change(screen.getByLabelText('Window duration'), { target: { value: '0.01' } });
  expect(useApp.getState().captureSettings.num_samples).toBe(1000);
  fireEvent.click(screen.getByRole('button', { name: 'Uncompressed' }));
  expect(useApp.getState().captureSettings.readback_compression).toBe('raw');
  fireEvent.click(screen.getByRole('button', { name: 'RLE' }));
  expect(useApp.getState().captureSettings.readback_compression).toBe('rle');
  fireEvent.click(screen.getByRole('button', { name: 'Delta + RLE' }));
  expect(useApp.getState().captureSettings.readback_compression).toBe('delta_rle');
  fireEvent.click(screen.getByRole('checkbox', { name: /Use packed rolling transfer/ }));
  expect(screen.getByText(/Rolling ceiling raised/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Capture name'), { target: { value: 'bench run' } });

  fireEvent.click(screen.getByRole('button', { name: /Digital capture/ }));
  fireEvent.click(screen.getByRole('button', { name: 'One capture' }));
  fireEvent.change(screen.getByLabelText('Sample rate'), { target: { value: '100000000' } });
  fireEvent.change(screen.getByLabelText('Capture length'), { target: { value: '1024' } });
  fireEvent.change(screen.getByLabelText('Repeat N'), { target: { value: '0' } });
  expect(useApp.getState().captureSettings).toMatchObject({ num_samples: 1024, repeat_count: 1 });
});

it('loads mock scenarios, validates settings, and blocks invalid/read-only capture', async () => {
  vi.useFakeTimers();
  vi.spyOn(api, 'mockScenarios').mockResolvedValue({ scenarios: [
    { id: 'demo_mixed', name: 'Mixed demo' }, { id: 'uart', name: 'UART demo' },
  ] } as never);
  vi.mocked(api.validateSettings).mockResolvedValue({ valid: false, findings: [
    { level: 'warning', message: 'slow readback' }, { level: 'error', message: 'unsupported rate' },
  ] } as never);
  useApp.setState({ status: status({ device_kind: 'mock' }) as never });
  render(<CaptureControls />);
  fireEvent.click(screen.getByText('Advanced transfer options'));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole('option', { name: 'UART demo' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Mock scenario'), { target: { value: 'uart' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(screen.getByText('slow readback')).toBeTruthy();
  expect(screen.getByText('unsupported rate')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Capture' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button', { name: 'Run in background' }).hasAttribute('disabled')).toBe(true);

  act(() => useApp.setState({ controlMode: false }));
  expect(screen.getByRole('button', { name: 'Uncompressed' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('checkbox', { name: /Use packed rolling transfer/ }).hasAttribute('disabled')).toBe(true);
});

it('shows persisted out-of-profile options and the default mock scenario', async () => {
  const setCaptureSettings = vi.fn();
  vi.spyOn(api, 'mockScenarios').mockResolvedValue({ scenarios: [
    { id: 'demo_mixed', name: 'Mixed demo' },
  ] } as never);
  useApp.setState({ captureSettings: settings({ sample_rate: 12345, mock_scenario: null }),
    setCaptureSettings, status: status({ device_kind: 'mock' }) as never });
  render(<CaptureControls />);
  expect(screen.getByRole('option', { name: '12.345 kHz' })).toBeTruthy();
  await waitFor(() => expect(screen.getByLabelText('Mock scenario')).toHaveProperty('value', 'demo_mixed'));
  fireEvent.change(screen.getByLabelText('Mock scenario'), { target: { value: 'demo_mixed' } });
  expect(setCaptureSettings).toHaveBeenCalledWith({ mock_scenario: 'demo_mixed' });
});

it('starts and stops captures and reports both failures', async () => {
  const start = vi.spyOn(api, 'startCapture').mockResolvedValue({} as never);
  const stop = vi.spyOn(api, 'stopCapture').mockResolvedValue({} as never);
  const toast = vi.fn(); useApp.setState({ toast });
  const { rerender } = render(<CaptureControls />);
  fireEvent.change(screen.getByLabelText('Capture name'), { target: { value: 'named' } });
  fireEvent.click(screen.getByRole('button', { name: 'Capture' }));
  await waitFor(() => expect(start).toHaveBeenCalledWith(useApp.getState().captureSettings, 'named'));

  useApp.setState({ status: status({ capture_state: 'armed' }) as never }); rerender(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(stop).toHaveBeenCalledOnce());
  stop.mockRejectedValueOnce(new Error('stop failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'stop failed'));

  useApp.setState({ status: status() as never }); rerender(<CaptureControls />);
  start.mockRejectedValueOnce(new Error('start failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Capture' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'start failed'));
});

it('explains and starts the MIL-backed live digital path', async () => {
  vi.spyOn(api, 'milStatus').mockResolvedValue({ running: true } as never);
  const start = vi.spyOn(api, 'startCapture').mockResolvedValue({} as never);
  useApp.setState({ captureSettings: settings({ mode: 'rolling', auto_rearm: true }) });
  render(<CaptureControls />);

  expect((await screen.findByRole('status')).textContent).toContain(
    'MIL emulator is running — Capture will open a live RX/TX digital waveform fed by MIL transactions',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Capture' }));
  await waitFor(() => expect(start).toHaveBeenCalledWith(
    expect.objectContaining({ mode: 'rolling', auto_rearm: true }), '',
  ));
});

it('explains accelerometer bus precedence over MIL live RX/TX', async () => {
  vi.spyOn(api, 'milStatus').mockResolvedValue({ running: true } as never);
  vi.mocked(api.milAccelerometerStatus).mockResolvedValue({ running: true } as never);
  useApp.setState({ captureSettings: settings({ mode: 'rolling', auto_rearm: true }) });
  render(<CaptureControls />);

  expect((await screen.findByRole('status')).textContent).toContain(
    'Accelerometer tracking is active — Capture will open a live LIS3DH I²C bus waveform on CH13/CH14/CH15',
  );
  expect(screen.queryByText(/MIL emulator is running/)).toBeNull();
});

it('quietly falls back when MIL status polling is unavailable', async () => {
  const milStatus = vi.spyOn(api, 'milStatus').mockRejectedValue('status unavailable');
  useApp.setState({ captureSettings: settings({ mode: 'rolling', auto_rearm: true }) });
  render(<CaptureControls />);

  await waitFor(() => expect(milStatus).toHaveBeenCalled());
  expect(screen.queryByRole('status')).toBeNull();
});

it('quietly falls back when accelerometer status polling is unavailable', async () => {
  let rejectStatus!: (reason?: unknown) => void;
  const accelerometerStatus = vi.mocked(api.milAccelerometerStatus).mockImplementation(
    () => new Promise((_resolve, reject) => { rejectStatus = reject; }) as never,
  );
  useApp.setState({ captureSettings: settings({ mode: 'rolling', auto_rearm: true }) });
  render(<CaptureControls />);

  await waitFor(() => expect(accelerometerStatus).toHaveBeenCalled());
  await act(async () => {
    rejectStatus('accelerometer status unavailable');
    await Promise.resolve();
  });
  expect(screen.queryByRole('status')).toBeNull();
});

it('ignores a late accelerometer status failure after the panel unmounts', async () => {
  let rejectStatus!: (reason?: unknown) => void;
  const accelerometerStatus = vi.mocked(api.milAccelerometerStatus).mockImplementation(
    () => new Promise((_resolve, reject) => { rejectStatus = reject; }) as never,
  );
  const { unmount } = render(<CaptureControls />);
  expect(accelerometerStatus).toHaveBeenCalled();
  unmount();
  await act(async () => rejectStatus('accelerometer status unavailable'));
});

it('ignores a late MIL status response after the panel unmounts', async () => {
  let resolveStatus!: (value: any) => void;
  vi.spyOn(api, 'milStatus').mockImplementation(
    () => new Promise((resolve) => { resolveStatus = resolve; }) as never,
  );
  const { unmount } = render(<CaptureControls />);
  expect(resolveStatus).toBeTypeOf('function');
  unmount();
  await act(async () => resolveStatus({ running: true }));
});

it('ignores a late MIL status failure after the panel unmounts', async () => {
  let rejectStatus!: (reason?: unknown) => void;
  vi.spyOn(api, 'milStatus').mockImplementation(
    () => new Promise((_resolve, reject) => { rejectStatus = reject; }) as never,
  );
  const { unmount } = render(<CaptureControls />);
  expect(rejectStatus).toBeTypeOf('function');
  unmount();
  await act(async () => rejectStatus('status unavailable'));
});

it('polls queued jobs through running, done and error displays', async () => {
  vi.useFakeTimers();
  const submit = vi.spyOn(api, 'submitCaptureJob')
    .mockResolvedValueOnce({ id: 'j1', state: 'queued' } as never)
    .mockResolvedValueOnce({ id: 'j2', state: 'error', error: 'worker died' } as never);
  vi.spyOn(api, 'captureJob')
    .mockResolvedValueOnce({ id: 'j1', state: 'running' } as never)
    .mockResolvedValueOnce({ id: 'j1', state: 'done', session_id: 's2' } as never);
  const { rerender } = render(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Run in background' }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('Headless job queued')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getByText('Headless job running')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(400); });
  expect(screen.getByText(/Headless job done/).parentElement?.textContent).toContain('session s2');

  rerender(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Run in background' }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText(/Headless job error/).parentElement?.textContent).toContain('worker died');
  expect(submit).toHaveBeenCalledTimes(2);
});


it('clears its pending poll on unmount and skips the update when already gone', async () => {
  vi.useFakeTimers();
  vi.spyOn(api, 'submitCaptureJob')
    .mockResolvedValue({ id: 'j1', state: 'queued' } as never);
  let resolveJob!: (value: unknown) => void;
  vi.spyOn(api, 'captureJob').mockImplementationOnce(
    () => new Promise((resolve) => { resolveJob = resolve; }) as never);
  const view = render(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Run in background' }));
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  view.unmount();
  await act(async () => { resolveJob({ id: 'j1', state: 'running' }); });
  expect(api.captureJob).toHaveBeenCalledOnce();
  expect(screen.queryByText('Headless job running')).toBeNull();
});

it('reports queue errors and tolerates scenario/validation service failures', async () => {
  vi.useFakeTimers();
  vi.spyOn(api, 'mockScenarios').mockRejectedValue(new Error('catalog down'));
  vi.mocked(api.validateSettings).mockRejectedValue(new Error('validator down'));
  vi.spyOn(api, 'submitCaptureJob').mockRejectedValue(new Error('queue down'));
  const toast = vi.fn(); useApp.setState({ toast, status: status({ device_kind: 'mock' }) as never });
  render(<CaptureControls />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole('button', { name: 'Run in background' }));
  await act(async () => { await Promise.resolve(); });
  expect(toast).toHaveBeenCalledWith('error', 'queue down');
  expect(screen.queryByText('validator down')).toBeNull();
});

it('shows each live compression progress state and normalizes legacy modes', () => {
  const { rerender } = render(<CaptureControls />);
  useApp.setState({ captureSettings: settings({ mode: 'continuous', sample_rate: 50e6,
    num_samples: 100_000, auto_rearm: true, readback_compression: 'delta_rle' }),
    status: status({ capture_state: 'capturing', capture_progress: {
      samples_read: -10, samples_total: 100, message: 'reading',
    } }) as never }); rerender(<CaptureControls />);
  expect(screen.getByText(/0% full \(reading\)/)).toBeTruthy();

  useApp.setState({ status: status({ capture_state: 'capturing', capture_progress: {
    samples_read: 200, samples_total: 100, message: 'full',
  } }) as never }); rerender(<CaptureControls />);
  expect(screen.getByText(/100% full \(full\)/)).toBeTruthy();
  useApp.setState({ status: status({ capture_state: 'capturing' }) as never }); rerender(<CaptureControls />);
  expect(screen.getByText('Live compression buffer: filling...')).toBeTruthy();

  useApp.setState({ captureSettings: settings({ mode: 'triggered' }) }); rerender(<CaptureControls />);
  expect(screen.getByText('This mode is selected by the MAX1000 hardware profile.')).toBeTruthy();
  useApp.setState({ captureSettings: settings({ mode: 'analog' }) }); rerender(<CaptureControls />);
  expect(screen.getByText(/High-speed analog/)).toBeTruthy();
});

it('shows live dropped samples, gaps and observed share without alarming at zero', () => {
  const health = (overrides: Record<string, unknown> = {}) => ({
    active: true, mode: 'rolling', dropped_samples: 12, gaps: 2,
    truncated_captures: 0,
    observed_seconds: 0.1, elapsed_seconds: 1.0, observed_fraction: 0.1, ...overrides,
  });
  useApp.setState({
    captureSettings: settings({
      mode: 'rolling', sample_rate: 50e6, num_samples: 1_000_000, auto_rearm: true,
    }),
    status: status({ capture_state: 'capturing', capture_health: health() }) as never,
  });
  const { rerender } = render(<CaptureControls />);
  expect(screen.getByText(/Dropped samples: 12/)).toBeTruthy();
  expect(screen.getByText(/Truncated captures: 0/)).toBeTruthy();
  expect(screen.getByText(/Flagged gaps: 2/)).toBeTruthy();
  expect(screen.getByText(/10.0% of real time/)).toBeTruthy();
  expect(screen.getByText('Live sample coverage')).toBeTruthy();
  expect(screen.getByText(/host receives about 1 MS\/s/)).toBeTruthy();
  expect(screen.getByRole('status').className).toContain('finding warning');

  useApp.setState({ status: status({
    capture_state: 'capturing', capture_health: health({ dropped_samples: 0xFFFF }),
  }) as never });
  rerender(<CaptureControls />);
  expect(screen.getByText(/Dropped samples: 65535\+/)).toBeTruthy();

  // A truncated capture is a health problem of its own: it must be counted
  // next to the dropped samples and raise the warning even with zero drops.
  useApp.setState({ status: status({
    capture_state: 'capturing',
    capture_health: health({ dropped_samples: 0, gaps: 0, truncated_captures: 3 }),
  }) as never });
  rerender(<CaptureControls />);
  expect(screen.getByText(/Truncated captures: 3/)).toBeTruthy();
  expect(screen.getByText(/Dropped samples: 0/)).toBeTruthy();
  expect(screen.getByRole('status').className).toContain('finding warning');

  useApp.setState({ status: status({
    capture_state: 'capturing',
    capture_health: health({ dropped_samples: null, gaps: 0, observed_fraction: null }),
  }) as never });
  rerender(<CaptureControls />);
  expect(screen.getByText(/Dropped samples: unknown/)).toBeTruthy();
  expect(screen.queryByText(/of real time/)).toBeNull();
  expect(screen.getByRole('status').className).toContain('finding info');

  useApp.setState({ status: status({
    capture_state: 'capturing',
    capture_health: health({ dropped_samples: 0, gaps: 0, observed_fraction: 0 }),
  }) as never });
  rerender(<CaptureControls />);
  expect(screen.getByText(/Dropped samples: 0/)).toBeTruthy();
  expect(screen.getByRole('status').className).toContain('finding info');

  useApp.setState({
    captureSettings: settings({ mode: 'single' }),
    status: status({ capture_state: 'capturing', capture_health: health() }) as never,
  });
  rerender(<CaptureControls />);
  expect(screen.queryByText('Live capture health')).toBeNull();
  expect(screen.queryByText('Live sample coverage')).toBeNull();

  useApp.setState({
    captureSettings: settings({ mode: 'rolling', auto_rearm: true }),
    status: status({ capture_state: 'capturing' }) as never,
  });
  rerender(<CaptureControls />);
  expect(screen.queryByText('Live capture health')).toBeNull();
});

it('keeps a capture failure visible until the error state clears', () => {
  useApp.setState({ status: status({
    capture_state: 'error',
    last_error: 'capture is over-driven: lower the sample rate',
  }) as never });
  const { rerender } = render(<CaptureControls />);
  expect(screen.getByRole('alert').textContent).toContain('capture is over-driven');
  useApp.setState({ status: status({ capture_state: 'error', last_error: null }) as never });
  rerender(<CaptureControls />);
  expect(screen.queryByText(/capture is over-driven/)).toBeNull();
});

it('does not apply validation findings from superseded settings', async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: any) => void;
  let resolveNew!: (value: any) => void;
  vi.mocked(api.validateSettings)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
    .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }));
  render(<CaptureControls />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.change(screen.getByLabelText('Repeat N'), { target: { value: '2' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  await act(async () => resolveNew({ valid: true, findings: [{ level: 'warning', message: 'current finding' }] }));
  expect(screen.getByText('current finding')).toBeTruthy();
  await act(async () => resolveOld({ valid: false, findings: [{ level: 'error', message: 'stale finding' }] }));
  expect(screen.queryByText('stale finding')).toBeNull();
  expect(screen.getByText('current finding')).toBeTruthy();
});

it('normalizes every invalid hardware-setting dimension and clears findings on disconnect', async () => {
  vi.useFakeTimers();
  vi.mocked(api.validateSettings).mockResolvedValue({ valid: false,
    findings: [{ level: 'error', message: 'connected error' }] } as never);
  const { rerender } = render(<CaptureControls />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(screen.getByText('connected error')).toBeTruthy();
  act(() => useApp.setState({ status: status({ device_connected: false }) as never }));
  expect(screen.queryByText('connected error')).toBeNull();

  act(() => useApp.setState({ status: status() as never, captureSettings: settings({
    mode: 'single', analog_enabled: true, readback_compression: 'delta_rle',
  }) }));
  expect(useApp.getState().captureSettings).toMatchObject({ analog_enabled: false,
    sample_rate: 1e6, num_samples: 100_000, readback_compression: 'delta_rle' });

  act(() => useApp.setState({ captureSettings: settings({ mode: 'rolling', sample_rate: 50e6,
    num_samples: 10_000_000, enabled_digital: [], auto_rearm: true,
  }) }));
  expect(useApp.getState().captureSettings.num_samples).toBeLessThanOrEqual(4_194_304);
  expect(useApp.getState().captureSettings.enabled_digital).toHaveLength(16);

  act(() => useApp.setState({ captureSettings: settings({ mode: 'single', num_samples: 12_345 }) }));
  expect(useApp.getState().captureSettings.num_samples).toBe(10_000);

  act(() => useApp.setState({ captureSettings: settings({ mode: 'analog_fast', analog_enabled: false,
    sample_rate: 12_345, num_samples: 12_345, enabled_digital: [1], readback_compression: 'delta_rle',
  }) }));
  expect(useApp.getState().captureSettings).toMatchObject({ analog_enabled: true,
    sample_rate: 1e6, num_samples: 10_000, enabled_digital: [], readback_compression: 'raw' });

  act(() => useApp.setState({ captureSettings: settings({ mode: 'mixed', analog_enabled: false,
    sample_rate: 125e3, num_samples: 100_000, readback_compression: 'delta_rle',
  }) }));
  expect(useApp.getState().captureSettings.readback_compression).toBe('raw');
  rerender(<CaptureControls />);
});

it('defaults missing status to disconnected and ignores a stale validation rejection', async () => {
  vi.useFakeTimers();
  let rejectOld!: (error: Error) => void;
  vi.mocked(api.validateSettings)
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectOld = reject; }))
    .mockResolvedValueOnce({ valid: true, findings: [] } as never);
  useApp.setState({ status: null });
  const { rerender } = render(<CaptureControls />);
  expect(screen.getByRole('button', { name: 'Capture' }).hasAttribute('disabled')).toBe(true);

  act(() => useApp.setState({ status: status() as never })); rerender(<CaptureControls />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.change(screen.getByLabelText('Repeat N'), { target: { value: '2' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  await act(async () => rejectOld(new Error('obsolete validator failure')));
  expect(screen.queryByText('obsolete validator failure')).toBeNull();
  expect(screen.getByRole('button', { name: 'Capture' }).hasAttribute('disabled')).toBe(false);
});
