// Main capture view: waveform center, collapsible side panel with tabs,
// packet table bottom panel.
import { useEffect, useRef, useState } from 'react';
import { useApp } from '../state/appStore';
import { api } from '../api/client';
import { waveformView } from '../state/waveformStore';
import { DecoderTable } from '../decoders/DecoderTable';
import { AnalogPanel } from '../panels/AnalogPanel';
import { CaptureControls } from '../panels/CaptureControls';
import { ChannelPanel } from '../panels/ChannelPanel';
import { DecoderPanel } from '../panels/DecoderPanel';
import { ExportPanel } from '../panels/ExportPanel';
import { MarkerPanel } from '../panels/MarkerPanel';
import { MeasurementPanel } from '../panels/MeasurementPanel';
import { RawInspector } from '../panels/RawInspector';
import { TriggerPanel } from '../panels/TriggerPanel';
import { DashboardPanel } from '../panels/DashboardPanel';
import { EyePanel } from '../panels/EyePanel';
import { WaveformCanvas } from '../waveform/WaveformCanvas';

type Tab = 'capture' | 'channels' | 'trigger' | 'decoders' | 'measure'
  | 'markers' | 'export' | 'raw' | 'analog' | 'dashboard' | 'eye';

function sessionModeLabel(mode: string) {
  const labels: Record<string, string> = {
    single: 'Digital capture',
    rolling: 'Digital capture (continuous)',
    continuous: 'Digital capture (continuous)',
    digital_narrow: 'High-speed single channel',
    analog: 'Analog — one channel',
    analog_fast: 'Analog — one channel',
    analog_continuous: 'Analog — one channel (continuous)',
    analog_all: 'Analog — four channels',
    analog_all_continuous: 'Analog — four channels (continuous)',
    mixed: 'Digital + analog',
    mixed_continuous: 'Digital + analog (continuous)',
  };
  return labels[mode] ?? mode;
}

function sessionRateLabel(rate: number) {
  return rate >= 1e6 ? `${(rate / 1e6).toFixed(1)} MHz` : `${(rate / 1e3).toFixed(1)} kHz`;
}

function sessionChannelsLabel(channels: { type: string; board_label?: string | null; adc_channel?: number | null }[]) {
  const digital = channels.filter((channel) => channel.type === 'digital').length;
  const analog = channels
    .filter((channel) => channel.type === 'analog')
    .map((channel) => channel.adc_channel !== null && channel.adc_channel !== undefined
      ? `ADC${channel.adc_channel}/${channel.board_label ?? `a${channel.adc_channel}`}`
      : channel.board_label ?? 'analog')
    .join(', ');
  return [digital ? `${digital} digital` : '', analog ? `${analog}` : '']
    .filter(Boolean).join(' · ') || 'No channel metadata';
}

const TAB_GROUPS: { label: string; tabs: { id: Tab; label: string }[] }[] = [
  {
    label: 'Configure',
    tabs: [
      { id: 'capture', label: 'Capture setup' },
      { id: 'channels', label: 'Inputs' },
      { id: 'trigger', label: 'Trigger' },
      { id: 'analog', label: 'Analog' },
    ],
  },
  {
    label: 'Analyze',
    tabs: [
      { id: 'decoders', label: 'Decoders' },
      { id: 'measure', label: 'Measure' },
      { id: 'dashboard', label: 'Dashboard' },
      { id: 'eye', label: 'Eye diagram' },
      { id: 'markers', label: 'Markers' },
      { id: 'export', label: 'Export' },
    ],
  },
  { label: 'Advanced', tabs: [{ id: 'raw', label: 'Raw data' }] },
];

