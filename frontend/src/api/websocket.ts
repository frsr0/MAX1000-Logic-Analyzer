// Auto-reconnecting WebSocket wrapper with typed message handlers.
import type { WsMessage } from './types';

type Handler = (msg: WsMessage) => void;

export class ReconnectingSocket {
  private url: string;
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private closed = false;
  private retryMs = 500;
  private timer: ReturnType<typeof setTimeout> | null = null;
  onStateChange?: (connected: boolean) => void;

  constructor(path: string) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.url = `${proto}://${location.host}${path}`;
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.retryMs = 500;
      this.onStateChange?.(true);
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WsMessage;
        this.handlers.forEach((h) => h(msg));
      } catch { /* ignore malformed */ }
    };
    this.ws.onclose = () => {
      this.onStateChange?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private scheduleReconnect() {
    if (this.closed) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.connect(), this.retryMs);
    this.retryMs = Math.min(this.retryMs * 2, 10_000);
  }

  subscribe(h: Handler): () => void {
    this.handlers.add(h);
    return () => this.handlers.delete(h);
  }

  close() {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
  }
}
