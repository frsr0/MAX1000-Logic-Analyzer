// Analog-only views: FFT spectrum of one channel, and XY scope plot of two
// channels against each other. Reuses the existing /spectrum endpoint and the
// waveform worker's window fetch (already used by the main canvas renderer).
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { WaveformClient } from '../workers/waveformClient';

type Mode = 'spectrum' | 'xy';

function currentSelection(): [number, number] | null {
  const { selectionStart, selectionEnd, cursorA, cursorB } = waveformView;
  if (selectionStart !== null && selectionEnd !== null) {
    return [Math.floor(Math.min(selectionStart, selectionEnd)),
            Math.ceil(Math.max(selectionStart, selectionEnd))];
  }
  if (cursorA !== null && cursorB !== null) {
    return [Math.min(cursorA, cursorB), Math.max(cursorA, cursorB)];
  }
  return null;
}

export function AnalogPanel() {
  const { activeSession, toast } = useApp();
  const [mode, setMode] = useState<Mode>('spectrum');
  const analogChannels = (activeSession?.channels ?? []).filter((c) => c.type === 'analog');
  const [chA, setChA] = useState('');
  const [chB, setChB] = useState('');
  const [scopeAll, setScopeAll] = useState(true);
  const [busy, setBusy] = useState(false);
  const [spectrumData, setSpectrumData] = useState<{ freqs: number[]; magnitude: number[] } | null>(null);
  const [xyData, setXyData] = useState<{ x: Float32Array; y: Float32Array } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<WaveformClient | null>(null);

  useEffect(() => {
    clientRef.current = new WaveformClient();
    return () => clientRef.current?.dispose();
  }, []);

  useEffect(() => {
    setSpectrumData(null);
    setXyData(null);
    if (analogChannels.length && !analogChannels.some((c) => c.id === chA)) {
      setChA(analogChannels[0].id);
    }
    if (analogChannels.length > 1 && !analogChannels.some((c) => c.id === chB)) {
      setChB(analogChannels[1].id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSession?.id]);

  const runSpectrum = async () => {
    if (!activeSession || !chA) return;
    setBusy(true);
    try {
      const sel = scopeAll ? null : currentSelection();
      const r = await api.spectrum(activeSession.id, chA, sel?.[0] ?? 0, sel?.[1] ?? -1);
      setSpectrumData(r);
    } catch (e: any) {
      toast('error', e.message);
    } finally {
      setBusy(false);
    }
  };

  const runXY = async () => {
    if (!activeSession || !chA || !chB || !clientRef.current) return;
    setBusy(true);
    try {
      const sel = scopeAll ? null : currentSelection();
      const start = sel?.[0] ?? 0;
      const end = sel?.[1] ?? activeSession.num_samples;
      const p = await clientRef.current.fetchWindow(activeSession.id, start, end, 2000, [chA, chB]);
      const x = p.arrays.get(`analog:${chA}`) as Float32Array | undefined;
      const y = p.arrays.get(`analog:${chB}`) as Float32Array | undefined;
      if (!x || !y || !x.length || !y.length) {
        throw new Error('No analog samples for the selected channels/range');
      }
      setXyData({ x, y });
    } catch (e: any) {
      toast('error', e.message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const el = canvas;
    const ro = new ResizeObserver(() => draw());
    if (el.parentElement) ro.observe(el.parentElement);
    draw();
    return () => ro.disconnect();

    function draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = el.parentElement?.clientWidth ?? 480;
    const h = 260;
    el.width = w * dpr;
    el.height = h * dpr;
    el.style.width = `${w}px`;
    el.style.height = `${h}px`;
    const ctx = el.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#10131a';
    ctx.fillRect(0, 0, w, h);

    if (mode === 'spectrum' && spectrumData) {
      const { freqs, magnitude } = spectrumData;
      if (!freqs.length) return;
      let maxMag = 1e-9;
      for (const m of magnitude) if (m > maxMag) maxMag = m;
      const maxFreq = freqs[freqs.length - 1] || 1;
      ctx.strokeStyle = '#7fa3c8';
      ctx.beginPath();
      for (let i = 0; i < freqs.length; i++) {
        const px = (freqs[i] / maxFreq) * w;
        const py = h - (magnitude[i] / maxMag) * (h - 8) - 4;
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
      ctx.fillStyle = '#9aa7b8';
      ctx.font = '11px monospace';
      ctx.fillText('0 Hz', 4, h - 4);
      ctx.fillText(`${(maxFreq / 1e3).toFixed(1)} kHz`, w - 70, h - 4);
    } else if (mode === 'xy' && xyData) {
      const { x, y } = xyData;
      let xlo = Infinity, xhi = -Infinity, ylo = Infinity, yhi = -Infinity;
      for (let i = 0; i < x.length; i++) {
        if (x[i] < xlo) xlo = x[i];
        if (x[i] > xhi) xhi = x[i];
        if (y[i] < ylo) ylo = y[i];
        if (y[i] > yhi) yhi = y[i];
      }
      const xrng = xhi - xlo || 1;
      const yrng = yhi - ylo || 1;
      ctx.strokeStyle = 'rgba(255,213,79,0.6)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = 0; i < x.length; i++) {
        const px = ((x[i] - xlo) / xrng) * (w - 16) + 8;
        const py = h - (((y[i] - ylo) / yrng) * (h - 16) + 8);
        if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.stroke();
    } else {
      ctx.fillStyle = '#5a6472';
      ctx.font = '12px sans-serif';
      ctx.fillText('Run to plot', 10, h / 2);
    }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, spectrumData, xyData]);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;
  if (!analogChannels.length) {
    return <div className="panel-body hint">No analog channels in this capture.</div>;
  }

  return (
    <div className="panel-body">
      <div className="button-row">
        <button className={mode === 'spectrum' ? 'active' : ''} onClick={() => setMode('spectrum')}>Spectrum</button>
        <button className={mode === 'xy' ? 'active' : ''} onClick={() => setMode('xy')}>XY scope</button>
      </div>

      <label className="field">
        <span>{mode === 'spectrum' ? 'Channel' : 'X channel'}</span>
        <select value={chA} onChange={(e) => setChA(e.target.value)}>
          {analogChannels.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.name})</option>)}
        </select>
      </label>

      {mode === 'xy' && (
        <label className="field">
          <span>Y channel</span>
          <select value={chB} onChange={(e) => setChB(e.target.value)}>
            {analogChannels.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.name})</option>)}
          </select>
        </label>
      )}

      <label className="field checkbox">
        <input type="checkbox" checked={scopeAll} onChange={(e) => setScopeAll(e.target.checked)} />
        <span>Whole capture (uncheck to use cursor/selection range)</span>
      </label>

      <button className="primary" disabled={busy || (mode === 'xy' && chA === chB)}
        onClick={mode === 'spectrum' ? runSpectrum : runXY}>
        {busy ? 'Working…' : mode === 'spectrum' ? 'Compute spectrum' : 'Plot XY'}
      </button>
      {mode === 'xy' && chA === chB && (
        <div className="hint">Pick two different channels for an XY plot.</div>
      )}

      <canvas ref={canvasRef} className="analog-canvas" />
    </div>
  );
}
