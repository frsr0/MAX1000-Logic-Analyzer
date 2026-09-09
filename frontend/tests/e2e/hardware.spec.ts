import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { writeArtifactWithRetry } from '../../src/test/artifactWrite';
import { installMockApp, screenshotsDir } from './mockApp';

/** True when the backend reports a MAX1000 available via /api/devices. */
function hardwareAvailable(payload: unknown): boolean {
  if (!payload || typeof payload !== 'object' || !('devices' in payload)) {
    return false;
  }
  const devices = payload.devices; // narrowed to unknown by `in`
  if (!Array.isArray(devices)) return false;
  return devices.some((d) => {
    if (!d || typeof d !== 'object' || !('id' in d) || !('available' in d)) {
      return false;
    }
    return d.id === 'hardware' && d.available === true;
  });
}

const shots = path.resolve(process.cwd(), screenshotsDir());
const mockOverride = process.env.PLAYWRIGHT_USE_MOCK; // '1' force mock | '0' force live | unset auto-detect
let resolvedMock: boolean | undefined;
let mockResolve: Promise<boolean> | undefined;

/**
 * Effective harness mode. PLAYWRIGHT_USE_MOCK=1/0 forces mock/live; unset means
 * auto-detect: a MAX1000 available to the backend (/api/devices -> hardware
 * entry available) runs the live suite, anything else (CI, no board, no
 * backend) falls back to the mock harness. The old default was mock, which
 * silently skipped the live tests even with hardware attached.
 */
async function effectiveMock(page: Page): Promise<boolean> {
  if (mockOverride === '1') return true;
  if (mockOverride === '0') return false;
  if (resolvedMock !== undefined) return resolvedMock;
  mockResolve ??= (async () => {
    // Probe /api/devices with short retries so a transient backend blip does
    // not silently flip a hardware run to the mock harness; log the resolved
    // mode (mock/live) and the reason exactly once.
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const r = await page.request.get('/api/devices', { timeout: 5000 });
        const body: unknown = r.ok() ? await r.json() : { devices: [] };
        if (hardwareAvailable(body)) {
          resolvedMock = false;
          console.log('[harness] resolved live mode: /api/devices advertises an available MAX1000');
        } else {
          resolvedMock = true;
          console.log('[harness] resolved mock mode: /api/devices advertises no available MAX1000');
        }
        return resolvedMock;
      } catch {
        if (attempt < 3) {
          await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
          continue;
        }
        resolvedMock = true;
        console.log('[harness] resolved mock mode: /api/devices unreachable after 3 attempts');
        return resolvedMock;
      }
    }
    return resolvedMock!;
  })();
  return mockResolve;
}
const liveClientId = process.env.PLAYWRIGHT_LIVE_CLIENT_ID ?? 'web_o0v91tvupd';

function shot(name: string) {
  return path.join(shots, name);
}

/**
 * Screenshot with retry. On Windows, Defender briefly locks freshly-written
 * PNGs when several are written in rapid succession (matrix runs), which
 * surfaces as "UNKNOWN: unknown error, open <path>". Space the writes out
 * and retry once the scan releases the file.
 */
async function takeScreenshot(page: any, name: string, opts: { fullPage?: boolean } = {}) {
  await page.waitForTimeout(150);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.screenshot({ path: shot(name), ...opts });
      return;
    } catch (err: any) {
      const msg = String(err?.message ?? err);
      if (!/UNKNOWN/.test(msg)) throw err;
      await page.waitForTimeout(300 * (attempt + 1));
    }
  }
  await page.screenshot({ path: shot(name), ...opts });
}

/** Capture a scrollable app page after expanding its internal content scroller.
 * Playwright's fullPage mode cannot see content hidden behind .content's
 * overflow:auto, which otherwise truncates the lower hardware cards. */
async function takeExpandedPageScreenshot(page: any, name: string) {
  await page.evaluate(() => {
    const content = document.querySelector('.content') as HTMLElement | null;
    const target = document.querySelector('.device-page') as HTMLElement | null;
    if (!content || !target) return;
    content.dataset.screenshotOverflow = content.style.overflow;
    content.dataset.screenshotHeight = content.style.height;
    target.dataset.screenshotHeight = target.style.height;
    target.dataset.screenshotOverflow = target.style.overflow;
    content.style.overflow = 'visible';
    content.style.height = 'auto';
    target.style.height = `${target.scrollHeight}px`;
    target.style.overflow = 'visible';
  });
  try {
    await takeScreenshot(page, name, { fullPage: true });
  } finally {
    await page.evaluate(() => {
      const content = document.querySelector('.content') as HTMLElement | null;
      const target = document.querySelector('.device-page') as HTMLElement | null;
      if (!content || !target) return;
      content.style.overflow = content.dataset.screenshotOverflow ?? '';
      content.style.height = content.dataset.screenshotHeight ?? '';
      target.style.height = target.dataset.screenshotHeight ?? '';
      target.style.overflow = target.dataset.screenshotOverflow ?? '';
      delete content.dataset.screenshotOverflow;
      delete content.dataset.screenshotHeight;
      delete target.dataset.screenshotHeight;
      delete target.dataset.screenshotOverflow;
    });
  }
}

async function takeElementScreenshot(page: any, selector: string, name: string) {
  await page.waitForTimeout(150);
  const element = page.locator(selector);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await element.screenshot({ path: shot(name) });
      return;
    } catch (err: any) {
      if (!/UNKNOWN/.test(String(err?.message ?? err))) throw err;
      await page.waitForTimeout(300 * (attempt + 1));
    }
  }
  await element.screenshot({ path: shot(name) });
}

async function captureState(page: any) {
  return page.evaluate(async () => {
    const res = await fetch('/api/capture/state');
    return res.json();
  });
}

async function deviceDebug(page: any) {
  return page.evaluate(async () => {
    const res = await fetch('/api/device/debug');
    return res.json();
  });
}

