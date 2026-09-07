// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { ChannelInfo, DecoderDescription, DecoderInstance } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import { DecoderPanel } from './DecoderPanel';

const channel = (id: string, type: ChannelInfo['type']): ChannelInfo => ({ id, name: id.toUpperCase(), type,
  enabled: true, color: null, units: '', volts_per_div: 1, offset: 0, probe_attenuation: 1,
  cal_gain: 1, cal_offset: 0, threshold: 0, coupling: 'DC', members: [], display_base: 'hex' });
const decoder = (id: string, extra: Partial<DecoderInstance> = {}): DecoderInstance => ({ id, decoder_id: 'uart',
  name: '', enabled: true, channels: { rx: 'd0' }, settings: {}, status: 'done', event_count: 3,
  warning_count: 0, ...extra });
const uart: DecoderDescription = { id: 'uart', name: 'UART', description: '', consumes: null,
  channels: [{ role: 'rx', name: 'RX', required: true, types: ['digital', 'analog'] }], settings: [
    { key: 'baud', name: 'Baud', type: 'enum', default: 9600, options: [9600, 'auto'], help: '' },
    { key: 'invert', name: 'Invert', type: 'bool', default: false, help: '' },
    { key: 'label', name: 'Label', type: 'str', default: '', help: '' },
    { key: 'bits', name: 'Bits', type: 'int', default: 8, help: '' },
  ] };
const multi: DecoderDescription = { id: 'multi', name: 'Parallel', description: '', consumes: null,
  channels: Array.from({ length: 10 }, (_, bit) => ({ role: `bit${bit}`, name: `Bit ${bit}`,
    required: bit < 2, types: ['digital'] })), settings: [] };
const stacked: DecoderDescription = { id: 'stacked', name: 'Stacked', description: '', consumes: 'uart', channels: [], settings: [] };
const custom: DecoderDescription = { id: 'custom', name: 'Custom', description: '', consumes: null, channels: [], settings: [
  { key: 'mode', name: 'Mode', type: 'enum', default: 'legacy', options: null, help: '' },
  { key: 'note', name: 'Note', type: 'str', default: undefined, help: '' },
] };

const nativeSetInterval = globalThis.setInterval;
const nativeClearInterval = globalThis.clearInterval;
let intervalCallback: (() => Promise<void>) | undefined;
beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  intervalCallback = undefined;
  vi.spyOn(globalThis, 'setInterval').mockImplementation(((callback: TimerHandler, delay?: number, ...args: unknown[]) => {
    if (delay === 700) { intervalCallback = callback as () => Promise<void>; return 42 as never; }
    return nativeSetInterval(callback, delay, ...args);
  }) as typeof setInterval);
  vi.spyOn(globalThis, 'clearInterval').mockImplementation(((handle?: number) => {
    if (handle !== 42) nativeClearInterval(handle);
  }) as typeof clearInterval);
  vi.spyOn(api, 'addDecoder').mockResolvedValue({} as never);
  vi.spyOn(api, 'runDecoder').mockResolvedValue({} as never);
  vi.spyOn(api, 'patchDecoder').mockResolvedValue({} as never);
  vi.spyOn(api, 'cancelDecoder').mockResolvedValue({} as never);
  vi.spyOn(api, 'deleteDecoder').mockResolvedValue({} as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); waveformView.selectionStart = null; waveformView.selectionEnd = null; });

it('shows the empty state', () => {
  render(<DecoderPanel />); expect(screen.getByText('No session open.')).toBeTruthy();
});

