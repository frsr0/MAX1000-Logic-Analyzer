// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { CapturePage } from './CapturePage';
import { useApp } from '../state/appStore';
import { defaultCaptureSettings, type ChannelInfo } from '../api/types';
import { session } from '../test/session';

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
  WaveformCanvas: () => <div>waveform canvas</div>,
}));

function setWidth(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
}

beforeEach(() => {
  localStorage.clear();
  useApp.setState(useApp.getInitialState(), true);
  setWidth(1200);
});

afterEach(() => cleanup());

it('keeps loaded analogue metadata separate from the next capture configuration', () => {
  const loaded = session('history', {
    name: 'Analogue history',
    settings: {
      ...defaultCaptureSettings(),
      mode: 'analog_fast',
      analog_enabled: true,
      sample_rate: 500_000,
      num_samples: 10_000,
    },
    sample_rate: 500_000,
    num_samples: 10_000,
    channels: [{
      id: 'a1', name: 'AIN3 J1 / 5', type: 'analog', enabled: true,
      units: 'V', volts_per_div: 0.5, offset: 0, probe_attenuation: 1,
      cal_gain: 1, cal_offset: 0, threshold: 1.65, coupling: 'DC',
      members: [], display_base: 'hex', board_label: 'AIN3', adc_channel: 1,
      physical_available: true,
    } as ChannelInfo],
  });
  useApp.setState({
    activeSession: loaded,
    status: {
      device_connected: true,
      device_kind: 'hardware',
      capture_state: 'idle',
    } as never,
    controlMode: true,
    captureSettings: {
      ...defaultCaptureSettings(),
      mode: 'single',
      analog_enabled: false,
      sample_rate: 200_000_000,
      num_samples: 1024,
    },
  });

  const view = render(<CapturePage />);

  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain(
    'Loaded session');
  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain(
    'Analog — one channel');
  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain(
    '500.0 kHz · ADC1/AIN3');
  expect(screen.getByLabelText('Next capture configuration').textContent).toContain(
    'Settings for the next capture');
  expect(screen.getByLabelText('Next capture configuration').textContent).toContain(
    'Opening a saved session does not change these settings.');
  expect(screen.getByLabelText('Sample rate')).toHaveProperty('value', '200000000');
  expect(view.container.querySelector('.mode-tile.active')?.textContent).toContain('Digital capture');
});

it('handles legacy session metadata without inventing a physical mapping', () => {
  useApp.setState({
    activeSession: session('minimal', {
      settings: { ...defaultCaptureSettings(), mode: 'future_mode' as never, sample_rate: 2_000_000 },
      sample_rate: 2_000_000,
      channels: [
        {
          id: 'a3', name: 'Analog', type: 'analog', enabled: true,
          units: 'V', volts_per_div: 0.5, offset: 0, probe_attenuation: 1,
          cal_gain: 1, cal_offset: 0, threshold: 1.65, coupling: 'DC',
          members: [], display_base: 'hex', adc_channel: 3, board_label: null,
          physical_available: false,
        },
        {
          id: 'a0', name: 'Unmapped', type: 'analog', enabled: true,
          units: 'V', volts_per_div: 0.5, offset: 0, probe_attenuation: 1,
          cal_gain: 1, cal_offset: 0, threshold: 1.65, coupling: 'DC',
          members: [], display_base: 'hex', adc_channel: null, board_label: null,
          physical_available: false,
        },
        {
          id: 'a0', name: 'ADC0', type: 'analog', enabled: true,
          units: 'V', volts_per_div: 0.5, offset: 0, probe_attenuation: 1,
          cal_gain: 1, cal_offset: 0, threshold: 1.65, coupling: 'DC',
          members: [], display_base: 'hex', adc_channel: 0, board_label: null,
          physical_available: false,
        },
      ] as ChannelInfo[],
    }),
    status: { device_connected: true, device_kind: 'hardware', capture_state: 'idle' } as never,
    controlMode: true,
  });

  render(<CapturePage />);

  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain('future_mode');
  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain('2.0 MHz');
  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain('ADC3/a3, analog');
  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain('ADC0/a0');
});

it('shows when a loaded session has no channel metadata', () => {
  useApp.setState({
    activeSession: session('no-channels', {
      settings: { ...defaultCaptureSettings(), mode: 'single' },
      sample_rate: 1_000,
      channels: [],
    }),
    status: { device_connected: true, device_kind: 'hardware', capture_state: 'idle' } as never,
    controlMode: true,
  });

  render(<CapturePage />);

  expect(screen.getByLabelText('Loaded session metadata').textContent).toContain('No channel metadata');
});
