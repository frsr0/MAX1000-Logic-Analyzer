import { defineConfig, devices } from '@playwright/test';

const isCi = Boolean(process.env.CI);

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results/playwright',
  globalSetup: isCi ? './playwright.global-setup.ts' : undefined,
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
  // CI uses the project-owned global setup so Windows cleanup can terminate
  // the exact child processes without Playwright's shell process-group hang.
  // Local runs retain Playwright webServer/reuseExistingServer behavior. In
  // auto-detect mode the specs probe /api/devices: a MAX1000 -> live suite,
  // otherwise the mock harness.
  webServer: isCi ? undefined : process.env.PLAYWRIGHT_USE_MOCK === '1'
    ? [{
        command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }]
    : [{
        command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173',
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
