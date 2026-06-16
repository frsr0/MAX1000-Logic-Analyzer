// Canvas waveform renderer. Pure drawing — reads the WaveformView (data +
// viewport) and a channel layout, paints one frame. No React, no allocation
// of per-sample objects; TypedArrays are indexed directly.

import type { ChannelInfo } from '../api/types';
import type { WaveformView } from '../state/waveformStore';

export interface RowLayout {
  channel: ChannelInfo;
  y: number;
  height: number;
}

export interface RenderLayout {
  axisHeight: number;
  annotRows: string[];      // decoder instance ids
  annotHeight: number;
  rows: RowLayout[];
  totalHeight: number;
  labelWidth: number;
}

export const COLORS = {
  bg: '#0d0f13',
  grid: '#1c212b',
  axis: '#9fb2cc',
  label: '#aebdd4',
  labelBg: '#12151c',
  cursorA: '#ffd54f',
  cursorB: '#4dd0e1',
  trigger: '#ef5350',
  marker: '#ba68c8',
  selection: 'rgba(79,195,247,0.13)',
  annotNormal: '#2e4a66',
  annotWarning: '#7a5a22',
  annotError: '#7a2e2e',
  density: '#3d6a8f',
};

const DIGITAL_ROW = 30;
const ANALOG_ROW = 96;
const ANNOT_ROW = 22;

export function buildLayout(channels: ChannelInfo[], view: WaveformView,
                            labelWidth: number): RenderLayout {
  const axisHeight = 24;
  const annotRows = view.decoderRows();
  const annotHeight = annotRows.length * ANNOT_ROW;
  let y = axisHeight + annotHeight + 4;
  const rows: RowLayout[] = [];
  for (const ch of channels) {
    if (!ch.enabled) continue;
    const height = ch.type === 'analog' ? ANALOG_ROW : DIGITAL_ROW;
    rows.push({ channel: ch, y, height });
    y += height + 4;
  }
  return { axisHeight, annotRows, annotHeight, rows, totalHeight: y + 8, labelWidth };
}

export function sampleToX(view: WaveformView, layout: RenderLayout,
                          width: number, sample: number): number {
  const span = view.span() || 1;
  return layout.labelWidth +
    ((sample - view.start) / span) * (width - layout.labelWidth);
}

export function xToSample(view: WaveformView, layout: RenderLayout,
                          width: number, x: number): number {
  const span = view.span() || 1;
  return view.start +
    ((x - layout.labelWidth) / Math.max(1, width - layout.labelWidth)) * span;
}

export function fmtTime(t: number): string {
  const a = Math.abs(t);
  if (a >= 1) return `${t.toFixed(3)} s`;
  if (a >= 1e-3) return `${(t * 1e3).toFixed(3)} ms`;
  if (a >= 1e-6) return `${(t * 1e6).toFixed(3)} µs`;
  return `${(t * 1e9).toFixed(1)} ns`;
}

export function fmtFreq(f: number): string {
  if (f >= 1e6) return `${(f / 1e6).toFixed(4)} MHz`;
  if (f >= 1e3) return `${(f / 1e3).toFixed(4)} kHz`;
  return `${f.toFixed(2)} Hz`;
}

export function render(ctx: CanvasRenderingContext2D, view: WaveformView,
                       layout: RenderLayout, width: number, height: number) {
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, width, height);
  drawTimeAxis(ctx, view, layout, width);
  drawSelection(ctx, view, layout, width, height);
  for (const row of layout.rows) {
    drawRow(ctx, view, layout, row, width);
  }
  drawAnnotations(ctx, view, layout, width);
  drawCursorsAndMarkers(ctx, view, layout, width, height);
  drawLabels(ctx, view, layout, width);
  if (view.loading) {
    ctx.fillStyle = 'rgba(140,170,210,0.8)';
    ctx.font = '11px system-ui';
    ctx.fillText('loading…', width - 64, 14);
  }
}

