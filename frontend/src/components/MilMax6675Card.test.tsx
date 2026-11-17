// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { Max6675AlarmEvent, Max6675ChannelStatus, Max6675Data, Max6675Reading, Max6675Row, Max6675Status } from '../api/types';
import { useApp } from '../state/appStore';
import { formatClock } from './max6675Chart';
import { MilMax6675Card } from './MilMax6675Card';

const UNIT_KEY = 'msa_max6675_unit';
const ALERTS_KEY = 'msa_max6675_desktop_alerts';
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const input = (label: string) => screen.getByLabelText(label) as HTMLInputElement;
const select = (label: string) => screen.getByLabelText(label) as HTMLSelectElement;
const button = (name: string) => screen.getByRole('button', { name }) as HTMLButtonElement;
const ok = (temp_c: number): Max6675Reading => ({ temp_c, status: 'ok' });
const row = (seq: number, t: number, readings: (Max6675Reading | null)[]): Max6675Row =>
  ({ seq, t, elapsed_s: seq, readings });
const event = (t: number, name: string, kind: Max6675AlarmEvent['kind'], temp_c: number | null = null): Max6675AlarmEvent =>
  ({ t, channel: 1, name, kind, temp_c });
const channel = (name: string, enabled: boolean, cs_pin: number, last: Max6675Reading | null = null,
  over: Partial<Max6675ChannelStatus> = {}): Max6675ChannelStatus => ({
  name, enabled, so_pin: 15, cs_pin, sck_pin: 17,
  offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true,
  last, alarm: null, alarm_since: null, ...over,
});
const statusOf = (overrides: Partial<Max6675Status> = {}): Max6675Status => ({
  running: false, interval_s: 1, paused_reason: null, last_error: null, started_at: null,
  row_count: 0, dropped_rows: 0, next_seq: 0,
  channels: [channel('Boiler', true, 16), channel('Oven', true, 18),
    channel('Spare', false, 19), channel('Chamber', false, 20)],
  stop_after_s: null, auto_resume: true, auto_reconnect: true,
  alarm_events: [], stop_at: null, stopped_reason: null, persistent: false, corrupt_lines: 0,
  ...overrides,
});
/** Table cells for the channel row whose name cell matches: name, samples, min, max, mean, latest, rate. */
const cellsFor = (name: string) => Array.from(
  screen.getByText(name, { selector: 'td' }).closest('tr')!.querySelectorAll('td'), (cell) => cell.textContent);
/** The most recent config payload sent to the backend. */
const lastSaved = () => {
  const calls = vi.mocked(api.milMax6675Config).mock.calls;
  return calls[calls.length - 1][0];
};

// Notification stub: records each alert and the permission state the test sets.
const sent: string[] = [];
const requestPermission = vi.fn().mockResolvedValue('granted');
class FakeNotification {
  static permission = 'granted';
  static requestPermission = requestPermission;
  constructor(title: string, options: { body: string }) {
    sent.push(`${title}: ${options.body}`);
  }
}
function stubNotifications(permission: string) {
  FakeNotification.permission = permission;
  vi.stubGlobal('Notification', FakeNotification);
}

/** Dispatches a pointer event with a horizontal client position, as the browser would. */
function pointer(element: Element, type: 'pointermove' | 'pointerdown', clientX: number) {
  act(() => { element.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX })); });
}

async function mount() {
  render(<MilMax6675Card />);
  await flush();
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  sent.length = 0;
  requestPermission.mockClear();
  useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ controlMode: true, toast: vi.fn() });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(api, 'milMax6675Status').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Data').mockResolvedValue({ next_seq: 0, dropped_rows: 0, rows: [] });
  vi.spyOn(api, 'milMax6675Config').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Read').mockResolvedValue({ t: 1000, readings: [null, null, null, null] });
  vi.spyOn(api, 'milMax6675Start').mockResolvedValue(statusOf({ running: true }));
  vi.spyOn(api, 'milMax6675Stop').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Clear').mockResolvedValue(statusOf());
  vi.spyOn(api, 'milMax6675Notes').mockResolvedValue({ notes: [] });
  vi.spyOn(api, 'milMax6675AddNote').mockResolvedValue({ t: 1000, text: 'Fan on' });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
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
  expect(api.milMax6675Notes).toHaveBeenCalled();
});

