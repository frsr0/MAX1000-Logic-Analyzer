// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { Max6675ChannelStatus, Max6675Data, Max6675Reading, Max6675Row, Max6675Status } from '../api/types';
import { useApp } from '../state/appStore';
import { formatClock } from './max6675Chart';
import { MilMax6675Card } from './MilMax6675Card';

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement;
const ok = (temp_c: number): Max6675Reading => ({ temp_c, status: 'ok' });
const row = (seq: number, t: number, readings: (Max6675Reading | null)[]): Max6675Row =>
  ({ seq, t, elapsed_s: seq, readings });
const channel = (name: string, enabled: boolean, cs_pin: number, last: Max6675Reading | null = null): Max6675ChannelStatus =>
  ({ name, enabled, so_pin: 15, cs_pin, sck_pin: 17, last });
const statusOf = (overrides: Partial<Max6675Status> = {}): Max6675Status => ({
  running: false, interval_s: 1, paused_reason: null, last_error: null, started_at: null,
  row_count: 0, dropped_rows: 0, next_seq: 0,
  channels: [channel('Boiler', true, 16), channel('Oven', true, 18),
    channel('Spare', false, 19), channel('Chamber', false, 20)],
  ...overrides,
});
/** Table cells for the channel row whose name cell matches: name, samples, min, max, mean. */
const cellsFor = (name: string) => Array.from(
  screen.getByText(name, { selector: 'td' }).closest('tr')!.querySelectorAll('td'), (cell) => cell.textContent);

async function mount() {
  render(<MilMax6675Card />);
  await flush();
}

beforeEach(() => {
  vi.useFakeTimers();
  useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn() });
  vi.spyOn(api, 'milMax6675Status').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Data').mockResolvedValue({ next_seq: 0, dropped_rows: 0, rows: [] });
  vi.spyOn(api, 'milMax6675Config').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Read').mockResolvedValue({ t: 1000, readings: [null, null, null, null] });
  vi.spyOn(api, 'milMax6675Start').mockResolvedValue(statusOf({ running: true }));
  vi.spyOn(api, 'milMax6675Stop').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Clear').mockResolvedValue(statusOf());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it('loads saved channel settings and shows the heading', async () => {
  await mount();
  expect(screen.getByRole('heading', { name: 'MAX6675 thermocouple datalogger' })).toBeTruthy();
  expect(input('Interval (s)').value).toBe('1');
  expect(input('Channel 1 name').value).toBe('Boiler');
  expect(input('Channel 1 enabled').checked).toBe(true);
  expect(input('Channel 3 enabled').checked).toBe(false);
  expect(select('Channel 1 SO').value).toBe('15');
  expect(select('Channel 2 CS').value).toBe('18');
  expect(select('Channel 2 SCK').value).toBe('17');
  expect(screen.getAllByText('No reading yet')).toHaveLength(4);
  expect(api.milMax6675Data).toHaveBeenCalledWith(0);
});

it('shows a status load failure as an alert', async () => {
  vi.mocked(api.milMax6675Status).mockRejectedValueOnce(new Error('backend offline'));
  await mount();
  expect(screen.getByRole('alert').textContent).toBe('backend offline');
});

it('ignores a status response that arrives after unmount', async () => {
  let resolveStatus: (value: Max6675Status) => void = () => {};
  vi.mocked(api.milMax6675Status).mockReturnValueOnce(new Promise<Max6675Status>((resolve) => { resolveStatus = resolve; }));
  const view = render(<MilMax6675Card />);
  view.unmount();
  await act(async () => { resolveStatus(statusOf()); });
  expect(api.milMax6675Data).not.toHaveBeenCalled();
});

it('ignores a status failure that arrives after unmount', async () => {
  let rejectStatus: (error: Error) => void = () => {};
  vi.mocked(api.milMax6675Status).mockReturnValueOnce(new Promise<Max6675Status>((_, reject) => { rejectStatus = reject; }));
  const view = render(<MilMax6675Card />);
  view.unmount();
  await act(async () => { rejectStatus(new Error('late failure')); });
  expect(screen.queryByRole('alert')).toBeNull();
});

it('pages existing rows until a short page is returned', async () => {
  const full = Array.from({ length: 5000 }, (_, i) => row(i, 1000, [ok(20), null, null, null]));
  vi.mocked(api.milMax6675Data)
    .mockResolvedValueOnce({ next_seq: 5000, dropped_rows: 0, rows: full })
    .mockResolvedValueOnce({ next_seq: 5001, dropped_rows: 0, rows: [row(5000, 1000, [ok(22), null, null, null])] });
  await mount();
  expect(api.milMax6675Data).toHaveBeenNthCalledWith(1, 0);
  expect(api.milMax6675Data).toHaveBeenNthCalledWith(2, 5000);
  expect(cellsFor('Boiler')[1]).toBe('5001');
});

