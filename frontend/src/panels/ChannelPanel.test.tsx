// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { ChannelInfo } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';
import { ChannelPanel } from './ChannelPanel';

const channel = (id: string, type: ChannelInfo['type'], extra: Partial<ChannelInfo> = {}): ChannelInfo => ({
  id, name: id.toUpperCase(), type, enabled: true, color: null, units: type === 'analog' ? 'V' : '',
  volts_per_div: 1, offset: 0, probe_attenuation: 1, cal_gain: 1, cal_offset: 0,
  threshold: 1.5, coupling: 'DC', members: [], display_base: 'hex', ...extra,
});
const channels = [channel('d0', 'digital', { header: 'J1.1', fpga_pin: 'A1' }), channel('d1', 'digital'),
  channel('a0', 'analog', { enabled: false }), channel('f0', 'derived')];

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  vi.spyOn(api, 'patchSession').mockResolvedValue(session('s'));
  vi.spyOn(api, 'addDerivedChannel').mockResolvedValue({} as never);
  vi.spyOn(api, 'addBus').mockResolvedValue({} as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('renders the empty state and performs channel visibility, editing, ordering and error handling', async () => {
  const empty = render(<ChannelPanel />);
  expect(screen.getByText('No session open.')).toBeTruthy(); empty.unmount();
  const refreshActiveSession = vi.fn().mockResolvedValue(undefined); const toast = vi.fn();
  useApp.setState({ activeSession: session('s', { channels }), refreshActiveSession, toast });
  const fetch = vi.spyOn(waveformView, 'requestFetch').mockResolvedValue();
  const notify = vi.spyOn(waveformView, 'notify');
  const { container } = render(<ChannelPanel />);
  expect(screen.getByText('J1.1 · A1')).toBeTruthy();
  expect(screen.getByText('~')).toBeTruthy(); expect(screen.getByText('f')).toBeTruthy();
  const rows = () => Array.from(container.querySelectorAll('.channel-row')) as HTMLElement[];

  fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
  fireEvent.click(screen.getByRole('button', { name: 'Digital only' }));
  fireEvent.click(screen.getByRole('button', { name: 'Analog only' }));
  fireEvent.click(within(rows()[0]).getByTitle('show/hide'));
  fireEvent.change(within(rows()[0]).getByDisplayValue('#4fc3f7'), { target: { value: '#123456' } });
  const name = within(rows()[0]).getByDisplayValue('D0');
  fireEvent.blur(name); fireEvent.change(name, { target: { value: 'CLK' } }); fireEvent.blur(name);
  fireEvent.click(within(rows()[0]).getByTitle('solo'));
  fireEvent.click(within(rows()[0]).getByTitle('move up'));
  fireEvent.click(within(rows()[3]).getByTitle('move down'));
  fireEvent.click(within(rows()[0]).getByTitle('move down'));
  fireEvent.dragStart(rows()[0]); fireEvent.dragOver(rows()[1]); fireEvent.drop(rows()[1]);
  fireEvent.drop(rows()[2]);
  fireEvent.dragStart(rows()[2]); fireEvent.drop(rows()[2]);
  fireEvent.change(within(rows()[2]).getByTitle('volts/div'), { target: { value: '2' } });
  await waitFor(() => expect(refreshActiveSession).toHaveBeenCalled());
  expect(fetch).toHaveBeenCalledWith(0); expect(notify).toHaveBeenCalled();
  expect(api.patchSession).toHaveBeenCalledWith('s', { channels: [{ id: 'd0', name: 'CLK' }] });

  vi.mocked(api.patchSession).mockRejectedValueOnce(new Error('patch failed'));
  fireEvent.click(within(rows()[0]).getByTitle('show/hide'));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'patch failed'));
});