it('loads the run options and calibration, defaulting fields an older status lacks', async () => {
  const legacy = statusOf().channels.map(({ offset_c, alarm_high_c, alarm_low_c, alarm_on_fault, alarm, alarm_since, ...rest }) => rest);
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce({
    ...statusOf(), channels: legacy, stop_after_s: undefined, auto_resume: undefined, auto_reconnect: undefined,
  } as unknown as Max6675Status);
  await mount();
  expect(select('Stop after').value).toBe('none');
  expect(input('Resume after a backend restart').checked).toBe(true);
  expect(input('Reconnect the board automatically').checked).toBe(true);
  expect(input('Channel 1 offset (°C)').value).toBe('0');
  expect(input('Channel 1 high alarm (°C)').value).toBe('');
  expect(input('Channel 1 alert on sensor fault').checked).toBe(true);
  fireEvent.click(button('Read once'));
  await flush();
  expect(lastSaved()).toMatchObject({ stop_after_s: null, auto_resume: true, auto_reconnect: true });
  expect(lastSaved().channels[0]).toMatchObject({ offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true });
});

it('loads the saved run options and calibration as stored', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({
    stop_after_s: 3600, auto_resume: false, auto_reconnect: false,
    channels: [channel('Boiler', true, 16, null, { offset_c: -1.5, alarm_high_c: 80, alarm_low_c: 10, alarm_on_fault: false }),
      channel('Oven', true, 18), channel('Spare', false, 19), channel('Chamber', false, 20)],
  }));
  await mount();
  expect(select('Stop after').value).toBe('3600');
  expect(input('Resume after a backend restart').checked).toBe(false);
  expect(input('Channel 1 offset (°C)').value).toBe('-1.5');
  expect(input('Channel 1 high alarm (°C)').value).toBe('80');
  expect(input('Channel 1 low alarm (°C)').value).toBe('10');
  expect(input('Channel 1 alert on sensor fault').checked).toBe(false);
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

it('shows pause, stop and backend error notices', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({
    paused_reason: 'interval too short', last_error: 'bus stalled',
  }));
  await mount();
  expect(screen.getByRole('status').textContent).toBe('Logging paused: interval too short');
  expect(screen.getByText('bus stalled').className).toContain('error');
});

it('shows why logging stopped', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ stopped_reason: 'reached the 10 min limit' }));
  await mount();
  expect(screen.getByRole('status').textContent).toBe('Logging stopped: reached the 10 min limit');
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
  expect(screen.getByText('Interval must be at least 1 s for 2 enabled channels (each read takes about 0.4 s)')).toBeTruthy();
  expect(button('Start logging').disabled).toBe(true);

  fireEvent.change(input('Interval (s)'), { target: { value: '1' } });
  expect(button('Start logging').disabled).toBe(false);
});

it('validates offsets and alarm limits in the calibration section', async () => {
  await mount();
  fireEvent.change(input('Channel 1 low alarm (°C)'), { target: { value: '30' } });
  fireEvent.change(input('Channel 1 high alarm (°C)'), { target: { value: '30' } });
  expect(screen.getByText('Channel 1: low alarm must be below the high alarm')).toBeTruthy();
  expect(button('Start logging').disabled).toBe(true);

  fireEvent.change(input('Channel 1 high alarm (°C)'), { target: { value: '1200' } });
  expect(screen.getByText('Channel 1: high alarm must be between -50 and 1100 °C')).toBeTruthy();
  fireEvent.change(input('Channel 1 high alarm (°C)'), { target: { value: '' } });
  fireEvent.change(input('Channel 1 offset (°C)'), { target: { value: '25' } });
  expect(screen.getByText('Channel 1: offset must be between -20 and 20 °C')).toBeTruthy();
  fireEvent.change(input('Channel 1 offset (°C)'), { target: { value: '-2' } });
  expect(button('Start logging').disabled).toBe(false);
});

