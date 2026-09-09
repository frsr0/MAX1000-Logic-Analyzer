// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../pages/CapturePage', () => ({ CapturePage: () => <div>Capture page</div> }));
vi.mock('../pages/DevicePage', () => ({ DevicePage: () => <div>Device page</div> }));
vi.mock('../pages/DiagnosticsPage', () => ({ DiagnosticsPage: () => <div>Diagnostics page</div> }));
vi.mock('../pages/GeneratorPage', () => ({ GeneratorPage: () => <div>Generator page</div> }));
vi.mock('../pages/MachineInLoopPage', () => ({ MachineInLoopPage: () => <div>MIL page</div> }));
vi.mock('../pages/SessionsPage', () => ({ SessionsPage: () => <div>Sessions page</div> }));
vi.mock('../pages/SettingsPage', () => ({ SettingsPage: () => <div>Settings page</div> }));

import { AppShell } from './AppShell';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

const meta = { device_name: 'MAX1000', mock: false, sample_clk_hz: 200_000_000 };
const status = { app_version: '3.0', device_connected: true, device_kind: 'hardware', device: meta,
  capture_state: 'idle', session_count: 2, ws_clients: 1, last_error: null };

beforeEach(async () => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  await waveformView.load('', 100, 1000, null); waveformView.setView(0, 100);
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:file'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(new Proxy({ canvas: { height: 500 } }, {
    get: (object, key) => Reflect.get(object, key) ?? (() => {}), set: Reflect.set,
  }) as unknown as CanvasRenderingContext2D);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView'); });

it('renders status, navigation, read-only and toast chrome and dismisses notifications', () => {
  useApp.setState({ page: 'diagnostics', status: { ...status, capture_state: 'error', last_error: 'capture failed' } as never,
    wsConnected: true, controlMode: false, activeSession: session('s', { name: 'Open session' }),
    toasts: [{ id: 1, level: 'warning', message: 'check cable' }] });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => Response.json(String(input).includes('/serial/virtual')
    ? { running: false, driver: { available: false } } : {})));
  render(<AppShell />);
  expect(screen.getAllByText('MAX1000')).toHaveLength(2);
  expect(screen.getByText('200.0 MHz sample clock')).toBeTruthy();
  expect(screen.getByText('read-only')).toBeTruthy(); expect(screen.getByText('capture failed')).toBeTruthy();
  expect(screen.getByText('backend connected')).toBeTruthy(); expect(screen.getByText('2 sessions')).toBeTruthy();
  fireEvent.click(screen.getByText('check cable'));
  expect(screen.queryByText('check cable')).toBeNull();
});

it('opens, searches, runs and closes command-palette navigation', async () => {
  useApp.setState({ page: 'diagnostics' });
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => Response.json(String(input).includes('/serial/virtual')
    ? { running: false, driver: { available: false } } : {})));
  render(<AppShell />);
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByLabelText('Command search'));
  fireEvent.change(screen.getByLabelText('Command search'), { target: { value: 'go to settings' } });
  fireEvent.keyDown(screen.getByLabelText('Command search'), { key: 'Enter' });
  await waitFor(() => expect(useApp.getState().page).toBe('settings'));
  fireEvent.keyDown(window, { key: 'k', metaKey: true });
  fireEvent.change(screen.getByLabelText('Command search'), { target: { value: 'not a command' } });
  expect(screen.getByText('No matching commands')).toBeTruthy();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  fireEvent.click(screen.getByRole('dialog').parentElement!);
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('runs capture, decoder, trigger and export commands through public behavior', async () => {
  const requests: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input); requests.push(url);
    if (url.includes('trigger-search')) return Response.json({ sample: 75 });
    if (url.includes('/decoders/') && url.includes('/table')) return Response.json({ events: [], total: 0 });
    if (url.includes('/serial/virtual')) return Response.json({ running: false, driver: { available: false } });
    return url.includes('/export/') ? new Response('file') : Response.json({});
  }));
  const active = session('s', { decoders: [{ id: 'd', status: 'done' }] as never[] });
  useApp.setState({ page: 'diagnostics', status: status as never, activeSession: active });
  const { rerender } = render(<AppShell />);
  const command = async (query: string) => {
    useApp.setState({ page: 'diagnostics' }); rerender(<AppShell />);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    fireEvent.change(screen.getByLabelText('Command search'), { target: { value: query } });
    fireEvent.keyDown(screen.getByLabelText('Command search'), { key: 'Enter' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  };
  await command('start or stop capture');
  useApp.setState({ status: { ...status, capture_state: 'capturing' } as never });
  await command('start or stop capture');
  await command('run first decoder');
  await command('search current trigger');
  expect([waveformView.start, waveformView.end]).toEqual([0, 100]);
  await command('export session json'); await command('export html report');
  expect(requests).toEqual(expect.arrayContaining(['/api/capture/start', '/api/capture/stop',
    '/api/sessions/s/decoders/d/run', '/api/sessions/s/trigger-search',
    '/api/sessions/s/export/json', '/api/sessions/s/export/report']));
});