it('shows pause and backend error notices', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({
    paused_reason: 'interval too short', last_error: 'bus stalled',
  }));
  await mount();
  expect(screen.getByRole('status').textContent).toBe('Logging paused: interval too short');
  expect(screen.getByText('bus stalled').className).toContain('error');
});

it('shows the latest reading state for each channel', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ channels: [
    channel('Boiler', true, 16, ok(21.25)),
    channel('Oven', true, 18, { temp_c: null, status: 'open' }),
    channel('Spare', false, 19, null),
    channel('Chamber', false, 20, { temp_c: null, status: 'bad_frame' }),
  ] }));
  await mount();
  expect(screen.getByText('21.25 °C')).toBeTruthy();
  expect(screen.getByText('OK')).toBeTruthy();
  expect(screen.getByText('Open thermocouple')).toBeTruthy();
  expect(screen.getByText('No reading yet')).toBeTruthy();
  expect(screen.getByText('No sensor / bad frame')).toBeTruthy();
});

it('lists configuration problems and blocks Read and Start', async () => {
  await mount();
  fireEvent.change(select('Channel 2 CS'), { target: { value: '16' } });
  expect(screen.getByText('CS pin 16 is used by more than one enabled channel')).toBeTruthy();
  expect(button('Read once').disabled).toBe(true);
  expect(button('Start logging').disabled).toBe(true);

  fireEvent.change(select('Channel 2 CS'), { target: { value: '18' } });
  fireEvent.change(input('Interval (s)'), { target: { value: '0.2' } });
  expect(screen.getByText('Interval must be between 0.5 and 3600 s')).toBeTruthy();
  expect(button('Start logging').disabled).toBe(true);

  fireEvent.change(input('Interval (s)'), { target: { value: '1' } });
  expect(button('Start logging').disabled).toBe(false);
});

it('disables control buttons without control mode and links the CSV export', async () => {
  useApp.setState({ controlMode: false });
  await mount();
  for (const name of ['Read once', 'Start logging', 'Stop', 'Clear']) {
    expect(button(name).disabled).toBe(true);
  }
  const link = screen.getByRole('link', { name: 'Export CSV' });
  expect(link.getAttribute('href')).toBe('/api/mil/max6675/export.csv');
  expect(link.hasAttribute('download')).toBe(true);
});

it('saves the configuration and shows the one-shot readings', async () => {
  vi.mocked(api.milMax6675Read).mockResolvedValueOnce({ t: 1000, readings: [ok(21.25), null, null, null] });
  await mount();
  fireEvent.change(input('Interval (s)'), { target: { value: '2' } });
  fireEvent.click(button('Read once'));
  await flush();

  const saved = vi.mocked(api.milMax6675Config).mock.calls[0][0];
  expect(saved.interval_s).toBe(2);
  expect(saved.channels[0]).toEqual({ name: 'Boiler', enabled: true, so_pin: 15, cs_pin: 16, sck_pin: 17 });
  expect(api.milMax6675Read).toHaveBeenCalledOnce();
  expect(screen.getByText('21.25 °C')).toBeTruthy();
  expect(screen.getByText('OK')).toBeTruthy();
});

it('reports read, start, clear and stop failures through the toast', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.mocked(api.milMax6675Config)
    .mockRejectedValueOnce(new Error('config rejected'))
    .mockResolvedValue(statusOf());
  vi.mocked(api.milMax6675Read).mockRejectedValueOnce(new Error('read failed'));
  vi.mocked(api.milMax6675Start).mockRejectedValueOnce(new Error('start refused'));
  vi.mocked(api.milMax6675Clear).mockRejectedValueOnce(new Error('clear refused'));
  await mount();

  fireEvent.click(button('Read once'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'config rejected');
  fireEvent.click(button('Read once'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'read failed');
  fireEvent.click(button('Start logging'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'start refused');
  fireEvent.click(button('Clear'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'clear refused');
});

it('reports a failed stop through the toast', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  vi.mocked(api.milMax6675Stop).mockRejectedValueOnce(new Error('stop refused'));
  await mount();
  fireEvent.click(button('Stop'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'stop refused');
});

it('starts logging, polls new rows and advances the sequence', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true, next_seq: 2 }));
  vi.mocked(api.milMax6675Data)
    .mockResolvedValueOnce({ next_seq: 0, dropped_rows: 0, rows: [] })
    .mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
      row(0, 1000, [ok(21), null, null, null]), row(1, 1001, [ok(23), null, null, null]),
    ] })
    .mockResolvedValue({ next_seq: 2, dropped_rows: 0, rows: [] });
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf());
  await mount();

  fireEvent.click(button('Start logging'));
  await flush();
  expect(api.milMax6675Start).toHaveBeenCalledOnce();
  expect(input('Channel 1 name').disabled).toBe(true);
  expect(button('Start logging').disabled).toBe(true);
  expect(button('Stop').disabled).toBe(false);

  await tick(1000);
  expect(api.milMax6675Data).toHaveBeenLastCalledWith(0);
  expect(cellsFor('Boiler')[1]).toBe('2');

  await tick(1000);
  expect(api.milMax6675Data).toHaveBeenLastCalledWith(2);
});

