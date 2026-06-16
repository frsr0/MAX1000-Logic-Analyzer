// Root component: wires WebSockets to the stores, loads catalogs, renders shell.
import { useEffect } from 'react';
import { ReconnectingSocket } from './api/websocket';
import { AppShell } from './layout/AppShell';
import { useApp } from './state/appStore';
import { waveformView } from './state/waveformStore';

export default function App() {
  const { refreshStatus, refreshSessions, refreshCapabilities, loadCatalogs,
          setWsConnected, pushLog, toast } = useApp();

  useEffect(() => {
    refreshStatus();
    refreshSessions();
    refreshCapabilities();
    loadCatalogs();
    document.documentElement.dataset.theme = useApp.getState().viewerSettings.theme;

    const statusWs = new ReconnectingSocket('/ws/status');
    statusWs.onStateChange = (ok) => {
      setWsConnected(ok);
      if (ok) { refreshStatus(); refreshSessions(); }
    };
    const unsubStatus = statusWs.subscribe((msg) => {
      switch (msg.type) {
        case 'status_snapshot':
          useApp.setState({ status: msg.data });
          break;
        case 'device_connected':
        case 'device_disconnected':
          refreshStatus();
          refreshCapabilities();
          break;
        case 'session_created':
          refreshSessions();
          refreshStatus();
          break;
      }
    });

    const captureWs = new ReconnectingSocket('/ws/capture');
    const unsubCapture = captureWs.subscribe((msg) => {
      const st = useApp.getState().status;
      switch (msg.type) {
        case 'capture_progress':
          if (st) {
            useApp.setState({
              status: {
                ...st, capture_state: 'capturing',
                capture_progress: {
                  samples_read: msg.data.samples_read,
                  samples_total: msg.data.samples_total,
                  message: msg.data.phase, repeat: msg.data.repeat,
                },
              },
            });
          }
          break;
        case 'capture_armed':
        case 'capture_started':
          refreshStatus();
          break;
        case 'capture_complete':
          refreshStatus();
          refreshSessions();
          break;
        case 'capture_error':
          toast('error', `Capture failed: ${msg.data.message}`);
          refreshStatus();
          break;
        case 'warning':
          toast('warning', msg.data.message);
          break;
      }
    });

    const logsWs = new ReconnectingSocket('/ws/logs');
    const unsubLogs = logsWs.subscribe((msg) => {
      if (msg.type === 'log') pushLog(msg.data);
    });

    return () => {
      unsubStatus(); unsubCapture(); unsubLogs();
      statusWs.close(); captureWs.close(); logsWs.close();
    };
  }, []);

  // per-session decoder websocket
  const activeId = useApp((s) => s.activeSession?.id);
  useEffect(() => {
    if (!activeId) return;
    const ws = new ReconnectingSocket(`/ws/decoder/${activeId}`);
    const unsub = ws.subscribe((msg) => {
      if (msg.type === 'decoder_complete') {
        useApp.getState().refreshActiveSession();
        waveformView.requestAnnotations(0);
        if (msg.data.error) toast('error', `Decoder failed: ${msg.data.error}`);
        else if (!msg.data.cancelled) {
          toast('success', `Decoder finished: ${msg.data.event_count} events`);
        }
      }
    });
    const sesWs = new ReconnectingSocket(`/ws/session/${activeId}`);
    const unsubSes = sesWs.subscribe((msg) => {
      if (msg.type === 'measurement_updated') {
        useApp.getState().refreshActiveSession();
      }
    });
    return () => { unsub(); ws.close(); unsubSes(); sesWs.close(); };
  }, [activeId]);

  return <AppShell />;
}
