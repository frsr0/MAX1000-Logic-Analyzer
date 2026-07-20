import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

export function EyePanel() {
  const { activeSession, toast } = useApp();
  const channels = (activeSession?.channels ?? []).filter((c) => c.type === 'digital' || c.type === 'derived');
  const [channel, setChannel] = useState('');
  const [baud, setBaud] = useState(115200);
  const [data, setData] = useState<{ grid: number[][]; traces: number; unit_samples: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (channels.length && !channels.some((c) => c.id === channel)) setChannel(channels[0].id);
    setData(null);
  }, [activeSession?.id]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data) return;
    const parent = canvas.parentElement;
    const width = parent?.clientWidth ?? 480;
    const height = 280;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr; canvas.height = height * dpr;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const rows = data.grid.length;
    const cols = data.grid[0]?.length ?? 0;
    let max = 1e-9;
    for (const row of data.grid) for (const value of row) max = Math.max(max, value);
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      const level = data.grid[y][x] / max;
      ctx.fillStyle = `hsl(${240 - level * 240} 80% ${18 + level * 58}%)`;
      ctx.fillRect(x * width / cols, y * height / rows, Math.ceil(width / cols), Math.ceil(height / rows));
    }
    ctx.strokeStyle = 'rgba(255,255,255,0.35)';
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(width / 2, 0); ctx.lineTo(width / 2, height); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#d5dbe3'; ctx.font = '11px monospace';
    ctx.fillText('−1 UI', 4, height - 5); ctx.fillText('0 UI', width / 2 - 10, height - 5); ctx.fillText('+1 UI', width - 34, height - 5);
  }, [data]);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;
  if (!channels.length) return <div className="panel-body hint">No digital channels in this capture.</div>;

  const run = async () => {
    if (!channel) return;
    setBusy(true);
    try { setData(await api.eyeDiagram(activeSession.id, channel, baud)); }
    catch (e: any) { toast('error', e.message); }
    finally { setBusy(false); }
  };

  return <div className="panel-body">
    <div className="hint">Fold UART, SPI, or I²C line samples into unit intervals. Baud/clock rate is software analysis only.</div>
    <label className="field"><span>Digital channel</span>
      <select value={channel} onChange={(e) => setChannel(e.target.value)}>
        {channels.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.name})</option>)}
      </select>
    </label>
    <label className="field"><span>Bit/clock rate (baud)</span>
      <input type="number" min={1} value={baud} onChange={(e) => setBaud(Math.max(1, Number(e.target.value)))} />
    </label>
    <button className="primary" disabled={busy} onClick={run}>{busy ? 'Computing…' : 'Compute eye diagram'}</button>
    {data && <div className="hint">{data.traces.toLocaleString()} folded traces · {data.unit_samples.toFixed(2)} samples/UI</div>}
    <canvas ref={canvasRef} className="analog-canvas" aria-label="Eye diagram" />
  </div>;
}