it('does not start a second poll while the previous one is pending', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  vi.mocked(api.milMax6675Data)
    .mockResolvedValueOnce({ next_seq: 0, dropped_rows: 0, rows: [] })
    .mockReturnValue(new Promise<Max6675Data>(() => {}));
  await mount();

  await tick(1000);
  expect(api.milMax6675Data).toHaveBeenCalledTimes(2);
  await tick(1000);
  expect(api.milMax6675Data).toHaveBeenCalledTimes(2);
});

it('shows a lost-contact alert when a poll fails and clears it on the next success', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  await mount();
  vi.mocked(api.milMax6675Data).mockRejectedValueOnce(new Error('poll failed'));
  await tick(1000);
  expect(screen.getByRole('alert').textContent).toBe('Lost contact with the logger: poll failed');
  expect(toast).not.toHaveBeenCalled();
  await tick(1000);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('notices logging started elsewhere while idle and catches up on rows first', async () => {
  vi.mocked(api.milMax6675Status)
    .mockResolvedValueOnce(statusOf())
    .mockResolvedValueOnce(statusOf({ running: true, next_seq: 3 }))
    .mockResolvedValue(statusOf({ running: true, next_seq: 3 }));
  vi.mocked(api.milMax6675Data)
    .mockResolvedValueOnce({ next_seq: 0, dropped_rows: 0, rows: [] })
    .mockResolvedValueOnce({ next_seq: 3, dropped_rows: 0, rows: [
      row(0, 1000, [ok(20), null, null, null]), row(1, 1001, [ok(21), null, null, null]),
      row(2, 1002, [ok(22), null, null, null]),
    ] })
    .mockResolvedValue({ next_seq: 3, dropped_rows: 0, rows: [] });
  await mount();
  expect(api.milMax6675Status).toHaveBeenCalledTimes(1);

  await tick(5000);
  expect(api.milMax6675Data).toHaveBeenNthCalledWith(2, 0);
  expect(cellsFor('Boiler')[1]).toBe('3');
  expect(button('Stop').disabled).toBe(false);

  await tick(1000);
  expect(api.milMax6675Data).toHaveBeenLastCalledWith(3);
});

it('keeps idle refreshes status-only while logging is still stopped', async () => {
  await mount();
  await tick(5000);
  expect(api.milMax6675Status).toHaveBeenCalledTimes(2);
  expect(api.milMax6675Data).toHaveBeenCalledTimes(1);
  expect(button('Start logging').disabled).toBe(false);
});

it('ignores a failed idle status refresh', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.mocked(api.milMax6675Status)
    .mockResolvedValueOnce(statusOf())
    .mockRejectedValueOnce(new Error('offline'));
  await mount();
  await tick(5000);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(toast).not.toHaveBeenCalled();
  expect(button('Start logging').disabled).toBe(false);
});

it('stops idle refreshes when the card unmounts', async () => {
  const view = render(<MilMax6675Card />);
  await flush();
  view.unmount();
  const calls = vi.mocked(api.milMax6675Status).mock.calls.length;
  await tick(10000);
  expect(vi.mocked(api.milMax6675Status).mock.calls.length).toBe(calls);
});

it('shows the logged row count and a note when readings were discarded', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ row_count: 500, dropped_rows: 12 }));
  await mount();
  expect(screen.getByText('500 readings logged')).toBeTruthy();
  expect(screen.getByText('Oldest 12 readings were discarded (log is full)')).toBeTruthy();
});

