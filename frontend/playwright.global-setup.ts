import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import path from 'node:path';
import { spawn, type ChildProcess } from 'node:child_process';

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const backendDir = path.resolve(frontendDir, '..', 'backend');

function waitForUrl(url: string, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise<void>((resolve, reject) => {
    const poll = () => {
      const request = http.get(url, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
      request.on('error', retry);
      request.setTimeout(2_000, () => request.destroy());
    };
    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error(`Timed out waiting for ${url}`));
        return;
      }
      setTimeout(poll, 100);
    };
    poll();
  });
}

function stopProcess(child: ChildProcess) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    try {
      execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        stdio: 'ignore',
        timeout: 5_000,
      });
    } catch {
      child.kill();
    }
  } else {
    child.kill('SIGTERM');
  }
}

export default async function globalSetup() {
  const children: ChildProcess[] = [];
  const env = { ...process.env, BROWSER: 'none', FORCE_COLOR: '1', DEBUG_COLORS: '1' };
  const start = (command: string, args: string[], cwd: string) => {
    const child = spawn(command, args, { cwd, env, stdio: 'inherit', windowsHide: true });
    children.push(child);
    return child;
  };

  try {
    start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '4173'], frontendDir);
    await waitForUrl('http://127.0.0.1:4173/');

    if (process.env.PLAYWRIGHT_USE_MOCK !== '1') {
      start(process.platform === 'win32' ? 'python' : 'python3', ['run.py'], backendDir);
      await waitForUrl('http://127.0.0.1:8000/api/status');
    }
  } catch (error) {
    for (const child of children.reverse()) stopProcess(child);
    throw error;
  }

  return async () => {
    for (const child of children.reverse()) stopProcess(child);
  };
}
