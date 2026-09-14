// Auto-reconnecting WebSocket wrapper with typed message handlers.
import type { WsMessage } from './types';
import { backendWebSocketUrl } from './endpoint';

type Handler = (msg: WsMessage) => void;
type BinaryHandler = (data: ArrayBuffer) => void;

export class ReconnectingSocket {
  private url: string;
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private binaryHandlers = new Set<BinaryHandler>();
  private binaryMode: boolean;
  private closed = false;
  private retryMs = 500;
  private timer: ReturnType<typeof setTimeout> | null = null;
  onStateChange?: (connected: boolean) => void;

  constructor(path: string, binaryMode = false) {
    this.url = backendWebSocketUrl(path);
    this.binaryMode = binaryMode;
    this.connect();
  }

  private connect() {
    if (this.closed) return;
    try {
      this.ws = new WebSocket(this.url);
      if (this.binaryMode) this.ws.binaryType = 'arraybuffer';
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.retryMs = 500;
      this.onStateChange?.(true);
    };
    this.ws.onmessage = (ev) => {
      if (this.binaryMode) {
        if (ev.data instanceof ArrayBuffer) {
          this.binaryHandlers.forEach((h) => h(ev.data));
          return;
        }
        if (ev.data instanceof Blob) {
          ev.data.arrayBuffer().then((data) => {
            if (!this.closed) this.binaryHandlers.forEach((h) => h(data));
          }).catch(() => {});
          return;
        }
      }
      try {
        const msg = JSON.parse(ev.data) as WsMessage;
        if ((msg as any).type === 'ping') {
          this.ws?.send(JSON.stringify({ type: 'pong' }));
          return;
        }
        this.handlers.forEach((h) => h(msg));
      } catch { /* ignore malformed */ }
    };
    this.ws.onclose = () => {
      this.onStateChange?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
  }

  subscribeBinary(h: BinaryHandler): () => void {
    this.binaryHandlers.add(h);
    return () => this.binaryHandlers.delete(h);
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