async function ensureConnected(page: any) {
  if (await effectiveMock(page)) return;
  await page.addInitScript((id) => {
    localStorage.setItem('msa_client_id', id);
  }, liveClientId);
  await page.goto('/');
  await page.getByRole('button', { name: 'Hardware', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'MAX1000 OLS Logic Analyzer' }).first()).toBeVisible();
  await page.evaluate(async () => {
    const clientId = localStorage.getItem('msa_client_id') ?? '';
    const res = await fetch('/api/control/acquire', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
      body: JSON.stringify({ name: 'playwright', force: true }),
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
  });
  await page.evaluate(async () => {
    const clientId = localStorage.getItem('msa_client_id') ?? '';
    const status = await fetch('/api/status', {
      headers: { 'X-Client-Id': clientId },
    }).then((res) => res.json());
    if (status.device_connected && status.device_kind === 'hardware') return;
    const res = await fetch('/api/connect', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
      body: JSON.stringify({ device_id: 'hardware' }),
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
  });
  await page.reload();
  await page.getByRole('button', { name: 'Hardware', exact: true }).click();
  await expect.poll(async () => {
    return page.evaluate(async () => {
      const res = await fetch('/api/status');
      const status = await res.json();
      return {
        held: Boolean(status.control?.held),
        holder_name: status.control?.holder_name ?? '',
        device_connected: Boolean(status.device_connected),
        device_kind: status.device_kind ?? null,
      };
    });
  }, { timeout: 15_000 }).toEqual({
    held: true,
    holder_name: 'playwright',
    device_connected: true,
    device_kind: 'hardware',
  });
  await expect(page.getByText('held by playwright')).toBeVisible({ timeout: 15_000 });
}

async function stopActiveCapture(page: any) {
  if (await effectiveMock(page)) return;
  await page.evaluate(async () => {
    const clientId = localStorage.getItem('msa_client_id') ?? '';
    await fetch('/api/capture/stop', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
    });
  });
}

async function waitForGeneratorProtocolOptions(page: any, timeoutMs = 15_000) {
  const options = page.getByLabel('Generator protocol').locator('option');
  const deadline = Date.now() + timeoutMs;
  let count = 0;
  while (Date.now() < deadline) {
    count = await options.count();
    if (count > 0) return count;
    await page.waitForTimeout(250);
  }
  return count;
}

async function listLiveSessions(page: any) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15_000);
  const data = await fetch('http://127.0.0.1:8000/api/sessions', {
    headers: {
      'X-Client-Id': liveClientId,
    },
    signal: controller.signal,
  }).then((res) => res.json());
  clearTimeout(timeout);
  return Array.isArray(data.sessions) ? data.sessions : [];
}

async function openLiveSession(page: any, query: string, requireDecoder = true) {
  const sessions = await listLiveSessions(page);
  const pick = sessions.find((s: any) => String(s.name) === query)
    ?? sessions.find((s: any) => String(s.name).includes(query));
  expect(pick, `expected a live session matching ${query}`).toBeTruthy();

  await page.getByRole('button', { name: 'Sessions', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  const nameBox = page.locator(`input.ch-name[value="${pick.name}"]`).first();
  await expect(nameBox).toBeVisible({ timeout: 15_000 });
  const row = nameBox.locator('xpath=ancestor::tr');
  await row.scrollIntoViewIfNeeded();
  await row.getByRole('button', { name: 'Open' }).click({ force: true });
  const canvas = page.getByLabel(`Waveform for ${pick.name}`);
  await expect(canvas).toBeVisible({ timeout: 15_000 });
  await expect(canvas).toHaveAttribute('aria-busy', 'false', { timeout: 15_000 });
  if (requireDecoder) {
    await expect(page.locator('.decoder-table')).toBeVisible({ timeout: 15_000 });
  }
}

test.beforeEach(async ({ page }) => {
  fs.mkdirSync(shots, { recursive: true });
  const isMockTest = test.info().title.toLowerCase().includes('mock');
  if (isMockTest) {
    // Mock-harness UI tests run in EVERY environment (CI, no board, or board
    // attached): the harness intercepts all API routes at the browser layer,
    // so they never touch the real device/backend and never conflict with the
    // live tests in the same run. Nothing hardware-dependent skips here.
    await installMockApp(page, {
      mockDevice: test.info().title.includes('mock device scenarios'),
      denyAcquire: test.info().title.includes('control lock denial'),
    });
    await page.goto('/');
    return;
  }
  const mockMode = await effectiveMock(page);
  if (mockMode) {
    await installMockApp(page, { mockDevice: false });
    await page.goto('/');
  } else {
    await ensureConnected(page);
  }
  await stopActiveCapture(page);
});

test.afterEach(async ({ page }) => {
  if (await effectiveMock(page)) return;
  await page.evaluate(async () => {
    const clientId = localStorage.getItem('msa_client_id') ?? '';
    await fetch('/api/generator/stop', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
    }).catch(() => {});
    await fetch('/api/capture/stop', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
    }).catch(() => {});
    await fetch('/api/control/release', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
    }).catch(() => {});
  }).catch(() => {});
});

