// @vitest-environment jsdom
import { act, cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const socketHarness = vi.hoisted(() => {
  type Handler = (message: any) => any;
  const instances: Array<{
    url: string;
    handler?: Handler;
    onStateChange?: (connected: boolean) => void;
    unsubscribe: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
  }> = [];
  class Socket {
    url: string;
    handler?: Handler;
    onStateChange?: (connected: boolean) => void;
    unsubscribe = vi.fn();
    close = vi.fn();
    constructor(url: string) { this.url = url; instances.push(this); }
    subscribe(handler: Handler) { this.handler = handler; return this.unsubscribe; }
  }
  return { instances, Socket };
});

vi.mock('./api/websocket', () => ({ ReconnectingSocket: socketHarness.Socket }));
vi.mock('./layout/AppShell', () => ({ AppShell: () => <div>shell</div> }));

import App from './App';
import { useApp } from './state/appStore';
import { waveformView } from './state/waveformStore';
import { session } from './test/session';

const socket = (url: string) => socketHarness.instances.find((item) => item.url === url)!;
const message = async (url: string, type: string, data: any = {}) => {
  await act(async () => { await socket(url).handler?.({ type, data }); });
};

beforeEach(() => {
  socketHarness.instances.length = 0;
  useApp.setState(useApp.getInitialState(), true);
  document.documentElement.removeAttribute('data-theme');
  document.title = '';
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('initializes sockets, catalogs, status messages, logs, and cleans up', async () => {
  const refreshStatus = vi.fn();
  const refreshSessions = vi.fn();
  const refreshCapabilities = vi.fn();
  const loadCatalogs = vi.fn();
  const setWsConnected = vi.fn();
  const pushLog = vi.fn();
  useApp.setState({ refreshStatus, refreshSessions, refreshCapabilities, loadCatalogs,
    setWsConnected, pushLog, viewerSettings: { ...useApp.getState().viewerSettings, theme: 'light' } });

  const view = render(<App />);
  expect(view.getByText('shell')).toBeTruthy();
  expect(refreshStatus).toHaveBeenCalledTimes(1);
  expect(refreshSessions).toHaveBeenCalledTimes(1);
  expect(refreshCapabilities).toHaveBeenCalledTimes(1);
  expect(loadCatalogs).toHaveBeenCalledTimes(1);
  expect(document.documentElement.dataset.theme).toBe('light');
  expect(document.title).toBe('MAX1000 Logic Analyzer');
  expect(socketHarness.instances.map((item) => item.url)).toEqual(['/ws/status', '/ws/capture', '/ws/logs']);

  act(() => socket('/ws/status').onStateChange?.(true));
  act(() => socket('/ws/status').onStateChange?.(false));
  expect(setWsConnected).toHaveBeenNthCalledWith(1, true);
  expect(setWsConnected).toHaveBeenNthCalledWith(2, false);
  expect(refreshStatus).toHaveBeenCalledTimes(2);
  expect(refreshSessions).toHaveBeenCalledTimes(2);

  const snapshot = { capture_state: 'idle', session_count: 7 };
  await message('/ws/status', 'status_snapshot', snapshot);
  expect(useApp.getState().status).toBe(snapshot);
  await message('/ws/status', 'device_connected');
  await message('/ws/status', 'device_disconnected');
  expect(refreshCapabilities).toHaveBeenCalledTimes(3);
  await message('/ws/status', 'session_created');
  expect(refreshSessions).toHaveBeenCalledTimes(3);
  expect(refreshStatus).toHaveBeenCalledTimes(5);
  await message('/ws/status', 'ignored');

  const log = { level: 'info', message: 'ready', timestamp: 1 };
  await message('/ws/logs', 'log', log);
  await message('/ws/logs', 'ignored');
  expect(pushLog).toHaveBeenCalledOnce();
  expect(pushLog).toHaveBeenCalledWith(log);

  view.unmount();
  for (const item of socketHarness.instances) {
    expect(item.unsubscribe).toHaveBeenCalledOnce();
    expect(item.close).toHaveBeenCalledOnce();
  }
});

it('handles every capture event including progress with and without status', async () => {
  const refreshStatus = vi.fn();
  const refreshSessions = vi.fn();
  const toast = vi.fn();
  const setLiveFollow = vi.spyOn(waveformView, 'setLiveFollow');
  useApp.setState({ refreshStatus, refreshSessions, toast });
  render(<App />);

  useApp.setState({ status: null });
  await message('/ws/capture', 'capture_progress', { samples_read: 1, samples_total: 2, phase: 'read', repeat: true });
  expect(useApp.getState().status).toBeNull();
  const base = { capture_state: 'idle', device_connected: true, session_count: 0, ws_clients: 0 };
  useApp.setState({ status: base as never });
  await message('/ws/capture', 'capture_progress', { samples_read: 3, samples_total: 4, phase: 'transfer', repeat: false });
  expect(useApp.getState().status).toMatchObject({ capture_state: 'capturing', capture_progress: {
    samples_read: 3, samples_total: 4, message: 'transfer', repeat: false,
  } });

  await message('/ws/capture', 'capture_armed');
  await message('/ws/capture', 'capture_started');
  await message('/ws/capture', 'capture_complete');
  await message('/ws/capture', 'capture_cancelled');
  await message('/ws/capture', 'capture_error', { message: 'bad wire' });
  await message('/ws/capture', 'warning', { message: 'slow clock' });
  await message('/ws/capture', 'ignored');
  expect(refreshStatus).toHaveBeenCalledTimes(6);
  expect(refreshSessions).toHaveBeenCalledTimes(3);
  expect(setLiveFollow).toHaveBeenCalledWith(false);
  expect(toast).toHaveBeenNthCalledWith(1, 'error', 'Capture failed: bad wire');
  expect(toast).toHaveBeenNthCalledWith(2, 'warning', 'slow clock');
});

it('rebinds session sockets and handles decoder and live-waveform messages', async () => {
  const refreshActiveSession = vi.fn();
  const toast = vi.fn();
  const requestAnnotations = vi.spyOn(waveformView, 'requestAnnotations').mockResolvedValue();
  const updateLive = vi.spyOn(waveformView, 'updateLive').mockResolvedValue();
  useApp.setState({ activeSession: session('one', { num_samples: 50, sample_rate: 2000, trigger_sample: 8 }),
    refreshActiveSession, toast });
  const view = render(<App />);
  await waitFor(() => expect(socket('/ws/decoder/one')).toBeTruthy());

  await message('/ws/decoder/one', 'decoder_complete', { error: 'decode failed', cancelled: false, event_count: 0 });
  await message('/ws/decoder/one', 'decoder_complete', { error: '', cancelled: true, event_count: 1 });
  await message('/ws/decoder/one', 'decoder_complete', { error: '', cancelled: false, event_count: 12 });
  await message('/ws/decoder/one', 'ignored');
  expect(refreshActiveSession).toHaveBeenCalledTimes(3);
  expect(requestAnnotations).toHaveBeenCalledTimes(3);
  expect(toast).toHaveBeenNthCalledWith(1, 'error', 'Decoder failed: decode failed');
  expect(toast).toHaveBeenNthCalledWith(2, 'success', 'Decoder finished: 12 events');

  await message('/ws/session/one', 'measurement_updated');
  expect(refreshActiveSession).toHaveBeenCalledTimes(4);
  await message('/ws/session/one', 'waveform_ready', { num_samples: 80, sample_rate: 4000, rolling: true, chunk_samples: 16 });
  expect(updateLive).toHaveBeenCalledWith(80, 4000, 8, true, 16);
  expect(refreshActiveSession).toHaveBeenCalledTimes(5);
  await message('/ws/session/one', 'waveform_ready');
  expect(updateLive).toHaveBeenLastCalledWith(50, 2000, 8, false, 0);
  await message('/ws/session/one', 'ignored');

  const oldDecoder = socket('/ws/decoder/one');
  const oldSession = socket('/ws/session/one');
  act(() => useApp.setState({ activeSession: session('two') }));
  await waitFor(() => expect(socket('/ws/decoder/two')).toBeTruthy());
  expect(oldDecoder.unsubscribe).toHaveBeenCalledOnce();
  expect(oldDecoder.close).toHaveBeenCalledOnce();
  expect(oldSession.unsubscribe).toHaveBeenCalledOnce();
  expect(oldSession.close).toHaveBeenCalledOnce();

  await message('/ws/session/two', 'waveform_ready', {});
  expect(updateLive).toHaveBeenLastCalledWith(0, 1000, null, false, 0);
  useApp.setState({ activeSession: session('other') });
  await message('/ws/session/two', 'waveform_ready', { num_samples: 10 });
  expect(updateLive).toHaveBeenCalledTimes(3);
  view.unmount();
});
