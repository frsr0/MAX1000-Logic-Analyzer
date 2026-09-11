import { afterEach, describe, expect, it, vi } from 'vitest';
import { backendUrl, backendWebSocketUrl } from './endpoint';

describe('backend endpoint URLs', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('avoids the Windows IPv6 localhost fallback for the packaged app', () => {
    vi.stubGlobal('location', {
      hostname: 'localhost', host: 'localhost:8000', port: '8000', protocol: 'http:',
    });
    expect(backendUrl('/api/mil/accelerometer/status'))
      .toBe('http://127.0.0.1:8000/api/mil/accelerometer/status');
    expect(backendWebSocketUrl('/ws/accelerometer'))
      .toBe('ws://127.0.0.1:8000/ws/accelerometer');
  });

  it('preserves LAN and development origins', () => {
    vi.stubGlobal('location', {
      hostname: '192.168.1.12', host: '192.168.1.12:8000', port: '8000', protocol: 'http:',
    });
    expect(backendUrl('/api/status')).toBe('/api/status');
    expect(backendWebSocketUrl('/ws/status')).toBe('ws://192.168.1.12:8000/ws/status');

    vi.stubGlobal('location', {
      hostname: 'localhost', host: 'localhost:5173', port: '5173', protocol: 'http:',
    });
    expect(backendUrl('/api/status')).toBe('/api/status');
    expect(backendWebSocketUrl('/ws/status')).toBe('ws://localhost:5173/ws/status');
  });
});