export function CapturePage() {
  const { activeSession, sessions, openSession, status, controlMode, setPage, toast } = useApp();
  const [tab, setTab] = useState<Tab>('capture');
  const [panelOpen, setPanelOpen] = useState(window.innerWidth > 900);
  const [tableOpen, setTableOpen] = useState(true);
  const previousLastSessionId = useRef(status?.last_session_id);

  useEffect(() => {
    const last = status?.last_session_id;
    const isNewCapture = last !== previousLastSessionId.current;
    previousLastSessionId.current = last;
    if (isNewCapture && last && last !== activeSession?.id
        && (status?.capture_state === 'done' || status?.capture_state === 'capturing')) {
      openSession(last).catch(() => {});
    }
  }, [status?.last_session_id, status?.capture_state]);

  useEffect(() => {
    if (!activeSession && sessions.length) {
      openSession(sessions[0].id).catch(() => {});
    }
  }, [sessions.length]);

  const enabledChannels = activeSession?.channels ?? [];
  const deviceName = activeSession?.device.device_name ?? 'No capture loaded';
  const connected = Boolean(status?.device_connected);

  const acquireControl = async () => {
    try {
      const result = await api.acquireControl('me');
      toast(result.acquired ? 'success' : 'warning',
        result.acquired ? 'Control acquired' : 'Another client is using the device');
      await useApp.getState().refreshStatus();
    } catch (error: any) {
      toast('error', error.message);
    }
  };

  return (
    <div className={`capture-page ${panelOpen ? 'panel-open' : ''}`}>
      <div className="capture-main">
        {activeSession ? (
          <>
            <div className="session-bar">
              <strong>{activeSession.name}</strong>
              <span className="hint">
                {activeSession.num_samples.toLocaleString()} samples @{' '}
                {activeSession.sample_rate >= 1e6
                  ? `${(activeSession.sample_rate / 1e6).toFixed(1)} MHz`
                  : `${(activeSession.sample_rate / 1e3).toFixed(1)} kHz`}
                {activeSession.device.mock ? ' · MOCK' : ''}
              </span>
              <span className="badge badge-soft">{deviceName}</span>
              <button className="slim" onClick={() => setTableOpen(!tableOpen)}>
                {tableOpen ? 'Hide packets' : 'Show packets'}
              </button>
              <button className="slim" onClick={() => setPanelOpen(!panelOpen)}>
                {panelOpen ? 'Collapse' : 'Expand'}
              </button>
            </div>
            <div className="loaded-session-context" aria-label="Loaded session metadata">
              <span className="context-label">Loaded session</span>
              <strong>{sessionModeLabel(activeSession.settings.mode)}</strong>
              <span>{sessionRateLabel(activeSession.sample_rate)} · {sessionChannelsLabel(activeSession.channels)}</span>
            </div>
            <WaveformCanvas
              channels={enabledChannels}
              onSelectRegion={() => waveformView.notify()}
            />
            {tableOpen && <DecoderTable />}
          </>
        ) : (
          <div className="empty-state capture-setup-state">
            <span className="setup-step">Capture setup</span>
            <h2>{!connected ? 'Connect a device to begin' : !controlMode ? 'Request control to capture' : 'Ready for your first capture'}</h2>
            <p>{!connected
              ? 'Choose a hardware or mock device, then return here to configure a capture.'
              : !controlMode
                ? 'This device is connected, but another client has control. Request control before sending commands.'
                : 'Choose your inputs and trigger, then press Capture setup to configure the acquisition.'}</p>
            <div className="button-row setup-actions">
              {!connected && <button className="primary" onClick={() => setPage('device')}>Connect device</button>}
              {connected && !controlMode && <button className="primary" onClick={() => void acquireControl()}>Request control</button>}
              {connected && controlMode && <button className="primary" onClick={() => { setPanelOpen(true); setTab('capture'); }}>Open capture setup</button>}
              <button onClick={() => setPage('sessions')}>Open saved sessions</button>
            </div>
          </div>
        )}
      </div>
      {panelOpen && (
        <div className="side-panel">
          <div className="tab-bar" aria-label="Capture workflow">
            {TAB_GROUPS.map((group) => (
              <div key={group.label} className="tab-group">
                <span className="tab-group-label">{group.label}</span>
                <div className="tab-group-buttons">
                  {group.tabs.map((t) => (
                    <button key={t.id} className={tab === t.id ? 'active' : ''}
                      onClick={() => setTab(t.id)}>{t.label}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {tab === 'capture' && <CaptureControls />}
          {tab === 'channels' && <ChannelPanel />}
          {tab === 'trigger' && <TriggerPanel />}
          {tab === 'decoders' && <DecoderPanel />}
          {tab === 'measure' && <MeasurementPanel />}
          {tab === 'analog' && <AnalogPanel />}
          {tab === 'markers' && <MarkerPanel />}
          {tab === 'export' && <ExportPanel />}
          {tab === 'raw' && <RawInspector />}
          {tab === 'dashboard' && <DashboardPanel />}
          {tab === 'eye' && <EyePanel />}
        </div>
      )}
    </div>
  );
}
