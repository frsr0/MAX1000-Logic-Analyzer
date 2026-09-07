import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results/playwright',
  // The live suites share one physical MAX1000/FTDI device and backend.
  // Parallel workers can interrupt one another's captures and lock channel B.
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 1400 },
    deviceScaleFactor: 1,
  },
  // The API backend (uvicorn on :8000) is started here whenever the suite may
  // need real hardware: forced-mock CI (PLAYWRIGHT_USE_MOCK=1) intercepts all
  // API routes and has no backend deps, so it is excluded there. In auto-detect
  // mode the specs probe /api/devices: a MAX1000 -> live suite, otherwise the
  // mock harness. reuseExistingServer lets a manually started backend (or the
  // dev's own instance) be reused.
  webServer: process.env.PLAYWRIGHT_USE_MOCK === '1'
    ? [{
        command: 'npm run dev -- --host 127.0.0.1 --port 4173',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }]
    : [{
        command: 'npm run dev -- --host 127.0.0.1 --port 4173',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }, {
        command: 'cd ../backend && python run.py',
        url: 'http://127.0.0.1:8000/api/status',
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      }],
  projects: [
    {
      name: 'chromium',
      // The device preset contains its own 1280x720 viewport, so keep the
      // documentation-sized viewport after the spread. Mixed-scan captures
      // need the height to show their digital and analog lanes together.
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1400 } },
    },
  ],
});