test('hardware-aligned device page', async ({ page }) => {
  await page.getByRole('button', { name: 'Hardware', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Hardware', exact: true })).toBeVisible();
  await expect(page.getByText('held by playwright')).toBeVisible();
  await expect(page.locator('.hero-badges .badge-hw')).toContainText('200.4 MHz sample clock');
  await expect(page.getByRole('button', { name: 'Raw debug inspector' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run self-test' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Digital pin pool' })).toBeVisible();
  await expect(page.getByText('D0-D14, PMOD, sensor bus')).toBeVisible();
  await takeExpandedPageScreenshot(page, 'device-page.png');
});

test('capture controls reflect MAX1000 modes', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await expect(page.getByText('Capture source')).toBeVisible();
  await expect(page.getByText('Advanced transfer options')).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Digital capture' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Digital + analog' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'High-speed single channel' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Analog — one channel' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Analog — four channels' }).first()).toBeVisible();
  await expect(page.getByRole('option', { name: '200 MHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Digital capture' }).click();
  await page.getByText('Advanced transfer options').click();
  await page.getByRole('button', { name: 'Compressed', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Compressed', exact: true })).toHaveClass(/active/);
  await takeScreenshot(page, 'capture-compression-delta-rle.png', { fullPage: true });

  await page.getByRole('button', { name: 'Continuous view' }).click();
  await expect(page.getByRole('option', { name: '50 MHz' })).toBeAttached();
  await expect(page.getByText('Live compression buffer: ready')).toBeVisible();
  await takeScreenshot(page, 'capture-live-50mhz-latest.png', { fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Analog — one channel' }).click();
  await expect(page.getByText('High-speed analog captures one analog input (AIN3) at the best ADC rate.')).toBeVisible();
  await expect(page.getByRole('option', { name: '1 MHz' })).toBeAttached();
  await expect(page.getByText('Analog and mixed captures transfer without compression.')).toBeVisible();
  await takeScreenshot(page, 'capture-analog-fast.png', { fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Analog — four channels' }).click();
  await expect(page.getByText('Maximum analog captures the physical MAX1000 analog profile: AIN3, AIN1, AIN4, and AIN6.')).toBeVisible();
  await expect(page.getByRole('option', { name: '24 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Digital + analog' }).click();
  await expect(page.getByText('Mixed mode captures 16 digital bits plus ADC1/AIN3 and ADC2/AIN1, sampled together at a shared scan frame rate.')).toBeVisible();
  await expect(page.getByText('Analog and mixed captures transfer without compression.')).toBeVisible();
  await expect(page.getByRole('option', { name: '125 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'High-speed single channel' }).click();
  await expect(page.getByText('High-speed single-channel capture runs continuously (packed narrow hardware path).')).toBeVisible();
  await takeScreenshot(page, 'capture-controls.png', { fullPage: true });
});

test('mock device scenarios expose protocol and fault fixtures', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  const scenario = page.locator('label.field').filter({ hasText: 'Mock scenario' }).locator('select');
  await expect(scenario).toBeVisible();
  await expect(scenario.locator('option')).toHaveCount(17);
  await expect(scenario.locator('option[value="swd"]')).toBeAttached();
  await expect(scenario.locator('option[value="i2c_nack"]')).toBeAttached();
  await expect(scenario.locator('option[value="uart_fault"]')).toBeAttached();
  await scenario.selectOption('swd');
  await expect(scenario).toHaveValue('swd');

  // Observable consequence beyond the select's own value: the selected
  // scenario rides in the settings submitted with POST /api/capture/start
  // (CaptureControls.start -> api.startCapture).
  const startBodies: Array<{ settings?: { mock_scenario?: string | null } }> = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && new URL(req.url()).pathname === '/api/capture/start') {
      startBodies.push((req.postDataJSON() ?? {}) as { settings?: { mock_scenario?: string | null } });
    }
  });
  await page.locator('.panel-body button.primary.big').click();
  await expect.poll(() => startBodies.length).toBeGreaterThan(0);
  expect(startBodies[startBodies.length - 1].settings?.mock_scenario).toBe('swd');
});

test('mock device scenarios surface a capture start failure toast', async ({ page }) => {
  // Mock-only: the 'mock device scenarios' title makes beforeEach install the
  // mock device (device_kind 'mock'), which is what renders the scenario
  // select, and skips this test in live/hardware mode.
  await page.locator('.sidebar button[title="Capture"]').click();
  const scenario = page.locator('label.field').filter({ hasText: 'Mock scenario' }).locator('select');
  await expect(scenario).toBeVisible();
  await scenario.selectOption('capture_start_failure');
  await expect(scenario).toHaveValue('capture_start_failure');

  await page.locator('.panel-body button.primary.big').click();

  // POST /api/capture/start answers 500 { detail: 'Capture failed: ...' } for
  // the fixture scenario; client.ts throws ApiError(detail) and CaptureControls
  // start() toasts toast('error', e.message), rendered as .toast.toast-error.
  const toast = page.locator('.toast.toast-error').filter({ hasText: 'capture_start_failure' });
  await expect(toast).toBeVisible();
  await expect(toast).toContainText('Capture failed: mock capture rejected by fixture scenario (capture_start_failure)');
  await takeScreenshot(page, 'capture-start-failure-toast.png', { fullPage: true });
});

test('mock capture websocket emits a capture error toast', async ({ page }) => {
  // App.tsx renders a ws capture_error message as toast('error',
  // `Capture failed: ${msg.data.message}`) (App.tsx:77-79). The mock
  // WebSocket stub records every socket so the test can push a
  // backend-originated message into /ws/capture.
  const emitted = await page.evaluate(() => {
    type EmitterWindow = Window & { __mockWsEmit?: (urlSuffix: string, message: unknown) => boolean };
    return (window as unknown as EmitterWindow).__mockWsEmit?.('/ws/capture', {
      type: 'capture_error',
      data: { message: 'mock websocket capture rejected (fixture)' },
    });
  });
  expect(emitted).toBe(true);
  const toast = page.locator('.toast.toast-error').filter({
    hasText: 'Capture failed: mock websocket capture rejected (fixture)',
  });
  await expect(toast).toBeVisible();
  await takeScreenshot(page, 'capture-ws-error-toast.png', { fullPage: true });
});

test('mock capture websocket deduplicates and expires repeated narrow-mode warnings', async ({ page }) => {
  const emitted = await page.evaluate(() => {
    type EmitterWindow = Window & { __mockWsEmit?: (urlSuffix: string, message: unknown) => boolean };
    const message = { type: 'warning', data: { message: 'Packed 1-channel narrow digital mode on d0' } };
    const emit = (window as unknown as EmitterWindow).__mockWsEmit;
    return [emit?.('/ws/capture', message), emit?.('/ws/capture', message)];
  });
  expect(emitted).toEqual([true, true]);
  const toast = page.locator('.toast.toast-warning').filter({ hasText: 'Packed 1-channel narrow digital mode on d0' });
  await expect(toast).toHaveCount(1);
  await expect(toast).toBeVisible();
  await page.waitForTimeout(4200);
  await expect(toast).toHaveCount(0);
});

test('compression sweep shows raw and delta_rle throughput differences', async ({ page }) => {
  test.skip(await effectiveMock(page), 'live hardware only');
  test.setTimeout(240_000);

  const sampleCount = 50_000;
  const sweepRates = [1_000_000, 10_000_000];
  const codecs = ['raw', 'delta_rle'] as const;
  const results: Array<{
    rate_hz: number;
    codec: typeof codecs[number];
    elapsed_ms: number;
    throughput_msps: number;
    session_id: string | null;
    timings: Record<string, number | null>;
  }> = [];

  await page.locator('.sidebar button[title="Capture"]').click();
  await page.locator('.mode-tile', { hasText: 'Digital capture' }).click();
  await page.getByLabel('Capture length').selectOption(String(sampleCount));
  await page.getByText('Advanced transfer options').click();
  const compressionGroup = page.locator('.panel-body .seg-toggle[aria-label="Digital readback compression"]');

  for (const rate of sweepRates) {
    await page.getByLabel('Sample rate').selectOption(String(rate));
    await expect(page.getByLabel('Sample rate')).toHaveValue(String(rate));
    for (const codec of codecs) {
      await compressionGroup.getByRole('button', {
        name: codec === 'raw' ? 'Uncompressed' : 'Compressed',
        exact: true,
      }).click();
      const startedAt = Date.now();
      await page.locator('.panel-body button.primary.big').click();
      await expect.poll(async () => (await captureState(page)).state, {
        timeout: 90_000,
      }).toBe('done');
      await expect(page.locator('canvas.waveform-canvas')).toBeVisible({ timeout: 30_000 });
      const elapsedMs = Date.now() - startedAt;
      const throughputMsps = (sampleCount / (elapsedMs / 1000)) / 1_000_000;
      const state = await captureState(page);
      const debug = await deviceDebug(page);
      const timings = debug.timings ?? {};
      results.push({
        rate_hz: rate,
        codec,
        elapsed_ms: elapsedMs,
        throughput_msps: Number(throughputMsps.toFixed(3)),
        session_id: state.last_session_id ?? null,
        timings: {
          capture_s: timings.last_capture_s ?? null,
          wait_s: timings.last_capture_wait_s ?? null,
          readback_s: timings.last_capture_readback_s ?? null,
          blocks_s: timings[`last_readback_blocks_s_${codec}`] ?? null,
          decode_s: timings[`last_readback_decode_s_${codec}`] ?? null,
          raw_retry_s: timings[`last_readback_raw_retry_s_${codec}`] ?? null,
        },
      });
    await takeScreenshot(page, `compression-sweep-${rate}-${codec}.png`);
    }
  }

  const lines = [
    '# Compression sweep results',
    '',
    '| rate Hz | codec | elapsed ms | throughput Msps | capture ms | wait ms | readback ms | blocks ms | decode ms | retry ms | session |',
    '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |',
    ...results.map((r) => `| ${r.rate_hz.toLocaleString()} | ${r.codec} | ${r.elapsed_ms} | ${r.throughput_msps} | ${((r.timings.capture_s ?? 0) * 1000).toFixed(1)} | ${((r.timings.wait_s ?? 0) * 1000).toFixed(1)} | ${((r.timings.readback_s ?? 0) * 1000).toFixed(1)} | ${((r.timings.blocks_s ?? 0) * 1000).toFixed(1)} | ${((r.timings.decode_s ?? 0) * 1000).toFixed(1)} | ${((r.timings.raw_retry_s ?? 0) * 1000).toFixed(1)} | ${r.session_id ?? ''} |`),
    '',
    'Higher throughput means the hardware returned the capture faster for the same waveform window.',
  ];
  const writeArtifact = (filePath: string, data: string) =>
    fs.promises.writeFile(filePath, data).then(() => undefined);
  await writeArtifactWithRetry(path.join(shots, 'compression-sweep-results.md'), lines.join('\n'), { write: writeArtifact });
  await writeArtifactWithRetry(path.join(shots, 'compression-sweep-results.json'), `${JSON.stringify(results, null, 2)}\n`, { write: writeArtifact });

  const byRate = new Map<number, Record<string, number>>();
  for (const row of results) {
    const cur = byRate.get(row.rate_hz) ?? {};
    cur[row.codec] = row.throughput_msps;
    byRate.set(row.rate_hz, cur);
  }
  for (const [rate, row] of byRate.entries()) {
    expect(row.raw).toBeDefined();
    expect(row.delta_rle).toBeDefined();
  }

  // The meaningful, physically-sound difference: delta_rle MUST run the
  // decompression step and raw MUST NOT (the driver only records
  // last_readback_decode_s for the compressed path). A broken codec selector
  // that captures the same data twice leaves delta_rle's decode timing null.
  // Wall-clock throughput ordering is deliberately NOT asserted: RLE worst
  // case ships 2x the bytes, so delta_rle can legitimately be slower than raw
  // on incompressible signals.
  for (const rate of sweepRates) {
    const rawRow = results.find((r) => r.rate_hz === rate && r.codec === 'raw');
    const rleRow = results.find((r) => r.rate_hz === rate && r.codec === 'delta_rle');
    expect(rawRow, `raw sweep row missing for ${rate.toLocaleString()} Hz`).toBeDefined();
    expect(rleRow, `delta_rle sweep row missing for ${rate.toLocaleString()} Hz`).toBeDefined();
    expect(rawRow!.timings.blocks_s, `raw readback timing missing for ${rate.toLocaleString()} Hz`).not.toBeNull();
    expect(rleRow!.timings.blocks_s, `delta_rle readback timing missing for ${rate.toLocaleString()} Hz`).not.toBeNull();
    expect(rleRow!.timings.decode_s, `delta_rle decode timing missing at ${rate.toLocaleString()} Hz (codec selector broken?)`).not.toBeNull();
    expect(rawRow!.timings.decode_s, `raw codec unexpectedly ran a decode at ${rate.toLocaleString()} Hz`).toBeNull();
  }

});

test('generator page matches supported board protocols', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('heading', { name: 'Signal generator' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send and capture' })).toBeVisible({ timeout: 15_000 });
  const protocolCount = await waitForGeneratorProtocolOptions(page);
  // A generator-capabilities regression must FAIL this test, not silently
  // convert it into a skip that keeps CI green.
  expect(protocolCount, 'generator protocol options must render; a capabilities regression should fail, not skip').toBeGreaterThan(0);
  await expect(page.getByTestId('generator-route-capabilities')).toBeVisible();
  // The protocol options are driven by /api/generator/capabilities; assert
  // the rendered list matches the backend-advertised protocols instead of a
  // static copy (the old 'Hardware support on this board…' paragraph was
  // hard-coded JSX, not backend data).
  const caps = await page.evaluate(async () => {
    const res = await fetch('/api/generator/capabilities');
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ protocols: string[] }>;
  });
  expect(Array.isArray(caps.protocols) && caps.protocols.length).toBeGreaterThan(0);
  await expect(page.getByLabel('Generator protocol').locator('option'))
    .toHaveText(caps.protocols.map((p) => p.toUpperCase()));
  await takeScreenshot(page, 'generator-page-latest.png', { fullPage: true });
});

test('mock generator exposes Bit Banger templates and bounded preview controls', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  const protocol = page.getByLabel('Generator protocol');
  await protocol.selectOption('bitbang');
  await expect(page.getByLabel('Protocol template')).toBeVisible();
  await page.locator('select').filter({ has: page.locator('option[value="counter"]') }).selectOption('counter');
  await expect(page.getByLabel('Preset symbols')).toHaveValue('32');
  await page.getByRole('button', { name: 'Preview waveform' }).click();
  await expect(page.getByText(/symbols/).last()).toBeVisible();
  await page.getByRole('button', { name: 'Preview sweep' }).click();
  await expect(page.getByText('3/3 variants valid')).toBeVisible();
  await takeScreenshot(page, 'bit-banger-preview-sweep.png', { fullPage: true });
  await protocol.selectOption('uart');
  await page.getByRole('button', { name: 'Run sweep + capture' }).click();
  await expect(page.getByText('2/2 variants valid')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open capture' })).toHaveCount(2);
});

test('mock SWD generator captures and reports decoded transactions', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await page.getByLabel('Generator protocol').selectOption('swd');
  await expect(page.getByLabel('SWD requests (JSON)')).toBeVisible();
  await page.getByRole('button', { name: 'Send and capture' }).click();
  await expect(page.locator('.toast').filter({ hasText: 'decoded 1 SWD transaction' })).toBeVisible();
  await takeScreenshot(page, 'swd-generator-capture.png', { fullPage: true });
});

test('mock capture dashboard shows protocol activity and errors', async ({ page }) => {
  await page.getByRole('button', { name: 'Sessions', exact: true }).click();
  const row = page.locator('tr').filter({ has: page.locator('input[value="MAX1000 mixed analog sweep"]') }).first();
  await row.getByRole('button', { name: 'Open' }).click();
  await page.getByRole('button', { name: 'Dashboard' }).click();
  await expect(page.getByText('12', { exact: true })).toBeVisible();
  await expect(page.getByText('uart_byte').first()).toBeVisible();
  await expect(page.getByText('Activity heatmap')).toBeVisible();
  await expect(page.getByText('Bus transaction timeline')).toBeVisible();
  await expect(page.getByText('framing error')).toBeVisible();
  await expect(page.getByText('Suspect timing annotations')).toBeVisible();
  await expect(page.getByText('40 samples')).toBeVisible();
  await expect(page.getByRole('region', { name: 'CAN and LIN bus health summaries' })).toBeVisible();
  await takeScreenshot(page, 'session-dashboard.png', { fullPage: true });
});

test('mock trigger builder previews pattern qualifiers', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Trigger', exact: true }).click();
  await page.getByLabel('Start capture when').selectOption('pattern');
  await page.getByLabel('Bit pattern (1, 0, or x per channel)').fill('1x01');
  await expect(page.getByLabel('Trigger preview')).toBeVisible();
  await expect(page.getByLabel('Trigger preview')).toContainText('1x01');
  await expect(page.getByLabel('Trigger preview')).toContainText("don't care");
  await takeScreenshot(page, 'trigger-builder.png', { fullPage: true });
});

test('mock decoder builder adds and runs a decoder instance', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Decoders' }).click();
  await page.getByRole('button', { name: '+ Add decoder' }).click();
  await expect(page.getByLabel('Decoder')).toBeVisible();
  await page.getByLabel('Decoder').selectOption('uart');
  await page.getByRole('button', { name: 'Add & run' }).click();
  await expect(page.locator('.decoder-card')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '+ Add decoder' })).toBeVisible();
  await takeScreenshot(page, 'decoder-builder.png', { fullPage: true });
});