it('saves, loads and validates named and default layouts', async () => {
  const toast = vi.fn(); useApp.setState({ activeSession: session('s', { channels }), refreshActiveSession: vi.fn(), toast });
  render(<ChannelPanel />);
  const name = screen.getByLabelText('Channel layout name');
  fireEvent.change(name, { target: { value: '' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load layout' }));
  expect(toast).toHaveBeenCalledWith('warning', "No saved layout 'default'");
  fireEvent.click(screen.getByRole('button', { name: 'Save layout' }));
  expect(toast).toHaveBeenCalledWith('success', "Channel layout 'default' saved");
  fireEvent.change(name, { target: { value: 'bench' } });
  fireEvent.click(screen.getByRole('button', { name: 'Load layout' }));
  expect(toast).toHaveBeenCalledWith('warning', "No saved layout 'bench'");
  fireEvent.click(screen.getByRole('button', { name: 'Save layout' }));
  fireEvent.click(screen.getByRole('button', { name: 'Load layout' }));
  await waitFor(() => expect(api.patchSession).toHaveBeenCalledWith('s', expect.objectContaining({ channels: expect.any(Array) })));
});

it('adds every derived-filter shape and reports failures', async () => {
  const toast = vi.fn(); useApp.setState({ activeSession: session('s', { channels }), refreshActiveSession: vi.fn(), toast });
  render(<ChannelPanel />);
  const selects = screen.getAllByRole('combobox');
  const source = selects[1]; const filter = selects[2];
  fireEvent.change(source, { target: { value: 'a0' } });
  for (const [kind, key] of [
    ['debounce', 'hold'], ['min_pulse', 'min_width'], ['glitch_suppress', 'max_glitch'],
    ['threshold', 'level'], ['moving_average', 'window'], ['median', 'window'],
    ['lowpass', 'cutoff_hz'], ['highpass', 'cutoff_hz'],
  ]) {
    fireEvent.change(filter, { target: { value: kind } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add derived channel' }));
    await waitFor(() => expect(api.addDerivedChannel).toHaveBeenCalledWith('s', 'a0', { kind, [key]: 7 }));
  }
  fireEvent.change(filter, { target: { value: 'majority3' } });
  expect(screen.queryByRole('spinbutton')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Add derived channel' }));
  await waitFor(() => expect(api.addDerivedChannel).toHaveBeenCalledWith('s', 'a0', { kind: 'majority3' }));
  expect(toast).toHaveBeenCalledWith('success', 'Derived channel added (raw data unchanged)');
  vi.mocked(api.addDerivedChannel).mockRejectedValueOnce(new Error('derive failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Add derived channel' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'derive failed'));
});

it('requires two bus members, supports removal, and handles bus success and failure', async () => {
  const toast = vi.fn(); useApp.setState({ activeSession: session('s', { channels }), refreshActiveSession: vi.fn(), toast });
  const notify = vi.spyOn(waveformView, 'notify');
  render(<ChannelPanel />);
  fireEvent.click(screen.getByRole('button', { name: 'Create bus' }));
  expect(toast).toHaveBeenCalledWith('warning', 'Pick 2 or more members (bit 0 first)');
  const members = screen.getAllByRole('checkbox').slice(channels.length);
  fireEvent.click(members[0]); fireEvent.click(members[1]); fireEvent.click(members[1]);
  fireEvent.click(screen.getByRole('button', { name: 'Create bus' }));
  expect(api.addBus).not.toHaveBeenCalled();
  fireEvent.click(members[1]);
  const busName = screen.getByText('Name').parentElement!.querySelector('input')!;
  fireEvent.change(busName, { target: { value: 'ADDR' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create bus' }));
  await waitFor(() => expect(api.addBus).toHaveBeenCalledWith('s', 'ADDR', ['d0', 'd1']));
  expect(notify).toHaveBeenCalled(); expect(toast).toHaveBeenCalledWith('success', 'Bus ADDR added');
  vi.mocked(api.addBus).mockRejectedValueOnce(new Error('bus failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Create bus' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'bus failed'));
});