function drawTimeAxis(ctx: CanvasRenderingContext2D, view: WaveformView,
                      layout: RenderLayout, width: number) {
  const plotW = width - layout.labelWidth;
  const spanT = view.span() / view.sampleRate;
  const target = plotW / 110;
  const rawStep = spanT / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep || 1e-9)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10) * mag;
  const t0 = view.start / view.sampleRate;
  const first = Math.ceil(t0 / step) * step;
  ctx.font = '10px system-ui';
  ctx.strokeStyle = COLORS.grid;
  ctx.fillStyle = COLORS.axis;
  ctx.beginPath();
  for (let t = first; t <= t0 + spanT; t += step) {
    const x = sampleToX(view, layout, width, t * view.sampleRate);
    if (x < layout.labelWidth) continue;
    ctx.moveTo(x, layout.axisHeight);
    ctx.lineTo(x, ctx.canvas.height);
    ctx.fillText(fmtTime(t), x + 3, 14);
  }
  ctx.stroke();
}

function drawSelection(ctx: CanvasRenderingContext2D, view: WaveformView,
                       layout: RenderLayout, width: number, height: number) {
  if (view.selectionStart === null || view.selectionEnd === null) return;
  const x0 = sampleToX(view, layout, width, Math.min(view.selectionStart, view.selectionEnd));
  const x1 = sampleToX(view, layout, width, Math.max(view.selectionStart, view.selectionEnd));
  ctx.fillStyle = COLORS.selection;
  ctx.fillRect(x0, layout.axisHeight, x1 - x0, height - layout.axisHeight);
}

function drawRow(ctx: CanvasRenderingContext2D, view: WaveformView,
                 layout: RenderLayout, row: RowLayout, width: number) {
  const ch = row.channel;
  ctx.strokeStyle = COLORS.grid;
  ctx.strokeRect(layout.labelWidth, row.y, width - layout.labelWidth, row.height);
  if (!view.payload) return;
  if (ch.type === 'analog') drawAnalogRow(ctx, view, layout, row, width);
  else if (ch.type === 'bus') drawBusRow(ctx, view, layout, row, width);
  else drawDigitalRow(ctx, view, layout, row, width);
}

function digitalBit(arr: Uint16Array, i: number, bit: number): number {
  return (arr[i] >> bit) & 1;
}

