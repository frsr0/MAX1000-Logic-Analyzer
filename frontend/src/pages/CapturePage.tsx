// Main capture view: waveform center, collapsible side panel with tabs,
// packet table bottom panel.
import { useEffect, useState } from 'react';
import { useApp } from '../state/appStore';
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
import { WaveformCanvas } from '../waveform/WaveformCanvas';

type Tab = 'capture' | 'channels' | 'trigger' | 'decoders' | 'measure'
  | 'markers' | 'export' | 'raw' | 'analog';

const TABS: { id: Tab; label: string }[] = [
  { id: 'capture', label: 'Capture' },
  { id: 'channels', label: 'Channels' },
  { id: 'trigger', label: 'Trigger' },
  { id: 'decoders', label: 'Decoders' },
  { id: 'measure', label: 'Measure' },
  { id: 'analog', label: 'Analog' },
  { id: 'markers', label: 'Markers' },
  { id: 'export', label: 'Export' },
  { id: 'raw', label: 'Raw' },
];

export function CapturePage() {
  const { activeSession, sessions, openSession, status } = useApp();
  const [tab, setTab] = useState<Tab>('capture');
  const [panelOpen, setPanelOpen] = useState(window.innerWidth > 900);
  const [tableOpen, setTableOpen] = useState(true);

  useEffect(() => {
    const last = status?.last_session_id;
    if (last && last !== activeSession?.id
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
            <WaveformCanvas
              channels={enabledChannels}
              onSelectRegion={() => waveformView.notify()}
            />
            {tableOpen && <DecoderTable />}
          </>
        ) : (
          <div className="empty-state">
            <h2>No capture loaded</h2>
            <p>Connect a device on the Device page, then start a capture or open a saved session.</p>
          </div>
        )}
      </div>
      {panelOpen && (
        <div className="side-panel">
          <div className="tab-bar">
            {TABS.map((t) => (
              <button key={t.id} className={tab === t.id ? 'active' : ''}
                onClick={() => setTab(t.id)}>{t.label}</button>
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
        </div>
      )}
    </div>
  );
}