it('disables control buttons without control mode and links the CSV export', async () => {
  useApp.setState({ controlMode: false });
  await mount();
  for (const name of ['Read once', 'Start logging', 'Stop', 'Clear', 'Add note']) {
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

  const saved = lastSaved();
  expect(saved.interval_s).toBe(2);
  expect(saved.channels[0]).toEqual({
    name: 'Boiler', enabled: true, so_pin: 15, cs_pin: 16, sck_pin: 17,
    offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true,
  });
  expect(saved).toMatchObject({ stop_after_s: null, auto_resume: true, auto_reconnect: true });
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

it('keeps the card usable when the notes request fails', async () => {
  vi.mocked(api.milMax6675Notes).mockRejectedValue(new Error('notes offline'));
  await mount();
  await tick(5000);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByRole('list', { name: 'Notes' })).toBeNull();
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

it('clears the local rows and notes after the backend clears', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 1, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), null, null, null]),
  ] });
  vi.mocked(api.milMax6675Notes).mockResolvedValueOnce({ notes: [{ t: 1000, text: 'Fan on' }] });
  vi.mocked(api.milMax6675Clear).mockResolvedValue(statusOf({ next_seq: 1 }));
  await mount();
  expect(cellsFor('Boiler')[1]).toBe('1');
  expect(screen.getByRole('list', { name: 'Notes' }).textContent).toContain('Fan on');

  fireEvent.click(button('Clear'));
  await flush();
  expect(api.milMax6675Clear).toHaveBeenCalledOnce();
  expect(cellsFor('Boiler')[1]).toBe('—');
  expect(screen.getByText('No readings yet')).toBeTruthy();
  expect(screen.queryByRole('list', { name: 'Notes' })).toBeNull();
});

it('asks before clearing and does nothing when the question is cancelled', async () => {
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  await mount();
  fireEvent.click(button('Clear'));
  await flush();
  expect(window.confirm).toHaveBeenCalledWith('Clear the whole log? This also deletes the saved log file.');
  expect(api.milMax6675Clear).not.toHaveBeenCalled();
});

it('filters the graph and table by the selected window', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 0, [ok(20), null, null, null]), row(1, 700, [ok(30), ok(40), null, null]),
  ] });
  await mount();
  expect(select('Graph window').value).toBe('600');
  expect(cellsFor('Boiler')[1]).toBe('1');

  fireEvent.change(select('Graph window'), { target: { value: 'all' } });
  expect(cellsFor('Boiler')).toEqual(['Boiler', '2', '20.00', '30.00', '25.00', '—', '—']);
  expect(cellsFor('Oven')[1]).toBe('1');

  fireEvent.change(select('Graph window'), { target: { value: '60' } });
  expect(cellsFor('Boiler')[1]).toBe('1');
  fireEvent.change(select('Graph window'), { target: { value: '3600' } });
  expect(cellsFor('Boiler')[1]).toBe('2');
  fireEvent.change(select('Graph window'), { target: { value: '21600' } });
  expect(cellsFor('Boiler')[1]).toBe('2');
  fireEvent.change(select('Graph window'), { target: { value: '86400' } });
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
  expect(graph.textContent).toContain('°C');
});

it('shows a band around the mean where a bucket has a spread', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 3, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), null, null, null]), row(1, 1001, [ok(30), null, null, null]),
    row(2, 1002, [ok(25), null, null, null]),
  ] });
  await mount();
  expect(screen.getByTestId('max6675-band-1').getAttribute('d')).toMatch(/^M /);
  expect(screen.getByTestId('max6675-band-1').getAttribute('d')).toMatch(/Z$/);
});

it('draws alarm thresholds only where they fall inside the graph range', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), ok(40), null, null]), row(1, 1010, [ok(30), ok(41), null, null]),
  ] });
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ channels: [
    channel('Boiler', true, 16, null, { alarm_high_c: 25, alarm_low_c: 5 }),
    channel('Oven', true, 18, null, { alarm_high_c: 200 }),
    channel('Spare', false, 19), channel('Chamber', false, 20),
  ] }));
  await mount();
  expect(screen.getByTestId('max6675-threshold-1-alarm_high_c')).toBeTruthy();
  expect(screen.queryByTestId('max6675-threshold-1-alarm_low_c')).toBeNull();
  expect(screen.queryByTestId('max6675-threshold-2-alarm_high_c')).toBeNull();
});