test('mock raw inspector loads packed samples and supports paging', async ({ page }) => {
  // The raw inspector needs an active session (it renders 'No session open'
  // otherwise), so open the mixed-sweep fixture session first.
  await page.getByRole('button', { name: 'Sessions', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  const sessionRow = page.locator('tr').filter({
    has: page.locator('input[value="MAX1000 mixed analog sweep"]'),
  }).first();
  await sessionRow.getByRole('button', { name: 'Open' }).click();
  await expect(page.locator('canvas.waveform-canvas')).toBeVisible();

  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Raw data', exact: true }).click();
  // Scope to the raw inspector's table (header row sample|hex|bits) — other
  // tables on the page also match '.table-scroll table.data-table'.
  const rawTable = page.locator('table.data-table').filter({
    has: page.locator('th', { hasText: 'hex' }),
  });
  await expect(rawTable).toBeVisible();
  // The inspector opens at waveformView.start/cursorA (not necessarily 0), so
  // derive the expected values from the FIRST rendered row instead of
  // assuming a fixed start. Fixture values are ((start+i)*3)&0xffff.
  const firstRow = rawTable.locator('tbody tr').first();
  const start0 = Number(await firstRow.locator('td').nth(0).textContent());
  expect(Number.isInteger(start0) && start0 >= 0).toBeTruthy();
  await expect(firstRow.locator('td').nth(1)).toHaveText(
    `0x${((start0 * 3) & 0xffff).toString(16).toUpperCase().padStart(4, '0')}`);
  // ⟩ pages forward by the 64-row window: the first row's sample address
  // must advance to start + count and show the value at the new absolute
  // sample, proving the window actually moved (the old fixture ignored
  // start/end and kept re-showing page 1).
  await page.getByRole('button', { name: '⟩', exact: true }).last().click();
  await expect(firstRow.locator('td').nth(0)).toHaveText(String(start0 + 64));
  await expect(firstRow.locator('td').nth(1)).toHaveText(
    `0x${(((start0 + 64) * 3) & 0xffff).toString(16).toUpperCase().padStart(4, '0')}`);
  await takeScreenshot(page, 'raw-inspector.png', { fullPage: true });
});

test('mock marker panel adds a named bookmark from waveform hover', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.locator('canvas.waveform-canvas').hover({ position: { x: 420, y: 80 } });
  await page.getByRole('button', { name: 'Markers' }).click();
  await page.getByPlaceholder('marker label').fill('bus-start');
  await page.getByRole('button', { name: '@ hover' }).click();
  await expect(page.getByText('bus-start', { exact: true })).toBeVisible();
  await takeScreenshot(page, 'markers-panel.png', { fullPage: true });
});

test('mock eye diagram folds a digital channel at a configured rate', async ({ page }) => {
  await page.getByRole('button', { name: 'Sessions', exact: true }).click();
  const row = page.locator('tr').filter({ has: page.locator('input[value="MAX1000 mixed analog sweep"]') }).first();
  await row.getByRole('button', { name: 'Open' }).click();
  await page.getByRole('button', { name: 'Eye diagram', exact: true }).click();
  await page.getByLabel('Bit/clock rate (baud)').fill('10000');
  await page.getByRole('button', { name: 'Compute eye diagram' }).click();
  await expect(page.getByText(/24 folded traces/)).toBeVisible();
  const eye = await page.evaluate(async () => {
    const response = await fetch('/api/sessions/session-analog/eye?channel=d0&baud=10000');
    return response.json() as Promise<{ baud: number; unit_samples: number; grid: number[][] }>;
  });
  expect(eye.baud).toBe(10_000);
  expect(eye.unit_samples).toBeGreaterThan(8);
  const rails = eye.grid.slice(8, 18).flat().reduce((sum, value) => sum + value, 0)
    + eye.grid.slice(46, 56).flat().reduce((sum, value) => sum + value, 0);
  const center = eye.grid.slice(25, 40).flat().reduce((sum, value) => sum + value, 0);
  expect(rails, 'eye fixture should show stable high/low rails').toBeGreaterThan(center * 2);
  await expect(page.getByLabel('Eye diagram')).toBeVisible();
  await takeScreenshot(page, 'eye-diagram.png', { fullPage: true });
});

test('mock channel panel saves a visibility layout and exposes drag ordering', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Inputs', exact: true }).click();
  await expect(page.locator('.channel-row[draggable="true"]').first()).toBeVisible();
  await page.getByLabel('Channel layout name').fill('smoke');
  await page.getByRole('button', { name: 'Save layout' }).click();
  await expect(page.getByText("Channel layout 'smoke' saved")).toBeVisible();
  await page.getByRole('button', { name: 'Digital only' }).click();
  await takeScreenshot(page, 'channel-layout.png', { fullPage: true });
});

