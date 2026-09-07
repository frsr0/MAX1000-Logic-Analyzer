// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { SessionSummary } from '../api/types';
import { useApp } from '../state/appStore';
import { SessionsPage } from './SessionsPage';

const summary = (id: string, extra: Partial<SessionSummary> = {}): SessionSummary => ({
  id, name: `Session ${id}`, created_at: 1_700_000_000, num_samples: 2048,
  sample_rate: 2_000_000, duration_s: 2.5, decoder_count: 2, tags: ['lab'],
  device: 'MAX1000', mock: false, has_analog: false, modified_at: 1_700_000_001,
  channel_count: 16, marker_count: 0, notes: '', ...extra,
});
const listing = (sessions: SessionSummary[], total = sessions.length) => ({ sessions, total, offset: 0, limit: 100 });

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  vi.spyOn(api, 'sessions').mockResolvedValue(listing([]));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('keeps the library usable when initial and reload list requests fail', async () => {
  vi.mocked(api.sessions).mockRejectedValueOnce(new Error('offline'));
  const first = render(<SessionsPage />);
  await waitFor(() => expect(api.sessions).toHaveBeenCalledOnce());
  first.unmount();

  vi.mocked(api.sessions).mockResolvedValueOnce(listing([summary('a')]));
  vi.spyOn(api, 'duplicateSession').mockResolvedValue(summary('a') as never);
  render(<SessionsPage />);
  const duplicate = await screen.findByRole('button', { name: 'Dup' });
  vi.mocked(api.sessions).mockRejectedValueOnce(new Error('reload failed'));
  fireEvent.click(duplicate);
  await waitFor(() => expect(api.sessions).toHaveBeenCalledTimes(3));
});

