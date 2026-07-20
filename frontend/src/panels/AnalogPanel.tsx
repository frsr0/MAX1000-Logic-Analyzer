// Analog-only views: FFT spectrum of one channel, and XY scope plot of two
// channels against each other. Reuses the existing /spectrum endpoint and the
// waveform worker's window fetch (already used by the main canvas renderer).
import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { WaveformClient } from '../workers/waveformClient';

type Mode = 'spectrum' | 'xy' | 'spectrogram' | 'correlation';

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
  const [spectrumData, setSpectrumData] = useState<{ freqs: number[]; magnitude: number[]; peaks?: { frequency_hz: number; magnitude: number }[] } | null>(null);
  const [xyData, setXyData] = useState<{ x: Float32Array; y: Float32Array } | null>(null);
  const [spectrogramData, setSpectrogramData] = useState<{ freqs: number[]; times: number[]; magnitude: number[][] } | null>(null);
  const [correlationData, setCorrelationData] = useState<{ delay_s: number | null; correlation?: number } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const clientRef = useRef<WaveformClient | null>(null);

  useEffect(() => {
    clientRef.current = new WaveformClient();
    return () => clientRef.current?.dispose();
  }, []);

  useEffect(() => {
    setSpectrumData(null);
    setXyData(null);
    setSpectrogramData(null);
    setCorrelationData(null);
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

  const runSpectrogram = async () => {
    if (!activeSession || !chA) return;
    setBusy(true);
    try {
      const sel = scopeAll ? null : currentSelection();
      setSpectrogramData(await api.spectrogram(activeSession.id, chA, sel?.[0] ?? 0, sel?.[1] ?? -1));
    } catch (e: any) { toast('error', e.message); }
    finally { setBusy(false); }
  };

  const runCorrelation = async () => {
    if (!activeSession || !chA || !chB) return;
    setBusy(true);
    try {
      const sel = scopeAll ? null : currentSelection();
      setCorrelationData(await api.correlation(activeSession.id, chA, chB, sel?.[0] ?? 0, sel?.[1] ?? -1));
    } catch (e: any) { toast('error', e.message); }
    finally { setBusy(false); }
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
    } else if (mode === 'spectrogram' && spectrogramData) {
      const rows = spectrogramData.magnitude;
      let maxMag = 1e-9;
      for (const row of rows) for (const value of row) maxMag = Math.max(maxMag, value);
      const cellW = w / Math.max(1, rows.length);
      const cellH = h / Math.max(1, spectrogramData.freqs.length);
      for (let x = 0; x < rows.length; x++) {
        for (let y = 0; y < (rows[x]?.length ?? 0); y++) {
          const level = Math.min(1, (rows[x][y] ?? 0) / maxMag);
          ctx.fillStyle = `hsl(${240 - level * 240} 80% ${20 + level * 55}%)`;
          ctx.fillRect(x * cellW, h - (y + 1) * cellH, Math.ceil(cellW), Math.ceil(cellH) + 1);
        }
      }
      ctx.fillStyle = '#d5dbe3'; ctx.font = '11px monospace';
      ctx.fillText('time →', 4, h - 4);
      const lastFreq = spectrogramData.freqs.length
        ? spectrogramData.freqs[spectrogramData.freqs.length - 1] : 0;
      ctx.fillText(`${(lastFreq / 1e3).toFixed(1)} kHz`, 4, 12);
    } else if (mode === 'correlation' && correlationData) {
      ctx.fillStyle = '#d5dbe3'; ctx.font = '14px monospace';
      ctx.fillText(`delay: ${correlationData.delay_s == null ? 'n/a' : `${(correlationData.delay_s * 1e6).toFixed(3)} µs`}`, 12, 80);
      ctx.fillText(`correlation: ${(correlationData.correlation ?? 0).toFixed(5)}`, 12, 110);
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
  }, [mode, spectrumData, xyData, spectrogramData, correlationData]);

  if (!activeSession) return <div className="panel-body hint">No session open.</div>;
  if (!analogChannels.length) {
    return <div className="panel-body hint">No analog channels in this capture.</div>;
  }

  return (
    <div className="panel-body">
      <div className="button-row">
        <button className={mode === 'spectrum' ? 'active' : ''} onClick={() => setMode('spectrum')}>Spectrum</button>
        <button className={mode === 'xy' ? 'active' : ''} onClick={() => setMode('xy')}>XY scope</button>
        <button className={mode === 'spectrogram' ? 'active' : ''} onClick={() => setMode('spectrogram')}>Spectrogram</button>
        <button className={mode === 'correlation' ? 'active' : ''} onClick={() => setMode('correlation')}>Correlation</button>
      </div>

      <label className="field">
        <span>{mode === 'spectrum' || mode === 'spectrogram' ? 'Channel' : 'X channel'}</span>
        <select value={chA} onChange={(e) => setChA(e.target.value)}>
          {analogChannels.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.name})</option>)}
        </select>
      </label>

      {(mode === 'xy' || mode === 'correlation') && (
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

      <button className="primary" disabled={busy || ((mode === 'xy' || mode === 'correlation') && chA === chB)}
        onClick={mode === 'spectrum' ? runSpectrum : mode === 'xy' ? runXY : mode === 'spectrogram' ? runSpectrogram : runCorrelation}>
        {busy ? 'Working…' : mode === 'spectrum' ? 'Compute spectrum' : mode === 'xy' ? 'Plot XY' : mode === 'spectrogram' ? 'Compute spectrogram' : 'Correlate channels'}
      </button>
      {mode === 'spectrum' && spectrumData?.peaks?.length ? (
        <div className="hint">Peaks: {spectrumData.peaks.map((p) => `${(p.frequency_hz / 1e3).toFixed(2)} kHz`).join(' · ')}</div>
      ) : null}
      {mode === 'xy' && chA === chB && (
        <div className="hint">Pick two different channels for an XY plot.</div>
      )}

      <canvas ref={canvasRef} className="analog-canvas" />
    </div>
  );
}
