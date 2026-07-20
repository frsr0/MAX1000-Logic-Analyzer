import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

export function DashboardPanel() {
  const { activeSession } = useApp();
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    if (activeSession) api.sessionDashboard(activeSession.id).then(setData).catch(() => setData(null));
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
  </div>;
}
