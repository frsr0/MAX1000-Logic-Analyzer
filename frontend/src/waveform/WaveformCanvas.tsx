// Canvas waveform viewer: zoom/pan (wheel, drag, pinch), cursors, selection,
// minimap. Renders straight from the WaveformView store on rAF — no React
// re-render per frame.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ChannelInfo } from '../api/types';
import { useApp } from '../state/appStore';
import { waveformView } from '../state/waveformStore';
import { Minimap } from './minimap';
import { buildLayout, fmtFreq, fmtTime, render, RenderLayout, sampleToX, xToSample } from './renderer';

interface Props {
  channels: ChannelInfo[];
  onSelectRegion?: (start: number, end: number) => void;
}

export function WaveformCanvas({ channels, onSelectRegion }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const layoutRef = useRef<RenderLayout | null>(null);
  const [, setTick] = useState(0);
  const [hoverInfo, setHoverInfo] = useState<string>('');
  const activeSession = useApp((s) => s.activeSession);
  const toast = useApp((s) => s.toast);

  const labelWidth = window.innerWidth < 700 ? 70 : 110;

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const wrap = wrapRef.current!;
    const dpr = window.devicePixelRatio || 1;
    const layout = buildLayout(channels, waveformView, labelWidth);
    layoutRef.current = layout;
    const cssW = wrap.clientWidth;
    const cssH = Math.max(layout.totalHeight, wrap.clientHeight - 64);
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
    }
    const ctx = canvas.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    render(ctx, waveformView, layout, cssW, cssH);
  }, [channels, labelWidth]);

  // redraw on store changes + element resize
  useEffect(() => {
    let raf = 0;
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => { draw(); setTick((t) => t + 1); });
    };
    const unsub = waveformView.subscribe(schedule);
    const ro = new ResizeObserver(schedule);
    if (wrapRef.current) ro.observe(wrapRef.current);
    schedule();
    return () => { unsub(); ro.disconnect(); cancelAnimationFrame(raf); };
  }, [draw]);

  // ── pointer interaction ────────────────────────────────────────────
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ startX: number; viewStart: number; mode: 'pan' | 'select' } | null>(null);
  const pinch = useRef<{ dist: number; center: number } | null>(null);

  const widthOf = () => canvasRef.current?.clientWidth ?? 1;

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY });
    if (pointers.current.size === 2) {
      const [p1, p2] = [...pointers.current.values()];
      const layout = layoutRef.current!;
      pinch.current = {
        dist: Math.abs(p1.x - p2.x) || 1,
        center: xToSample(waveformView, layout, widthOf(), (p1.x + p2.x) / 2),
      };
      drag.current = null;
      return;
    }
    drag.current = {
      startX: e.nativeEvent.offsetX,
      viewStart: waveformView.start,
      mode: e.shiftKey ? 'select' : 'pan',
    };
    if (e.shiftKey) {
      const layout = layoutRef.current!;
      const s = xToSample(waveformView, layout, widthOf(), e.nativeEvent.offsetX);
      waveformView.selectionStart = s;
      waveformView.selectionEnd = s;
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const layout = layoutRef.current;
    if (!layout) return;
    const x = e.nativeEvent.offsetX;
    if (pointers.current.has(e.pointerId)) {
      pointers.current.set(e.pointerId, { x, y: e.nativeEvent.offsetY });
    }
    if (pinch.current && pointers.current.size === 2) {
      const [p1, p2] = [...pointers.current.values()];
      const dist = Math.abs(p1.x - p2.x) || 1;
      const factor = pinch.current.dist / dist;
      waveformView.zoomAround(pinch.current.center, factor);
      pinch.current.dist = dist;
      return;
    }
    const sample = xToSample(waveformView, layout, widthOf(), x);
    waveformView.hoverSample = Math.max(0, Math.min(waveformView.numSamples - 1, sample));
    waveformView.hoverY = e.nativeEvent.offsetY;
    updateHoverInfo(Math.round(waveformView.hoverSample));
    if (drag.current) {
      if (drag.current.mode === 'pan') {
        const dx = x - drag.current.startX;
        const spp = waveformView.span() / Math.max(1, widthOf() - layout.labelWidth);
        waveformView.setView(drag.current.viewStart - dx * spp,
          drag.current.viewStart - dx * spp + waveformView.span());
        return;
      }
      waveformView.selectionEnd = sample;
    }
    waveformView.notify();
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (drag.current?.mode === 'select' && waveformView.selectionStart !== null
        && waveformView.selectionEnd !== null) {
      const a = Math.min(waveformView.selectionStart, waveformView.selectionEnd);
      const b = Math.max(waveformView.selectionStart, waveformView.selectionEnd);
      if (b - a > 2) onSelectRegion?.(Math.floor(a), Math.ceil(b));
      else { waveformView.selectionStart = waveformView.selectionEnd = null; }
    }
    drag.current = null;
    waveformView.notify();
  };

  const onWheel = (e: React.WheelEvent) => {
    const layout = layoutRef.current;
    if (!layout) return;
    if (e.ctrlKey || !e.shiftKey) {
      const sample = xToSample(waveformView, layout, widthOf(), e.nativeEvent.offsetX);
      const factor = e.deltaY > 0 ? 1.25 : 0.8;
      waveformView.zoomAround(sample, factor);
    } else {
      waveformView.pan(Math.sign(e.deltaY) * waveformView.span() * 0.1);
    }
  };

  const onDoubleClick = async (e: React.MouseEvent) => {
    // double-click: place cursor A (with alt: B), snapped to the nearest edge
    const layout = layoutRef.current;
    if (!layout || !activeSession) return;
    let sample = Math.round(xToSample(waveformView, layout, widthOf(), e.nativeEvent.offsetX));
    sample = Math.max(0, Math.min(waveformView.numSamples - 1, sample));
    sample = await snapToEdge(sample);
    placeCursor(e.altKey ? 'b' : 'a', sample);
  };

  const snapToEdge = async (sample: number): Promise<number> => {
    // snap to nearest digital edge of the first enabled channel within 12 px
    const layout = layoutRef.current!;
    const ch = channels.find((c) => c.enabled && (c.type === 'digital' || c.type === 'derived'));
    if (!ch || !activeSession) return sample;
    const spp = waveformView.span() / Math.max(1, widthOf() - layout.labelWidth);
    const tol = Math.max(1, Math.round(spp * 12));
    try {
      const r = await api.edges(activeSession.id, ch.id, 'any',
        Math.max(0, sample - tol), sample + tol, 64);
      if (r.edges.length) {
        let best = r.edges[0];
        for (const ed of r.edges) {
          if (Math.abs(ed - sample) < Math.abs(best - sample)) best = ed;
        }
        return best;
      }
    } catch { /* no snap */ }
    return sample;
  };

  const placeCursor = useCallback((which: 'a' | 'b', sample: number) => {
    if (which === 'a') waveformView.cursorA = sample;
    else waveformView.cursorB = sample;
    waveformView.notify();
    persistCursor(which, sample);
  }, [activeSession]);

  const persistCursor = async (which: 'a' | 'b', sample: number) => {
    if (!activeSession) return;
    const kind = which === 'a' ? 'cursor_a' : 'cursor_b';
    try {
      const existing = waveformView.markers.find((m) => m.kind === kind);
      if (existing) await api.patchMarker(activeSession.id, existing.id, { sample });
      else {
        const m = await api.addMarker(activeSession.id,
          { sample, kind, label: which.toUpperCase() });
        waveformView.markers.push(m);
      }
    } catch (e: any) {
      toast('warning', `Cursor not saved: ${e.message}`);
    }
  };

  const updateHoverInfo = async (sample: number) => {
    const t = sample / waveformView.sampleRate;
    let rel = '';
    if (waveformView.cursorA !== null) {
      rel = `  ΔA ${fmtTime((sample - waveformView.cursorA) / waveformView.sampleRate)}`;
    }
    setHoverInfo(`#${sample}  ${fmtTime(t)}${rel}`);
  };

  // keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT'
        || (e.target as HTMLElement).tagName === 'TEXTAREA'
        || (e.target as HTMLElement).tagName === 'SELECT') return;
      const v = waveformView;
      switch (e.key) {
        case 'f': v.fit(); break;
        case 't': if (v.trigSample !== null) v.jumpTo(v.trigSample); break;
        case 'a': if (v.hoverSample !== null) placeCursor('a', Math.round(v.hoverSample)); break;
        case 'b': if (v.hoverSample !== null) placeCursor('b', Math.round(v.hoverSample)); break;
        case 'ArrowLeft': v.pan(-v.span() * 0.15); break;
        case 'ArrowRight': v.pan(v.span() * 0.15); break;
        case '+': case '=': v.zoomAround(v.start + v.span() / 2, 0.7); break;
        case '-': v.zoomAround(v.start + v.span() / 2, 1.4); break;
        case 'n': jumpAnnotation(1); break;
        case 'p': jumpAnnotation(-1); break;
        default: return;
      }
      e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [placeCursor]);

  const jumpAnnotation = (dir: 1 | -1) => {
    const v = waveformView;
    const centre = v.start + v.span() / 2;
    const evs = v.annotations;
    if (!evs.length) return;
    const next = dir > 0
      ? evs.find((ev) => ev.start_sample > centre + 1)
      : [...evs].reverse().find((ev) => ev.start_sample < centre - 1);
    if (next) v.jumpTo(next.start_sample);
  };

  const cursorReadout = () => {
    const v = waveformView;
    if (v.cursorA === null || v.cursorB === null) return null;
    const dt = Math.abs(v.cursorB - v.cursorA) / v.sampleRate;
    return (
      <span className="cursor-readout">
        |A−B| {fmtTime(dt)} ({Math.abs(v.cursorB - v.cursorA)} smp)
        {dt > 0 && <> · {fmtFreq(1 / dt)}</>}
      </span>
    );
  };

  return (
    <div className="waveform-wrap" ref={wrapRef}>
      <div className="waveform-toolbar">
        <button onClick={() => waveformView.fit()} title="Fit capture (f)">Fit</button>
        <button onClick={() => waveformView.zoomAround(waveformView.start + waveformView.span() / 2, 0.6)} title="Zoom in (+)">+</button>
        <button onClick={() => waveformView.zoomAround(waveformView.start + waveformView.span() / 2, 1.6)} title="Zoom out (-)">−</button>
        {waveformView.trigSample !== null && (
          <button onClick={() => waveformView.jumpTo(waveformView.trigSample!)} title="Jump to trigger (t)">⭢T</button>
        )}
        {waveformView.cursorA !== null && (
          <button onClick={() => waveformView.jumpTo(waveformView.cursorA!)}>⭢A</button>
        )}
        {waveformView.cursorB !== null && (
          <button onClick={() => waveformView.jumpTo(waveformView.cursorB!)}>⭢B</button>
        )}
        <button onClick={() => jumpAnnotation(-1)} title="Previous event (p)">⟨ ev</button>
        <button onClick={() => jumpAnnotation(1)} title="Next event (n)">ev ⟩</button>
        <span className="hover-info">{hoverInfo}</span>
        {cursorReadout()}
        {waveformView.error && <span className="wave-error">{waveformView.error}</span>}
      </div>
      <Minimap />
      <div className="waveform-scroll">
        <canvas
          ref={canvasRef}
          className="waveform-canvas"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
          onDoubleClick={onDoubleClick}
        />
      </div>
    </div>
  );
}
