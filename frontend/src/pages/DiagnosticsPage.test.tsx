// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DiagnosticsPage } from './DiagnosticsPage';
import { useApp } from '../state/appStore';
import { session } from '../test/session';

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); });

it('shows LAN URLs and filters live log levels without changing stored logs', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ lan_urls: ['http://127.0.0.1', 'http://board.local'] })));
  useApp.setState({ controlMode: false, logs: [
    { ts: 0, level: 'info', logger: 'app', message: 'started' },
    { ts: 1, level: 'error', logger: 'hw', message: 'lost' },
  ] });
  render(<DiagnosticsPage />);
  expect(await screen.findByText('http://board.local')).toBeTruthy();
  expect(screen.getByText('started')).toBeTruthy(); expect(screen.getByText('lost')).toBeTruthy();
  expect(screen.getAllByRole('button').filter((button) => button.hasAttribute('disabled'))).toHaveLength(8);
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'error' } });
  expect(screen.queryByText('started')).toBeNull(); expect(screen.getByText('lost')).toBeTruthy();
  fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
  expect(screen.getByText('started')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Run capture sanity checks' }));
  expect(useApp.getState().toasts.some((toast) => toast.message === 'Open a session first')).toBe(true);
});

it('runs sanity, all mock scenarios and a downloadable debug bundle through exact endpoints', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/diagnostics') return Response.json({});
    if (url.endsWith('/sanity')) return Response.json({ findings: [{ level: 'warning', check: 'edges', message: 'few transitions' }] });
    return new Response('ok');
  });
  vi.stubGlobal('fetch', fetcher);
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:zip'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  useApp.setState({ activeSession: session('s') });
  render(<DiagnosticsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Run capture sanity checks' }));
  expect(await screen.findByText('[edges] few transitions')).toBeTruthy();
  for (const name of ['Demo mixed', 'UART', 'I2C', 'SPI', 'PWM', 'Glitchy', 'Analog demo', 'Stress test']) {
    fireEvent.click(screen.getByRole('button', { name }));
  }
  fireEvent.click(screen.getByRole('button', { name: 'Debug bundle (ZIP)' }));
  await waitFor(() => expect(useApp.getState().toasts.filter((toast) => toast.message.startsWith('Mock capture started')).length).toBe(8));
  const mockBodies = fetcher.mock.calls.filter(([url]) => String(url) === '/api/diagnostics/mock-capture')
    .map(([, init]) => JSON.parse(String(init?.body)));
  expect(mockBodies[6]).toEqual({ scenario: 'analog_demo', sample_rate: 1_000_000, num_samples: 100_000, analog: true });
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'Debug bundle downloaded')).toBe(true));
});

it('reports tool failures while treating unavailable startup diagnostics as optional', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));
  useApp.setState({ activeSession: session('s') });
  render(<DiagnosticsPage />);
  fireEvent.click(screen.getByRole('button', { name: 'Run capture sanity checks' }));
  fireEvent.click(screen.getByRole('button', { name: 'UART' }));
  fireEvent.click(screen.getByRole('button', { name: 'Debug bundle (ZIP)' }));
  await waitFor(() => expect(useApp.getState().toasts.filter((toast) => toast.level === 'error')).toHaveLength(3));
  expect(useApp.getState().toasts.every((toast) => toast.message === 'backend offline')).toBe(true);
});