it('manages existing decoders, selection runs, presets, polling and errors', async () => {
  waveformView.selectionStart = 20.8; waveformView.selectionEnd = 5.2;
  const decoders = [decoder('run', { name: 'Serial', status: 'running', quality_score: .87,
    region: [2, 9], error: 'framing' }), decoder('done', { channels: {}, quality_score: null })];
  const refreshActiveSession = vi.fn().mockResolvedValue(undefined); const toast = vi.fn();
  useApp.setState({ activeSession: session('s', { channels: [channel('d0', 'digital')], decoders }),
    refreshActiveSession, toast });
  const annotations = vi.spyOn(waveformView, 'requestAnnotations').mockResolvedValue();
  render(<DecoderPanel />);
  expect(screen.getByText('Serial')).toBeTruthy(); expect(screen.getByText(/quality 87%/)).toBeTruthy();
  expect(screen.getByText(/region 2–9/)).toBeTruthy(); expect(screen.getByText('framing')).toBeTruthy();
  const cards = screen.getAllByText(/events/).map((node) => node.closest('.decoder-card') as HTMLElement);
  fireEvent.click(within(cards[0]).getByTitle('enable/disable'));
  await waitFor(() => expect(api.patchDecoder).toHaveBeenCalledWith('s', 'run', { enabled: false }));
  fireEvent.click(within(cards[0]).getByRole('button', { name: 'Run' }));
  await waitFor(() => expect(api.runDecoder).toHaveBeenCalledWith('s', 'run', undefined));
  fireEvent.click(within(cards[0]).getByRole('button', { name: 'Run on selection' }));
  await waitFor(() => expect(api.runDecoder).toHaveBeenCalledWith('s', 'run', [5, 21]));
  await waitFor(() => expect(setInterval).toHaveBeenCalled());
  fireEvent.click(within(cards[0]).getByRole('button', { name: 'Cancel' }));
  expect(api.cancelDecoder).toHaveBeenCalledWith('s', 'run');
  fireEvent.click(within(cards[0]).getByRole('button', { name: 'Preset' }));
  expect(JSON.parse(localStorage.getItem('msa_decoder_presets')!)[0]).toMatchObject({ decoder_id: 'uart' });
  expect(toast).toHaveBeenCalledWith('success', 'Preset saved (Settings page)');
  fireEvent.click(within(cards[0]).getByRole('button', { name: '✕' }));
  await waitFor(() => expect(api.deleteDecoder).toHaveBeenCalledWith('s', 'run'));
  expect(annotations).toHaveBeenCalledWith(0);

  const annotationCalls = annotations.mock.calls.length;
  expect(intervalCallback).toBeTypeOf('function');
  await intervalCallback!();
  expect(annotations.mock.calls.length).toBeGreaterThan(annotationCalls);
  useApp.setState({ activeSession: session('s', { decoders: [decoder('done')] }) });
  await intervalCallback?.(); expect(clearInterval).toHaveBeenCalledWith(42);
  useApp.setState({ activeSession: null }); await intervalCallback?.();

  vi.mocked(api.runDecoder).mockRejectedValueOnce(new Error('run failed'));
  useApp.setState({ activeSession: session('s', { channels: [channel('d0', 'digital')], decoders }) });
  await waitFor(() => expect(screen.getAllByText(/events/)).toHaveLength(2));
  const freshCard = screen.getAllByText(/events/)[1].closest('.decoder-card') as HTMLElement;
  fireEvent.click(within(freshCard).getByRole('button', { name: 'Run' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'run failed'));
});

it('adds a single-input decoder with typed settings and resets the form', async () => {
  waveformView.selectionStart = 9.9; waveformView.selectionEnd = 2.1;
  const refreshActiveSession = vi.fn().mockResolvedValue(undefined); const toast = vi.fn();
  useApp.setState({ activeSession: session('s', { channels: [channel('d0', 'digital'), channel('a0', 'analog')] }),
    decoderTypes: [uart, multi, stacked], refreshActiveSession, toast });
  render(<DecoderPanel />);
  fireEvent.click(screen.getByRole('button', { name: '+ Add decoder' }));
  expect(screen.getByText('a0 (A0, threshold)')).toBeTruthy();
  const selects = screen.getAllByRole('combobox');
  fireEvent.change(selects[2], { target: { value: 'auto' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Invert' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Label' }), { target: { value: 'console' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Bits' }), { target: { value: '7' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add & run on selection' }));
  await waitFor(() => expect(api.addDecoder).toHaveBeenCalledWith('s', {
    decoder_id: 'uart', channels: { rx: 'd0' }, settings: { baud: 'auto', invert: true, label: 'console', bits: 7 }, region: [2, 10],
  }));
  expect(screen.getByRole('button', { name: '+ Add decoder' })).toBeTruthy();
  expect(toast).not.toHaveBeenCalledWith('error', expect.anything());
});

it('validates multi-channel assignments, supports stacked decoders, and reports add failures', async () => {
  const user = userEvent.setup();
  const toast = vi.fn(); useApp.setState({ activeSession: session('s', { channels: [channel('d0', 'digital')] }),
    decoderTypes: [uart, multi, stacked], refreshActiveSession: vi.fn(), toast });
  render(<DecoderPanel />); fireEvent.click(screen.getByRole('button', { name: '+ Add decoder' }));
  const type = screen.getAllByRole('combobox')[0];
  fireEvent.change(type, { target: { value: 'multi' } });
  expect(screen.queryByText('Bit 8')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add & run' }));
  expect(toast).toHaveBeenCalledWith('warning', 'Assign channels: Bit 0, Bit 1');
  await user.selectOptions(screen.getByRole('combobox', { name: 'Bit 0 *' }), 'd0');
  await user.selectOptions(screen.getByRole('combobox', { name: 'Bit 1 *' }), 'd0');
  vi.mocked(api.addDecoder).mockRejectedValueOnce(new Error('add failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Add & run' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'add failed'));
  fireEvent.change(type, { target: { value: 'stacked' } });
  expect(screen.getByText(/needs a completed 'uart'/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Add & run' }));
  await waitFor(() => expect(api.addDecoder).toHaveBeenLastCalledWith('s', {
    decoder_id: 'stacked', channels: {}, settings: {}, region: undefined,
  }));
});

it('cancels add and ignores a preset request after its decoder disappears', () => {
  useApp.setState({ activeSession: session('s', { decoders: [decoder('gone')] }), decoderTypes: [uart] });
  const { rerender } = render(<DecoderPanel />);
  useApp.setState({ activeSession: session('s', { decoders: [] }) }); rerender(<DecoderPanel />);
  fireEvent.click(screen.getByRole('button', { name: '+ Add decoder' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByRole('button', { name: '+ Add decoder' })).toBeTruthy();
});

it('handles disappearing decoder/catalog entries and an enum catalog with no options', async () => {
  const active = session('s', { decoders: [decoder('transient')] });
  const toast = vi.fn(); useApp.setState({ activeSession: active, decoderTypes: [uart, custom],
    refreshActiveSession: vi.fn(), toast });
  render(<DecoderPanel />);
  const preset = screen.getByRole('button', { name: 'Preset' });
  active.decoders.splice(0);
  fireEvent.click(preset);
  expect(localStorage.getItem('msa_decoder_presets')).toBeNull();

  fireEvent.click(screen.getByRole('button', { name: '+ Add decoder' }));
  const type = screen.getByRole('combobox', { name: 'Decoder' });
  fireEvent.change(type, { target: { value: 'custom' } });
  expect((screen.getByRole('textbox', { name: 'Note' }) as HTMLInputElement).value).toBe('');
  fireEvent.change(screen.getByRole('combobox', { name: 'Mode' }), { target: { value: 'legacy-v2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add & run' }));
  await waitFor(() => expect(api.addDecoder).toHaveBeenCalledWith('s', {
    decoder_id: 'custom', channels: {}, settings: { mode: '' }, region: undefined,
  }));

  fireEvent.click(screen.getByRole('button', { name: '+ Add decoder' }));
  fireEvent.change(screen.getByRole('combobox', { name: 'Decoder' }), { target: { value: 'missing' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add & run' }));
  expect(api.addDecoder).toHaveBeenCalledTimes(1);
});