it('lists, searches and paginates sessions with hardware metadata', async () => {
  const sessions = [summary('a'), summary('b', { sample_rate: 20_000, duration_s: 0.01, mock: true, has_analog: true })];
  vi.mocked(api.sessions).mockResolvedValue(listing(sessions, 201));
  useApp.setState({ activeSession: { id: 'a' } as never });
  const { container } = render(<SessionsPage />);
  await waitFor(() => expect(screen.getByText('201 matching sessions')).toBeTruthy());
  expect(screen.getByText('2.0 MHz')).toBeTruthy();
  expect(screen.getByText('20.0 kHz')).toBeTruthy();
  expect(screen.getByText('2.50 s')).toBeTruthy();
  expect(screen.getByText('10.0 ms')).toBeTruthy();
  expect(screen.getByText('MAX1000 (mock) ∿')).toBeTruthy();
  expect((container.querySelector('tr.selected input') as HTMLInputElement).value).toBe('Session a');
  expect(screen.getByText('Page 1 of 3')).toBeTruthy();
  expect((screen.getByRole('button', { name: 'Previous' }) as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(screen.getByRole('button', { name: 'Next' }));
  await waitFor(() => expect(api.sessions).toHaveBeenLastCalledWith('', 100));
  expect(screen.getByText('Page 2 of 3')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Previous' }));
  await waitFor(() => expect(api.sessions).toHaveBeenLastCalledWith('', 0));
  fireEvent.change(screen.getByLabelText('Search sessions'), { target: { value: 'tagged' } });
  await waitFor(() => expect(api.sessions).toHaveBeenLastCalledWith('tagged', 0));
});

it('opens, renames, tags, duplicates and conditionally deletes rows', async () => {
  const sessions = [summary('a')];
  vi.mocked(api.sessions).mockResolvedValue({ ...listing(sessions), total: undefined as never });
  const openSession = vi.fn().mockResolvedValue(undefined);
  const setPage = vi.fn();
  const toast = vi.fn();
  useApp.setState({ openSession, setPage, toast });
  vi.spyOn(api, 'patchSession').mockResolvedValue(sessions[0] as never);
  vi.spyOn(api, 'duplicateSession').mockResolvedValue(sessions[0] as never);
  vi.spyOn(api, 'deleteSession').mockResolvedValue({ ok: true } as never);
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal('confirm', confirm);
  const { container } = render(<SessionsPage />);
  await screen.findByDisplayValue('Session a');
  const row = container.querySelector('tbody tr') as HTMLElement;

  fireEvent.click(within(row).getByRole('button', { name: 'Open' }));
  await waitFor(() => expect(openSession).toHaveBeenCalledWith('a'));
  expect(setPage).toHaveBeenCalledWith('capture');
  const name = within(row).getByDisplayValue('Session a');
  fireEvent.blur(name);
  expect(api.patchSession).not.toHaveBeenCalled();
  fireEvent.change(name, { target: { value: 'Renamed' } });
  fireEvent.blur(name);
  await waitFor(() => expect(api.patchSession).toHaveBeenCalledWith('a', { name: 'Renamed' }));
  const tags = within(row).getByDisplayValue('lab');
  fireEvent.change(tags, { target: { value: ' one, , two ' } });
  fireEvent.blur(tags);
  await waitFor(() => expect(api.patchSession).toHaveBeenCalledWith('a', { tags: ['one', 'two'] }));
  fireEvent.click(within(row).getByRole('button', { name: 'Dup' }));
  await waitFor(() => expect(api.duplicateSession).toHaveBeenCalledWith('a'));
  fireEvent.click(within(row).getByRole('button', { name: 'Del' }));
  expect(api.deleteSession).not.toHaveBeenCalled();
  fireEvent.click(within(row).getByRole('button', { name: 'Del' }));
  await waitFor(() => expect(api.deleteSession).toHaveBeenCalledWith('a'));

  openSession.mockRejectedValueOnce(new Error('cannot open'));
  fireEvent.click(within(row).getByRole('button', { name: 'Open' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'cannot open'));
});

it('compares sessions with automatic and explicit alignment and renders all diff forms', async () => {
  const sessions = [summary('a'), summary('b')];
  vi.mocked(api.sessions).mockResolvedValue(listing(sessions, 2));
  const result = {
    a: { id: 'a', name: 'A' }, b: { id: 'b', name: 'B' }, identical_digital: false,
    sample_count_diff: -3, alignment_offset: 4, first_divergence: { a: 10, b: 14 },
    timing_deltas: [
      { channel: 0, first_edge_delta_samples: null, mean_period_delta_samples: 1.25, median_period_delta_samples: 2 },
      { channel: 1, first_edge_delta_samples: 3, mean_period_delta_samples: 0, median_period_delta_samples: -1 },
    ],
    settings_diff: { rate: { a: 1, b: 2 } },
    channel_diffs: [
      { channel: 0, a: { edges: 3, duty: .5 }, b: null },
      { channel: 1, a: null, b: { edges: 4, duty: .25 } },
    ],
  };
  vi.spyOn(api, 'compareSessions').mockResolvedValue(result as never);
  const toast = vi.fn(); useApp.setState({ toast });
  const { container } = render(<SessionsPage />);
  await screen.findByDisplayValue('Session a');
  const rows = container.querySelectorAll('tbody tr');
  fireEvent.click(within(rows[0] as HTMLElement).getByRole('button', { name: 'Cmp...' }));
  expect(screen.getByText('Pick the second session to compare with...')).toBeTruthy();
  fireEvent.click(within(rows[0] as HTMLElement).getByRole('button', { name: 'x' }));
  expect(screen.queryByText('Pick the second session to compare with...')).toBeNull();
  fireEvent.click(within(rows[0] as HTMLElement).getByRole('button', { name: 'Cmp...' }));
  fireEvent.click(within(rows[1] as HTMLElement).getByRole('button', { name: 'Cmp!' }));
  await screen.findByText(/Compare: A vs B/);
  expect(api.compareSessions).toHaveBeenCalledWith('a', 'b', undefined);
  expect(screen.getByText(/first divergence A 10 \/ B 14/)).toBeTruthy();
  expect(screen.getAllByText('—')).toHaveLength(3);
  expect(screen.getByText(/3 \/ 50\.0%/)).toBeTruthy();
  expect(screen.getByText(/4 \/ 25\.0%/)).toBeTruthy();

  vi.mocked(api.compareSessions).mockResolvedValueOnce({ ...result, identical_digital: true,
    first_divergence: null, timing_deltas: undefined, settings_diff: {}, channel_diffs: [] } as never);
  fireEvent.change(screen.getByPlaceholderText('auto'), { target: { value: '-2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Recompare' }));
  await waitFor(() => expect(api.compareSessions).toHaveBeenLastCalledWith('a', 'b', -2));
  expect(screen.getByText('yes')).toBeTruthy();
  expect(screen.getByText(/no digital divergence/)).toBeTruthy();
  vi.mocked(api.compareSessions).mockRejectedValueOnce(new Error('compare failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Recompare' }));
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'compare failed'));
  fireEvent.click(within(screen.getByRole('heading', { name: /Compare: A vs B/ })).getByRole('button', { name: 'x' }));
  expect(screen.queryByText(/Compare: A vs B/)).toBeNull();
});

it('imports JSON, CSV and VCD files and reports import failures', async () => {
  vi.spyOn(api, 'importSession').mockResolvedValue({ name: 'JSON capture' } as never);
  vi.spyOn(api, 'importWaveform').mockImplementation(async (_text, kind) => ({ name: `${kind} capture` }) as never);
  const toast = vi.fn(); useApp.setState({ toast });
  const { container } = render(<SessionsPage />);
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const upload = async (name: string, text = 'data') => {
    const file = new File([text], name);
    Object.defineProperty(file, 'text', { value: vi.fn().mockResolvedValue(text) });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(toast).toHaveBeenCalledWith('success', expect.stringContaining('Imported')));
  };
  fireEvent.click(screen.getByRole('button', { name: 'Import JSON / CSV / VCD' }));
  await upload('capture.json');
  await upload('capture.CSV');
  await upload('capture.vcd');
  expect(api.importSession).toHaveBeenCalledWith('data');
  expect(api.importWaveform).toHaveBeenCalledWith('data', 'csv');
  expect(api.importWaveform).toHaveBeenCalledWith('data', 'vcd');

  vi.mocked(api.importSession).mockRejectedValueOnce(new Error('bad file'));
  const bad = new File(['bad'], 'bad.json');
  Object.defineProperty(bad, 'text', { value: vi.fn().mockResolvedValue('bad') });
  fireEvent.change(input, { target: { files: [bad] } });
  await waitFor(() => expect(toast).toHaveBeenCalledWith('error', 'Import failed: bad file'));
  fireEvent.change(input, { target: { files: [] } });
});
