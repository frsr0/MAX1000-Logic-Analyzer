# Windows desktop package

The desktop package is a portable Windows `.exe` around the existing React
frontend and FastAPI backend. Opening the executable starts the backend on a
loopback-only port, waits for `/api/status`, opens the frontend in an Electron
window, and stops the backend when the window closes.

## Build

Build from the repository root on Windows PowerShell:

```powershell
python -m pip install -r desktop/requirements-build.txt
.\desktop\build-windows.ps1
```

The default output is a single portable executable under `desktop/dist/`.
For an installer instead:

```powershell
.\desktop\build-windows.ps1 -Installer
```

The build machine needs Node.js 20+, Python 3.10+, and the backend's Python
dependencies. `-SkipFrontend`, `-SkipBackend`, and `-SkipDesktopInstall` are
available for incremental builds after the relevant artifact already exists.

## Hardware note

The packaged app includes the Python FTDI wrapper when it is installed in the
build environment. Windows users still need the FTDI D2XX driver installed for
real MAX1000 hardware. Mock mode works without hardware and is useful for
checking the package on a clean machine.

Session data is stored in the normal per-user Electron data directory, not
inside the read-only packaged executable.