it('shows a tooltip with the time and each channel value under the pointer', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), ok(40), null, null]),
    row(1, 1010, [ok(30), { temp_c: null, status: 'open' }, null, null]),
  ] });
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0, width: 720, top: 0, right: 720, bottom: 240, height: 240, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
  await mount();
  const svg = screen.getByRole('img', { name: 'Temperature graph' });
  expect(screen.queryByTestId('max6675-tooltip')).toBeNull();

  pointer(svg, 'pointermove', 48);
  const tooltip = screen.getByTestId('max6675-tooltip');
  expect(tooltip.textContent).toContain(formatClock(1000));
  expect(tooltip.textContent).toContain('Boiler: 20.00 °C');
  expect(tooltip.textContent).toContain('Oven: 40.00 °C');
  expect(screen.getByTestId('max6675-cursor')).toBeTruthy();

  pointer(svg, 'pointerdown', 48 + 664);
  expect(screen.getByTestId('max6675-tooltip').textContent).toContain('Oven: —');

  fireEvent.pointerLeave(svg);
  expect(screen.queryByTestId('max6675-tooltip')).toBeNull();
});

it('does not show a tooltip on an empty graph', async () => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
    left: 0, width: 720, top: 0, right: 720, bottom: 240, height: 240, x: 0, y: 0, toJSON: () => ({}),
  } as DOMRect);
  await mount();
  pointer(screen.getByRole('img', { name: 'Temperature graph' }), 'pointermove', 100);
  expect(screen.queryByTestId('max6675-tooltip')).toBeNull();
});

it('marks notes inside the graph window and lists them newest first', async () => {
  vi.mocked(api.milMax6675Notes).mockResolvedValueOnce({ notes: [{ t: 1005, text: 'Fan on' }, { t: 9000, text: 'Later' }] });
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), null, null, null]), row(1, 1010, [ok(30), null, null, null]),
  ] });
  await mount();
  expect(screen.getAllByTestId('max6675-note-marker')).toHaveLength(1);
  const list = screen.getByRole('list', { name: 'Notes' });
  expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
    `${formatClock(9000)} Later`, `${formatClock(1005)} Fan on`,
  ]);
});

it('draws a marker for a note inside the window', async () => {
  vi.mocked(api.milMax6675Notes).mockResolvedValueOnce({ notes: [{ t: 1005, text: 'Fan on' }] });
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 2, dropped_rows: 0, rows: [
    row(0, 1000, [ok(20), null, null, null]), row(1, 1010, [ok(30), null, null, null]),
  ] });
  await mount();
  const markers = screen.getAllByTestId('max6675-note-marker');
  expect(markers).toHaveLength(1);
  expect(markers[0].textContent).toBe('Fan on');
});

it('adds a trimmed note, clears the field and refreshes the list', async () => {
  await mount();
  expect(button('Add note').disabled).toBe(true);
  fireEvent.change(input('Note text'), { target: { value: '  Heater on  ' } });
  expect(button('Add note').disabled).toBe(false);

  vi.mocked(api.milMax6675Notes).mockResolvedValueOnce({ notes: [{ t: 1000, text: 'Heater on' }] });
  fireEvent.click(button('Add note'));
  await flush();
  expect(api.milMax6675AddNote).toHaveBeenCalledWith('Heater on');
  expect(input('Note text').value).toBe('');
  expect(screen.getByRole('list', { name: 'Notes' }).textContent).toContain('Heater on');
});

it('reports a failed note through the toast', async () => {
  const toast = vi.fn();
  useApp.setState({ toast });
  vi.mocked(api.milMax6675AddNote).mockRejectedValueOnce(new Error('note refused'));
  await mount();
  fireEvent.change(input('Note text'), { target: { value: 'Fan' } });
  fireEvent.click(button('Add note'));
  await flush();
  expect(toast).toHaveBeenCalledWith('error', 'note refused');
  expect(input('Note text').value).toBe('Fan');
});

it('warns that a steady 0 °C usually means the SCK pin is wrong', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 6, dropped_rows: 0, rows: [
    row(0, 1000, [ok(0), ok(40), null, null]), row(1, 1001, [ok(0), ok(41), null, null]),
    row(2, 1002, [ok(0), ok(41), null, null]), row(3, 1003, [ok(0), ok(40), null, null]),
    row(4, 1004, [ok(0), ok(40), null, null]), row(5, 1005, [ok(0), ok(40), null, null]),
  ] });
  await mount();
  expect(screen.getAllByText('A steady 0.00 °C usually means the SCK pin is wrong.')).toHaveLength(1);
});

