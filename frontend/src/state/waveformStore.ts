// Waveform view state + data cache. Deliberately NOT React state: sample
// arrays are big TypedArrays and view changes happen at animation rate.
// React components subscribe to coarse change events for labels only.

import { api } from '../api/client';
import type { WaveformPayload } from '../api/binary';
import type { DecoderEvent, Marker } from '../api/types';

export type ViewListener = () => void;

const MAX_DECODER_ROWS = 6;

export class WaveformView {
  sessionId = '';
  numSamples = 0;
  sampleRate = 1;
  trigSample: number | null = null;

  // visible window in fractional sample units
  start = 0;
  end = 1;

  cursorA: number | null = null;
  cursorB: number | null = null;
  hoverSample: number | null = null;
  hoverY = 0;
  selectionStart: number | null = null;
  selectionEnd: number | null = null;

  payload: WaveformPayload | null = null;
  overview: WaveformPayload | null = null;
  annotations: DecoderEvent[] = [];
  markers: Marker[] = [];

  loading = false;
  error: string | null = null;

  private listeners = new Set<ViewListener>();
  private fetchTimer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;
  private annotTimer: ReturnType<typeof setTimeout> | null = null;
  private channelFilter: string[] | undefined;
  decodersVersion = 0; // bump to refetch annotations

  subscribe(l: ViewListener): () => void {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  notify() {
    this.listeners.forEach((l) => l());
  }

  async load(sessionId: string, numSamples: number, sampleRate: number,
             trigSample: number | null) {
    this.sessionId = sessionId;
    this.numSamples = numSamples;
    this.sampleRate = sampleRate;
    this.trigSample = trigSample;
    this.start = 0;
    this.end = Math.max(1, numSamples);
    this.payload = null;
    this.overview = null;
    this.annotations = [];
    this.cursorA = this.cursorB = null;
    this.selectionStart = this.selectionEnd = null;
    this.error = null;
    this.notify();
    if (!sessionId || !numSamples) return;
    try {
      this.overview = await api.overview(sessionId);
    } catch (e: any) {
      this.error = String(e.message ?? e);
    }
    this.requestFetch(0);
    this.requestAnnotations();
    this.notify();
  }

  setChannelFilter(channels: string[] | undefined) {
    this.channelFilter = channels;
    this.requestFetch(0);
  }

  span(): number {
    return this.end - this.start;
  }

  clampView() {
    const minSpan = 8;
    const maxSpan = Math.max(minSpan, this.numSamples);
    let span = Math.min(Math.max(this.span(), minSpan), maxSpan);
    if (this.start < 0) this.start = 0;
    if (this.start + span > this.numSamples) this.start = Math.max(0, this.numSamples - span);
    this.end = this.start + span;
  }

  setView(start: number, end: number) {
    this.start = start;
    this.end = end;
    this.clampView();
    this.requestFetch();
    this.requestAnnotations();
    this.notify();
  }

  zoomAround(sample: number, factor: number) {
    const span = this.span() * factor;
    const frac = (sample - this.start) / this.span();
    this.setView(sample - span * frac, sample - span * frac + span);
  }

  pan(deltaSamples: number) {
    this.setView(this.start + deltaSamples, this.end + deltaSamples);
  }

  fit() {
    this.setView(0, this.numSamples);
  }

  jumpTo(sample: number) {
    const span = this.span();
    this.setView(sample - span / 2, sample + span / 2);
  }

  // ── data fetching ────────────────────────────────────────────────

  requestFetch(debounceMs = 60) {
    if (!this.sessionId) return;
    if (this.fetchTimer) clearTimeout(this.fetchTimer);
    this.fetchTimer = setTimeout(() => this.doFetch(), debounceMs);
  }

  private async doFetch() {
    if (!this.sessionId || !this.numSamples) return;
    this.abort?.abort();
    const ctl = new AbortController();
    this.abort = ctl;
    this.loading = true;
    this.notify();
    // request ~2 bins per CSS pixel, capped to the server max
    const res = Math.min(4096, Math.max(512,
      Math.ceil((window.innerWidth || 1200) * 1.5)));
    try {
      const p = await api.waveformWindow(
        this.sessionId, Math.floor(this.start), Math.ceil(this.end), res,
        this.channelFilter, ctl.signal);
      if (!ctl.signal.aborted) {
        this.payload = p;
        this.error = null;
      }
    } catch (e: any) {
      if (e.name !== 'AbortError' && !ctl.signal.aborted) {
        this.error = String(e.message ?? e);
      }
    } finally {
      if (this.abort === ctl) {
        this.loading = false;
        this.notify();
      }
    }
  }

  requestAnnotations(debounceMs = 120) {
    if (this.annotTimer) clearTimeout(this.annotTimer);
    this.annotTimer = setTimeout(() => this.doFetchAnnotations(), debounceMs);
  }

  private async doFetchAnnotations() {
    if (!this.sessionId) return;
    try {
      const res = await fetch(
        `/api/sessions/${this.sessionId}/decoder-events?start=${Math.floor(this.start)}&end=${Math.ceil(this.end)}&limit=3000`);
      if (res.ok) {
        const j = await res.json();
        this.annotations = j.events as DecoderEvent[];
        this.notify();
      }
    } catch { /* annotations are best-effort */ }
  }

  async refreshMarkers() {
    if (!this.sessionId) return;
    try {
      const r = await api.markers(this.sessionId);
      this.markers = r.markers;
      const a = this.markers.find((m) => m.kind === 'cursor_a');
      const b = this.markers.find((m) => m.kind === 'cursor_b');
      if (a) this.cursorA = a.sample;
      if (b) this.cursorB = b.sample;
      this.notify();
    } catch { /* ignore */ }
  }

  decoderRows(): string[] {
    const ids: string[] = [];
    for (const ev of this.annotations) {
      if (!ids.includes(ev.decoder_id)) ids.push(ev.decoder_id);
      if (ids.length >= MAX_DECODER_ROWS) break;
    }
    return ids;
  }

  // edge navigation helpers
  async jumpToEdge(channel: string, fromSample: number, direction: 1 | -1) {
    const kind = 'any';
    const start = direction > 0 ? Math.floor(fromSample) + 1 : 0;
    const end = direction > 0 ? -1 : Math.floor(fromSample);
    try {
      const r = await api.edges(this.sessionId, channel, kind, start, end, 50000);
      if (!r.edges.length) return null;
      const target = direction > 0 ? r.edges[0] : r.edges[r.edges.length - 1];
      this.jumpTo(target);
      return target;
    } catch {
      return null;
    }
  }
}

export const waveformView = new WaveformView();
