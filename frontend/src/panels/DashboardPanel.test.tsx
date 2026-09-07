// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DashboardPanel } from './DashboardPanel';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

beforeEach(async () => {
  useApp.setState(useApp.getInitialState(), true);
  await waveformView.load('', 1000, 1000, null);
  waveformView.setView(0, 100);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('requires an open session', () => {
  render(<DashboardPanel />);
  expect(screen.getByText('No session open.')).toBeTruthy();
});

it('renders protocol health, heatmap, event and timing navigation from server results', async () => {
  const dashboard = { event_count: 7, error_count: 2, events_per_second: 3.25,
    by_type: { uart: 4, spi: 0 }, timeline: [0, 2], error_timeline: [0, 1],
    bus_health: {
      can: { frames: 3, error_frames: 1, load_pct: 12.34, crc_errors: 1, ack_errors: 2 },
      lin: { frames: 2, error_frames: 0, load_pct: 4, checksum_errors: 1 },
    },
    events: [{ id: 'e', start_sample: 400, start_time: 0.4, type: 'UART', label: '', severity: undefined }],
  };
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => String(input).includes('timing-suspects')
    ? Response.json({ suspects: [{ start_sample: 600, end_sample: 610, duration_samples: 10, kind: 'short', median_samples: 20 }] })
    : Response.json(dashboard)));
  useApp.setState({ activeSession: session('s', { channels: [{ id: 'd0', type: 'digital' }] as never[] }) });
  render(<DashboardPanel />);
  expect(await screen.findByText('7')).toBeTruthy();
  expect(screen.getByText(/CAN health/).parentElement?.textContent).toContain('1 CRC · 2 ACK error(s)');
  expect(screen.getByText(/LIN health/).parentElement?.textContent).toContain('1 checksum error(s)');
  expect(screen.getByTitle('event activity over capture time').children).toHaveLength(2);
  fireEvent.click(screen.getByTitle('0.400000 s · UART'));
  expect([waveformView.start, waveformView.end]).toEqual([350, 450]);
  fireEvent.click(screen.getByText('10 samples'));
  expect([waveformView.start, waveformView.end]).toEqual([550, 650]);
});

it('shows empty outcomes and handles unavailable dashboard and timing services', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(Response.json({ event_count: 0, error_count: 0, events_per_second: 0,
      by_type: {}, timeline: [], error_timeline: [], bus_health: { can: { frames: 0 } } }))
    .mockRejectedValueOnce(new Error('timing unavailable')));
  useApp.setState({ activeSession: session('timing-failure', { channels: [{ id: 'd0', type: 'digital' }] as never[] }) });
  const { rerender } = render(<DashboardPanel />);
  expect(await screen.findByText('No decoded transactions available.')).toBeTruthy();
  expect(screen.getByText('No out-of-family pulse widths detected.')).toBeTruthy();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  useApp.setState({ activeSession: session('broken') });
  rerender(<DashboardPanel />);
  await waitFor(() => expect(screen.getByText('Loading protocol dashboard…')).toBeTruthy());
});

it('uses a derived channel for timing analysis when no digital channel exists', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).includes('timing-suspects')
    ? Response.json({ suspects: [] })
    : Response.json({ event_count: 0, error_count: 0, events_per_second: 0,
      by_type: {}, timeline: [], error_timeline: [], bus_health: {}, events: [] }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ activeSession: session('derived', { channels: [{ id: 'filtered', type: 'derived' }] as never[] }) });
  render(<DashboardPanel />);
  await screen.findByText('No decoded transactions available.');
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/timing-suspects?channel=filtered'))).toBe(true);
});