it('drops local rows the backend has evicted', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  vi.mocked(api.milMax6675Data)
    .mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
      row(0, 1000, [ok(20), null, null, null]), row(1, 1001, [ok(21), null, null, null]),
    ] })
    .mockResolvedValue({ next_seq: 2, dropped_rows: 2, rows: [] });
  await mount();
  expect(cellsFor('Boiler')[1]).toBe('2');

  await tick(1000);
  expect(cellsFor('Boiler')[1]).toBe('—');
});

it('stops logging and fetches the rows written before the stop', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  await mount();
  expect(button('Start logging').disabled).toBe(true);
  expect(input('Interval (s)').disabled).toBe(true);

  fireEvent.click(button('Stop'));
  await flush();
  expect(api.milMax6675Stop).toHaveBeenCalledOnce();
  expect(api.milMax6675Data).toHaveBeenLastCalledWith(0);
  expect(button('Start logging').disabled).toBe(false);
});

it('stops polling when the card unmounts', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true }));
  const view = render(<MilMax6675Card />);
  await flush();
  view.unmount();
  const calls = vi.mocked(api.milMax6675Data).mock.calls.length;
  await tick(3000);
  expect(vi.mocked(api.milMax6675Data).mock.calls.length).toBe(calls);
});

it('clears the local rows after the backend clears', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 1, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), null, null, null]),
  ] });
  vi.mocked(api.milMax6675Clear).mockResolvedValue(statusOf({ next_seq: 1 }));
  await mount();
  expect(cellsFor('Boiler')[1]).toBe('1');

  fireEvent.click(button('Clear'));
  await flush();
  expect(api.milMax6675Clear).toHaveBeenCalledOnce();
  expect(cellsFor('Boiler')[1]).toBe('—');
  expect(screen.getByText('No readings yet')).toBeTruthy();
});

it('filters the graph and table by the selected window', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 0, [ok(20), null, null, null]), row(1, 700, [ok(30), ok(40), null, null]),
  ] });
  await mount();
  expect(select('Graph window').value).toBe('600');
  expect(cellsFor('Boiler')[1]).toBe('1');

  fireEvent.change(select('Graph window'), { target: { value: 'all' } });
  expect(cellsFor('Boiler')).toEqual(['Boiler', '2', '20.00', '30.00', '25.00']);
  expect(cellsFor('Oven')[1]).toBe('1');

  fireEvent.change(select('Graph window'), { target: { value: '60' } });
  expect(cellsFor('Boiler')[1]).toBe('1');
  fireEvent.change(select('Graph window'), { target: { value: '3600' } });
  expect(cellsFor('Boiler')[1]).toBe('2');
});

it('draws one trace per enabled channel and labels the time axis', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), ok(40), null, null]),
    row(1, 1010, [ok(30), { temp_c: null, status: 'open' }, null, null]),
  ] });
  await mount();

  expect(screen.getByTestId('max6675-line-1').getAttribute('d')).toMatch(/^M 0\.0 \d+\.\d L 664\.0 \d+\.\d$/);
  expect(screen.getByTestId('max6675-line-2').getAttribute('d')).toMatch(/^M 0\.0 \d+\.\d$/);
  expect(screen.queryByTestId('max6675-line-3')).toBeNull();
  const graph = screen.getByRole('img', { name: 'Temperature graph' });
  expect(graph.querySelectorAll('line')).toHaveLength(5);
  expect(screen.getByText(formatClock(1000))).toBeTruthy();
  expect(screen.getByText(formatClock(1010))).toBeTruthy();
  expect(screen.getByTestId('max6675-legend-1').textContent).toBe('Boiler');
  expect(screen.getByTestId('max6675-legend-2').textContent).toBe('Oven');
  expect(screen.queryByTestId('max6675-legend-3')).toBeNull();
  expect(screen.getByText('°C')).toBeTruthy();
});

it('edits channel settings and sends them with the next save', async () => {
  await mount();
  fireEvent.click(input('Channel 3 enabled'));
  fireEvent.change(input('Channel 3 name'), { target: { value: 'Chamber' } });
  fireEvent.change(select('Channel 3 SO'), { target: { value: '14' } });
  fireEvent.change(select('Channel 3 CS'), { target: { value: '19' } });
  fireEvent.change(select('Channel 3 SCK'), { target: { value: '13' } });
  fireEvent.click(button('Read once'));
  await flush();

  const calls = vi.mocked(api.milMax6675Config).mock.calls;
  expect(calls[calls.length - 1][0].channels[2]).toEqual({
    name: 'Chamber', enabled: true, so_pin: 14, cs_pin: 19, sck_pin: 13,
  });
});
