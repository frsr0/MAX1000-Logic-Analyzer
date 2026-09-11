/**
 * Build a local backend URL without the Windows localhost IPv6 fallback.
 *
 * Uvicorn is intentionally bound to IPv4 (0.0.0.0) so LAN clients continue
 * to work. On Windows, however, `localhost` resolves to ::1 first; when the
 * browser connects to an IPv4-only listener it waits for the IPv6 refusal
 * timeout before retrying 127.0.0.1. That turns a live status/sample request
 * into an apparent multi-second UI lag. Normalize only the default local
 * app origin; LAN hostnames and development proxies retain same-origin URLs.
 */
export function backendUrl(path: string): string {
  if (typeof location === 'undefined'
      || location.hostname !== 'localhost'
      || location.port !== '8000') {
    return path;
  }
  return `${location.protocol}//127.0.0.1:8000${path}`;
}

export function backendWebSocketUrl(path: string): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  if (location.hostname === 'localhost' && location.port === '8000') {
    return `${protocol}//127.0.0.1:8000${path}`;
  }
  return `${protocol}//${location.host}${path}`;
}
