import { buildWaveformPayload } from './waveformPayload';

// Browser boundary: the real client and store run unchanged. Tests choose
// when each posted worker message completes, including out-of-order replies.
export class ControlledWorker {
  static instances: ControlledWorker[] = [];
  static throwNext: unknown;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  messages: Record<string, unknown>[] = [];
  constructor() { ControlledWorker.instances.push(this); }
  postMessage(message: Record<string, unknown>) {
    if (ControlledWorker.throwNext !== undefined) {
      const error = ControlledWorker.throwNext;
      ControlledWorker.throwNext = undefined;
      throw error;
    }
    this.messages.push(message);
  }
  terminate() {}
  reply(index: number, sessionId: string) {
    this.onmessage?.(new MessageEvent('message', { data: {
      id: this.messages[index].id, header: {}, buf: buildWaveformPayload(sessionId),
    } }));
  }
  fail(index: number, error: string) {
    this.onmessage?.(new MessageEvent('message', {
      data: { id: this.messages[index].id, error },
    }));
  }
}
