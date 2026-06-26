// Waveform view state + data cache. Deliberately NOT React state: sample
// arrays are big TypedArrays and view changes happen at animation rate.
// React components subscribe to coarse change events for labels only.

import { api } from '../api/client';
import type { WaveformPayload } from '../api/binary';
import type { ChannelInfo, DecoderEvent, Marker } from '../api/types';

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

  // per-channel vertical height multiplier (1 = default row height)
  heightScale = new Map<string, number>();
  // channel ids selected in the label gutter for group height edits
  selectedRows = new Set<string>();

  payload: WaveformPayload | null = null;
  overview: WaveformPayload | null = null;
  annotations: DecoderEvent[] = [];
  markers: Marker[] = [];

  loading = false;
  error: string | null = null;
  liveFollow = true;
  liveRolling = false;
  liveChunkSamples = 0;
  liveUpdatedAt = 0;

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
             trigSample: number | null, channels?: ChannelInfo[]) {
    this.sessionId = sessionId;
    this.numSamples = numSamples;
    this.sampleRate = sampleRate;
    this.trigSample = trigSample;
    this.start = 0;
    this.end = Math.max(1, numSamples);
    this.payload = null;
    this.overview = null;
    this.annotations = [];
    this.liveRolling = false;
    this.liveChunkSamples = 0;
    this.liveUpdatedAt = 0;
    this.cursorA = this.cursorB = null;
    this.selectionStart = this.selectionEnd = null;
    this.heightScale.clear();
    this.selectedRows.clear();
    for (const ch of channels ?? []) {
      const sc = ch.display_height_scale ?? 1;
      if (sc !== 1) this.heightScale.set(ch.id, sc);
    }
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

  async updateLive(numSamples: number, sampleRate: number,
                   trigSample: number | null, followEnd = true,
                   chunkSamples = 0) {
    const oldSpan = this.span();
    const oldEnd = this.end;
    const oldSamples = this.numSamples;
    this.numSamples = numSamples;
    this.sampleRate = sampleRate;
    this.trigSample = trigSample;
    this.liveRolling = followEnd;
    this.liveChunkSamples = Math.max(0, Math.min(numSamples, chunkSamples));
    this.liveUpdatedAt = performance.now();
    this.overview = null;
    if (!followEnd) this.payload = null;
    this.error = null;
    if (followEnd || oldEnd >= Math.max(0, oldSamples - oldSpan * 0.05)) {
      const span = Math.min(Math.max(oldSpan, 8), Math.max(8, numSamples));
      this.start = Math.max(0, numSamples - span);
      this.end = Math.max(1, numSamples);
    } else {
      this.clampView();
    }
    try {
      this.overview = await api.overview(this.sessionId);
    } catch (e: any) {
      this.error = String(e.message ?? e);
    }
    this.requestFetch(0);
    this.requestAnnotations();
    this.notify();
  }

  setLiveFollow(enabled: boolean) {
    this.liveFollow = enabled;
    this.notify();
  }

  liveShiftSamples(now = performance.now()): number {
    if (!this.liveRolling || !this.liveFollow || !this.liveChunkSamples) return 0;
    const elapsedSamples = ((now - this.liveUpdatedAt) / 1000) * this.sampleRate;
    return Math.max(0, Math.min(this.liveChunkSamples, elapsedSamples));
  }

  displayStart(): number {
    return this.start + this.liveShiftSamples();
  }

  displayEnd(): number {
    return this.end + this.liveShiftSamples();
  }

  liveAnimating(): boolean {
    return this.liveRolling && this.liveFollow && this.liveShiftSamples() < this.liveChunkSamples;
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

  // ── row height / selection ───────────────────────────────────────

  static MIN_SCALE = 0.5;
  static MAX_SCALE = 8;

  rowScale(id: string): number {
    return this.heightScale.get(id) ?? 1;
  }

  private clampScale(s: number): number {
    return Math.max(WaveformView.MIN_SCALE, Math.min(WaveformView.MAX_SCALE, s));
  }

  // set an explicit scale for one or more rows
  setRowScales(ids: Iterable<string>, scale: number) {
    const s = this.clampScale(scale);
    for (const id of ids) this.heightScale.set(id, s);
    this.notify();
  }

  // multiply the current scale of each row by a factor (proportional stretch)
  scaleRowsBy(ids: Iterable<string>, factor: number) {
    for (const id of ids) {
      this.heightScale.set(id, this.clampScale(this.rowScale(id) * factor));
    }
    this.notify();
  }

  // snap every visible row to a preset scale (1 = default, 2 = 2×, …)
  setAllScales(scale: number) {
    const s = this.clampScale(scale);
    this.heightScale.clear();
    if (s !== 1) {
      // record the preset against currently-known rows so it persists; an
      // empty map already means "all default", so only populate when needed
      for (const id of this.knownRowIds) this.heightScale.set(id, s);
    }
    this.notify();
  }

  // ids the renderer last laid out — lets presets address all rows
  knownRowIds: string[] = [];

  selectRow(id: string, additive: boolean) {
    if (additive) {
      if (this.selectedRows.has(id)) this.selectedRows.delete(id);
      else this.selectedRows.add(id);
    } else {
      const only = this.selectedRows.size === 1 && this.selectedRows.has(id);
      this.selectedRows.clear();
      if (!only) this.selectedRows.add(id);
    }
    this.notify();
  }

  clearRowSelection() {
    if (!this.selectedRows.size) return;
    this.selectedRows.clear();
    this.notify();
  }

  // persist current row heights to the session record (best-effort)
  async commitRowHeights(ids: Iterable<string>) {
    if (!this.sessionId) return;
    const channels = [...ids].map((id) => ({
      id, display_height_scale: this.rowScale(id),
    }));
    if (!channels.length) return;
    try {
      await api.patchSession(this.sessionId, { channels });
    } catch { /* display preference, best-effort */ }
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
        if (this.liveRolling && this.liveFollow) {
          this.liveUpdatedAt = performance.now();
        }
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