function drawDigitalRow(ctx: CanvasRenderingContext2D, view: WaveformView,
                        layout: RenderLayout, row: RowLayout, width: number) {
  const p = view.payload!;
  const h = p.header;
  const ch = row.channel;
  const color = ch.color ?? '#4fc3f7';
  const yHi = row.y + 4;
  const yLo = row.y + row.height - 4;
  const isDerived = ch.type === 'derived';
  const bit = isDerived ? 0 : parseInt(ch.id.slice(1), 10);

  ctx.strokeStyle = color;
  ctx.lineWidth = 1.4;

  if (h.mode === 'raw') {
    const arr = isDerived
      ? (p.arrays.get(`derived:${ch.id}`) as Uint8Array | undefined)
      : (p.arrays.get('digital') as Uint16Array | undefined);
    if (!arr) return;
    ctx.beginPath();
    let prevY: number | null = null;
    const n = arr.length;
    for (let i = 0; i < n; i++) {
      const sample = h.start + i;
      const x = sampleToX(view, layout, width, sample);
      if (x > width + 2) break;
      const v = isDerived ? (arr as Uint8Array)[i]
        : digitalBit(arr as Uint16Array, i, bit);
      const y = v ? yHi : yLo;
      if (prevY === null) ctx.moveTo(x, y);
      else {
        if (y !== prevY) { ctx.lineTo(x, prevY); ctx.lineTo(x, y); }
      }
      prevY = y;
    }
    const xEnd = sampleToX(view, layout, width, h.end);
    if (prevY !== null) ctx.lineTo(Math.min(xEnd, width), prevY);
    ctx.stroke();
    return;
  }

  // LOD mode: and/or masks per bin (+ optional edge density)
  const andArr = (isDerived ? p.arrays.get(`derived_and:${ch.id}`)
    : p.arrays.get('digital_and')) as Uint16Array | undefined;
  const orArr = (isDerived ? p.arrays.get(`derived_or:${ch.id}`)
    : p.arrays.get('digital_or')) as Uint16Array | undefined;
  if (!andArr || !orArr) return;
  const edges = !isDerived ? p.arrays.get('digital_edges') as Uint32Array | undefined : undefined;
  const nCh = h.edges_channels ?? 16;
  const binStart = h.bin_start ?? h.start;
  const spb = h.samples_per_bin;
  const bins = andArr.length;
  let maxEdges = 1;
  if (edges) {
    for (let i = 0; i < bins; i++) {
      const e = edges[i * nCh + bit];
      if (e > maxEdges) maxEdges = e;
    }
  }
  ctx.beginPath();
  const blocks: { x0: number; x1: number; alpha: number }[] = [];
  let prevY: number | null = null;
  for (let i = 0; i < bins; i++) {
    const s0 = binStart + i * spb;
    const x0 = sampleToX(view, layout, width, s0);
    const x1 = sampleToX(view, layout, width, s0 + spb);
    if (x1 < layout.labelWidth || x0 > width) { prevY = null; continue; }
    const a = isDerived ? andArr[i] & 1 : digitalBit(andArr, i, bit);
    const o = isDerived ? orArr[i] & 1 : digitalBit(orArr, i, bit);
    if (a === o) {
      const y = a ? yHi : yLo;
      if (prevY === null) ctx.moveTo(Math.max(x0, layout.labelWidth), y);
      else if (y !== prevY) { ctx.lineTo(x0, prevY); ctx.lineTo(x0, y); }
      ctx.lineTo(Math.min(x1, width), y);
      prevY = y;
    } else {
      const e = edges ? edges[i * nCh + bit] : maxEdges;
      blocks.push({ x0: Math.max(x0, layout.labelWidth), x1: Math.min(x1, width),
        alpha: 0.35 + 0.6 * Math.min(1, e / maxEdges) });
      prevY = null;
    }
  }
  ctx.stroke();
  // transition-density blocks
  for (const b of blocks) {
    ctx.globalAlpha = b.alpha;
    ctx.fillStyle = color;
    ctx.fillRect(b.x0, yHi, Math.max(1, b.x1 - b.x0), yLo - yHi);
  }
  ctx.globalAlpha = 1;
}

