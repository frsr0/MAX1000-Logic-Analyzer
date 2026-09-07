// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api, clientId } from '../api/client';
import { useApp } from '../state/appStore';
import { SettingsPage } from './SettingsPage';

const unavailable = { running: false, transport: null, app_port: null, tcp_endpoint: null,
  driver: { available: false, setup_path: null, error: null } };
const available = { running: false, transport: null, app_port: null, tcp_endpoint: null,
  driver: { available: true, setup_path: 'setupc.exe', error: 'elevation required' } };

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  vi.spyOn(api, 'virtualSerialStatus').mockResolvedValue(unavailable as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('survives a virtual-status failure and identifies another lock holder', async () => {
  vi.mocked(api.virtualSerialStatus).mockRejectedValueOnce(new Error('offline'));
  useApp.setState({ status: { device_connected: true, control: {
    held: true, holder: 'other-client', holder_name: 'Alice', expires_at: null,
  } } as never });
  render(<SettingsPage />);
  await waitFor(() => expect(api.virtualSerialStatus).toHaveBeenCalledOnce());
  expect(screen.getByText(/held by/).textContent).toBe('Lock: held by Alice');
  fireEvent.change(screen.getByRole('combobox', { name: 'Bridge transport' }), { target: { value: 'tcp' } });
});

it('updates appearance/defaults, lock ownership and decoder presets', async () => {
  localStorage.setItem('msa_decoder_presets', JSON.stringify([{ name: 'UART lab' }, { name: 'SPI lab' }]));
  const setViewerSettings = vi.fn(); const setCaptureSettings = vi.fn();
  const setControlMode = vi.fn(); const refreshStatus = vi.fn(); const toast = vi.fn();
  useApp.setState({ setViewerSettings, setCaptureSettings, setControlMode, refreshStatus, toast,
    controlMode: true, status: { device_connected: false, control: {
      held: true, holder: clientId(), holder_name: 'me', expires_at: null,
    } } as never });
  vi.spyOn(api, 'acquireControl')
    .mockResolvedValueOnce({ acquired: true } as never)
    .mockResolvedValueOnce({ acquired: false } as never)
    .mockResolvedValue({ acquired: true } as never);
  vi.spyOn(api, 'releaseControl').mockResolvedValue({ released: true } as never);
  render(<SettingsPage />);
  await screen.findByText(/No com0com driver/);
  expect(screen.getByText(/held by/).textContent).toContain('(you)');
  expect(screen.getByText(/no device is connected/i)).toBeTruthy();

  fireEvent.change(screen.getByDisplayValue('Dark'), { target: { value: 'light' } });
  expect(setViewerSettings).toHaveBeenCalledWith({ theme: 'light' });
  const numbers = screen.getAllByRole('spinbutton');
  fireEvent.change(numbers[0], { target: { value: '25000000' } });
  fireEvent.change(numbers[1], { target: { value: '4096' } });
  expect(setViewerSettings).toHaveBeenCalledWith({ defaultSampleRate: 25_000_000 });
  expect(setViewerSettings).toHaveBeenCalledWith({ defaultNumSamples: 4096 });
  fireEvent.click(screen.getByRole('button', { name: 'Apply now' }));
  expect(setCaptureSettings).toHaveBeenCalledWith(expect.objectContaining({ sample_rate: expect.any(Number), num_samples: expect.any(Number) }));
  expect(toast).toHaveBeenCalledWith('success', 'Applied defaults to capture settings');
  fireEvent.click(screen.getByRole('checkbox'));
  expect(setControlMode).toHaveBeenCalledWith(false);

  fireEvent.click(screen.getByRole('button', { name: 'Acquire control' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('success', 'Control acquired'));
  fireEvent.click(screen.getByRole('button', { name: 'Acquire control' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('warning', 'Another client holds control'));
  fireEvent.click(screen.getByRole('button', { name: 'Force take' }));
  await waitFor(() => expect(api.acquireControl).toHaveBeenCalledWith('me', true));
  expect(toast).toHaveBeenCalledWith('success', 'Control taken. Connect a device on the Device page.');
  fireEvent.click(screen.getByRole('button', { name: 'Release' }));
  await waitFor(() => expect(api.releaseControl).toHaveBeenCalledOnce());
  expect(refreshStatus).toHaveBeenCalledTimes(4);

  expect(screen.getByText('UART lab')).toBeTruthy();
  fireEvent.click(screen.getAllByRole('button', { name: 'x' })[0]);
  expect(screen.queryByText('UART lab')).toBeNull();
  expect(JSON.parse(localStorage.getItem('msa_decoder_presets')!)).toEqual([{ name: 'SPI lab' }]);
});

it('runs, refreshes and stops the driver-free TCP bridge, including errors', async () => {
  const running = { ...unavailable, running: true, transport: 'tcp', tcp_endpoint: '127.0.0.1:9000' };
  vi.spyOn(api, 'startVirtualBridge').mockResolvedValue(running as never);
  vi.spyOn(api, 'stopVirtualBridge').mockResolvedValue(unavailable as never);
  const toast = vi.fn(); useApp.setState({ toast });
  const { rerender } = render(<SettingsPage />);
  expect(screen.getByText(/Checking virtual-port support/)).toBeTruthy();
  await screen.findByText(/No com0com driver/);
  expect(screen.getByText(/Lock: free/)).toBeTruthy();
  expect(screen.getByText(/Save presets from/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Start SWD bridge' }));
  await screen.findByText(/Endpoint:/);
  expect(api.startVirtualBridge).toHaveBeenCalledWith({ transport: 'tcp', app_port: '' });
  expect(toast).toHaveBeenCalledWith('success', 'Bridge listening at 127.0.0.1:9000');
  fireEvent.click(screen.getByRole('button', { name: 'Stop bridge' }));
  await waitFor(() => expect(api.stopVirtualBridge).toHaveBeenCalledOnce());
  expect(toast).toHaveBeenCalledWith('success', 'SWD bridge stopped');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() => expect(api.virtualSerialStatus).toHaveBeenCalledTimes(2));

  vi.mocked(api.startVirtualBridge).mockRejectedValueOnce(new Error('start failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Start SWD bridge' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'start failed'));
  useApp.setState({ status: useApp.getState().status }); rerender(<SettingsPage />);
});

it('creates and uses a virtual COM pair and handles pair/bridge failures', async () => {
  vi.mocked(api.virtualSerialStatus).mockResolvedValue(available as never);
  vi.spyOn(api, 'createVirtualComPair')
    .mockResolvedValueOnce({ port_a: 'COM30', port_b: 'COM31' } as never)
    .mockRejectedValueOnce(new Error('pair failed'));
  vi.spyOn(api, 'startVirtualBridge')
    .mockResolvedValueOnce({ ...available, running: true, transport: 'com', app_port: 'COM30' } as never)
    .mockRejectedValueOnce(new Error('bridge failed'));
  vi.spyOn(api, 'stopVirtualBridge').mockRejectedValueOnce(new Error('stop failed'));
  const toast = vi.fn(); useApp.setState({ toast });
  render(<SettingsPage />);
  await screen.findByText(/elevation to enumerate/);
  expect(screen.getByText(/setupc.exe/)).toBeTruthy();
  const combobox = screen.getByRole('combobox', { name: 'Bridge transport' });
  fireEvent.change(combobox, { target: { value: 'com' } });
  const endpoints = screen.getAllByPlaceholderText('COM20');
  fireEvent.change(endpoints[0], { target: { value: 'COM30' } });
  fireEvent.change(screen.getByPlaceholderText('COM21'), { target: { value: 'COM31' } });
  fireEvent.click(screen.getByRole('button', { name: 'Make COM pair' }));
  await waitFor(() => expect(api.createVirtualComPair).toHaveBeenCalledWith('COM30', 'COM31'));
  expect(toast).toHaveBeenCalledWith('success', 'Created COM30 ↔ COM31');
  fireEvent.change(screen.getByRole('textbox', { name: 'App-side COM endpoint' }), { target: { value: 'COM32' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start SWD bridge' }));
  await waitFor(() => expect(api.startVirtualBridge).toHaveBeenCalledWith({ transport: 'com', app_port: 'COM32' }));
  expect(toast).toHaveBeenCalledWith('success', 'Bridge opened COM30');

  fireEvent.click(screen.getByRole('button', { name: 'Stop bridge' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'stop failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Make COM pair' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'pair failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Stop bridge' }));
  await waitFor(() => expect(api.stopVirtualBridge).toHaveBeenCalledTimes(2));
});