test('command palette navigates between app pages', async ({ page }) => {
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true })));
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  await takeScreenshot(page, 'command-palette.png', { fullPage: true });
  await page.getByLabel('Command search').fill('generator');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Signal generator' })).toBeVisible();
});

test('command palette exposes capture, decode, trigger, and export actions', async ({ page }) => {
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true })));
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Start or stop capture/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Run first decoder/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Search current trigger/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Export session JSON/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Export HTML report/ })).toBeVisible();
});

test('signal generator loopback shows waveform and decode', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('button', { name: 'Send and capture' })).toBeEnabled({ timeout: 15_000 });
  const protocolCount = await waitForGeneratorProtocolOptions(page);
  // A generator-capabilities regression must FAIL this test, not silently
  // convert it into a skip that keeps CI green.
  expect(protocolCount, 'generator protocol options must render; a capabilities regression should fail, not skip').toBeGreaterThan(0);
  // Self-sufficient in BOTH modes: run the real Send and capture (mock harness
  // or live hardware) instead of opening a pre-existing session — the backend
  // creates the 'Generator self-test (uart)' session this flow produces.
  await page.getByLabel('TX pin').fill('3');
  await page.getByRole('button', { name: 'Send and capture' }).click({ timeout: 15_000 });
  const generatorResult = page.locator('.card').filter({
    has: page.getByRole('heading', { name: 'Result' }),
  });
  await expect(generatorResult.getByText('PASS', { exact: true })).toBeVisible({ timeout: 45_000 });
  await expect(generatorResult.getByText('decoded:')).toBeVisible();
  await expect(generatorResult.getByText('Open loopback capture')).toBeVisible({ timeout: 45_000 });
  await page.getByRole('button', { name: 'Open loopback capture' }).click();
  await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
  await expect(page.locator('.decoder-table')).toBeVisible();
  await expect(page.locator('.decoder-table .table-toolbar select option').first()).toBeAttached();
  await takeScreenshot(page, 'generator-loopback-capture.png', { fullPage: true });
});