it('supports global capture/save shortcuts and reports command failures', async () => {
  const fetcher = vi.fn(async () => Response.json({ detail: 'denied' }, { status: 409 }));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ page: 'diagnostics', status: status as never, activeSession: session('s') });
  render(<AppShell />);
  fireEvent.keyDown(window, { key: ' ', target: document.body });
  fireEvent.keyDown(window, { key: 's', ctrlKey: true, target: document.body });
  await waitFor(() => expect(useApp.getState().toasts.filter((toast) => toast.level === 'error').length).toBe(2));
  const select = document.createElement('select'); document.body.append(select);
  fireEvent.keyDown(select, { key: ' ' });
  expect(fetcher).toHaveBeenCalledTimes(2);
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  fireEvent.change(screen.getByLabelText('Command search'), { target: { value: 'run first decoder' } });
  fireEvent.click(screen.getByRole('button', { name: /Run first decoder/ }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'No decoder is available')).toBe(true));
});

it('covers navigation, disconnected chrome, ignored shortcuts, and missing-session commands', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => Response.json(String(input).includes('/serial/virtual')
    ? { running: false, driver: { available: false } } : {}));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ page: 'diagnostics', status: null, activeSession: null, wsConnected: false, controlMode: false });
  render(<AppShell />);
  expect(screen.getByText('No device')).toBeTruthy();
  expect(screen.getByText('reconnecting...')).toBeTruthy();
  expect(screen.getByText('0 sessions')).toBeTruthy();
  expect(screen.getByText('v...')).toBeTruthy();
  fireEvent.keyDown(window, { key: ' ', target: document.body });
  fireEvent.keyDown(window, { key: 's', ctrlKey: true, target: document.body });
  fireEvent.keyDown(window, { key: 'Escape', target: document.body });
  for (const [name, page] of [[/Sessions$/, 'sessions'], [/Hardware$/, 'device'], [/Generator$/, 'generator'], [/Hardware lab$/, 'mil'], [/Capture$/, 'capture']] as const) {
    fireEvent.click(screen.getByRole('button', { name }));
    expect(useApp.getState().page).toBe(page);
  }

  useApp.setState({ status: status as never });
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  const input = screen.getByLabelText('Command search');
  fireEvent.change(input, { target: { value: 'not a command' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(screen.getByRole('dialog')).toBeTruthy();
  fireEvent.change(input, { target: { value: 'start or stop capture' } });
  fireEvent.click(screen.getByRole('button', { name: /Start or stop capture/ }));
  await waitFor(() => expect(useApp.getState().page).toBe('capture'));
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  for (const command of ['run first decoder', 'search current trigger', 'export session json', 'export html report']) {
    fireEvent.change(input, { target: { value: command } });
    fireEvent.click(screen.getByRole('button', { name: new RegExp(command, 'i') }));
    await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'Open a session first')).toBe(true));
  }
});

it('stops captures and reports successful JSON saves from global shortcuts', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).includes('/export/')
    ? new Response('saved')
    : Response.json({}));
  vi.stubGlobal('fetch', fetcher);
  useApp.setState({ page: 'diagnostics', status: { ...status, capture_state: 'armed',
    device: { mock: true } } as never, activeSession: session('saved'), controlMode: true });
  render(<AppShell />);
  expect(screen.getByText('Connected device (mock)')).toBeTruthy();
  fireEvent.keyDown(window, { key: ' ', target: document.body });
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/capture/stop', expect.anything()));
  const callsAfterStop = fetcher.mock.calls.length;
  useApp.setState({ status: { ...status, device_connected: false, capture_state: 'idle' } as never });
  fireEvent.keyDown(window, { key: ' ', target: document.body });
  expect(fetcher).toHaveBeenCalledTimes(callsAfterStop);
  fireEvent.keyDown(window, { key: 's', metaKey: true, target: document.body });
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'Session saved (JSON download)')).toBe(true));
});

it('reports a trigger command with no matching sample', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => Response.json(String(input).includes('trigger-search')
    ? { sample: null } : {})));
  useApp.setState({ page: 'diagnostics', activeSession: session('s') });
  render(<AppShell />);
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
  fireEvent.change(screen.getByLabelText('Command search'), { target: { value: 'search current trigger' } });
  fireEvent.click(screen.getByRole('button', { name: /Search current trigger/ }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'No trigger match found')).toBe(true));
});
