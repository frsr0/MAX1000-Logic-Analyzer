// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ExportPanel } from './ExportPanel';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  waveformView.selectionStart = waveformView.selectionEnd = null;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('requires an open session', () => {
  render(<ExportPanel />);
  expect(screen.getByText('No session open.')).toBeTruthy();
});

it('exports every supported artifact with exact region and decoder bodies', async () => {
  waveformView.selectionStart = 9.1; waveformView.selectionEnd = 2.9;
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('artifact'));
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:export'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  useApp.setState({ activeSession: session('s', {
    decoders: [
      { id: 'done', decoder_id: 'uart', name: 'UART RX', status: 'done' },
      { id: 'done-spi', decoder_id: 'spi', name: '', status: 'done' },
      { id: 'idle', decoder_id: 'spi', name: '', status: 'idle' },
    ] as never[],
    exports: Array.from({ length: 14 }, (_, index) => ({ id: String(index), format: 'csv', filename: `f${index}`, timestamp: index })),
  }) });
  render(<ExportPanel />);
  const names = ['Raw samples CSV', 'Selection CSV', 'JSON session', 'VCD (digital)',
    'PulseView-compatible VCD', 'NumPy NPZ', 'HTML report', 'PDF report', 'Decoded CSV: UART RX', 'Decoded CSV: spi'];
  for (const name of names) fireEvent.click(screen.getByRole('button', { name }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(names.length));
  const requests = fetcher.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))]);
  expect(requests).toEqual([
    ['/api/sessions/s/export/csv', { start: 0, end: -1 }],
    ['/api/sessions/s/export/csv', { start: 2, end: 10 }],
    ['/api/sessions/s/export/json', { include_raw: true }],
    ['/api/sessions/s/export/vcd', {}], ['/api/sessions/s/export/pulseview', {}],
    ['/api/sessions/s/export/npz', {}], ['/api/sessions/s/export/report', {}],
    ['/api/sessions/s/export/pdf', {}], ['/api/sessions/s/export/csv', { decoder_instance: 'done' }],
    ['/api/sessions/s/export/csv', { decoder_instance: 'done-spi' }],
  ]);
  expect(screen.getAllByRole('row')).toHaveLength(13);
  expect(screen.getByText('f13')).toBeTruthy();
  expect(screen.queryByText('f1')).toBeNull();
  expect(useApp.getState().toasts.filter((toast) => toast.level === 'success')).toHaveLength(10);
});

it('reports failed downloads and handles absent, empty and valid waveform screenshots', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ detail: 'denied' }, { status: 409 })));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:png'), revokeObjectURL: vi.fn() });
  const clicks: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicks.push(this.download); });
  useApp.setState({ activeSession: session('s', { name: 'Capture #1', exports: [] }) });
  const { rerender } = render(<ExportPanel />);
  expect(screen.queryByRole('button', { name: 'Selection CSV' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Raw samples CSV' }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'Export failed: denied')).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'PNG screenshot' }));
  expect(useApp.getState().toasts.some((toast) => toast.message === 'No waveform canvas visible')).toBe(true);
  const canvas = document.createElement('canvas'); canvas.className = 'waveform-canvas'; document.body.append(canvas);
  const toBlob = vi.spyOn(canvas, 'toBlob').mockImplementation((callback) => callback(null));
  rerender(<ExportPanel />);
  fireEvent.click(screen.getByRole('button', { name: 'PNG screenshot' }));
  expect(clicks).toEqual([]);
  toBlob.mockImplementation((callback) => callback(new Blob(['png'])));
  fireEvent.click(screen.getByRole('button', { name: 'PNG screenshot' }));
  expect(clicks).toEqual(['Capture_1.png']);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:png');
  canvas.remove();
});
