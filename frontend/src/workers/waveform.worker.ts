/**
 * Web Worker for waveform binary fetch, parse, and transform.
 *
 * Offloads MSAW binary fetch + parse from the main thread.
 * Transfers the raw ArrayBuffer back (zero-copy), so the main thread
 * can re-create TypedArray views without copying the data.
 */

import { parseWaveformPayload } from '../api/binary';
import type { WaveformHeader } from '../api/binary';

interface FetchWindowRequest {
  type: 'window';
  id: string;
  sessionId: string;
  start: number;
  end: number;
  resolution: number;
  channels?: string[];
}

interface FetchOverviewRequest {
  type: 'overview';
  id: string;
  sessionId: string;
  bins?: number;
}

type WorkerRequest = FetchWindowRequest | FetchOverviewRequest;

interface WorkerResult {
  id: string;
  header: WaveformHeader;
  buf: ArrayBuffer;
}

interface WorkerError {
  id: string;
  error: string;
}

self.onmessage = async (e: MessageEvent<WorkerRequest>) => {
  const req = e.data;
  try {
    const url = req.type === 'window'
      ? buildWindowUrl(req.sessionId, req.start, req.end, req.resolution, req.channels)
      : buildOverviewUrl(req.sessionId, req.bins);

    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      self.postMessage({ id: req.id, error: `fetch ${res.status}: ${text}` } satisfies WorkerError);
      return;
    }
    const buf = await res.arrayBuffer();
    const { header } = parseWaveformPayload(buf);
    // Transfer the buffer — the main thread re-creates TypedArray views
    // from the header's array descriptors.
    const wResult: WorkerResult = { id: req.id, header, buf };
    (self.postMessage as (msg: unknown, transfer: Transferable[]) => void)(wResult, [buf]);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    self.postMessage({ id: req.id, error: msg } satisfies WorkerError);
  }
};

function buildWindowUrl(sessionId: string, start: number, end: number,
                         resolution: number, channels?: string[]): string {
  const ch = channels?.length ? `&channels=${channels.join(',')}` : '';
  return `/api/sessions/${sessionId}/waveform?start=${Math.floor(start)}&end=${Math.ceil(end)}&resolution=${resolution}${ch}`;
}

function buildOverviewUrl(sessionId: string, bins: number = 1024): string {
  return `/api/sessions/${sessionId}/overview?bins=${bins}`;
}
