import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { installMockApp, screenshotsDir } from './mockApp';

const shots = path.resolve(process.cwd(), screenshotsDir());
const useMockHarness = process.env.PLAYWRIGHT_USE_MOCK !== '0';
const liveClientId = process.env.PLAYWRIGHT_LIVE_CLIENT_ID ?? 'web_o0v91tvupd';

function shot(name: string) {
  return path.join(shots, name);
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
  if (useMockHarness) return;
  await page.addInitScript((id) => {
    localStorage.setItem('msa_client_id', id);
  }, liveClientId);
  await page.goto('/');
  await page.getByRole('button', { name: 'Device' }).click();
  await expect(page.getByRole('heading', { name: 'MAX1000 OLS Logic Analyzer' })).toBeVisible();
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
  await page.reload();
  await page.getByRole('button', { name: 'Device' }).click();
  await expect(page.getByText('held by playwright')).toBeVisible({ timeout: 15_000 });
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

test.beforeEach(async ({ page }) => {
  fs.mkdirSync(shots, { recursive: true });
  if (!useMockHarness && test.info().title.includes('mock fixture')) {
    test.skip(true, 'fixture session is only available in mock mode');
  }
  if (useMockHarness) {
    await installMockApp(page);
  }
  await ensureConnected(page);
});

test('hardware-aligned device page', async ({ page }) => {
  await page.getByRole('button', { name: 'Device' }).click();
  await expect(page.getByRole('heading', { name: 'Device' })).toBeVisible();
  await expect(page.getByText('held by playwright')).toBeVisible();
  await expect(page.locator('.hero-badges .badge-hw')).toContainText('200.4 MHz sample clock');
  await expect(page.getByRole('button', { name: 'Raw debug inspector' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run self-test' })).toBeVisible();
  await page.screenshot({ path: shot('device-page.png'), fullPage: true });
});

test('capture controls reflect MAX1000 modes', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await expect(page.getByText('Hardware mode')).toBeVisible();
  await expect(page.getByText('Readback codec')).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Digital deep' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Mixed scan' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Packed narrow' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Analog fast' }).first()).toBeVisible();
  await expect(page.locator('.mode-tile', { hasText: 'Analog wide' }).first()).toBeVisible();
  await expect(page.getByRole('option', { name: '200 MHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Digital deep' }).click();
  await page.getByRole('button', { name: 'DELTA RLE' }).click();
  await expect(page.getByRole('button', { name: 'DELTA RLE' })).toHaveClass(/active/);
  await page.screenshot({ path: shot('capture-compression-delta-rle.png'), fullPage: true });

  await page.getByRole('button', { name: 'Live ring' }).click();
  await expect(page.getByRole('option', { name: '50 MHz' })).toBeAttached();
  await page.screenshot({ path: shot('capture-live-50mhz.png'), fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Analog fast' }).click();
  await expect(page.getByText('High-speed analog uses one physical analog input at the best ADC rate.')).toBeVisible();
  await expect(page.getByRole('option', { name: '1 MHz' })).toBeAttached();
  await expect(page.getByText('Analog and mixed captures use raw readback.')).toBeVisible();
  await page.screenshot({ path: shot('capture-analog-fast.png'), fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Analog wide' }).click();
  await expect(page.getByText('Maximum analog exposes the full board analog map, including the dedicated AIN pin.')).toBeVisible();
  await expect(page.getByRole('option', { name: '125 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Mixed scan' }).click();
  await expect(page.getByText('Mixed mode captures 16 digital bits plus ADC0-ADC7 at a shared scan frame rate.')).toBeVisible();
  await expect(page.getByText('Analog and mixed captures use raw readback.')).toBeVisible();
  await expect(page.getByRole('option', { name: '125 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Packed narrow' }).click();
  await expect(page.getByText('Packed narrow is live-only.')).toBeVisible();
  await page.screenshot({ path: shot('capture-controls.png'), fullPage: true });
});

test('compression sweep shows raw and delta_rle throughput differences', async ({ page }) => {
  test.skip(useMockHarness, 'live hardware only');
  test.setTimeout(240_000);

  const sampleCount = 250_000;
  const sweepRates = [1_000_000, 10_000_000, 50_000_000];
  const codecs = ['raw', 'delta_rle'] as const;
  const results: Array<{
    rate_hz: number;
    codec: typeof codecs[number];
    elapsed_ms: number;
    throughput_msps: number;
    session_id: string | null;
    timings: Record<string, number | null>;
  }> = [];

  await page.getByRole('button', { name: 'Generator' }).click();
  await page.getByLabel('Generator protocol').selectOption('pwm');
  await page.getByLabel('Frequency (Hz)').fill('1');
  await page.getByLabel('Duty (%)').fill('50');
  await page.getByLabel('Output pin').fill('0');
  await page.getByRole('button', { name: 'Send', exact: true }).click();

  await page.locator('.sidebar button[title="Capture"]').click();
  await page.locator('.mode-tile', { hasText: 'Digital deep' }).click();
  await page.getByLabel('Samples').selectOption(String(sampleCount));
  const compressionGroup = page.locator('.panel-body .seg-toggle[aria-label="Digital readback compression"]');

  for (const rate of sweepRates) {
    await page.getByLabel('Sample rate').selectOption(String(rate));
    await expect(page.getByLabel('Sample rate')).toHaveValue(String(rate));
    for (const codec of codecs) {
      await compressionGroup.getByRole('button', {
        name: codec === 'raw' ? 'RAW' : 'DELTA RLE',
        exact: true,
      }).click();
      const startedAt = Date.now();
      await page.locator('.panel-body button.primary.big').click();
      await expect.poll(async () => (await captureState(page)).progress?.samples_read, {
        timeout: 90_000,
      }).toBe(sampleCount);
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
    await page.screenshot({ path: shot(`compression-sweep-${rate}-${codec}.png`) });
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
  fs.writeFileSync(path.join(shots, 'compression-sweep-results.md'), lines.join('\n'));
  fs.writeFileSync(path.join(shots, 'compression-sweep-results.json'), `${JSON.stringify(results, null, 2)}\n`);

  const byRate = new Map<number, Record<string, number>>();
  for (const row of results) {
    const cur = byRate.get(row.rate_hz) ?? {};
    cur[row.codec] = row.throughput_msps;
    byRate.set(row.rate_hz, cur);
  }
  for (const [rate, row] of byRate.entries()) {
    expect(row.raw).toBeDefined();
    expect(row.delta_rle).toBeDefined();
    const values = [row.raw!, row.delta_rle!];
    expect(Math.max(...values) - Math.min(...values)).toBeGreaterThan(Math.max(...values) * 0.1);
  }

  await page.screenshot({ path: shot('compression-sweep-summary.png'), fullPage: true });
});

test('generator page matches supported board protocols', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('heading', { name: 'Signal generator' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send + capture' })).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: shot('generator-page.png'), fullPage: true });
});

test('signal generator loopback shows waveform and decode', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('button', { name: 'Send + capture' })).toBeEnabled({ timeout: 15_000 });
  await page.getByLabel('TX pin').fill('3');
  await page.getByRole('button', { name: 'Send + capture' }).click({ timeout: 15_000 });
  const generatorResult = page.locator('.card').filter({
    has: page.getByRole('heading', { name: 'Result' }),
  });
  await expect(generatorResult.getByText('PASS', { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(generatorResult.getByText('decoded:')).toBeVisible();
  await expect(generatorResult.getByText('Open loopback capture')).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Open loopback capture' }).click();
  await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
  await expect(page.locator('.decoder-table')).toBeVisible();
  await expect(page.locator('.decoder-table .table-toolbar select option').first()).toBeAttached();
  await expect(page.locator('.decoder-table tbody tr').first()).toContainText('uart_byte');
  await expect(page.locator('.decoder-table tbody tr').first()).toContainText('0x48');
  await expect(page.locator('.decoder-table tbody tr').first()).toContainText('H');
  await page.screenshot({ path: shot('generator-loopback-capture.png'), fullPage: true });
});

test('bit banger loopback shows waveform and decode', async ({ page }) => {
  await page.getByRole('button', { name: 'MIL' }).click();
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
  await expect(milResult.getByText('READ', { exact: true })).toBeVisible();
  await page.screenshot({ path: shot('bit-banger-loopback-capture.png'), fullPage: true });
});

test('settings page keeps control lock and viewer settings clear', async ({ page }) => {
  await page.getByRole('button', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();
  await expect(page.getByText('Acquire the control lock before sending capture or generator commands to the hardware.')).toBeVisible();
  await page.screenshot({ path: shot('settings-page.png'), fullPage: true });
});

test('diagnostics page shows the control plane without hardware', async ({ page }) => {
  await page.getByRole('button', { name: 'Diagnostics' }).click();
  await expect(page.getByRole('heading', { name: 'Diagnostics' })).toBeVisible();
  await expect(page.getByText('Mock captures')).toBeVisible();
  await page.screenshot({ path: shot('diagnostics-page.png'), fullPage: true });
});

test('live hardware sessions show waveform screenshots across digital and analog modes', async ({ page }) => {
  test.skip(useMockHarness, 'real hardware sessions only exist in live mode');
  test.skip(process.env.PLAYWRIGHT_LIVE_SESSION_SCREENSHOTS !== '1',
    'optional live session screenshot pass; core hardware validation already covers generator and MIL waveforms');

  const sessions = await listLiveSessions(page);
  const picks = [
    { query: 'Generator self-test (uart)', shot: 'live-generator-session-waveform.png' },
    { query: 'MIL transaction - Modbus RTU demo', shot: 'live-mil-session-waveform.png' },
    { query: 'HW smoke test capture', shot: 'live-hw-smoke-session-waveform.png' },
  ].filter((pick) => sessions.some((s: any) => String(s.name).includes(pick.query)));
  expect(picks.length).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Sessions' }).click();
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();

  for (const pick of picks) {
    const row = page.locator('tr').filter({
      has: page.locator(`input[value="${pick.query}"]`),
    }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('canvas.waveform-canvas')).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: shot(pick.shot) });
    await page.getByRole('button', { name: 'Sessions' }).click();
    await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  }
});

test('analog session renders waveforms and decode on the mock fixture', async ({ page }) => {
  test.skip(!useMockHarness, 'fixture session is only available in mock mode');

  await page.getByRole('button', { name: 'Sessions' }).click();
  const analogRow = page.locator('tr', { hasText: 'MAX1000 mixed analog sweep' });
  await expect(analogRow).toBeVisible();
  await analogRow.getByRole('button', { name: 'Open' }).click();
  await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
  await expect(page.locator('.decoder-table')).toBeVisible();
  await expect(page.locator('.decoder-table tbody tr').first()).toContainText('START');

  await page.getByRole('button', { name: 'Channels' }).click();
  await expect(page.getByRole('option', { name: 'a1 (analog)' })).toBeAttached();
  await expect(page.getByRole('option', { name: 'a16 (analog)' })).toBeAttached();

  await page.screenshot({ path: shot('analog-session-waveform.png'), fullPage: true });
});
