# Frontend Build and Test

**Files:** `frontend/package.json`, `vite.config.ts`, `vitest.config.ts`,
`playwright.config.ts`

## Toolchain

| Tool | Current range | Purpose |
|---|---:|---|
| React / React DOM | ^18.3.1 | UI |
| Zustand | ^4.5.4 | Application state |
| TypeScript | ^5.5.3 | Type checking |
| Vite | ^8.1.5 | Development and production build |
| Vitest / V8 coverage | ^4.1.11 | Unit and coverage tests |
| Playwright | ^1.61.1 | Browser E2E and hardware matrix |

## Commands

```powershell
cd frontend
npm ci
npm run dev
npm run typecheck
npm run build
npm run test:unit
$env:PLAYWRIGHT_USE_MOCK='1'
npm run test:e2e -- hardware.spec.ts
```

`npm run build` runs `tsc && vite build`. The Vite development server proxies
`/api` and `/ws` to the backend on port 8000; the production backend serves
the built SPA.

## Unit tests

The Vitest suite covers rendered UI interactions, exported store/API methods,
worker messages, canvas output, and browser/network boundaries. Representative
transport tests include:

| Test | Contract |
|---|---|
| `src/api/binary.test.ts` | MSAW framing, typed arrays, malformed/truncated payloads |
| `src/api/websocket.test.ts` | URL construction, subscriptions, reconnect/close behavior |
| `src/workers/waveformClient.test.ts` | Request IDs, concurrent responses, cancellation/termination behavior |

Coverage uses V8 and now inventories **all** production `src/**/*.ts` and
`src/**/*.tsx` files, excluding only tests and test fixtures. The 100%
statement/branch/function/line thresholds are unchanged.

The initial full-source audit on 2026-09-04 found only 110/3,362 statements
(3.27%) and 43/2,512 branches (1.71%); the former 100% report measured only
three transport modules. The completed strict suite now has **275 passing
tests** and literal 100% coverage: 3,379/3,379 statements, 2,470/2,470
branches, 941/941 functions, and 2,868/2,868 lines. Playwright and real-board
passes remain complementary evidence, not unmeasured coverage credit.

This tranche found and fixed stale asynchronous results crossing session or
page changes in waveform overviews, viewport payloads, decoder annotations,
markers, raw-window inspection, app session selection, background session
refresh, and eye-diagram analysis. Tests control response ordering at the
browser/network/worker boundaries; they do not mock the modules under test.

## Playwright modes

The suite always uses one worker because a physical MAX1000/FTDI connection is
exclusive.

| Environment | Behavior |
|---|---|
| `PLAYWRIGHT_USE_MOCK=1` | Starts Vite only; `mockApp.ts` intercepts API and WebSocket behavior |
| `PLAYWRIGHT_USE_MOCK=0` | Starts/reuses Vite and the real backend, then runs against hardware |
| unset | Specs may probe the backend and choose their supported path |
| `PLAYWRIGHT_HARDWARE_MATRIX=1` | Enables the strict 37-capture physical matrix |

```powershell
# Real board
$env:PLAYWRIGHT_USE_MOCK='0'
npm run test:e2e -- hardware.spec.ts

# Strict advertised-mode matrix
$env:PLAYWRIGHT_HARDWARE_MATRIX='1'
npm run test:e2e -- hardware-features.spec.ts
```

The 37-case matrix requires `status=passed`, a completed waveform, and an
effective-rate error below 2% for every advertised combination. Its manifest
is `frontend/test-results/screenshots/hardware-validated-matrix.json`.

## CI gates

`.github/workflows/test.yml` runs:

- host tests with 100% statement and branch coverage;
- backend tests with 100% statement and branch coverage;
- frontend typecheck/build and 100% statement, branch, function, and line
  coverage for all production TypeScript/TSX;
- Playwright mock E2E on Chromium;
- all 57 GHDL testbenches with no expected failures or exclusions.

`.github/workflows/hardware-matrix.yml` runs the two live Playwright suites on
a self-hosted runner labeled `max1000`. The workflow attempts a real connect
and fails when the board or native D2XX stack is unavailable. It runs manually,
on relevant pull requests, and weekly.

## Screenshot evidence

Durable screenshots live in `frontend/test-results/screenshots/`. Key files:

| Screenshot | Scope |
|---|---|
| `capture-controls.png` | Source/acquisition/rate/depth/compression controls |
| `capture-live-50mhz-latest.png` | Live rolling waveform |
| `capture-start-failure-toast.png` / `capture-ws-error-toast.png` | Error handling |
| `generator-page-latest.png` | Capability-driven generator routes |
| `bit-banger-preview-sweep.png` | Preset/script preview and sweep |
| `settings-control-denial.png` | Control-lock denial |
| `decoder-builder.png` | Decoder creation/run |
| `exports.png` | HTML, PDF, and PulseView-VCD downloads |
| `hardware-validated-matrix-*.png` | Real-board capture matrix |

The current matrix index is [Hardware Screenshot Matrix](../hardware-screenshot-matrix.md).
Decoder interaction details remain on [Decoder UI](decoder-ui.md).
