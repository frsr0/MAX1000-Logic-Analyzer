// REST client. Every request carries a stable per-browser client id used by
// the backend's hardware control lock.
import type {
  BackendStatus, CaptureSettings, ChannelInfo, DecoderDescription,
  DecoderEvent, DecoderInstance, DeviceCapabilities, DeviceDescriptor,
  DeviceMetadata, GeneratorConfig, LogEntry, Marker, MeasurementInstance,
  MeasurementType, Session, SessionSummary,
} from './types';
import { parseWaveformPayload, WaveformPayload } from './binary';

export function clientId(): string {
  let id = localStorage.getItem('msa_client_id');
  if (!id) {
    id = `web_${Math.random().toString(36).slice(2, 12)}`;
    localStorage.setItem('msa_client_id', id);
  }
  return id;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Client-Id': clientId(),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('application/json')) return res.json() as Promise<T>;
  return res.text() as unknown as Promise<T>;
}

const get = <T,>(p: string) => req<T>('GET', p);
const post = <T,>(p: string, b?: unknown) => req<T>('POST', p, b);
const patch = <T,>(p: string, b?: unknown) => req<T>('PATCH', p, b);
const del = <T,>(p: string) => req<T>('DELETE', p);

export const api = {
  // status / control
  status: () => get<BackendStatus>('/api/status'),
  acquireControl: (name: string, force = false) =>
    post<{ acquired: boolean }>('/api/control/acquire', { name, force }),
  releaseControl: () => post('/api/control/release'),

  // devices
  devices: () => get<{ devices: DeviceDescriptor[] }>('/api/devices'),
  connect: (device_id: string) =>
    post<{ connected: boolean; metadata: DeviceMetadata }>('/api/connect', { device_id }),
  disconnect: () => post('/api/disconnect'),
  deviceMetadata: () => get<DeviceMetadata>('/api/device/metadata'),
  capabilities: () => get<DeviceCapabilities>('/api/device/capabilities'),
  deviceDebug: () => get<Record<string, any>>('/api/device/debug'),
  selfTest: () => post<{ passed: boolean; checks: any[]; message: string }>('/api/device/self-test'),

  // capture
  startCapture: (settings: CaptureSettings, name = '') =>
    post('/api/capture/start', { settings, name }),
  stopCapture: () => post('/api/capture/stop'),
  captureState: () => get<{ state: string; progress: any; last_session_id: string | null; last_error: string | null }>('/api/capture/state'),
  validateSettings: (settings: CaptureSettings) =>
    post<{ findings: { level: string; message: string }[] }>('/api/capture/settings/validate', settings),
  mockScenarios: () => get<{ scenarios: { id: string; name: string }[] }>('/api/capture/scenarios'),

  // sessions
  sessions: () => get<{ sessions: SessionSummary[] }>('/api/sessions'),
  session: (id: string) => get<Session>(`/api/sessions/${id}`),
  patchSession: (id: string, body: Partial<{ name: string; notes: string; tags: string[]; channels: Partial<ChannelInfo>[] }>) =>
    patch<Session>(`/api/sessions/${id}`, body),
  deleteSession: (id: string) => del(`/api/sessions/${id}`),
  duplicateSession: (id: string) => post<SessionSummary>(`/api/sessions/${id}/duplicate`),
  compareSessions: (a: string, b: string) => post<any>(`/api/sessions/${a}/compare/${b}`),
  importSession: (json_text: string) => post<SessionSummary>('/api/sessions', { json_text }),

  // waveform
  waveformMeta: (id: string) => get<any>(`/api/sessions/${id}/metadata`),
  waveformWindow: async (id: string, start: number, end: number,
                         resolution: number, channels?: string[],
                         signal?: AbortSignal): Promise<WaveformPayload> => {
    const ch = channels?.length ? `&channels=${channels.join(',')}` : '';
    const res = await fetch(
      `/api/sessions/${id}/waveform?start=${Math.floor(start)}&end=${Math.ceil(end)}&resolution=${resolution}${ch}`,
      { signal, headers: { 'X-Client-Id': clientId() } });
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return parseWaveformPayload(await res.arrayBuffer());
  },
  overview: async (id: string, bins = 1024): Promise<WaveformPayload> => {
    const res = await fetch(`/api/sessions/${id}/overview?bins=${bins}`);
    if (!res.ok) throw new ApiError(res.status, await res.text());
    return parseWaveformPayload(await res.arrayBuffer());
  },
  edges: (id: string, channel: string, kind: string, start = 0, end = -1, limit = 5000) =>
    get<{ edges: number[]; times: number[]; count: number; truncated: boolean }>(
      `/api/sessions/${id}/edges?channel=${channel}&kind=${kind}&start=${start}&end=${end}&limit=${limit}`),
  valueAt: (id: string, sample: number, channels: string[]) =>
    get<{ sample: number; time_s: number; values: Record<string, number | null>; buses: Record<string, any> }>(
      `/api/sessions/${id}/value-at?sample=${sample}&channels=${channels.join(',')}`),
  rawWindow: (id: string, start: number, end: number) =>
    get<any>(`/api/sessions/${id}/raw?start=${start}&end=${end}`),
  sanity: (id: string) => get<{ findings: any[] }>(`/api/sessions/${id}/sanity`),
  addBus: (id: string, name: string, members: string[], display_base = 'hex') =>
    post<ChannelInfo>(`/api/sessions/${id}/buses`, { name, members, display_base }),
  addDerivedChannel: (id: string, source: string, derive: Record<string, unknown>, name?: string) =>
    post<ChannelInfo>(`/api/sessions/${id}/derived-channels`, { source, derive, name }),
  spectrum: (id: string, channel: string) =>
    get<{ freqs: number[]; magnitude: number[] }>(`/api/sessions/${id}/spectrum?channel=${channel}`),

  // decoders
  decoderTypes: () => get<{ decoders: DecoderDescription[] }>('/api/decoders'),
  addDecoder: (id: string, body: { decoder_id: string; name?: string; channels: Record<string, string>; settings?: Record<string, unknown>; region?: number[] }) =>
    post<DecoderInstance>(`/api/sessions/${id}/decoders`, body),
  patchDecoder: (id: string, decId: string, body: Record<string, unknown>) =>
    patch<DecoderInstance>(`/api/sessions/${id}/decoders/${decId}`, body),
  deleteDecoder: (id: string, decId: string) => del(`/api/sessions/${id}/decoders/${decId}`),
  runDecoder: (id: string, decId: string, region?: number[]) =>
    post(`/api/sessions/${id}/decoders/${decId}/run`, { region: region ?? null }),
  cancelDecoder: (id: string, decId: string) =>
    post(`/api/sessions/${id}/decoders/${decId}/cancel`),
  decoderAnnotations: (id: string, decId: string, start: number, end: number, limit = 3000) =>
    get<{ events: DecoderEvent[]; truncated: boolean }>(
      `/api/sessions/${id}/decoders/${decId}/annotations?start=${Math.floor(start)}&end=${Math.ceil(end)}&limit=${limit}`),
  decoderTable: (id: string, decId: string, offset: number, limit: number, search = '', severity = '') =>
    get<{ total: number; events: DecoderEvent[] }>(
      `/api/sessions/${id}/decoders/${decId}/table?offset=${offset}&limit=${limit}&search=${encodeURIComponent(search)}&severity=${severity}`),

  // measurements
  measurementTypes: () => get<{ types: MeasurementType[] }>('/api/measurements/types'),
  addMeasurement: (id: string, body: { type: string; channels: string[]; scope?: string; region?: number[]; settings?: Record<string, unknown> }) =>
    post<MeasurementInstance>(`/api/sessions/${id}/measurements`, body),
  deleteMeasurement: (id: string, mid: string) => del(`/api/sessions/${id}/measurements/${mid}`),
  measurementResults: (id: string, cursorA?: number, cursorB?: number) => {
    const q = cursorA !== undefined && cursorB !== undefined
      ? `?cursor_a=${Math.floor(cursorA)}&cursor_b=${Math.floor(cursorB)}` : '';
    return get<{ measurements: MeasurementInstance[] }>(`/api/sessions/${id}/measurements/results${q}`);
  },

  // markers
  markers: (id: string) => get<{ markers: Marker[] }>(`/api/sessions/${id}/markers`),
  addMarker: (id: string, body: Partial<Marker> & { sample: number }) =>
    post<Marker>(`/api/sessions/${id}/markers`, body),
  patchMarker: (id: string, mid: string, body: Partial<Marker>) =>
    patch<Marker>(`/api/sessions/${id}/markers/${mid}`, body),
  deleteMarker: (id: string, mid: string) => del(`/api/sessions/${id}/markers/${mid}`),

  // generator
  generatorCapabilities: () => get<{ protocols: string[]; status: any }>('/api/generator/capabilities'),
  generatorConfigure: (cfg: GeneratorConfig) => post('/api/generator/configure', cfg),
  generatorStart: () => post('/api/generator/start'),
  generatorStop: () => post('/api/generator/stop'),
  generatorStatus: () => get<any>('/api/generator/status'),
  generatorSend: (body: { config: GeneratorConfig; capture: boolean; capture_rate?: number; capture_samples?: number; expected_hex?: string }) =>
    post<any>('/api/generator/send', body),
  generatorSelfTest: () => post<any>('/api/generator/self-test'),

  // diagnostics
  logs: (limit = 500) => get<{ logs: LogEntry[] }>(`/api/logs?limit=${limit}`),
  diagnostics: () => get<any>('/api/diagnostics'),
  mockCapture: (scenario: string, sample_rate = 1_000_000, num_samples = 50_000, analog = false) =>
    post('/api/diagnostics/mock-capture', { scenario, sample_rate, num_samples, analog }),
};

// Exports trigger a browser download via a hidden form post.
export async function downloadExport(sessionId: string, format: string, body: unknown = {}) {
  const res = await fetch(`/api/sessions/${sessionId}/export/${format}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Client-Id': clientId() },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(res.status, (await res.json()).detail ?? res.statusText);
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') ?? '';
  const m = cd.match(/filename="([^"]+)"/);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = m?.[1] ?? `${sessionId}.${format}`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export async function downloadDebugBundle() {
  const res = await fetch('/api/diagnostics/debug-bundle', { method: 'POST' });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'debug_bundle.zip';
  a.click();
  URL.revokeObjectURL(a.href);
}
