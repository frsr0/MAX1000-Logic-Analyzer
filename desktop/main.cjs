const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');

const APP_NAME = 'OLS Logic Analyzer';
const BACKEND_START_TIMEOUT_MS = 30_000;
let backendProcess = null;
let mainWindow = null;
let isQuitting = false;

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
}

app.on('second-instance', () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
});

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function waitForBackend(port) {
  const deadline = Date.now() + BACKEND_START_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const check = () => {
      const request = http.get(
        { hostname: '127.0.0.1', port, path: '/api/status', timeout: 1_000 },
        (response) => {
          response.resume();
          if (response.statusCode && response.statusCode < 500) {
            resolve();
            return;
          }
          retry();
        },
      );
      request.on('error', retry);
      request.on('timeout', () => request.destroy());
    };
    const retry = () => {
      if (Date.now() >= deadline) {
        reject(new Error('The backend did not become ready before the timeout.'));
        return;
      }
      setTimeout(check, 250);
    };
    check();
  });
}

function backendCommand(port) {
  const resourcesBackend = path.join(process.resourcesPath, 'backend', 'ols-backend.exe');
  if (app.isPackaged) {
    return {
      command: resourcesBackend,
      args: ['--host', '127.0.0.1', '--port', String(port)],
    };
  }

  const localBackend = path.join(__dirname, 'build', 'backend', 'ols-backend.exe');
  if (fs.existsSync(localBackend)) {
    return {
      command: localBackend,
      args: ['--host', '127.0.0.1', '--port', String(port)],
    };
  }

  const repoRoot = path.resolve(__dirname, '..');
  const python = process.env.PYTHON || 'python';
  return {
    command: python,
    args: [path.join(repoRoot, 'backend', 'run.py'),
      '--host', '127.0.0.1', '--port', String(port)],
    cwd: path.join(repoRoot, 'backend'),
  };
}

function startBackend(port) {
  const command = backendCommand(port);
  const dataDir = path.join(app.getPath('userData'), 'data');
  fs.mkdirSync(dataDir, { recursive: true });

  const child = spawn(command.command, command.args, {
    cwd: command.cwd || path.dirname(command.command),
    env: {
      ...process.env,
      MSA_HOST: '127.0.0.1',
      MSA_PORT: String(port),
      MSA_DATA_DIR: dataDir,
    },
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  child.stdout.on('data', (data) => console.log(`[backend] ${data}`.trimEnd()));
  child.stderr.on('data', (data) => console.error(`[backend] ${data}`.trimEnd()));
  backendProcess = child;
  return child;
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed) return;
  if (process.platform === 'win32') {
    // PyInstaller one-file executables have a short-lived parent stub and an
    // extracted worker process. Kill the whole tree or the worker can outlive
    // the Electron window.
    spawn('taskkill', ['/pid', String(backendProcess.pid), '/t', '/f'], {
      windowsHide: true,
      stdio: 'ignore',
    });
  } else {
    backendProcess.kill();
  }
  backendProcess = null;
}

async function createMainWindow() {
  const port = await findFreePort();
  const child = startBackend(port);
  child.once('error', (error) => {
    console.error(`${APP_NAME} backend failed to start`, error);
  });
  child.once('exit', (code, signal) => {
    if (backendProcess === child) backendProcess = null;
    if (!isQuitting && mainWindow && !mainWindow.isDestroyed() && code !== 0) {
      dialog.showErrorBox(APP_NAME, `The backend stopped unexpectedly (${signal || `exit ${code}`}).`);
    }
  });

  try {
    await waitForBackend(port);
  } catch (error) {
    stopBackend();
    throw error;
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 700,
    title: APP_NAME,
    backgroundColor: '#101820',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
    },
  });
  await mainWindow.loadURL(`http://127.0.0.1:${port}/`);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  if (!hasSingleInstanceLock) return;
  try {
    await createMainWindow();
  } catch (error) {
    dialog.showErrorBox(APP_NAME, error.message);
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  stopBackend();
});
app.on('window-all-closed', () => {
  isQuitting = true;
  stopBackend();
  app.quit();
});