test('machine-in-loop transaction shows request and response waveforms', async ({ page }) => {
  await page.getByRole('button', { name: 'Hardware lab' }).click();
  await page.getByRole('button', { name: 'Load' }).click();
  await expect(page.getByRole('button', { name: 'Start emulator' })).toBeEnabled({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Start emulator' }).click();
  await expect(page.getByRole('button', { name: 'Send to emulator' })).toBeEnabled({ timeout: 15_000 });
  await page.getByRole('button', { name: 'Send to emulator' }).click();
  await expect(page.getByText('TX / RX waveforms')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('request:')).toBeVisible();
  await expect(page.getByText('response:')).toBeVisible();
  const milResult = page.locator('.card').filter({
    has: page.getByRole('heading', { name: 'Commands' }),
  });
  // Both directions must render in the Commands card: the read command
  // ('Example read') and the RESPONSE action / 'response:' line. Regex with
  // the /i flag is used because the read option is rendered lowercase.
  await expect(milResult).toContainText(/READ/i);
  await expect(milResult).toContainText(/RESPONSE/i);
  await takeScreenshot(page, 'mil-transaction.png', { fullPage: true });
});

test('settings page keeps control lock and viewer settings clear', async ({ page }) => {
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  // Lock state comes from /api/status control.holder_name (backend data),
  // not static copy: mock fixture reports 'Playwright', live 'playwright'
  // (getByText is case-insensitive).
  await expect(page.getByText('held by playwright')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Appearance' })).toBeVisible();
  await takeScreenshot(page, 'settings-page.png', { fullPage: true });
});

test('mock control lock denial toasts when acquire is denied', async ({ page }) => {
  // Mock-only: the 'control lock denial' title makes beforeEach install the
  // harness with denyAcquire, so POST /api/control/acquire answers
  // { acquired: false } (the backend shape from backend/app/api/status.py)
  // and SettingsPage toasts the rendered denial.
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await page.getByRole('button', { name: 'Acquire control' }).click();
  const toast = page.locator('.toast.toast-warning').filter({ hasText: 'Another client holds control' });
  await expect(toast).toBeVisible();
  await takeScreenshot(page, 'settings-control-denial.png', { fullPage: true });
});

test('diagnostics page shows the control plane without hardware', async ({ page }) => {
  await page.getByRole('button', { name: 'Diagnostics' }).click();
  await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeVisible();
  await expect(page.getByText('Mock captures')).toBeVisible();
  await takeScreenshot(page, 'diagnostics-page-latest.png', { fullPage: true });
});

test('live hardware sessions show waveform screenshots across digital and analog modes', async ({ page }) => {
  // Hardware presence is the only gate: with a MAX1000 attached this runs
  // (auto-detected live mode), without one it skips. The old
  // PLAYWRIGHT_LIVE_SESSION_SCREENSHOTS=1 opt-in silently skipped it on
  // hardware.
  test.skip(await effectiveMock(page), 'real hardware sessions only exist in live mode');
  test.setTimeout(240_000);

  await page.getByRole('button', { name: 'Hardware', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Hardware', exact: true })).toBeVisible();
  await expect(page.getByText('held by playwright')).toBeVisible();
  await takeExpandedPageScreenshot(page, 'live-device-page.png');

  await page.locator('.sidebar button[title="Capture"]').click();
  await expect(page.getByText('Capture source')).toBeVisible();
  await expect(page.getByText('Advanced transfer options')).toBeVisible();
  await takeScreenshot(page, 'live-capture-controls.png', { fullPage: true });

  // The generator loopback session is created by the 'signal generator
  // loopback' test earlier in this file; the picks loop below opens it if
  // present (and fails if it is missing but expected), so no hard dependency
  // on pre-existing data here.
  await page.evaluate(async () => {
    const clientId = localStorage.getItem('msa_client_id') ?? '';
    const res = await fetch('/api/diagnostics/live-accel-session', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Client-Id': clientId,
      },
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
  });

  await openLiveSession(page, 'LIS3DH WHO_AM_I live');
  await takeScreenshot(page, 'live-accelerometer-session-waveform.png', { fullPage: true });

  const sessions = await listLiveSessions(page);
  const picks = [
    { query: 'Generator self-test (uart)', shot: 'live-generator-session-waveform.png', decoder: true },
    { query: 'MIL transaction - Modbus RTU demo', shot: 'live-mil-session-waveform.png', decoder: true },
    { query: 'LIS3DH WHO_AM_I live', shot: 'live-accelerometer-session-waveform.png', decoder: true },
    { query: 'HW validated Analog — one channel single 1000000', shot: 'live-analog-fast-waveform.png', decoder: false },
    { query: 'HW validated Analog — four channels single 24000', shot: 'live-maximum-analog-waveform.png', decoder: false },
    { query: 'HW validated Digital + analog single 125000', shot: 'live-mixed-analog-waveform.png', decoder: false },
  ];
  expect(sessions.some((s: any) => String(s.name).includes('HW validated Analog — one channel single 1000000')),
    'run hardware-features.spec.ts with PLAYWRIGHT_HARDWARE_MATRIX=1 before refreshing live screenshots').toBeTruthy();

  const failures: string[] = [];
  for (const pick of picks) {
    try {
      await openLiveSession(page, pick.query, pick.decoder);
      await takeScreenshot(page, pick.shot);
    } catch (err: unknown) {
      // Collect per-session failures instead of silently swallowing them: a
      // partial render regression must fail the test, not just skip a shot.
      failures.push(`${pick.query}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  expect(failures, `live session render failures:\n${failures.join('\n')}`).toEqual([]);
});

test.describe('mock fixture sessions', () => {
  test('analog session renders waveforms and decode on the mock fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions', exact: true }).click();
    const analogRow = page.locator('tr').filter({
      has: page.locator('input[value="MAX1000 mixed analog sweep"]'),
    }).first();
    await expect(analogRow).toBeVisible();
    await analogRow.getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
    await expect(page.locator('.decoder-table')).toBeVisible();
    await expect(page.locator('.decoder-table tbody tr').first()).toContainText('START');

    await page.getByRole('button', { name: 'Inputs', exact: true }).click();
    await expect(page.getByRole('option', { name: 'a1 (analog)' })).toBeAttached();
    await expect(page.getByRole('option', { name: 'a2 (analog)' })).toBeAttached();
    await expect(page.getByRole('option', { name: 'a0 (analog)' })).toHaveCount(0);
    const canvas = page.getByLabel('Waveform for MAX1000 mixed analog sweep');
    await expect(canvas).toHaveAttribute('aria-busy', 'false');
    await page.getByRole('button', { name: 'Capture setup', exact: true }).click();
    await expect(page.getByLabel('Loaded session metadata')).toContainText('Digital + analog');
    await expect(page.getByLabel('Loaded session metadata')).toContainText('125.0 kHz');
    await expect(page.getByLabel('Loaded session metadata')).toContainText('ADC1/AIN3');
    await expect(page.getByLabel('Loaded session metadata')).toContainText('ADC2/AIN1');
    await expect(page.getByLabel('Next capture configuration')).toContainText('Next capture');
    await expect(page.getByLabel('Sample rate')).toHaveValue('1000000');

    await takeScreenshot(page, 'analog-session-waveform.png', { fullPage: true });
  });

  test('mock analog panel computes a spectrum from the mixed capture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions', exact: true }).click();
    const analogRow = page.locator('tr').filter({ has: page.locator('input[value="MAX1000 mixed analog sweep"]') }).first();
    await analogRow.getByRole('button', { name: 'Open' }).click();
    await page.getByRole('button', { name: 'Analog', exact: true }).click();
  await page.getByRole('button', { name: 'Compute spectrum' }).click();
  await expect(page.getByText('Peaks: 1.00 kHz', { exact: true })).toBeVisible();
  await takeScreenshot(page, 'analog-spectrum.png', { fullPage: true });
  });

  test('accelerometer session renders waveform and decode on the mock fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions', exact: true }).click();
    const accelRow = page.locator('tr').filter({
      has: page.locator('input[value="LIS3DH WHO_AM_I dialogue"]'),
    }).first();
    await expect(accelRow).toBeVisible();
    await accelRow.getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
    await expect(page.locator('.decoder-table')).toBeVisible();
    await expect(page.getByRole('cell', { name: 'START', exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: '0x33', exact: true })).toBeVisible();

    await takeScreenshot(page, 'accelerometer-session-waveform.png', { fullPage: true });
  });

  test('session comparison shows alignment and first divergence on the mock fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions', exact: true }).click();
    const rows = page.locator('.sessions-table tbody tr');
    await expect(rows).toHaveCount(3);

    await rows.nth(0).getByRole('button', { name: 'Cmp...' }).click();
    await rows.nth(1).getByRole('button', { name: 'Cmp!' }).click();

    await expect(page.getByText(/Applied alignment: 2 samples/)).toBeVisible();
    await expect(page.getByText(/first divergence A 420 \/ B 418/)).toBeVisible();
    await expect(page.getByText('Timing deltas')).toBeVisible();
    await expect(page.getByText('0.50').first()).toBeVisible();
    await takeScreenshot(page, 'session-comparison.png', { fullPage: true });
  });

  test('mock trigger search auto-scopes the decoder window', async ({ page }) => {
    await page.locator('.sidebar button[title="Capture"]').click();
    await page.getByRole('button', { name: 'Trigger', exact: true }).click();
    await page.getByLabel('Start capture when').selectOption('any_edge');
    await page.getByRole('button', { name: 'Search existing capture' }).click();
  await expect(page.getByText(/scoped decoder to 1 event/)).toBeVisible();
    await expect(page.getByText(/pre-trigger 0 samples/)).toBeVisible();
    await takeScreenshot(page, 'trigger-decoder-auto-scope.png', { fullPage: true });
  });

  test('mock capture queue submits, polls, and reports the resulting session', async ({ page }) => {
    await page.locator('.sidebar button[title="Capture"]').click();
    await page.getByRole('button', { name: 'Run in background' }).click();
    await expect(page.getByText(/Headless job done/)).toBeVisible();
    await expect(page.getByText(/session session-demo/)).toBeVisible();
    await takeScreenshot(page, 'capture-job-queue.png', { fullPage: true });
  });

  test('mock dashboard exposes CAN and LIN health summaries', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions', exact: true }).click();
    const row = page.locator('tr').filter({ has: page.locator('input[value="MAX1000 mixed analog sweep"]') }).first();
    await row.getByRole('button', { name: 'Open' }).click();
    await page.getByRole('button', { name: 'Dashboard', exact: true }).click();
    await expect(page.getByText(/CAN health 3 frame/)).toBeVisible();
    await expect(page.getByText(/LIN health 2 frame/)).toBeVisible();
    await expect(page.getByText(/checksum error/)).toBeVisible();
    await expect(page.getByRole('region', { name: 'CAN and LIN bus health summaries' })).toBeVisible();
    await takeElementScreenshot(page, '[role="region"][aria-label="CAN and LIN bus health summaries"]', 'can-lin-health.png');
  });

  test('mock measurement panel renders a fixture result and recomputes it', async ({ page }) => {
    await page.getByRole('button', { name: 'Measure' }).click();
    const measurementRow = page.locator('.side-panel .data-table tbody tr').first();
    await expect(measurementRow).toContainText('Frequency');
    await expect(measurementRow).toContainText('115.2000 kHz');
  await page.getByRole('button', { name: /Recompute all/ }).click();
  await expect(measurementRow).toContainText('115.2000 kHz');
  await takeScreenshot(page, 'measurements.png', { fullPage: true });
  });

  test('mock export panel downloads a report and PulseView-compatible VCD', async ({ page }) => {
    await page.getByRole('button', { name: 'Export' }).click();
    await expect(page.getByRole('button', { name: 'HTML report' })).toBeVisible();
    await page.getByRole('button', { name: 'HTML report' }).click();
    await expect(page.getByText('REPORT export downloaded')).toBeVisible();
    await page.getByRole('button', { name: 'PDF report' }).click();
    await expect(page.getByText('PDF export downloaded')).toBeVisible();
  await page.getByRole('button', { name: 'PulseView-compatible VCD' }).click();
  await expect(page.getByText('PULSEVIEW export downloaded')).toBeVisible();
  await takeScreenshot(page, 'exports.png', { fullPage: true });
  });
  });
