// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('../panels/CaptureControls', () => ({ CaptureControls: () => <div>capture controls</div> }));
vi.mock('../panels/ChannelPanel', () => ({ ChannelPanel: () => <div>channel panel</div> }));
vi.mock('../panels/TriggerPanel', () => ({ TriggerPanel: () => <div>trigger panel</div> }));
vi.mock('../panels/DecoderPanel', () => ({ DecoderPanel: () => <div>decoder panel</div> }));
vi.mock('../panels/MeasurementPanel', () => ({ MeasurementPanel: () => <div>measurement panel</div> }));
vi.mock('../panels/AnalogPanel', () => ({ AnalogPanel: () => <div>analog panel</div> }));
vi.mock('../panels/MarkerPanel', () => ({ MarkerPanel: () => <div>marker panel</div> }));
vi.mock('../panels/ExportPanel', () => ({ ExportPanel: () => <div>export panel</div> }));
vi.mock('../panels/RawInspector', () => ({ RawInspector: () => <div>raw inspector</div> }));
vi.mock('../panels/DashboardPanel', () => ({ DashboardPanel: () => <div>dashboard panel</div> }));
vi.mock('../panels/EyePanel', () => ({ EyePanel: () => <div>eye panel</div> }));
vi.mock('../decoders/DecoderTable', () => ({ DecoderTable: () => <div>decoder table</div> }));
vi.mock('../waveform/WaveformCanvas', () => ({
  WaveformCanvas: ({ onSelectRegion }: { onSelectRegion: () => void }) => (
    <button onClick={onSelectRegion}>waveform canvas</button>
  ),
}));

import { CapturePage } from './CapturePage';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { session } from '../test/session';

function setWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
}

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  setWidth(1200);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('renders an empty narrow capture and automatically opens the first saved session', async () => {
  setWidth(800);
  const openSession = vi.fn().mockRejectedValue(new Error('removed'));
  useApp.setState({ sessions: [{ id: 'first' }] as never[], openSession });
  render(<CapturePage />);

  expect(screen.getByRole('heading', { name: 'Connect a device to begin' })).toBeTruthy();
  expect(screen.queryByText('capture controls')).toBeNull();
  await waitFor(() => expect(openSession).toHaveBeenCalledWith('first'));
});

