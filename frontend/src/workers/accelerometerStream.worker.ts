import { ReconnectingSocket } from '../api/websocket';

// Keep the socket and JSON parsing off the React/main thread.  The worker
// forwards samples as soon as they arrive; the card owns the bounded mailbox
// that decides when a browser frame should be rendered.
const socket = new ReconnectingSocket('/ws/accelerometer/pose');

socket.subscribe((message) => {
  if (message.type === 'accelerometer_sample' && message.data?.sample) {
    const trace = message.data.trace;
    self.postMessage(trace && typeof trace === 'object'
      ? { ...message, data: {
        ...message.data,
        trace: { ...trace, worker_received_at_ms: performance.now() },
      } }
      : message);
  }
});

self.addEventListener('unload', () => socket.close());
