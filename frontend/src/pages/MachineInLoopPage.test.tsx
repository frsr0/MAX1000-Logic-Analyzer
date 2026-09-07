// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { MachineInLoopPage } from './MachineInLoopPage';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import type { MilConfig, MilRuntimeStatus, MilTransactionResponse } from '../api/types';

const capture = { mode: 'auto' as const, sample_rate: 14_000_000, max_response_bytes: 64,
  manual_post_packet_us: 1000, extra_digital_channels: [4, 5] };
const config = (protocol: MilConfig['protocol'] = 'modbus_uart', overrides: Partial<MilConfig> = {}): MilConfig => ({
  name: `${protocol} fixture`, protocol, description: 'bench device', unit_id: protocol === 'rs485_modbus' ? 17 : 1,
  trigger: { mode: protocol === 'uart' ? 'uart_start_bit' : 'modbus_frame', rx_pin: 0, tx_pin: 1,
    baud: 115200, data_bits: 8, parity: 'none', stop_bits: 1, rs485_de_pin: protocol === 'rs485_modbus' ? 2 : null,
    frame_gap_chars: 3.5 },
  timing: { response_delay_us: 1000, inter_byte_gap_us: 5, jitter_us: 0 }, capture,
  registers: [
    { address: 0, name: 'Status', width: 16, access: 'ro', value: 1, description: 'read only' },
    { address: 0x100, name: 'Setpoint', width: 16, access: 'rw', value: 2, description: 'writable' },
  ], default_response_hex: '', notes: [], ...overrides,
});
const status = (cfg: MilConfig | null = config(), running = true, events: Record<string, any>[] = []): MilRuntimeStatus => ({
  loaded: Boolean(cfg), running, config: cfg, preset_id: 'fixture', events,
});
const presets = [{ id: 'modbus-rtu-demo', name: 'Modbus demo', protocol: 'modbus_uart', description: '', source: 'built-in' },
  { id: 'uart-demo', name: 'UART demo', protocol: 'uart', description: '', source: 'file' }];

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn(), openSession: vi.fn().mockResolvedValue(undefined), setPage: vi.fn() });
  vi.spyOn(api, 'milPresets').mockResolvedValue({ presets } as never);
  vi.spyOn(api, 'milStatus').mockResolvedValue(status());
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

async function renderReady() {
  const view = render(<MachineInLoopPage />);
  await screen.findByText('modbus uart');
  return view;
}

it('refreshes a running Modbus emulator, formats registers, events and waveform fallbacks', async () => {
  vi.mocked(api.milStatus).mockResolvedValue(status(config(), true, [
    { ts: 1, kind: 'notice', message: 'loaded' },
    { ts: 2, kind: 'transaction', message: 'read ok', protocol: 'modbus_uart',
      request_hex: '010300000002c40b', response_hex: '01030400010002', session_id: 'evidence' },
    { ts: 3, kind: 'transaction', message: 'empty response', protocol: 'modbus_uart', baud: 9600,
      inter_byte_gap_us: 0, response_delay_us: 0, request_hex: '01', response_hex: '',
      rx_pin: 3, tx_pin: 4, capture_mode: 'manual', max_response_bytes: 0,
      extra_digital_channels: [6], rs485_de_pin: 7 },
  ]));
  await renderReady();
  expect(screen.getByText('listening')).toBeTruthy();
  expect(screen.getByText('0x0000')).toBeTruthy(); expect(screen.getByText('0x0100')).toBeTruthy();
  expect(screen.getByText('bench device')).toBeTruthy(); expect(screen.getByText('DE CH7')).toBeTruthy();
  expect(screen.getAllByText('(none)').length).toBeGreaterThan(0);
  expect(document.querySelectorAll('path.rx')).toHaveLength(2);
  expect(screen.getByText('CH6')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open capture session' }));
  await waitFor(() => expect(useApp.getState().openSession).toHaveBeenCalledWith('evidence'));
  expect(useApp.getState().setPage).toHaveBeenCalledWith('capture');
});

it('builds read, write, bad-CRC and custom commands for Modbus and UART maps', async () => {
  await renderReady();
  const command = screen.getByLabelText('Command');
  fireEvent.change(command, { target: { value: 'write' } });
  expect(screen.getByLabelText('Request hex')).toHaveProperty('value', expect.stringMatching(/^010601000001/));
  fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'bad-crc' } });
  expect((screen.getByLabelText('Request hex') as HTMLInputElement).value.endsWith('00')).toBe(true);
  fireEvent.change(screen.getByLabelText('Request hex'), { target: { value: 'AA zz 01' } });
  expect(screen.getByLabelText('Command')).toHaveProperty('value', 'custom');
  expect(screen.getByLabelText('Request hex')).toHaveProperty('value', 'aa01');
  fireEvent.change(command, { target: { value: 'custom' } });
  fireEvent.click(screen.getByRole('button', { name: 'Example read' }));

  const load = vi.spyOn(api, 'milLoad')
    .mockResolvedValueOnce(status(config('modbus_uart', { registers: [config().registers[0]] }), false))
    .mockResolvedValueOnce(status(config('uart'), false));
  fireEvent.change(screen.getByLabelText('Preset path (.json)'), { target: { value: 'read-only.json' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load' }));
  await waitFor(() => expect(load).toHaveBeenNthCalledWith(1, { path: 'read-only.json' }));
  fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'write' } });
  expect(screen.getByLabelText('Request hex')).toHaveProperty('value', expect.stringMatching(/^010600000001/));

  fireEvent.change(screen.getByLabelText('Preset path (.json)'), { target: { value: 'uart.json' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load' }));
  await screen.findByText('uart');
  expect(screen.queryByRole('option', { name: 'Bad CRC / error path' })).toBeNull();
  fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'write' } });
  expect(screen.getByLabelText('Request hex')).toHaveProperty('value', '06010001');
  fireEvent.change(screen.getByLabelText('Command'), { target: { value: 'read' } });
  expect(screen.getByLabelText('Request hex')).toHaveProperty('value', '030000');
});

