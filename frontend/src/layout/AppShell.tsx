// App chrome: icon sidebar, top bar, status bar, toasts, global shortcuts.
import { useEffect } from 'react';
import { api, downloadExport } from '../api/client';
import { Page, useApp } from '../state/appStore';
import { CapturePage } from '../pages/CapturePage';
import { DevicePage } from '../pages/DevicePage';
import { DiagnosticsPage } from '../pages/DiagnosticsPage';
import { GeneratorPage } from '../pages/GeneratorPage';
import { SessionsPage } from '../pages/SessionsPage';
import { SettingsPage } from '../pages/SettingsPage';

const NAV: { id: Page; icon: string; label: string }[] = [
  { id: 'capture', icon: '〜', label: 'Capture' },
  { id: 'sessions', icon: '▤', label: 'Sessions' },
  { id: 'device', icon: '⚙', label: 'Device' },
  { id: 'generator', icon: '⌁', label: 'Generator' },
  { id: 'diagnostics', icon: '☲', label: 'Diagnostics' },
  { id: 'settings', icon: '⋯', label: 'Settings' },
];

export function AppShell() {
  const { page, setPage, status, wsConnected, toasts, dismissToast,
          activeSession, captureSettings, toast, controlMode } = useApp();

  // global shortcuts: space (start/stop), ctrl+s (save session JSON)
  useEffect(() => {
    const onKey = async (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === ' ' && controlMode) {
        e.preventDefault();
        const st = useApp.getState().status;
        try {
          if (st?.capture_state === 'capturing' || st?.capture_state === 'armed') {
            await api.stopCapture();
          } else if (st?.device_connected) {
            await api.startCapture(captureSettings);
          }
        } catch (err: any) { toast('error', err.message); }
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        const ses = useApp.getState().activeSession;
        if (ses) {
          downloadExport(ses.id, 'json', { include_raw: true })
            .then(() => toast('success', 'Session saved (JSON download)'))
            .catch((err) => toast('error', err.message));
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [captureSettings, controlMode]);

  const capState = status?.capture_state ?? 'idle';

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div className="logo" title="MAX1000 Mixed-Signal Analyser">MSA</div>
        {NAV.map((n) => (
          <button key={n.id} className={page === n.id ? 'active' : ''}
            onClick={() => setPage(n.id)} title={n.label}>
            <span className="nav-icon">{n.icon}</span>
            <span className="nav-label">{n.label}</span>
          </button>
        ))}
      </nav>
      <div className="main-col">
        <header className="topbar">
          <span className="app-title">MAX1000 Mixed-Signal Analyser</span>
          <span className={`badge ${status?.device_connected ? 'badge-hw' : 'badge-na'}`}>
            {status?.device_connected
              ? `${status.device?.device_name}${status.device?.mock ? ' (mock)' : ''}`
              : 'no device'}
          </span>
          {capState !== 'idle' && capState !== 'done' && (
            <span className={`badge ${capState === 'error' ? 'badge-na' : 'badge-soft'}`}>
              {capState}
            </span>
          )}
          {!controlMode && <span className="badge badge-soft">read-only</span>}
          <span className="spacer" />
          <span className="hint">{activeSession?.name ?? ''}</span>
        </header>
        <main className="content">
          {page === 'capture' && <CapturePage />}
          {page === 'sessions' && <SessionsPage />}
          {page === 'device' && <DevicePage />}
          {page === 'generator' && <GeneratorPage />}
          {page === 'diagnostics' && <DiagnosticsPage />}
          {page === 'settings' && <SettingsPage />}
        </main>
        <footer className="statusbar">
          <span className={`ws-dot ${wsConnected ? 'on' : 'off'}`} />
          <span>{wsConnected ? 'live' : 'reconnecting…'}</span>
          <span>·</span>
          <span>{status?.session_count ?? 0} sessions</span>
          <span>·</span>
          <span>{status?.ws_clients ?? 0} clients</span>
          {status?.last_error && <><span>·</span>
            <span className="err">{status.last_error}</span></>}
          <span className="spacer" />
          <span className="hint">v{status?.app_version ?? '…'}</span>
        </footer>
      </div>
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.level}`}
            onClick={() => dismissToast(t.id)}>
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}