it('groups the capture workflow and makes disconnected setup actionable', () => {
  setWidth(1200);
  useApp.setState({ status: { device_connected: false } as never });
  const { rerender } = render(<CapturePage />);

  expect(screen.getByText('Configure')).toBeTruthy();
  expect(screen.getByText('Analyze')).toBeTruthy();
  expect(screen.getByText('Advanced')).toBeTruthy();
  expect(screen.getByRole('heading', { name: 'Connect a device to begin' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Connect device' }));
  expect(useApp.getState().page).toBe('device');
});

it('opens capture setup from the ready state on a narrow layout', () => {
  setWidth(800);
  useApp.setState({ status: { device_connected: true } as never, controlMode: true });
  render(<CapturePage />);

  expect(screen.queryByText('capture controls')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Open capture setup' }));
  expect(screen.getByText('capture controls')).toBeTruthy();
});

it('offers control handoff and capture setup when a device is already connected', async () => {
  const refreshStatus = vi.fn().mockResolvedValue(undefined);
  const acquireControl = vi.spyOn(api, 'acquireControl').mockResolvedValue({ acquired: true });
  useApp.setState({
    status: { device_connected: true, control: { held: false } } as never,
    controlMode: false,
    refreshStatus,
  });
  const { rerender } = render(<CapturePage />);

  fireEvent.click(screen.getByRole('button', { name: 'Request control' }));
  await waitFor(() => expect(acquireControl).toHaveBeenCalledWith('me'));
  expect(refreshStatus).toHaveBeenCalled();

  acquireControl.mockResolvedValueOnce({ acquired: false });
  fireEvent.click(screen.getByRole('button', { name: 'Request control' }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message.includes('Another client'))).toBe(true));

  acquireControl.mockRejectedValueOnce(new Error('control offline'));
  useApp.setState({ controlMode: false });
  rerender(<CapturePage />);
  fireEvent.click(screen.getByRole('button', { name: 'Request control' }));
  await waitFor(() => expect(useApp.getState().toasts.some((toast) => toast.message === 'control offline')).toBe(true));

  useApp.setState({ controlMode: true });
  rerender(<CapturePage />);
  fireEvent.click(screen.getByRole('button', { name: 'Open capture setup' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open saved sessions' }));
});

it('opens the backend capture for both active capture states and ignores unrelated status', async () => {
  const openSession = vi.fn().mockRejectedValue(new Error('not ready'));
  const active = session('old');
  const { rerender } = render(<CapturePage />);
  useApp.setState({ activeSession: active, openSession, status: {
    last_session_id: 'new', capture_state: 'done',
  } as never });
  rerender(<CapturePage />);
  await waitFor(() => expect(openSession).toHaveBeenCalledWith('new'));

  openSession.mockClear();
  useApp.setState({ status: { last_session_id: 'live', capture_state: 'capturing' } as never });
  rerender(<CapturePage />);
  await waitFor(() => expect(openSession).toHaveBeenCalledWith('live'));

  openSession.mockClear();
  useApp.setState({ status: { last_session_id: 'old', capture_state: 'idle' } as never });
  rerender(<CapturePage />);
  await Promise.resolve();
  expect(openSession).not.toHaveBeenCalled();
});

it('preserves a manually selected session until the backend reports a newer capture', async () => {
  const openSession = vi.fn().mockResolvedValue(undefined);
  useApp.setState({
    activeSession: session('manual'),
    openSession,
    status: { last_session_id: 'previous-latest', capture_state: 'done' } as never,
  });

  const { rerender } = render(<CapturePage />);
  await Promise.resolve();
  expect(openSession).not.toHaveBeenCalled();

  useApp.setState({
    status: { last_session_id: 'new-capture', capture_state: 'done' } as never,
  });
  rerender(<CapturePage />);
  await waitFor(() => expect(openSession).toHaveBeenCalledWith('new-capture'));
});

it('renders a loaded capture and exercises every side-panel tab', () => {
  const notify = vi.spyOn(waveformView, 'notify');
  useApp.setState({ activeSession: session('fast', {
    name: 'Fast capture', num_samples: 1_234_567, sample_rate: 2_500_000,
    channels: [{ id: 'd0', name: 'D0', type: 'digital' }] as never[],
  }) });
  render(<CapturePage />);

  expect(screen.getByText('Fast capture')).toBeTruthy();
  expect(screen.getByText(/1,234,567 samples @ 2.5 MHz · MOCK/)).toBeTruthy();
  expect(screen.getByText('fixture')).toBeTruthy();
  expect(screen.getByText('decoder table')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'waveform canvas' }));
  expect(notify).toHaveBeenCalledOnce();

  const tabs: [string, string][] = [
    ['Inputs', 'channel panel'], ['Trigger', 'trigger panel'],
    ['Decoders', 'decoder panel'], ['Measure', 'measurement panel'],
    ['Analog', 'analog panel'], ['Dashboard', 'dashboard panel'],
    ['Eye diagram', 'eye panel'], ['Markers', 'marker panel'],
    ['Export', 'export panel'], ['Raw data', 'raw inspector'],
    ['Capture setup', 'capture controls'],
  ];
  for (const [tab, content] of tabs) {
    fireEvent.click(screen.getByRole('button', { name: tab }));
    expect(screen.getByText(content)).toBeTruthy();
  }

  fireEvent.click(screen.getByRole('button', { name: 'Hide packets' }));
  expect(screen.queryByText('decoder table')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Show packets' }));
  expect(screen.getByText('decoder table')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Collapse' }));
  expect(screen.queryByText('capture controls')).toBeNull();
});

it('expands a narrow non-mock capture and formats a sub-MHz sample rate', () => {
  setWidth(800);
  useApp.setState({ activeSession: session('slow', {
    sample_rate: 25_000,
    device: { ...session().device, mock: false, device_name: '' },
  }) });
  render(<CapturePage />);

  expect(screen.getByText(/0 samples @ 25.0 kHz/)).toBeTruthy();
  expect(screen.queryByText(/MOCK/)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
  expect(screen.getByText('capture controls')).toBeTruthy();
});
