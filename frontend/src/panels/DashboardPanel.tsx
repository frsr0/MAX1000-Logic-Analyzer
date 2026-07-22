import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';

export function DashboardPanel() {
  const { activeSession } = useApp();
  const [data, setData] = useState<any>(null);
  const [suspects, setSuspects] = useState<any[]>([]);
  useEffect(() => {
    if (!activeSession) return;
    const channel = activeSession.channels.find((item) => item.type === 'digital' || item.type === 'derived');
    api.sessionDashboard(activeSession.id).then(setData).catch(() => setData(null));
    if (channel) api.timingSuspects(activeSession.id, channel.id).then((result) => setSuspects(result.suspects)).catch(() => setSuspects([]));
    else setSuspects([]);
  }, [activeSession?.id]);
  if (!activeSession) return <div className="panel-body hint">No session open.</div>;
  if (!data) return <div className="panel-body hint">Loading protocol dashboard…</div>;
  const max = Math.max(1, ...Object.values(data.by_type as Record<string, number>));
  const timeMax = Math.max(1, ...data.timeline);
  return <div className="panel-body">
    <div className="card-grid">
      <div className="finding info"><strong>{data.event_count}</strong> events</div>
      <div className={`finding ${data.error_count ? 'error' : 'success'}`}><strong>{data.error_count}</strong> errors</div>
      <div className="finding warning"><strong>{data.events_per_second.toFixed(2)}</strong> events/s</div>
    </div>
    <h4>Events by type</h4>
    {Object.entries(data.by_type as Record<string, number>).map(([name, count]) =>
      <div key={name} className="dashboard-bar"><span>{name}</span><div><i style={{ width: `${(Number(count) / max) * 100}%` }} /></div><b>{count}</b></div>)}
    <h4>Activity heatmap</h4>
    <div className="dashboard-heatmap" title="event activity over capture time">
      {data.timeline.map((count: number, i: number) => <i key={i}
        style={{ opacity: Math.max(0.08, count / timeMax), background: data.error_timeline[i] ? '#ef5350' : '#4fc3f7' }} />)}
    </div>
    {(['can', 'lin'] as const).map((protocol) => {
      const health = data.bus_health?.[protocol];
      if (!health || !health.frames) return null;
      return <div key={protocol} className="finding info">
        <strong>{protocol.toUpperCase()} health</strong>{' '}
        {health.frames} frame(s), {health.error_frames} error(s), {Number(health.load_pct).toFixed(1)}% bus load
        {protocol === 'can' && ` · ${health.crc_errors} CRC · ${health.ack_errors} ACK error(s)`}
        {protocol === 'lin' && ` · ${health.checksum_errors} checksum error(s)`}
      </div>;
    })}
    <h4>Bus transaction timeline</h4>
    <div className="dashboard-timeline">
      {(data.events ?? []).map((event: any) => (
        <button key={`${event.id}-${event.start_sample}`} className={`timeline-event severity-${event.severity ?? 'normal'}`}
          title={`${event.start_time.toFixed(6)} s · ${event.label || event.type}`}
          onClick={() => waveformView.jumpTo(Number(event.start_sample))}>
          <span className="mono">{(Number(event.start_time) * 1e3).toFixed(3)} ms</span>
          <strong>{event.type}</strong>
          <span>{event.label || 'event'}</span>
        </button>
      ))}
      {!(data.events ?? []).length && <span className="hint">No decoded transactions available.</span>}
    </div>
    <h4>Suspect timing annotations</h4>
    {suspects.length ? <div className="dashboard-suspects">
      {suspects.slice(0, 12).map((item: any) => <button key={`${item.start_sample}-${item.end_sample}`}
        className="timing-suspect" onClick={() => waveformView.jumpTo(Number(item.start_sample))}>
        <span className="mono">{item.duration_samples} samples</span>
        <span>{item.kind}</span>
        <span className="hint">expected ~{Number(item.median_samples).toFixed(1)}</span>
      </button>)}
    </div> : <span className="hint">No out-of-family pulse widths detected.</span>}
  </div>;
}
