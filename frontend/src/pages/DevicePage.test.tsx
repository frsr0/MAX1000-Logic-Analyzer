// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DevicePage } from './DevicePage';
import { useApp } from '../state/appStore';

const devices = [
  { id: 'hardware', name: 'MAX1000', driver: 'ftdi', connection: 'USB', available: true, mock: false, detail: 'attached' },
  { id: 'offline', name: 'Offline', driver: 'none', connection: 'none', available: false, mock: false, detail: '' },
  { id: 'mock', name: 'Synthetic', driver: 'mock', connection: 'memory', available: true, mock: true, detail: 'preview' },
];
const meta = { driver: 'ftdi', device_name: 'MAX1000', connection: 'USB', port: 'B',
  firmware_version: '3', protocol_version: 'unknown', sys_clk_hz: 100_000, sample_clk_hz: 200_000_000, mock: false, extra: {} };
const status = { app_version: '3', uptime_s: 12.9, device_connected: true, device_kind: 'hardware', device: meta,
  capture_state: 'idle', capture_progress: {}, last_session_id: null, last_error: null,
  control: { held: true, holder: 'c', holder_name: 'Browser', acquired_at: 0 }, ws_clients: 2, session_count: 0 };
const caps = { digital_channels: 16, analog_channels: 8, max_sample_rate: 200_000_000,
  min_sample_rate: 10_000, max_samples: 4_194_304, bram_samples: 4096,
  generator_protocols: ['uart', 'spi'], notes: ['exact board metadata'], analog_rate_note: '',
  triggers: [
    { type: 'edge', execution: 'hardware', description: 'FPGA' },
    { type: 'uart', execution: 'post_capture', description: 'software' },
    { type: 'missing', execution: 'unavailable', description: 'none' },
  ], analog_pin_map: [
    { board_label: 'AIN0', adc_channel: 0, current_rtl_stream: true, header: 'P1', fpga_pin: 'A1' },
    { board_label: 'AIN1', available: true }, { board_label: 'AIN2', available: false },
  ], digital_pin_map: [{ pin_index: 0, board_label: 'D0' }],
};

beforeEach(() => { localStorage.clear(); useApp.setState(useApp.getInitialState(), true); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('scans and presents available, offline and mock devices in read-only mode', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ devices })));
  useApp.setState({ controlMode: false });
  render(<DevicePage />);
  expect(await screen.findByText('MAX1000')).toBeTruthy();
  expect(screen.getByText('Synthetic (mock)')).toBeTruthy();
  expect(screen.getByText('offline')).toBeTruthy();
  expect(screen.getAllByRole('button', { name: 'Connect' }).every((button) => button.hasAttribute('disabled'))).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Rescan' }));
});

it('renders exact connected-board metadata, capability maps and successful diagnostics', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/devices') return Response.json({ devices });
    if (url === '/api/device/debug') return Response.json({ raw_status: { ok: true } });
    if (url === '/api/device/self-test') return Response.json({ passed: true, message: 'healthy', checks: [
      { passed: true, name: 'SPI', detail: 'ok' }, { passed: false, name: 'ADC', detail: 'fixture absent' },
    ] });
    return Response.json({});
  });
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ status: status as never, capabilities: caps as never });
  render(<DevicePage />);
  expect(await screen.findByText('200.0 MHz sample clock')).toBeTruthy();
  expect(screen.getByText('100 kHz system clock')).toBeTruthy();
  expect(screen.getByText('protocol unknown')).toBeTruthy();
  expect(screen.getByText('4,194,304 samples')).toBeTruthy();
  expect(screen.getByText('Analog capture available')).toBeTruthy();
  expect(screen.getByText('captured')).toBeTruthy();
  expect(screen.getByText('board pin')).toBeTruthy();
  expect(screen.getByText('not input')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Raw debug inspector' }));
  expect(await screen.findByText(/raw_status/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Run self-test' }));
  expect(await screen.findByText('PASS')).toBeTruthy();
  expect(screen.getByText(/FAIL ADC: fixture absent/)).toBeTruthy();
});

it('connects and disconnects devices, refreshing public state around the transition', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/devices') return Response.json({ devices });
    if (url === '/api/status') return Response.json(status);
    if (url === '/api/device/capabilities') return Response.json(caps);
    return Response.json({ connected: true });
  });
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ status: { ...status, device_connected: false, device: null } as never });
  render(<DevicePage />);
  fireEvent.click((await screen.findAllByRole('button', { name: 'Connect' }))[0]);
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'Device connected')).toBe(true));
  useApp.setState({ status: status as never });
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url) === '/api/disconnect')).toBe(true));
});

it('reports connect, debug and self-test failures and tolerates scan/disconnect failures', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ devices }))
    .mockRejectedValue(new Error('transport lost'));
  vi.stubGlobal('fetch', fetcher);
  const { rerender } = render(<DevicePage />);
  await screen.findByText('MAX1000');
  fireEvent.click(screen.getByRole('button', { name: 'Rescan' }));
  useApp.setState({ status: status as never }); rerender(<DevicePage />);
  fireEvent.click(screen.getByRole('button', { name: 'Raw debug inspector' }));
  fireEvent.click(screen.getByRole('button', { name: 'Run self-test' }));
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
  await waitFor(() => expect(useApp.getState().toasts.filter((toast) => toast.message === 'transport lost').length).toBeGreaterThanOrEqual(2));
  useApp.setState({ status: { ...status, device_connected: false, device: null } as never });
  rerender(<DevicePage />);
  fireEvent.click((await screen.findAllByRole('button', { name: 'Connect' }))[0]);
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.level === 'error')).toBe(true));
});

it('renders mock/known-protocol/free-control metadata and a failed self-test', async () => {
  const mockStatus = { ...status, device: { ...meta, mock: true, protocol_version: '3' },
    control: { ...status.control, held: false } };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input) === '/api/devices') return Response.json({ devices });
    if (String(input) === '/api/device/self-test') return Response.json({ passed: false, message: 'loopback absent', checks: [] });
    return Response.json({});
  });
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ status: mockStatus as never, capabilities: { ...caps, generator_protocols: [] } as never });
  render(<DevicePage />);
  expect(await screen.findByText('MAX1000 (mock)')).toBeTruthy();
  expect(screen.getByText('protocol v3')).toBeTruthy();
  expect(screen.getAllByText('none')).toHaveLength(2);
  expect(screen.getByText('free')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Run self-test' }));
  expect(await screen.findByText('FAIL')).toBeTruthy();
  expect(screen.getByText(/loopback absent/)).toBeTruthy();
});
