// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
});

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.useRealTimers();
});

it('maps every hardware source across single and live acquisition', () => {
  render(<CaptureControls />);
  expect(screen.getByText(/full 16-channel probe pool/)).toBeTruthy();

  fireEvent.click(screen.getByRole('button', { name: 'Live ring' }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'rolling', auto_rearm: true, sample_rate: 50e6 });
  expect(screen.getByText(/SDRAM ring/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Single-shot' }));
  expect(useApp.getState().captureSettings.mode).toBe('single');

  fireEvent.click(screen.getByRole('button', { name: /Packed narrow/ }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'digital_narrow', enabled_digital: [0], sample_rate: 200e6 });
  expect(screen.getByText('Packed narrow is live-only.')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Single-shot' }).hasAttribute('disabled')).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: /Mixed scan/ }));
  expect(useApp.getState().captureSettings.mode).toBe('mixed_continuous');
  fireEvent.click(screen.getByRole('button', { name: 'Single-shot' }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'mixed', analog_enabled: true, readback_compression: 'raw' });

  fireEvent.click(screen.getByRole('button', { name: /Analog fast/ }));
  expect(useApp.getState().captureSettings).toMatchObject({ mode: 'analog_fast', enabled_digital: [] });
  fireEvent.click(screen.getByRole('button', { name: 'Live ring' }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_continuous');

  fireEvent.click(screen.getByRole('button', { name: /Maximum analog/ }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_all_continuous');
  fireEvent.click(screen.getByRole('button', { name: 'Single-shot' }));
  expect(useApp.getState().captureSettings.mode).toBe('analog_all');
  expect(screen.getByText(/physical MAX1000 analog profile/)).toBeTruthy();
});

it('updates rates, live windows, depth, compression, packing, repeat and name', () => {
  useApp.setState({ captureSettings: settings({ mode: 'rolling', sample_rate: 10e3,
    num_samples: 1, auto_rearm: true, readback_compression: 'delta_rle' }) });
  render(<CaptureControls />);
  expect(screen.getByText(/Live compression buffer: ready/)).toBeTruthy();
  expect(screen.getByRole('option', { name: /^5 s / })).toBeTruthy();
  expect(screen.getByRole('option', { name: /100 us/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Sample rate'), { target: { value: '100000' } });
  expect(useApp.getState().captureSettings.sample_rate).toBe(100e3);
  fireEvent.change(screen.getByLabelText('Live window'), { target: { value: '0.01' } });
  expect(useApp.getState().captureSettings.num_samples).toBe(1000);
  fireEvent.click(screen.getByRole('button', { name: 'RAW' }));
  fireEvent.click(screen.getByRole('button', { name: 'DELTA RLE' }));
  expect(useApp.getState().captureSettings.readback_compression).toBe('delta_rle');
  fireEvent.click(screen.getByRole('checkbox', { name: /Packed mode/ }));
  expect(screen.getByText(/Rolling ceiling raised/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Capture name'), { target: { value: 'bench run' } });

  fireEvent.click(screen.getByRole('button', { name: /Digital deep/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Single-shot' }));
  fireEvent.change(screen.getByLabelText('Sample rate'), { target: { value: '100000000' } });
  fireEvent.change(screen.getByLabelText('Samples'), { target: { value: '1024' } });
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
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole('option', { name: 'UART demo' })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Mock scenario'), { target: { value: 'uart' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(screen.getByText('slow readback')).toBeTruthy();
  expect(screen.getByText('unsupported rate')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Capture' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button', { name: 'Queue capture job' }).hasAttribute('disabled')).toBe(true);

  act(() => useApp.setState({ controlMode: false }));
  expect(screen.getByRole('button', { name: 'RAW' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('checkbox', { name: /Packed mode/ }).hasAttribute('disabled')).toBe(true);
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

it('polls queued jobs through running, done and error displays', async () => {
  vi.useFakeTimers();
  const submit = vi.spyOn(api, 'submitCaptureJob')
    .mockResolvedValueOnce({ id: 'j1', state: 'queued' } as never)
    .mockResolvedValueOnce({ id: 'j2', state: 'error', error: 'worker died' } as never);
  vi.spyOn(api, 'captureJob')
    .mockResolvedValueOnce({ id: 'j1', state: 'running' } as never)
    .mockResolvedValueOnce({ id: 'j1', state: 'done', session_id: 's2' } as never);
  const { rerender } = render(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Queue capture job' }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('Headless job queued')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(screen.getByText('Headless job running')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(400); });
  expect(screen.getByText(/Headless job done/).parentElement?.textContent).toContain('session s2');

  rerender(<CaptureControls />);
  fireEvent.click(screen.getByRole('button', { name: 'Queue capture job' }));
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText(/Headless job error/).parentElement?.textContent).toContain('worker died');
  expect(submit).toHaveBeenCalledTimes(2);
});

it('reports queue errors and tolerates scenario/validation service failures', async () => {
  vi.useFakeTimers();
  vi.spyOn(api, 'mockScenarios').mockRejectedValue(new Error('catalog down'));
  vi.mocked(api.validateSettings).mockRejectedValue(new Error('validator down'));
  vi.spyOn(api, 'submitCaptureJob').mockRejectedValue(new Error('queue down'));
  const toast = vi.fn(); useApp.setState({ toast, status: status({ device_kind: 'mock' }) as never });
  render(<CaptureControls />);
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  fireEvent.click(screen.getByRole('button', { name: 'Queue capture job' }));
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
