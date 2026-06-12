// App-level state (zustand). Waveform sample data intentionally lives in
// waveformStore (outside React) — only small metadata objects belong here.
import { create } from 'zustand';
import { api } from '../api/client';
import type {
  BackendStatus, CaptureSettings, DecoderDescription, DeviceCapabilities,
  LogEntry, MeasurementType, Session, SessionSummary,
} from '../api/types';
import { defaultCaptureSettings } from '../api/types';
import { waveformView } from './waveformStore';

export type Page = 'capture' | 'sessions' | 'device' | 'generator'
  | 'diagnostics' | 'settings';

export interface Toast {
  id: number;
  level: 'info' | 'warning' | 'error' | 'success';
  message: string;
}

interface AppState {
  page: Page;
  setPage: (p: Page) => void;

  wsConnected: boolean;
  setWsConnected: (v: boolean) => void;

  status: BackendStatus | null;
  refreshStatus: () => Promise<void>;

  capabilities: DeviceCapabilities | null;
  refreshCapabilities: () => Promise<void>;

  sessions: SessionSummary[];
  refreshSessions: () => Promise<void>;

  activeSession: Session | null;
  openSession: (id: string) => Promise<void>;
  refreshActiveSession: () => Promise<void>;

  captureSettings: CaptureSettings;
  setCaptureSettings: (s: Partial<CaptureSettings>) => void;

  decoderTypes: DecoderDescription[];
  measurementTypes: MeasurementType[];
  loadCatalogs: () => Promise<void>;

  logs: LogEntry[];
  pushLog: (e: LogEntry) => void;

  toasts: Toast[];
  toast: (level: Toast['level'], message: string) => void;
  dismissToast: (id: number) => void;

  controlMode: boolean;            // false = read-only viewer
  setControlMode: (v: boolean) => void;

  viewerSettings: {
    theme: 'dark' | 'light';
    defaultSampleRate: number;
    defaultNumSamples: number;
  };
  setViewerSettings: (s: Partial<AppState['viewerSettings']>) => void;
}

let toastSeq = 1;

export const useApp = create<AppState>((set, getState) => ({
  page: 'capture',
  setPage: (p) => set({ page: p }),

  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

  status: null,
  refreshStatus: async () => {
    try {
      set({ status: await api.status() });
    } catch { /* backend down; ws reconnect will retry */ }
  },

  capabilities: null,
  refreshCapabilities: async () => {
    try {
      set({ capabilities: await api.capabilities() });
    } catch {
      set({ capabilities: null });
    }
  },

  sessions: [],
  refreshSessions: async () => {
    try {
      const r = await api.sessions();
      set({ sessions: r.sessions });
    } catch { /* ignore */ }
  },

  activeSession: null,
  openSession: async (id) => {
    const s = await api.session(id);
    set({ activeSession: s });
    await waveformView.load(s.id, s.num_samples, s.sample_rate,
      s.trigger_sample ?? null);
    waveformView.markers = s.markers;
    const a = s.markers.find((m) => m.kind === 'cursor_a');
    const b = s.markers.find((m) => m.kind === 'cursor_b');
    waveformView.cursorA = a?.sample ?? null;
    waveformView.cursorB = b?.sample ?? null;
    waveformView.notify();
  },
  refreshActiveSession: async () => {
    const cur = getState().activeSession;
    if (!cur) return;
    try {
      const s = await api.session(cur.id);
      set({ activeSession: s });
    } catch { /* deleted? */ }
  },

  captureSettings: loadSavedSettings(),
  setCaptureSettings: (s) => {
    const next = { ...getState().captureSettings, ...s };
    localStorage.setItem('msa_capture_settings', JSON.stringify(next));
    set({ captureSettings: next });
  },

  decoderTypes: [],
  measurementTypes: [],
  loadCatalogs: async () => {
    try {
      const [d, m] = await Promise.all([api.decoderTypes(), api.measurementTypes()]);
      set({ decoderTypes: d.decoders, measurementTypes: m.types });
    } catch { /* retry on next mount */ }
  },

  logs: [],
  pushLog: (e) => set((st) => ({ logs: [...st.logs.slice(-499), e] })),

  toasts: [],
  toast: (level, message) => {
    const id = toastSeq++;
    set((st) => ({ toasts: [...st.toasts, { id, level, message }] }));
    setTimeout(() => getState().dismissToast(id),
      level === 'error' ? 8000 : 4000);
  },
  dismissToast: (id) => set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),

  controlMode: true,
  setControlMode: (v) => set({ controlMode: v }),

  viewerSettings: loadViewerSettings(),
  setViewerSettings: (s) => {
    const next = { ...getState().viewerSettings, ...s };
    localStorage.setItem('msa_viewer_settings', JSON.stringify(next));
    set({ viewerSettings: next });
    document.documentElement.dataset.theme = next.theme;
  },
}));

function loadSavedSettings(): CaptureSettings {
  try {
    const raw = localStorage.getItem('msa_capture_settings');
    if (raw) return { ...defaultCaptureSettings(), ...JSON.parse(raw) };
  } catch { /* fall through */ }
  return defaultCaptureSettings();
}

function loadViewerSettings() {
  const defaults = {
    theme: 'dark' as const,
    defaultSampleRate: 1_000_000,
    defaultNumSamples: 100_000,
  };
  try {
    const raw = localStorage.getItem('msa_viewer_settings');
    if (raw) return { ...defaults, ...JSON.parse(raw) };
  } catch { /* fall through */ }
  return defaults;
}
