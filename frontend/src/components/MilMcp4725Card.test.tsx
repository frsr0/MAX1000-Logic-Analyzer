// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { MilMcp4725Card } from './MilMcp4725Card';

beforeEach(() => {
  useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn(),
    status: { device_kind: 'mock' } as never,
    openSession: vi.fn().mockResolvedValue(undefined), setPage: vi.fn() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('sends the three DAC codes and displays measured failures', async () => {
  const test = vi.spyOn(api, 'milMcp4725Test').mockResolvedValue({
    mock: false, address: 0x60, sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', sda_pin: 4, scl_pin: 3,
    adc_channel: 3, analog_input: 'AIN4 (J1/6)',
    tolerance_v: 0.2, passed: false, bus_passed: false,
    steps: [{ code: 2048, expected_v: 1.65, measured_v: 0.1, error_v: -1.55,
      acknowledgements: [false, false, false], voltage_passed: false, bus_passed: false, passed: false }],
  });
  render(<MilMcp4725Card />);
  fireEvent.click(screen.getByRole('button', { name: 'Run three-point test' }));
  await waitFor(() => expect(test).toHaveBeenCalledWith({
    address: 0x60, sda_pin: 4, scl_pin: 3, adc_channel: 3, codes: [0, 2048, 4095], tolerance_v: 0.2,
  }));
  expect((await screen.findByRole('status')).textContent).toContain('FAIL');
  expect(screen.getByRole('status').textContent).toContain('0.100 V');
  expect(screen.getByRole('status').textContent).toContain('NACK · NACK · NACK');
});

it('routes mock waveform output to selected pins and opens the scope session', async () => {
  const generate = vi.spyOn(api, 'milMcp4725Waveform').mockResolvedValue({
    mock: true, passed: true, voltage_passed: true, timing_passed: true, bus_passed: true,
    trace_source: 'synthetic',
    session_id: 'dac-scope', waveform: 'sine',
    sda: 'PIO_08 (PMOD/10)', scl: 'D0 (J1/9)', adc_channel: 4, analog_input: 'AIN6 (J1/8)',
    requested_frequency_hz: 0.5, actual_frequency_hz: 0.5,
    measured_min_v: 0.2, measured_max_v: 3.1, max_error_v: 0,
    sample_rate_hz: 32, trace_v: [0.2, 1.65, 3.1],
  });
  render(<MilMcp4725Card />);
  fireEvent.change(screen.getByLabelText('SDA output pin'), { target: { value: '22' } });
  fireEvent.change(screen.getByLabelText('SCL output pin'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('VOUT analog input'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run waveform and save trace' }));
  await waitFor(() => expect(generate).toHaveBeenCalledWith({
    address: 0x60, sda_pin: 22, scl_pin: 0, adc_channel: 4, waveform: 'sine',
    frequency_hz: 0.25, steps_per_cycle: 8, cycles: 2,
    min_v: 0.2, max_v: 3.1, tolerance_v: 0.2,
  }));
  expect((await screen.findByRole('status')).textContent).toContain('SIMULATED PASS');
  fireEvent.click(screen.getByRole('button', { name: 'Open scope trace' }));
  await waitFor(() => expect(useApp.getState().openSession).toHaveBeenCalledWith('dac-scope'));
  expect(useApp.getState().setPage).toHaveBeenCalledWith('capture');
});

it('prevents selecting the same I2C output twice', () => {
  render(<MilMcp4725Card />);
  fireEvent.change(screen.getByLabelText('SCL output pin'), { target: { value: '4' } });
  expect((screen.getByRole('button', { name: 'Run waveform and save trace' }) as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Run three-point test' }) as HTMLButtonElement).disabled).toBe(true);
});

it('rejects invalid DAC codes, tolerances, and waveform voltage limits', () => {
  render(<MilMcp4725Card />);
  const setCode = screen.getByRole('button', { name: 'Set code and measure' }) as HTMLButtonElement;
  const threePoint = screen.getByRole('button', { name: 'Run three-point test' }) as HTMLButtonElement;
  const waveform = screen.getByRole('button', { name: 'Run waveform and save trace' }) as HTMLButtonElement;

  fireEvent.change(screen.getByLabelText('DAC code (0–4095)'), { target: { value: '4096' } });
  expect(setCode.disabled).toBe(true);
  expect(threePoint.disabled).toBe(false);

  fireEvent.change(screen.getByLabelText('Allowed voltage error (V)'), { target: { value: '0.6' } });
  expect(setCode.disabled).toBe(true);
  expect(threePoint.disabled).toBe(true);
  expect(waveform.disabled).toBe(true);

  fireEvent.change(screen.getByLabelText('Allowed voltage error (V)'), { target: { value: '0.2' } });
  fireEvent.change(screen.getByLabelText('Minimum (V)'), { target: { value: '3.2' } });
  expect(waveform.disabled).toBe(true);
});

it('reports DAC test and waveform request errors through the app toast', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.spyOn(api, 'milMcp4725Test').mockRejectedValueOnce(new Error('DAC bus unavailable'));
  vi.spyOn(api, 'milMcp4725Waveform').mockRejectedValueOnce(new Error('waveform rejected'));
  render(<MilMcp4725Card />);

  fireEvent.click(screen.getByRole('button', { name: 'Run three-point test' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'DAC bus unavailable'));
  fireEvent.click(screen.getByRole('button', { name: 'Run waveform and save trace' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'waveform rejected'));
});

it('shows a passing three-point result with successful acknowledgements', async () => {
  const test = vi.spyOn(api, 'milMcp4725Test').mockResolvedValue({
    mock: false, address: 0x60, sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', sda_pin: 4, scl_pin: 3,
    adc_channel: 3, analog_input: 'AIN4 (J1/6)', tolerance_v: 0.2, passed: true, bus_passed: true,
    steps: [{ code: 2048, expected_v: 1.65, measured_v: 1.64, error_v: -0.01,
      acknowledgements: [true, true, true], voltage_passed: true, bus_passed: true, passed: true }],
  });
  render(<MilMcp4725Card />);
  fireEvent.click(screen.getByRole('button', { name: 'Run three-point test' }));

  await waitFor(() => expect(test).toHaveBeenCalled());
  expect((await screen.findByRole('status')).textContent).toContain('HARDWARE PASS');
  expect(screen.getByRole('status').textContent).toContain('ACK · ACK · ACK');
});

it('sets a boundary DAC code and renders mock measurements without captured ACK data', async () => {
  const test = vi.spyOn(api, 'milMcp4725Test').mockResolvedValue({
    mock: true, address: 0x63, sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', sda_pin: 4, scl_pin: 3,
    adc_channel: 3, analog_input: 'AIN4 (J1/6)', tolerance_v: 0.5, passed: true, bus_passed: true,
    steps: [{ code: 4095, expected_v: 3.3, measured_v: 3.28, error_v: -0.02,
      acknowledgements: null, voltage_passed: true, bus_passed: true, passed: true }],
  });
  render(<MilMcp4725Card />);
  fireEvent.change(screen.getByLabelText('I²C address'), { target: { value: '99' } });
  fireEvent.change(screen.getByLabelText('DAC code (0–4095)'), { target: { value: '4095' } });
  fireEvent.change(screen.getByLabelText('Allowed voltage error (V)'), { target: { value: '0.5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set code and measure' }));

  await waitFor(() => expect(test).toHaveBeenCalledWith({
    address: 0x63, sda_pin: 4, scl_pin: 3, adc_channel: 3, codes: [4095], tolerance_v: 0.5,
  }));
  expect((await screen.findByRole('status')).textContent).toContain('SIMULATED PASS');
  expect(screen.getByRole('status').textContent).toContain('n/a');
});

it('sends alternate waveform settings including the full voltage range', async () => {
  const generate = vi.spyOn(api, 'milMcp4725Waveform').mockResolvedValue({
    mock: false, passed: false, voltage_passed: true, timing_passed: false, bus_passed: true,
    trace_source: 'preflight_measured_levels', session_id: 'triangle-scope', waveform: 'triangle',
    sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', adc_channel: 3, analog_input: 'AIN4 (J1/6)',
    requested_frequency_hz: 0.5, actual_frequency_hz: 0.48,
    measured_min_v: 0, measured_max_v: 3.3, max_error_v: 0.02,
    sample_rate_hz: 9.6, trace_v: [0, 1.65, 3.3],
  });
  render(<MilMcp4725Card />);
  fireEvent.change(screen.getByLabelText('Shape'), { target: { value: 'triangle' } });
  fireEvent.change(screen.getByLabelText('Frequency (Hz)'), { target: { value: '0.5' } });
  fireEvent.change(screen.getByLabelText('Steps per cycle'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Cycles'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Minimum (V)'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Maximum (V)'), { target: { value: '3.3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run waveform and save trace' }));

  await waitFor(() => expect(generate).toHaveBeenCalledWith({
    address: 0x60, sda_pin: 4, scl_pin: 3, adc_channel: 3, waveform: 'triangle',
    frequency_hz: 0.5, steps_per_cycle: 10, cycles: 3,
    min_v: 0, max_v: 3.3, tolerance_v: 0.2,
  }));
  const result = await screen.findByRole('status');
  expect(result.textContent).toContain('HARDWARE FAIL');
  expect(result.textContent).toContain('timing fail');
  expect(screen.getByText(/ADC measured each code before the timed run/)).toBeTruthy();
});

it('reports an error when opening the saved scope trace fails', async () => {
  const toast = vi.fn();
  useApp.setState({ toast, openSession: vi.fn().mockRejectedValue(new Error('scope session unavailable')) });
  vi.spyOn(api, 'milMcp4725Waveform').mockResolvedValue({
    mock: true, passed: true, voltage_passed: true, timing_passed: true, bus_passed: true,
    trace_source: 'synthetic', session_id: 'missing-scope', waveform: 'sine',
    sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', adc_channel: 3, analog_input: 'AIN4 (J1/6)',
    requested_frequency_hz: 0.5, actual_frequency_hz: 0.5,
    measured_min_v: 0.2, measured_max_v: 3.1, max_error_v: 0,
    sample_rate_hz: 32, trace_v: [0.2, 1.65, 3.1],
  });
  render(<MilMcp4725Card />);
  fireEvent.click(screen.getByRole('button', { name: 'Run waveform and save trace' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open scope trace' }));

  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'scope session unavailable'));
});

it('explains missing ACK capture and displays each failed waveform check', async () => {
  vi.spyOn(api, 'milMcp4725Test').mockResolvedValue({
    mock: false, address: 0x60, sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', sda_pin: 4, scl_pin: 3,
    adc_channel: 3, analog_input: 'AIN4 (J1/6)', tolerance_v: 0.2, passed: false, bus_passed: false,
    steps: [{ code: 2048, expected_v: 1.65, measured_v: 1.65, error_v: 0,
      acknowledgements: [], voltage_passed: true, bus_passed: true, passed: true }],
  });
  vi.spyOn(api, 'milMcp4725Waveform').mockResolvedValue({
    mock: false, passed: false, voltage_passed: false, timing_passed: false, bus_passed: false,
    trace_source: 'preflight_measured_levels', session_id: 'failed-scope', waveform: 'sine',
    sda: 'D4 (J1/13)', scl: 'D3 (J1/12)', adc_channel: 3, analog_input: 'AIN4 (J1/6)',
    requested_frequency_hz: 0.25, actual_frequency_hz: 0,
    measured_min_v: 0, measured_max_v: 0, max_error_v: 3.1,
    sample_rate_hz: 0, trace_v: [],
  });
  render(<MilMcp4725Card />);

  fireEvent.click(screen.getByRole('button', { name: 'Run three-point test' }));
  const testStatus = await screen.findByRole('status');
  expect(testStatus.textContent).toContain('not captured');
  expect(testStatus.textContent).toContain('No DAC ACK was seen');

  fireEvent.click(screen.getByRole('button', { name: 'Run waveform and save trace' }));
  const waveformStatus = await screen.findByText('Scope trace: ADC measured each code before the timed run; levels are replayed at the measured I²C cadence.');
  const result = waveformStatus.closest('[role="status"]');
  expect(result?.textContent).toContain('voltage fail');
  expect(result?.textContent).toContain('timing fail');
  expect(result?.textContent).toContain('I²C ACK fail');
});

it('shows fallback labels for pins and ADC channels outside the supported options', () => {
  useApp.setState({ status: { device_kind: 'hardware' } as never });
  render(<MilMcp4725Card />);
  const sda = screen.getByLabelText('SDA output pin') as HTMLSelectElement;
  const adc = screen.getByLabelText('VOUT analog input') as HTMLSelectElement;
  sda.add(new Option('Unknown pin', '99'));
  adc.add(new Option('Unknown channel', '99'));

  fireEvent.change(sda, { target: { value: '99' } });
  fireEvent.change(adc, { target: { value: '99' } });

  expect(screen.getByText(/Connect DAC SDA to Pin 99/)).toBeTruthy();
  expect(screen.getByText(/VOUT to ADC99/)).toBeTruthy();
  expect(screen.getByText(/ADC99 verifies each distinct level/)).toBeTruthy();
});
