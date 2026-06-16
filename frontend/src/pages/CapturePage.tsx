// Main capture view: waveform centre, collapsible side panel with tabs,
// packet table bottom panel.
import { useEffect, useState } from 'react';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { DecoderTable } from '../decoders/DecoderTable';
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
  | 'markers' | 'export' | 'raw';

const TABS: { id: Tab; label: string }[] = [
  { id: 'capture', label: 'Capture' },
  { id: 'channels', label: 'Channels' },
  { id: 'trigger', label: 'Trigger' },
  { id: 'decoders', label: 'Decoders' },
  { id: 'measure', label: 'Measure' },
  { id: 'markers', label: 'Markers' },
  { id: 'export', label: 'Export' },
  { id: 'raw', label: 'Raw' },
];

export function CapturePage() {
  const { activeSession, sessions, openSession, status } = useApp();
  const [tab, setTab] = useState<Tab>('capture');
  const [panelOpen, setPanelOpen] = useState(window.innerWidth > 900);
  const [tableOpen, setTableOpen] = useState(true);

  // auto-open newest session when a capture finishes
  useEffect(() => {
    const last = status?.last_session_id;
    if (last && last !== activeSession?.id
        && (status?.capture_state === 'done' || status?.capture_state === 'capturing')) {
      openSession(last).catch(() => {});
    }
  }, [status?.last_session_id, status?.capture_state]);

  // space = start/stop shortcut handled in AppShell; ctrl+s save handled there too
  useEffect(() => {
    if (!activeSession && sessions.length) {
      openSession(sessions[0].id).catch(() => {});
    }
  }, [sessions.length]);

  const enabledChannels = activeSession?.channels ?? [];

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
                  ? `${activeSession.sample_rate / 1e6} MHz`
                  : `${activeSession.sample_rate / 1e3} kHz`}
                {activeSession.device.mock ? ' · MOCK' : ''}
              </span>
              <button className="slim" onClick={() => setTableOpen(!tableOpen)}>
                {tableOpen ? '▾ packets' : '▸ packets'}
              </button>
              <button className="slim" onClick={() => setPanelOpen(!panelOpen)}>
                {panelOpen ? '⟩⟩' : '⟨⟨'}
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
            <p>Connect a device (Device page) and start a capture, or open a saved session.</p>
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
          {tab === 'markers' && <MarkerPanel />}
          {tab === 'export' && <ExportPanel />}
          {tab === 'raw' && <RawInspector />}
        </div>
      )}
    </div>
  );
}