it('renders transaction timing defaults when no device configuration is loaded', async () => {
  vi.mocked(api.milStatus).mockResolvedValue(status(null, false, [
    { ts: 4, kind: 'transaction', message: 'unconfigured UART packet', protocol: 'uart' },
  ]));
  render(<MachineInLoopPage />);
  expect(await screen.findAllByText('unconfigured UART packet')).toHaveLength(2);
  expect(screen.getByText('stopped')).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Custom hex' })).toBeTruthy();
  expect(screen.getAllByText('(none)')).toHaveLength(1);
  expect(screen.getByText('delay 0 us')).toBeTruthy();
  expect(document.querySelectorAll('path.rx')).toHaveLength(1);
  expect(document.querySelectorAll('path.tx')).toHaveLength(1);
});

it('loads preset IDs and paths, starts/stops, and reports lifecycle failures', async () => {
  const load = vi.spyOn(api, 'milLoad')
    .mockResolvedValueOnce(status(config('rs485_modbus'), false))
    .mockResolvedValueOnce(status(null, false))
    .mockRejectedValueOnce(new Error('load failed'));
  const start = vi.spyOn(api, 'milStart').mockResolvedValue(status(config(), true));
  const stop = vi.spyOn(api, 'milStop').mockResolvedValue(status(config(), false));
  await renderReady();
  fireEvent.change(screen.getByLabelText('Device / protocol file'), { target: { value: 'uart-demo' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load' }));
  await waitFor(() => expect(load).toHaveBeenNthCalledWith(1, { preset_id: 'uart-demo' }));
  expect(screen.getByText('rs485 modbus')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Start emulator' }));
  await waitFor(() => expect(start).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole('button', { name: 'Stop emulator' }));
  await waitFor(() => expect(stop).toHaveBeenCalledOnce());

  fireEvent.change(screen.getByLabelText('Preset path (.json)'), { target: { value: 'empty.json' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load' }));
  await waitFor(() => expect(load).toHaveBeenNthCalledWith(2, { path: 'empty.json' }));
  expect(screen.getByText('Load a preset to inspect registers.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Load' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'load failed'));

  start.mockRejectedValueOnce(new Error('start failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Start emulator' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'start failed'));
  vi.mocked(api.milStatus).mockRejectedValueOnce(new Error('refresh failed'));
});

it('sends probes, renders normal/exception responses, opens evidence and handles errors', async () => {
  const transaction = vi.spyOn(api, 'milTransaction')
    .mockResolvedValueOnce({ request_hex: '01', response_hex: '02', detail: 'read', action: 'read', session_id: 'probe' })
    .mockResolvedValueOnce({ request_hex: '01', response_hex: '', detail: 'bad crc', action: 'exception' })
    .mockRejectedValueOnce(new Error('probe failed'));
  await renderReady();
  fireEvent.click(screen.getByRole('button', { name: 'Send to emulator' }));
  expect(await screen.findByText('READ')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open TX/RX capture' }));
  await waitFor(() => expect(useApp.getState().openSession).toHaveBeenCalledWith('probe'));
  fireEvent.click(screen.getByRole('button', { name: 'Send to emulator' }));
  expect(await screen.findByText('EXCEPTION')).toBeTruthy();
  expect(screen.getByText('(none)')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Send to emulator' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'probe failed'));
  expect(transaction).toHaveBeenCalledTimes(3);
});

it('applies capture/timing parameters, filters extra channels and falls back when restart fails', async () => {
  const load = vi.spyOn(api, 'milLoad').mockResolvedValue(status(config(), false));
  const start = vi.spyOn(api, 'milStart')
    .mockRejectedValueOnce(new Error('restart unavailable'))
    .mockResolvedValueOnce(status(config(), true));
  await renderReady();
  fireEvent.change(screen.getByLabelText('Response delay (us)'), { target: { value: '2000' } });
  fireEvent.change(screen.getByLabelText('Inter-byte spacing (us)'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Response jitter budget (us)'), { target: { value: '4' } });
  fireEvent.change(screen.getByLabelText('Capture sizing'), { target: { value: 'manual' } });
  fireEvent.change(screen.getByLabelText('Deep digital capture rate'), { target: { value: '50000000' } });
  fireEvent.change(screen.getByLabelText('Max response bytes'), { target: { value: '4096' } });
  fireEvent.change(screen.getByLabelText('Capture after packet (us)'), { target: { value: '20000' } });
  fireEvent.change(screen.getByLabelText('Extra digital channels'), { target: { value: '0, 2, 2.5, 16, x, 15' } });
  expect(screen.getByText(/exceeds 1,000,000 sample budget/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Apply MIL settings' }));
  await waitFor(() => expect(load).toHaveBeenCalled());
  const submitted = vi.mocked(load).mock.calls[0][0].config!;
  expect(submitted.capture.extra_digital_channels).toEqual([0, 2, 15]);
  expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'MIL capture settings applied');
  fireEvent.click(screen.getByRole('button', { name: 'Apply MIL settings' }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));

  load.mockRejectedValueOnce(new Error('apply failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Apply MIL settings' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'apply failed'));
});

it('runs warning, successful and failed stress tests with strict counts', async () => {
  const transaction = vi.spyOn(api, 'milTransaction')
    .mockResolvedValueOnce({ action: 'read', response_hex: '01' } as MilTransactionResponse)
    .mockResolvedValueOnce({ action: 'exception', response_hex: 'ff' } as MilTransactionResponse)
    .mockResolvedValueOnce({ action: 'read', response_hex: '' } as MilTransactionResponse)
    .mockResolvedValue({ action: 'read', response_hex: '01' } as MilTransactionResponse);
  await renderReady();
  fireEvent.change(screen.getByLabelText('Stress count'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run stress test' }));
  expect(await screen.findByText('1/3 passed, 2 failed')).toBeTruthy();
  expect(useApp.getState().toast).toHaveBeenCalledWith('warning', 'Stress test: 1/3 passed');
  fireEvent.change(screen.getByLabelText('Stress count'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run stress test' }));
  expect(await screen.findByText('2/2 passed, 0 failed')).toBeTruthy();
  expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'Stress test: 2/2 passed');
  transaction.mockRejectedValueOnce(new Error('stress failed'));
  fireEvent.change(screen.getByLabelText('Stress count'), { target: { value: '1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run stress test' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'stress failed'));
});

it('polls safely, shows empty defaults, and enforces read-only/busy controls', async () => {
  vi.useFakeTimers();
  vi.mocked(api.milPresets).mockRejectedValue(new Error('offline'));
  vi.mocked(api.milStatus).mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(status(null, false, []))
    .mockRejectedValueOnce(new Error('poll failed'));
  const { rerender } = render(<MachineInLoopPage />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('stopped')).toBeTruthy();
  expect(screen.getByRole('option', { name: 'Custom hex' })).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  act(() => useApp.setState({ controlMode: false })); rerender(<MachineInLoopPage />);
  expect(screen.getByRole('button', { name: 'Load' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByRole('button', { name: 'Start emulator' }).hasAttribute('disabled')).toBe(true);
  expect(screen.getByText('No emulator events yet.')).toBeTruthy();
  expect(screen.getByText('Run a probe transaction to see request and response waveforms.')).toBeTruthy();
});
