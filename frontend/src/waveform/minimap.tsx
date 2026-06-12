// Whole-capture overview bar with draggable viewport window.
import { useEffect, useRef } from 'react';
import { waveformView } from '../state/waveformStore';

const H = 36;

export function Minimap() {
  const ref = useRef<HTMLCanvasElement>(null);
  const dragging = useRef(false);

  useEffect(() => {
    const draw = () => {
      const canvas = ref.current;
      if (!canvas) return;
      const dpr = window.devicePixelRatio || 1;
      const w = canvas.parentElement?.clientWidth ?? 600;
      canvas.width = w * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${H}px`;
      const ctx = canvas.getContext('2d')!;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = '#10131a';
      ctx.fillRect(0, 0, w, H);
      const ov = waveformView.overview;
      const n = waveformView.numSamples || 1;
      if (ov) {
        const act = ov.arrays.get('activity') as Uint32Array | undefined;
        if (act && act.length) {
          let max = 1;
          for (let i = 0; i < act.length; i++) if (act[i] > max) max = act[i];
          ctx.fillStyle = '#3d6a8f';
          const bw = w / act.length;
          for (let i = 0; i < act.length; i++) {
            const h = Math.max(1, (act[i] / max) * (H - 6));
            ctx.fillRect(i * bw, H - 3 - h, Math.max(1, bw), h);
          }
        }
        // first analog channel silhouette
        for (const [name, arr] of ov.arrays) {
          if (!name.startsWith('analog_min')) continue;
          const vmin = arr as Float32Array;
          const vmax = ov.arrays.get(name.replace('min', 'max')) as Float32Array;
          let lo = Infinity, hi = -Infinity;
          for (let i = 0; i < vmin.length; i++) {
            if (vmin[i] < lo) lo = vmin[i];
            if (vmax[i] > hi) hi = vmax[i];
          }
          const rng = hi - lo || 1;
          ctx.fillStyle = 'rgba(255,213,79,0.45)';
          const bw = w / vmin.length;
          for (let i = 0; i < vmin.length; i++) {
            const y0 = 3 + (1 - (vmax[i] - lo) / rng) * (H - 6);
            const y1 = 3 + (1 - (vmin[i] - lo) / rng) * (H - 6);
            ctx.fillRect(i * bw, y0, Math.max(1, bw), Math.max(1, y1 - y0));
          }
          break;
        }
      }
      // viewport window
      const x0 = (waveformView.start / n) * w;
      const x1 = (waveformView.end / n) * w;
      ctx.fillStyle = 'rgba(120,160,210,0.18)';
      ctx.fillRect(x0, 0, Math.max(3, x1 - x0), H);
      ctx.strokeStyle = '#7fa3c8';
      ctx.strokeRect(x0 + 0.5, 0.5, Math.max(3, x1 - x0) - 1, H - 1);
      // trigger tick
      if (waveformView.trigSample !== null) {
        const tx = (waveformView.trigSample / n) * w;
        ctx.strokeStyle = '#ef5350';
        ctx.beginPath(); ctx.moveTo(tx, 0); ctx.lineTo(tx, H); ctx.stroke();
      }
    };
    const unsub = waveformView.subscribe(draw);
    const ro = new ResizeObserver(draw);
    if (ref.current?.parentElement) ro.observe(ref.current.parentElement);
    draw();
    return () => { unsub(); ro.disconnect(); };
  }, []);

  const seek = (e: React.PointerEvent) => {
    const rect = (e.target as HTMLElement).getBoundingClientRect();
    const frac = (e.clientX - rect.left) / rect.width;
    const span = waveformView.span();
    const centre = frac * waveformView.numSamples;
    waveformView.setView(centre - span / 2, centre + span / 2);
  };

  return (
    <canvas
      ref={ref}
      className="minimap"
      onPointerDown={(e) => {
        dragging.current = true;
        (e.target as Element).setPointerCapture(e.pointerId);
        seek(e);
      }}
      onPointerMove={(e) => dragging.current && seek(e)}
      onPointerUp={() => { dragging.current = false; }}
    />
  );
}
