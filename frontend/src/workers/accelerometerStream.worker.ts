import { ReconnectingSocket } from '../api/websocket';

// Keep the socket and JSON parsing off the React/main thread.  The worker
// forwards samples as soon as they arrive; the card owns the bounded mailbox
// that decides when a browser frame should be rendered.
const socket = new ReconnectingSocket('/ws/accelerometer');

socket.subscribe((message) => {
  if (message.type === 'accelerometer_sample' && message.data?.sample) {
    self.postMessage(message);
  }
});

self.addEventListener('unload', () => socket.close());
