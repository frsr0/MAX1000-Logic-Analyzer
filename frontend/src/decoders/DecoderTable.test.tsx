// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DecoderTable } from './DecoderTable';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

beforeEach(async () => { useApp.setState(useApp.getInitialState(), true); await waveformView.load('', 1000, 1000, null); waveformView.setView(0, 100); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('requires one completed decoder', () => {
  const { rerender } = render(<DecoderTable />);
  expect(screen.getByText('Run a decoder to see the packet table.')).toBeTruthy();
  useApp.setState({ activeSession: session('s', { decoders: [{ status: 'running' }] as never[] }) });
  rerender(<DecoderTable />);
  expect(screen.getByText('Run a decoder to see the packet table.')).toBeTruthy();
});

it('formats fields, filters, paginates and jumps to the selected packet', async () => {
  const events = [{ id: 'e', decoder_id: 'uart', type: 'data', start_sample: 400, end_sample: 420,
    start_time: 0.002, end_time: 0.003, label: 'byte', severity: 'warning',
    fields: { none: null, missing: undefined, yes: true, no: false, byte: 65, fraction: 1.234567, text: 'ok' } },
  { id: 'e2', decoder_id: 'uart', type: 'data', start_sample: 500, end_sample: 510,
    start_time: 0.004, end_time: 0.005, label: 'next', severity: 'normal', fields: { byte: 1 } }];
  const fetcher = vi.fn(async (_input: RequestInfo | URL) => Response.json({ events, total: 250 }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('s', { decoders: [
    { id: 'uart', decoder_id: 'uart', name: '', status: 'done', event_count: 250 },
    { id: 'spi', decoder_id: 'spi', name: 'SPI bus', status: 'done', event_count: 1 },
  ] as never[] }) });
  render(<DecoderTable />);
  expect(await screen.findByText('65 (0x41)')).toBeTruthy();
  expect(screen.getByText('✓')).toBeTruthy(); expect(screen.getByText('✗')).toBeTruthy();
  expect(screen.getByText('1.2346')).toBeTruthy(); expect(screen.getByText('ok')).toBeTruthy();
  expect(screen.getByText('250 packets')).toBeTruthy();
  fireEvent.click(screen.getAllByText('byte')[1]);
  expect([waveformView.start, waveformView.end]).toEqual([360, 460]);
  expect(screen.getAllByText('byte')[1].closest('tr')?.className).toContain('selected');
  fireEvent.change(screen.getByPlaceholderText('search packets…'), { target: { value: 'AA & BB' } });
  fireEvent.change(screen.getAllByRole('combobox')[1], { target: { value: 'error' } });
  await waitFor(() => expect(String(fetcher.mock.calls[fetcher.mock.calls.length - 1][0])).toContain('search=AA%20%26%20BB&severity=error'));
  fireEvent.click(screen.getByRole('button', { name: '⟩' }));
  await waitFor(() => expect(screen.getByText('101–200')).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: '⟨' }));
  await waitFor(() => expect(screen.getByText('1–100')).toBeTruthy());
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'spi' } });
  await waitFor(() => expect(String(fetcher.mock.calls[fetcher.mock.calls.length - 1][0])).toContain('/decoders/spi/table?offset=0'));
});

it('clears current rows on failures and ignores responses from a replaced session', async () => {
  let finish!: (response: Response) => void;
  let fail!: (error: Error) => void;
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new Error('offline'))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }))
    .mockImplementationOnce(() => new Promise<Response>((_resolve, reject) => { fail = reject; }));
  vi.stubGlobal('fetch', fetcher);
  const done = [{ id: 'uart', decoder_id: 'uart', name: '', status: 'done', event_count: 1 }] as never[];
  useApp.setState({ activeSession: session('old', { decoders: done }) });
  const { rerender } = render(<DecoderTable />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  useApp.setState({ activeSession: session('pending', { decoders: done }) }); rerender(<DecoderTable />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  useApp.setState({ activeSession: session('none') }); rerender(<DecoderTable />);
  finish(Response.json({ events: [{ id: 'old', fields: {} }], total: 1 }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.getByText('Run a decoder to see the packet table.')).toBeTruthy();
  useApp.setState({ activeSession: session('rejecting', { decoders: done }) }); rerender(<DecoderTable />);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  useApp.setState({ activeSession: session('none') }); rerender(<DecoderTable />);
  fail(new Error('late failure'));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.getByText('Run a decoder to see the packet table.')).toBeTruthy();
});
