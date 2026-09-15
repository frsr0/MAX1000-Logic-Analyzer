// Root component: wires WebSockets to the stores, loads catalogs, renders shell.
import { useEffect, useRef } from 'react';
import { ReconnectingSocket } from './api/websocket';
import { api } from './api/client';
import { AppShell } from './layout/AppShell';
import { useApp } from './state/appStore';
import { waveformView } from './state/waveformStore';

export default function App() {
  const { refreshStatus, refreshSessions, refreshCapabilities, loadCatalogs,
          setWsConnected, pushLog, toast } = useApp();
  const autoConnectInFlight = useRef(false);

  const connectHardwareIfAvailable = async (statusPromise?: ReturnType<typeof refreshStatus>) => {
    // The mock backend remains available for explicit test/demo flows, but it
    // must never win the normal startup path over a real MAX1000 board.
    if (autoConnectInFlight.current) return;
    autoConnectInFlight.current = true;
    try {
      const current = await (statusPromise ?? refreshStatus());
      if (!current || current.device_connected || !useApp.getState().controlMode) return;
      const { devices } = await api.devices();
      const hardware = devices.find((device) => device.id === 'hardware');
      if (!hardware?.available) return;
      await api.connect('hardware');
      await Promise.all([refreshStatus(), refreshCapabilities()]);
      toast('success', 'Connected to the available MAX1000 hardware scope');
    } catch {
      // A board can disappear between discovery and connect, or another
      // browser can own the control lock. Leave the app disconnected rather
      // than silently substituting the mock scope.
    } finally {
      autoConnectInFlight.current = false;
    }
  };

  useEffect(() => {
    void connectHardwareIfAvailable();
    refreshSessions();
    refreshCapabilities();
    loadCatalogs();
    document.documentElement.dataset.theme = useApp.getState().viewerSettings.theme;
    document.title = 'MAX1000 Logic Analyzer';

    const statusWs = new ReconnectingSocket('/ws/status');
    statusWs.onStateChange = (ok) => {
      setWsConnected(ok);
      if (ok) {
        void connectHardwareIfAvailable(Promise.resolve(refreshStatus()));
        refreshSessions();
      }
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
        case 'capture_cancelled':
          // Backend publishes this on Stop; without it the status bar keeps
          // the stale "capturing" badge from the last capture_progress event.
          // Also stop the live-follow animation: it is driven by wall-clock
          // shift and would keep scrolling the view past the captured data.
          waveformView.setLiveFollow(false);
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
  const activeIsAccelerometer = useApp((s) =>
    Boolean(s.activeSession?.tags.includes('accelerometer')
      && s.activeSession.settings.mode === 'rolling'));
  useEffect(() => {
    if (!activeId) return;
    const ws = new ReconnectingSocket(`/ws/decoder/${activeId}`);
    const unsub = ws.subscribe((msg) => {
      if (msg.type === 'decoder_progress') {
        // Rolling captures re-run enabled decoders against each new window.
        // Refresh both the instance counters (used by the packet table) and
        // the annotation layer; live progress is intentionally quiet so a
        // packet stream does not fill the toast queue.
        const s = useApp.getState().activeSession;
        if (s?.id === activeId && msg.data?.live) {
          void useApp.getState().refreshActiveSession();
          waveformView.decodersVersion++;
          waveformView.requestAnnotations(0);
        }
      } else if (msg.type === 'decoder_complete') {
        useApp.getState().refreshActiveSession();
        waveformView.requestAnnotations(0);
        if (msg.data.error) toast('error', `Decoder failed: ${msg.data.error}`);
        else if (!msg.data.cancelled) {
          toast('success', `Decoder finished: ${msg.data.event_count} events`);
        }
      }
    });
    const sesWs = new ReconnectingSocket(`/ws/session/${activeId}`);
    const unsubSes = sesWs.subscribe(async (msg) => {
      if (msg.type === 'measurement_updated') {
        useApp.getState().refreshActiveSession();
      } else if (msg.type === 'waveform_ready') {
        const s = useApp.getState().activeSession;
        if (s?.id === activeId) {
          const rolling = Boolean(msg.data?.rolling);
          await waveformView.updateLive(
            Number(msg.data?.num_samples ?? s.num_samples),
            Number(msg.data?.sample_rate ?? s.sample_rate),
            s.trigger_sample ?? null,
            rolling,
            Number(msg.data?.chunk_samples ?? 0),
          );
          // Rolling decoder runs append events to the same session. Refresh
          // the session metadata so the packet table picks up new counts;
          // ordinary waveform updates do not need this extra request.
          if (rolling) void useApp.getState().refreshActiveSession();
        }
      }
    });
    return () => {
      unsub(); ws.close(); unsubSes(); sesWs.close();
    };
  }, [activeId, activeIsAccelerometer]);

  return <AppShell />;
}
