// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { DeviceCapabilities, TriggerConfig } from '../api/types';
import { defaultCaptureSettings } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import { TriggerPanel } from './TriggerPanel';

const types = ['none', 'pattern', 'generic_pattern', 'uart_byte', 'sequence', 'pulse_wider', 'timeout', 'decoder_error'];
const capabilities: DeviceCapabilities = { digital_channels: 4, analog_channels: 0, max_sample_rate: 200_000_000,
  min_sample_rate: 1, max_samples: 1_000_000, bram_samples: 1024, sample_clk_hz: 200_000_000,
  supports_pre_trigger: true, supports_rolling: true, supports_continuous: true, supports_analog: false,
  analog_rate_note: '', generator_protocols: [], triggers: [], notes: [], digital_pin_map: [], analog_pin_map: [],
  trigger_matrix: types.map((type, index) => ({ type, execution: index === 1 ? 'hardware'
    : index === types.length - 1 ? 'unavailable' : type === 'none' ? 'hardware' : 'post_capture', description: '' })),
};
const setTrigger = (trigger: Partial<TriggerConfig>) => useApp.setState({ captureSettings: {
  ...defaultCaptureSettings(), num_samples: 1000, trigger: { ...defaultCaptureSettings().trigger, ...trigger },
} });

beforeEach(() => {
  useApp.setState(useApp.getInitialState(), true); useApp.setState({ capabilities });
  vi.spyOn(api, 'triggerSearch').mockResolvedValue({ sample: 10 } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('selects trigger execution modes and renders pattern/channel/timing/pre-trigger behavior', () => {
  setTrigger({ type: 'pattern', channels: [2], pattern: '10x10101010101010101', execution: 'hardware', position_pct: 10,
    pre_trigger_samples: 100, min_duration_s: 2e-6, max_duration_s: 5e-6, consecutive: 2, holdoff_s: 3e-6 });
  render(<TriggerPanel />);
  fireEvent.click(screen.getByText('Timing and repeat rules'));
  expect(screen.getByText('Supported in hardware')).toBeTruthy();
  expect(screen.getByText(/20 steps; first 16 shown/)).toBeTruthy();
  expect(screen.getByLabelText('Trigger preview').textContent).toContain('10x');
  const channelBoxes = screen.getAllByRole('checkbox').slice(0, 4);
  fireEvent.click(channelBoxes[0]); fireEvent.click(channelBoxes[2]);
  expect(useApp.getState().captureSettings.trigger.channels).toEqual([0]);
  fireEvent.change(screen.getByPlaceholderText('1x0x'), { target: { value: '01x' } });
  expect(useApp.getState().captureSettings.trigger.pattern).toBe('01x');
  const nums = screen.getAllByRole('spinbutton');
  fireEvent.change(nums[0], { target: { value: '4' } }); fireEvent.change(nums[1], { target: { value: '8' } });
  fireEvent.change(nums[3], { target: { value: '6' } });
  fireEvent.change(nums[0], { target: { value: '' } }); fireEvent.change(nums[1], { target: { value: '' } });
  fireEvent.change(nums[2], { target: { value: '0' } }); fireEvent.change(nums[3], { target: { value: '' } });
  expect(useApp.getState().captureSettings.trigger).toMatchObject({ min_duration_s: null, max_duration_s: null, consecutive: 1, holdoff_s: null });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Re-arm for repeated captures' }));
  fireEvent.change(screen.getByRole('slider'), { target: { value: '25' } });
  expect(useApp.getState().captureSettings.trigger).toMatchObject({ rearm: true, position_pct: 25, pre_trigger_samples: 250 });
  fireEvent.change(screen.getByRole('combobox', { name: 'Start capture when' }), { target: { value: 'decoder_error' } });
  expect(useApp.getState().captureSettings.trigger.execution).toBe('unavailable');
});

it('configures every generic-pattern protocol field and clamps frame width', () => {
  setTrigger({ type: 'generic_pattern', channels: [0, 1, 2, 9], value: null, clock_channel: undefined,
    clock_source: undefined, clock_edge: undefined, baud: undefined, frame_width: undefined, match_mask: undefined,
    bit_order: undefined, start_channel: undefined, start_mode: undefined, start_polarity: undefined, execution: 'post_capture' });
  render(<TriggerPanel />);
  fireEvent.click(screen.getByText('Advanced protocol matching'));
  expect(screen.getByText('Post-capture only (software search)')).toBeTruthy();
  expect((screen.getAllByRole('checkbox')[3] as HTMLInputElement).disabled).toBe(true);
  const transport = screen.getByRole('combobox', { name: 'Protocol preset' });
  fireEvent.change(transport, { target: { value: 'uart' } });
  fireEvent.change(transport, { target: { value: 'spi' } });
  fireEvent.change(transport, { target: { value: 'parallel' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Clock channel' }), { target: { value: '3' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Clock source' }), { target: { value: 'internal_baud' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Clock edge' }), { target: { value: 'falling' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Baud' }), { target: { value: '9600' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Frame width (bits)' }), { target: { value: '99' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Frame width (bits)' }), { target: { value: '0' } });
  fireEvent.change(screen.getByRole('textbox', { name: 'Match mask (hex)' }), { target: { value: '' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Bit order' }), { target: { value: 'lsb_first' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Start channel' }), { target: { value: '2' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Start condition' }), { target: { value: 'none' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Start polarity' }), { target: { value: '1' } });
  expect(useApp.getState().captureSettings.trigger).toMatchObject({ clock_channel: 3, clock_source: 'internal_baud',
    clock_edge: 'falling', baud: 9600, frame_width: 1, match_mask: 0, bit_order: 'lsb_first', start_channel: 2,
    start_mode: 'none', start_polarity: 1 });
});

it('configures value, width, baud and occurrence trigger fields', () => {
  setTrigger({ type: 'uart_byte', channels: [], value: null, baud: undefined, occurrence: undefined, execution: 'post_capture' });
  const { rerender } = render(<TriggerPanel />);
  fireEvent.change(screen.getByPlaceholderText('3c'), { target: { value: '1' } });
  fireEvent.change(screen.getByPlaceholderText('3c'), { target: { value: '' } });
  expect(useApp.getState().captureSettings.trigger.value).toBe(0);
  fireEvent.change(screen.getByPlaceholderText('3c'), { target: { value: 'ff' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Baud' }), { target: { value: '57600' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Match occurrence' }), { target: { value: '0' } });
  expect(useApp.getState().captureSettings.trigger).toMatchObject({ value: 255, baud: 57600, occurrence: 1 });
  setTrigger({ type: 'pulse_wider', width_s: undefined, execution: 'post_capture' }); rerender(<TriggerPanel />);
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Pulse width (µs)' }), { target: { value: '2.5' } });
  expect(useApp.getState().captureSettings.trigger.width_s).toBe(2.5e-6);
  setTrigger({ type: 'timeout', width_s: 4e-6, execution: 'post_capture' }); rerender(<TriggerPanel />);
  expect(screen.queryByText('Channels')).toBeNull(); expect(screen.queryByText('Minimum duration (µs)')).toBeNull();
});

it('edits sequence JSON while tolerating incomplete JSON and limits its preview', () => {
  setTrigger({ type: 'sequence', sequence_steps: Array.from({ length: 17 }, (_, index) => ({ type: index ? 'event' : 'uart_byte' })),
    window_s: 2e-6, execution: 'post_capture' });
  render(<TriggerPanel />);
  fireEvent.click(screen.getByText('Sequence details'));
  expect(screen.getByText(/17 steps; first 16 shown/)).toBeTruthy();
  const input = screen.getByPlaceholderText(/uart_byte/);
  fireEvent.change(input, { target: { value: '[' } });
  expect(useApp.getState().captureSettings.trigger.sequence_steps).toHaveLength(17);
  fireEvent.change(input, { target: { value: '[{"type":"pattern"}]' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Sequence window (us)' }), { target: { value: '10' } });
  expect(useApp.getState().captureSettings.trigger).toMatchObject({ sequence_steps: [{ type: 'pattern' }], window_s: 1e-5 });
});

it('uses safe defaults when capabilities and optional trigger values are absent', async () => {
  useApp.setState({ capabilities: null, activeSession: session('s') });
  setTrigger({ type: 'pattern', pattern: undefined, channels: [], execution: 'post_capture' });
  const { rerender } = render(<TriggerPanel />);
  expect(screen.getAllByRole('checkbox')).toHaveLength(17);
  expect(screen.getByLabelText('Trigger preview').textContent).toContain('Preview');
  fireEvent.change(screen.getByRole('combobox', { name: 'Start capture when' }), { target: { value: 'missing' } });
  expect(useApp.getState().captureSettings.trigger.execution).toBe('unavailable');
  useApp.setState({ capabilities });
  setTrigger({ type: 'sequence', sequence_steps: undefined, window_s: undefined, execution: 'post_capture', occurrence: undefined });
  rerender(<TriggerPanel />);
  expect((screen.getByRole('button', { name: 'Previous match' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Search existing capture' }));
  await waitFor(() => expect(api.triggerSearch).toHaveBeenCalledWith('s', expect.objectContaining({ occurrence: 1 }), undefined, true));
  fireEvent.click(screen.getByRole('button', { name: 'Next match' }));
  await waitFor(() => expect(api.triggerSearch).toHaveBeenCalledWith('s', expect.objectContaining({ occurrence: 2 }), undefined, true));

  useApp.setState({ capabilities: { ...capabilities, digital_channels: undefined as never } });
  setTrigger({ type: 'generic_pattern', channels: Array.from({ length: 16 }, (_, index) => index + 20), execution: 'post_capture' });
  rerender(<TriggerPanel />);
  expect((screen.getAllByRole('checkbox')[0] as HTMLInputElement).disabled).toBe(true);
});

it('searches previous/current/next matches and handles scopes, misses and errors', async () => {
  const toast = vi.fn(); useApp.setState({ activeSession: session('s'), toast });
  setTrigger({ type: 'uart_byte', occurrence: 2, execution: 'post_capture' });
  vi.mocked(api.triggerSearch)
    .mockResolvedValueOnce({ sample: 20, scopes: [{ start_sample: 10, end_sample: 30, event_count: 4 }] } as never)
    .mockResolvedValueOnce({ sample: 40, scopes: [] } as never)
    .mockResolvedValueOnce({ sample: null } as never)
    .mockRejectedValueOnce(new Error('search failed'));
  const view = vi.spyOn(waveformView, 'setView'); const jump = vi.spyOn(waveformView, 'jumpTo');
  render(<TriggerPanel />);
  fireEvent.click(screen.getByRole('button', { name: 'Previous match' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('success', 'Match 1; scoped decoder to 4 event(s)'));
  expect(view).toHaveBeenCalledWith(10, 30); expect(jump).toHaveBeenCalledWith(20);
  fireEvent.click(screen.getByRole('button', { name: 'Search existing capture' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('success', 'Match 1 at sample 40'));
  fireEvent.click(screen.getByRole('button', { name: 'Next match' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('warning', 'No match for occurrence 2'));
  fireEvent.click(screen.getByRole('button', { name: 'Next match' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'search failed'));
});

it('falls back to a readable label for a trigger added by a newer backend', () => {
  useApp.setState({ capabilities: {
    ...capabilities,
    trigger_matrix: [...capabilities.trigger_matrix, { type: 'new_backend_trigger', execution: 'post_capture', description: '' }],
  } });
  setTrigger({ type: 'new_backend_trigger', execution: 'post_capture' });
  render(<TriggerPanel />);
  expect(screen.getByRole('option', { name: 'new backend trigger' })).toBeTruthy();
});
