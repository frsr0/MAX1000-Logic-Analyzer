# Build & Test

**Files:** `frontend/package.json`, `frontend/tsconfig.json`, `frontend/playwright.config.ts`, `frontend/vite.config.ts` (inferred)

## Build Configuration

### package.json

```json
{
  "name": "max1000-msa-frontend",
  "version": "2.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "test:e2e": "playwright test"
  }
}
```

### Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `react` | ^18.3.1 | UI framework |
| `react-dom` | ^18.3.1 | DOM rendering |
| `zustand` | ^4.5.4 | State management |

### Dev Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `@playwright/test` | ^1.61.1 | E2E testing |
| `@types/react` | ^18.3.3 | TypeScript types |
| `@types/react-dom` | ^18.3.0 | TypeScript types |
| `@vitejs/plugin-react` | ^4.3.1 | Vite React plugin |
| `typescript` | ^5.5.3 | TypeScript |
| `vite` | ^5.4.0 | Build tool |

### Commands

```bash
# Development (Vite dev server on :5173, proxies /api and /ws to :8000)
npm run dev

# Production build
npm run build       # tsc + vite build → frontend/dist/

# Type checking
npm run typecheck   # tsc --noEmit

# Preview production build
npm run preview
```

## Playwright E2E Tests

**Files:** `frontend/tests/e2e/hardware.spec.ts`, `frontend/tests/e2e/mockApp.ts`

### Configuration

```typescript
// playwright.config.ts
export default defineConfig({
  testDir: './tests/e2e',
  webServer: {
    command: 'cd backend && python run.py',       // starts backend with mock
    port: 8000,
    env: { PLAYWRIGHT_USE_MOCK: '1' },
  },
});
```

### Mock Mode

The `PLAYWRIGHT_USE_MOCK=1` env var sets the backend to use `MockDevice` instead of real hardware. All test scenarios (UART, I2C, SPI, analog, etc.) are available without hardware.

### Test Organization

| Test File | Tests | What it covers |
|---|---|---|
| `hardware.spec.ts` | 9 passed, 2 skipped | Full E2E flow through mock: connect, capture, decode, export |

### Screenshots

Test screenshots are saved to `frontend/test-results/screenshots/` for visual comparison:

| Screenshot | Description |
|---|---|
| `device-page.png` | Device page with mock connected |
| `capture-controls.png` | Capture settings panel |
| `capture-analog-fast.png` | Analog-fast capture result |
| `capture-compression-delta-rle.png` | Compressed capture readback |
| `generator-loopback-capture.png` | Generator self-test result |
| `generator-page-latest.png` | Generator route capabilities and protocol controls |
| `diagnostics-page-latest.png` | Diagnostics and control-plane tools |
| `bit-banger-preview-sweep.png` | Raw Bit Banger preview and parameter sweep |
| `mil-transaction.png` | Machine-in-loop request/response waveform |
| `swd-generator-capture.png` | SWD transaction capture result |
| `session-dashboard.png` | Protocol activity dashboard |
| `trigger-builder.png` | Pattern trigger preview |
| `decoder-builder.png` | Add-and-run decoder workflow |
| `raw-inspector.png` | Packed raw sample inspector |
| `markers-panel.png` | Named waveform marker |
| `eye-diagram.png` | Folded digital eye diagram |
| `channel-layout.png` | Channel visibility/layout controls |
| `command-palette.png` | Keyboard command palette |
| `analog-spectrum.png` | Analog spectrum analysis |
| `session-comparison.png` | Session alignment and divergence |
| `measurements.png` | Measurement panel |
| `exports.png` | HTML/PDF/PulseView export panel |
| `accelerometer-session-waveform.png` | LIS3DH WHO_AM_I waveform-viewer fixture |
| `live-accelerometer-session-waveform.png` | LIS3DH WHO_AM_I live hardware session |
| And many more (analog, mixed, diagnostics, sessions) |

### Running Tests

```bash
# Mock mode E2E
$env:PLAYWRIGHT_USE_MOCK='1'
npm run test:e2e -- hardware.spec.ts

# All tests
npx playwright test
```

---

# Decoder UI

**Files:** `frontend/src/decoders/DecoderTable.tsx` (name inferred from CapturePage imports)

## Purpose

UI integration for protocol decoders: annotation overlay on the waveform, packet table display, severity filtering, and event search.

## DecoderTable

Rendered at the bottom of `CapturePage`. Shows decoded events in a table:

| Column | Description |
|---|---|
| # | Event index |
| Time | Timestamp relative to trigger |
| Type | Event type icon/colour |
| Channel | Source channel |
| Label | Human-readable summary |
| Severity | normal | warning | error (colour-coded) |

Interactions:
- Click row → jump waveform to event position
- Severity filter (show/hide normal/warning/error)
- Search/filter by text
- Column sorting

## Annotation Overlay

Decoder events are rendered as coloured annotations above the waveform:
- Event type determines annotation colour
- Hover shows tooltip with full event data
- Click selects event and shows in packet table
- Stacked decoders show nested annotations

## Event Colours

| Severity | Colour |
|---|---|
| normal | Green |
| warning | Yellow/Amber |
| error | Red |

## Dependencies

| Module | File |
|---|---|
| `DecoderEvent` type | `api/types.ts` |
| `WaveformView` | `state/waveformStore.ts` |
| `WaveformCanvas` | `waveform/WaveformCanvas.tsx` |
