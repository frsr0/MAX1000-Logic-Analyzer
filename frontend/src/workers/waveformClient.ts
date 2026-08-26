/**
 * Main-thread wrapper for the waveform Web Worker.
 *
 * Provides typed async methods that return a WaveformPayload (same shape as
 * `parseWaveformPayload` in binary.ts), but the fetch + parse runs off the
 * main thread and the ArrayBuffer is transferred back zero-copy.
 */

import { parseWaveformPayload } from '../api/binary';
import type { WaveformHeader, WaveformPayload } from '../api/binary';

type PendingResolve = (value: WaveformPayload) => void;
type PendingReject = (reason: unknown) => void;

export class WaveformClient {
  private worker: Worker | null = null;
  private pending: Map<string, { resolve: PendingResolve; reject: PendingReject }> = new Map();
  private nextId = 0;

  private getWorker(): Worker {
    if (!this.worker) {
      this.worker = new Worker(
        new URL('./waveform.worker.ts', import.meta.url),
        { type: 'module' },
      );
      this.worker.onmessage = (e: MessageEvent) => this.handleMessage(e);
      this.worker.onerror = (e: ErrorEvent) => this.handleError(e);
    }
    return this.worker;
  }

  /**
   * Fetch a waveform window (viewport). Returns a WaveformPayload with
   * TypedArray views backed by the transferred ArrayBuffer.
   */
  async fetchWindow(sessionId: string, start: number, end: number,
                    resolution: number, channels?: string[]): Promise<WaveformPayload> {
    return this.send({
      type: 'window',
      sessionId,
      start,
      end,
      resolution: Math.min(4096, Math.max(512, resolution)),
      channels,
    });
  }

  /**
   * Fetch the waveform overview. Returns a parsed WaveformPayload.
   */
  async fetchOverview(sessionId: string, bins: number = 1024): Promise<WaveformPayload> {
    return this.send({ type: 'overview', sessionId, bins });
  }

  /** Terminate the worker (call when waveform view is destroyed). */
  dispose(): void {
    if (this.worker) {
      this.worker.terminate();
      this.worker = null;
    }
    for (const [, { reject }] of this.pending) {
      reject(new Error('WaveformClient disposed'));
    }
    this.pending.clear();
  }

  // ── private ──────────────────────────────────────────────────────

  private send(request: Record<string, unknown>): Promise<WaveformPayload> {
    return new Promise<WaveformPayload>((resolve, reject) => {
      const id = String(this.nextId++);
      this.pending.set(id, { resolve, reject });
      // The worker echoes this id back so concurrent window/overview
      // requests resolve their own promises — live mode keeps both in flight
      // at once, and resolving FIFO handed the overview payload to the
      // window fetch (canvas rendered the overview as a viewport, or nothing).
      this.getWorker().postMessage({ id, ...request });
    });
  }

  private handleMessage(e: MessageEvent): void {
    const data = e.data as Record<string, unknown>;
    // Worker errors come as { id, error: string }
    if (data.error && typeof data.error === 'string') {
      const id = typeof data.id === 'string' ? data.id : '';
      const entry = id ? this.pending.get(id) : undefined;
      if (entry) {
        entry.reject(new Error(data.error));
        this.pending.delete(id);
      }
      return;
    }
    // Success: { id, header: WaveformHeader, buf: ArrayBuffer }
    const header = data.header as WaveformHeader | undefined;
    const buf = data.buf as ArrayBuffer | undefined;
    if (!header || !buf) return;

    // Reconstruct WaveformPayload on the transferred buffer
    const payload = parseWaveformPayload(buf);
    const id = typeof data.id === 'string' ? data.id : '';
    const entry = id ? this.pending.get(id) : undefined;
    if (entry) {
      entry.resolve(payload);
      this.pending.delete(id);
    }
  }

  private handleError(e: ErrorEvent): void {
    for (const [, { reject }] of this.pending) {
      reject(new Error(e.message ?? 'Worker error'));
    }
    this.pending.clear();
  }
}