it('reports the rate of change in the stats table', async () => {
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 7, dropped_rows: 0, rows: Array.from({ length: 7 },
    (_, i) => row(i, i * 10, [ok(20 + i), null, null, null])) });
  await mount();
  expect(cellsFor('Boiler')[6]).toBe('6.00');
  fireEvent.change(select('Units'), { target: { value: 'F' } });
  expect(cellsFor('Boiler')[6]).toBe('10.80');
});

it('shows the latest reading and table values in the chosen unit', async () => {
  localStorage.setItem(UNIT_KEY, 'F');
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ channels: [
    channel('Boiler', true, 16, ok(21.25)), channel('Oven', true, 18), channel('Spare', false, 19), channel('Chamber', false, 20),
  ] }));
  vi.mocked(api.milMax6675Data).mockResolvedValueOnce({ next_seq: 1, dropped_rows: 0, rows: [
    row(0, 1000, [ok(21.25), null, null, null]),
  ] });
  await mount();
  expect(select('Units').value).toBe('F');
  expect(screen.getByText('70.25 °F')).toBeTruthy();
  expect(cellsFor('Boiler')).toEqual(['Boiler', '1', '70.25', '70.25', '70.25', '70.25', '—']);
  expect(screen.getByRole('columnheader', { name: 'Min (°F)' })).toBeTruthy();
});

it('switches units for display only and keeps the choice', async () => {
  await mount();
  expect(screen.getByText('°C', { selector: 'option' })).toBeTruthy();
  fireEvent.change(select('Units'), { target: { value: 'F' } });
  expect(localStorage.getItem(UNIT_KEY)).toBe('F');
  fireEvent.change(select('Units'), { target: { value: 'C' } });
  expect(localStorage.getItem(UNIT_KEY)).toBe('C');
});

it('falls back to defaults when storage is blocked', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full'); });
  await mount();
  expect(select('Units').value).toBe('C');
  fireEvent.change(select('Units'), { target: { value: 'F' } });
  expect(select('Units').value).toBe('F');
  fireEvent.click(input('Desktop alerts'));
  expect(input('Desktop alerts').checked).toBe(true);
});

it('edits offsets and alarms in the chosen unit and sends them as °C', async () => {
  await mount();
  fireEvent.change(input('Channel 1 offset (°C)'), { target: { value: '-2.5' } });
  fireEvent.change(input('Channel 1 high alarm (°C)'), { target: { value: '80' } });
  fireEvent.change(input('Channel 2 low alarm (°C)'), { target: { value: '5' } });
  fireEvent.click(input('Channel 1 alert on sensor fault'));
  fireEvent.change(select('Units'), { target: { value: 'F' } });
  expect(input('Channel 1 high alarm (°F)').value).toBe('176');
  fireEvent.change(input('Channel 1 offset (°F)'), { target: { value: '9' } });
  fireEvent.change(input('Channel 1 high alarm (°F)'), { target: { value: '' } });
  fireEvent.click(button('Read once'));
  await flush();

  const saved = lastSaved();
  expect(saved.channels[0]).toMatchObject({ offset_c: 5, alarm_high_c: null, alarm_on_fault: false });
  expect(saved.channels[1]).toMatchObject({ alarm_low_c: 5 });
});

it('keeps a lone minus sign typed in an offset until the field loses focus', async () => {
  await mount();
  const offset = input('Channel 1 offset (°C)');
  fireEvent.change(offset, { target: { value: '' } });
  expect(offset.value).toBe('');
  fireEvent.blur(offset);
  expect(offset.value).toBe('0');
});

it('sends the run options with the next save', async () => {
  await mount();
  fireEvent.change(select('Stop after'), { target: { value: '3600' } });
  fireEvent.click(input('Resume after a backend restart'));
  fireEvent.click(input('Reconnect the board automatically'));
  fireEvent.click(button('Read once'));
  await flush();
  expect(lastSaved()).toMatchObject({ stop_after_s: 3600, auto_resume: false, auto_reconnect: false });

  fireEvent.change(select('Stop after'), { target: { value: 'none' } });
  expect(select('Stop after').value).toBe('none');
});