function drawAnalogRow(ctx: CanvasRenderingContext2D, view: WaveformView,
                       layout: RenderLayout, row: RowLayout, width: number) {
  const p = view.payload!;
  const h = p.header;
  const ch = row.channel;
  const color = ch.color ?? '#ffd54f';
  const mid = row.y + row.height / 2;
  const pxPerVolt = (row.height / 8) / Math.max(1e-9, ch.volts_per_div);
  const toY = (v: number) => {
    const y = mid - (v * ch.cal_gain + ch.cal_offset - ch.offset) * pxPerVolt;
    return Math.max(row.y + 1, Math.min(row.y + row.height - 1, y));
  };
  // threshold line
  ctx.strokeStyle = 'rgba(170,180,200,0.25)';
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  const ty = toY(ch.threshold);
  ctx.moveTo(layout.labelWidth, ty);
  ctx.lineTo(width, ty);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  ctx.lineWidth = 1.2;
  if (h.mode === 'raw') {
    const arr = p.arrays.get(`analog:${ch.id}`) as Float32Array | undefined;
    if (!arr) return;
    ctx.beginPath();
    for (let i = 0; i < arr.length; i++) {
      const x = sampleToX(view, layout, width, h.start + i);
      if (x > width) break;
      const y = toY(arr[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  } else {
    const vmin = p.arrays.get(`analog_min:${ch.id}`) as Float32Array | undefined;
    const vmax = p.arrays.get(`analog_max:${ch.id}`) as Float32Array | undefined;
    if (!vmin || !vmax) return;
    const binStart = h.bin_start ?? h.start;
    const spb = h.samples_per_bin;
    ctx.globalAlpha = 0.85;
    for (let i = 0; i < vmin.length; i++) {
      const x0 = sampleToX(view, layout, width, binStart + i * spb);
      const x1 = sampleToX(view, layout, width, binStart + (i + 1) * spb);
      if (x1 < layout.labelWidth || x0 > width) continue;
      const y0 = toY(vmax[i]);
      const y1 = toY(vmin[i]);
      ctx.fillRect(Math.max(x0, layout.labelWidth), y0,
        Math.max(1, x1 - x0), Math.max(1, y1 - y0));
    }
    ctx.globalAlpha = 1;
  }
}

function drawBusRow(ctx: CanvasRenderingContext2D, view: WaveformView,
                    layout: RenderLayout, row: RowLayout, width: number) {
  const p = view.payload!;
  const h = p.header;
  const ch = row.channel;
  if (h.mode !== 'raw') {
    ctx.fillStyle = 'rgba(170,180,200,0.4)';
    ctx.font = '10px system-ui';
    ctx.fillText('zoom in for bus values', layout.labelWidth + 8, row.y + row.height / 2 + 3);
    return;
  }
  const arr = p.arrays.get('digital') as Uint16Array | undefined;
  if (!arr) return;
  const bits = ch.members.map((m) => parseInt(m.slice(1), 10));
  const val = (i: number) => {
    let v = 0;
    for (let b = 0; b < bits.length; b++) v |= ((arr[i] >> bits[b]) & 1) << b;
    return v;
  };
  const fmt = (v: number) => {
    switch (ch.display_base) {
      case 'bin': return v.toString(2).padStart(bits.length, '0');
      case 'dec': return String(v);
      case 'ascii': return v >= 32 && v < 127 ? String.fromCharCode(v) : `<${v.toString(16)}>`;
      default: return '0x' + v.toString(16).toUpperCase();
    }
  };
  const yTop = row.y + 5;
  const yBot = row.y + row.height - 5;
  const yMid = (yTop + yBot) / 2;
  ctx.strokeStyle = ch.color ?? '#aed581';
  ctx.font = '10px ui-monospace, monospace';
  let segStart = 0;
  let cur = val(0);
  ctx.beginPath();
  for (let i = 1; i <= arr.length; i++) {
    const v = i < arr.length ? val(i) : -1;
    if (v !== cur) {
      const x0 = sampleToX(view, layout, width, h.start + segStart);
      const x1 = sampleToX(view, layout, width, h.start + i);
      // hexagon segment
      const cx0 = Math.max(x0, layout.labelWidth);
      ctx.moveTo(cx0, yMid); ctx.lineTo(cx0 + 3, yTop); ctx.lineTo(x1 - 3, yTop);
      ctx.lineTo(x1, yMid); ctx.lineTo(x1 - 3, yBot); ctx.lineTo(cx0 + 3, yBot);
      ctx.closePath();
      if (x1 - x0 > 24) {
        ctx.fillStyle = '#cfe0f5';
        ctx.fillText(fmt(cur), cx0 + 6, yMid + 3);
      }
      segStart = i;
      cur = v;
    }
  }
  ctx.stroke();
}

function drawAnnotations(ctx: CanvasRenderingContext2D, view: WaveformView,
                         layout: RenderLayout, width: number) {
  if (!layout.annotRows.length) return;
  const rowOf = new Map(layout.annotRows.map((id, i) => [id, i]));
  ctx.font = '10px ui-monospace, monospace';
  for (const ev of view.annotations) {
    const ri = rowOf.get(ev.decoder_id);
    if (ri === undefined) continue;
    const y = layout.axisHeight + ri * 22 + 2;
    const x0 = sampleToX(view, layout, width, ev.start_sample);
    const x1 = Math.max(x0 + 2, sampleToX(view, layout, width, ev.end_sample));
    if (x1 < layout.labelWidth || x0 > width) continue;
    ctx.fillStyle = ev.severity === 'error' ? COLORS.annotError
      : ev.severity === 'warning' ? COLORS.annotWarning : COLORS.annotNormal;
    const cx0 = Math.max(x0, layout.labelWidth);
    ctx.beginPath();
    ctx.roundRect(cx0, y, Math.max(2, Math.min(x1, width) - cx0), 17, 3);
    ctx.fill();
    if (x1 - x0 > 28) {
      ctx.fillStyle = '#dde7f5';
      const maxChars = Math.floor((x1 - cx0 - 8) / 6);
      const label = ev.label.length > maxChars
        ? ev.label.slice(0, Math.max(1, maxChars - 1)) + '…' : ev.label;
      ctx.fillText(label, cx0 + 4, y + 12);
    }
  }
}

function drawCursorsAndMarkers(ctx: CanvasRenderingContext2D, view: WaveformView,
                               layout: RenderLayout, width: number, height: number) {
  const vline = (sample: number, color: string, tag: string) => {
    const x = sampleToX(view, layout, width, sample);
    if (x < layout.labelWidth || x > width) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, layout.axisHeight - 6);
    ctx.lineTo(x, height);
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fillRect(x - 7, layout.axisHeight - 18, 14, 13);
    ctx.fillStyle = '#10131a';
    ctx.font = 'bold 9px system-ui';
    ctx.fillText(tag, x - 3, layout.axisHeight - 8);
  };
  if (view.trigSample !== null) vline(view.trigSample, COLORS.trigger, 'T');
  for (const m of view.markers) {
    if (m.kind === 'cursor_a' || m.kind === 'cursor_b') continue;
    vline(m.sample, m.kind === 'error' || m.kind === 'glitch'
      ? COLORS.trigger : (m.color ?? COLORS.marker), 'M');
  }
  if (view.cursorA !== null) vline(view.cursorA, COLORS.cursorA, 'A');
  if (view.cursorB !== null) vline(view.cursorB, COLORS.cursorB, 'B');
  if (view.hoverSample !== null) {
    const x = sampleToX(view, layout, width, view.hoverSample);
    if (x >= layout.labelWidth) {
      ctx.strokeStyle = 'rgba(170,190,220,0.35)';
      ctx.beginPath();
      ctx.moveTo(x, layout.axisHeight);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
  }
}

function drawLabels(ctx: CanvasRenderingContext2D, view: WaveformView,
                    layout: RenderLayout, width: number) {
  ctx.fillStyle = COLORS.labelBg;
  ctx.fillRect(0, 0, layout.labelWidth, ctx.canvas.height);
  ctx.strokeStyle = COLORS.grid;
  ctx.beginPath();
  ctx.moveTo(layout.labelWidth - 0.5, 0);
  ctx.lineTo(layout.labelWidth - 0.5, ctx.canvas.height);
  ctx.stroke();
  ctx.font = '11px system-ui';
  for (const row of layout.rows) {
    const ch = row.channel;
    ctx.fillStyle = ch.color ?? COLORS.label;
    const icon = ch.type === 'analog' ? '∿' : ch.type === 'derived' ? 'ƒ'
      : ch.type === 'bus' ? '⛁' : '⎍';
    ctx.fillText(`${icon} ${ch.name}`, 8, row.y + 14);
    if (ch.type === 'analog') {
      ctx.fillStyle = 'rgba(170,180,200,0.6)';
      ctx.font = '9px system-ui';
      ctx.fillText(`${ch.volts_per_div} V/div`, 8, row.y + 27);
      ctx.font = '11px system-ui';
    }
  }
  // decoder row labels
  ctx.font = '10px system-ui';
  layout.annotRows.forEach((id, i) => {
    ctx.fillStyle = '#7fa3c8';
    ctx.fillText(`dec ${i + 1}`, 8, layout.axisHeight + i * 22 + 14);
  });
}