it('locks the calibration and run options while logging', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ running: true }));
  await mount();
  expect(input('Channel 1 offset (°C)').disabled).toBe(true);
  expect(input('Channel 1 high alarm (°C)').disabled).toBe(true);
  expect(input('Channel 1 alert on sensor fault').disabled).toBe(true);
  expect(select('Stop after').disabled).toBe(true);
  expect(input('Resume after a backend restart').disabled).toBe(true);
  expect(input('Reconnect the board automatically').disabled).toBe(true);
});

it('shows active alarms in a banner, outlines the channel and lists recent events', async () => {
  const events = Array.from({ length: 12 }, (_, i) => event(1000 + i, 'Oven', 'high', 30 + i));
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({
    channels: [
      channel('Boiler', true, 16, ok(21), { alarm: 'high', alarm_since: 1000, alarm_high_c: 20 }),
      channel('Oven', true, 18, null, { alarm: 'fault' }),
      channel('Spare', false, 19), channel('Chamber', false, 20),
    ],
    alarm_events: [...events, event(1012, 'Boiler', 'clear')],
  }));
  await mount();
  const banner = screen.getByRole('alert');
  expect(banner.textContent).toContain(`Boiler: HIGH since ${formatClock(1000)}`);
  expect(banner.textContent).toContain('Oven: FAULT');
  expect(document.querySelectorAll('.max6675-channel.alarming')).toHaveLength(2);
  expect(Array.from(document.querySelectorAll('.alarm-badge'), (badge) => badge.textContent)).toEqual(['HIGH', 'FAULT']);

  expect(screen.getByText('Alarm events (10 newest)')).toBeTruthy();
  const items = Array.from(document.querySelectorAll('.max6675-events li'), (item) => item.textContent);
  expect(items).toHaveLength(10);
  expect(items[0]).toBe(`${formatClock(1012)} Boiler: CLEARED`);
  expect(items[1]).toBe(`${formatClock(1011)} Oven: HIGH at 41.00 °C`);
});

it('shows no banner when no channel is in alarm', async () => {
  await mount();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(document.querySelectorAll('.alarm-badge')).toHaveLength(0);
});

it('tolerates a status without alarm events', async () => {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce({ ...statusOf(), alarm_events: undefined } as unknown as Max6675Status);
  await mount();
  expect(screen.getByText('Alarm events (0 newest)')).toBeTruthy();
});

it('sends desktop alerts for alarms raised after page load, when permitted', async () => {
  stubNotifications('granted');
  localStorage.setItem(ALERTS_KEY, '1');
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ running: true, alarm_events: [event(900, 'Boiler', 'high', 30)] }));
  await mount();
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true, alarm_events: [
    event(900, 'Boiler', 'high', 30), event(1005, 'Boiler', 'high', 25.5), event(1006, 'Boiler', 'clear', 20),
  ] }));
  await tick(1000);
  expect(sent).toEqual([`MAX6675 alarm: Boiler: HIGH since ${formatClock(1005)}`]);
});

/** Mounts a running logger whose first status holds `before`, then polls a status holding `after`. */
async function pollAlarms(before: Max6675AlarmEvent[], after: Max6675AlarmEvent[]) {
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ running: true, alarm_events: before }));
  await mount();
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true, alarm_events: after }));
  await tick(1000);
}

it('keeps logging when the browser refuses to construct a notification', async () => {
  class ThrowingNotification extends FakeNotification {
    constructor(title: string, options: { body: string }) {
      super(title, options);
      throw new TypeError('Illegal constructor');
    }
  }
  vi.stubGlobal('Notification', ThrowingNotification);
  localStorage.setItem(ALERTS_KEY, '1');
  await pollAlarms([event(900, 'Boiler', 'high', 30)], [event(900, 'Boiler', 'high', 30), event(1005, 'Boiler', 'low', 1)]);
  expect(sent).toEqual([`MAX6675 alarm: Boiler: LOW since ${formatClock(1005)}`]);
  expect(screen.getByText(/Boiler: LOW/)).toBeTruthy();
});

it('sends no desktop alerts while the preference is off', async () => {
  stubNotifications('granted');
  await pollAlarms([event(900, 'Boiler', 'high', 30)], [event(900, 'Boiler', 'high', 30), event(1005, 'Boiler', 'low', 1)]);
  expect(sent).toEqual([]);
});

it('sends no desktop alerts when permission is denied', async () => {
  localStorage.setItem(ALERTS_KEY, '1');
  stubNotifications('denied');
  await pollAlarms([event(900, 'Boiler', 'high', 30)], [event(900, 'Boiler', 'high', 30), event(1005, 'Boiler', 'low', 1)]);
  expect(sent).toEqual([]);
});

it('does not fail when desktop alerts are on but notifications are unsupported', async () => {
  localStorage.setItem(ALERTS_KEY, '1');
  vi.stubGlobal('Notification', undefined);
  vi.mocked(api.milMax6675Status).mockResolvedValueOnce(statusOf({ running: true, alarm_events: [event(900, 'Boiler', 'high', 30)] }));
  await mount();
  vi.mocked(api.milMax6675Status).mockResolvedValue(statusOf({ running: true, alarm_events: [
    event(900, 'Boiler', 'high', 30), event(1005, 'Boiler', 'high', 25),
  ] }));
  await tick(1000);
  expect(screen.getByRole('checkbox', { name: 'Desktop alerts' })).toBeTruthy();
});

it('requests notification permission when desktop alerts are turned on', async () => {
  stubNotifications('default');
  await mount();
  fireEvent.click(input('Desktop alerts'));
  expect(requestPermission).toHaveBeenCalledOnce();
  expect(localStorage.getItem(ALERTS_KEY)).toBe('1');
  fireEvent.click(input('Desktop alerts'));
  expect(requestPermission).toHaveBeenCalledOnce();
  expect(localStorage.getItem(ALERTS_KEY)).toBe('0');
});

it('does not ask for permission when notifications are unsupported', async () => {
  await mount();
  fireEvent.click(input('Desktop alerts'));
  expect(input('Desktop alerts').checked).toBe(true);
  expect(requestPermission).not.toHaveBeenCalled();
});

it('says so when desktop alerts are on but the browser blocks or lacks notifications', async () => {
  localStorage.setItem(ALERTS_KEY, '1');
  stubNotifications('denied');
  await mount();
  expect(screen.getByTestId('max6675-alert-hint').textContent).toContain('blocked for this page');
  cleanup();
  vi.stubGlobal('Notification', undefined);
  await mount();
  expect(screen.getByTestId('max6675-alert-hint').textContent).toContain('cannot show desktop notifications');
});

it('hides the alert hint once the browser grants permission', async () => {
  stubNotifications('default');
  requestPermission.mockImplementationOnce(async () => {
    FakeNotification.permission = 'granted';
    return 'granted';
  });
  await mount();
  expect(screen.queryByTestId('max6675-alert-hint')).toBeNull();
  fireEvent.click(input('Desktop alerts'));
  expect(screen.getByTestId('max6675-alert-hint').textContent).toContain('Allow notifications');
  await act(async () => {});
  expect(screen.queryByTestId('max6675-alert-hint')).toBeNull();
});

it('keeps the alert hint when the permission request fails', async () => {
  stubNotifications('default');
  requestPermission.mockRejectedValueOnce(new Error('dismissed'));
  await mount();
  fireEvent.click(input('Desktop alerts'));
  await act(async () => {});
  expect(screen.getByTestId('max6675-alert-hint').textContent).toContain('Allow notifications');
});

it('edits channel settings and sends them with the next save', async () => {
  await mount();
  fireEvent.click(input('Channel 3 enabled'));
  fireEvent.change(input('Channel 3 name'), { target: { value: 'Chamber' } });
  fireEvent.change(select('Channel 3 SO'), { target: { value: '14' } });
  fireEvent.change(select('Channel 3 CS'), { target: { value: '19' } });
  fireEvent.change(select('Channel 3 SCK'), { target: { value: '13' } });
  fireEvent.change(input('Interval (s)'), { target: { value: '2' } });
  fireEvent.click(button('Read once'));
  await flush();

  expect(lastSaved().channels[2]).toEqual({
    name: 'Chamber', enabled: true, so_pin: 14, cs_pin: 19, sck_pin: 13,
    offset_c: 0, alarm_high_c: null, alarm_low_c: null, alarm_on_fault: true,
  });
});
