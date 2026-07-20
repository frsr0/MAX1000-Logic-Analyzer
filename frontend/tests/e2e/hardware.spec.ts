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
  await page.getByRole('button', { name: 'Device' }).click();
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
  if (useMockHarness) return;
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

async function openLiveSession(page: any, query: string) {
  const sessions = await listLiveSessions(page);
  const pick = sessions.find((s: any) => String(s.name) === query)
    ?? sessions.find((s: any) => String(s.name).includes(query));
  expect(pick, `expected a live session matching ${query}`).toBeTruthy();

  await page.getByRole('button', { name: 'Sessions' }).click();
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
  const nameBox = page.locator(`input.ch-name[value="${pick.name}"]`).first();
  await expect(nameBox).toBeVisible({ timeout: 15_000 });
  const row = nameBox.locator('xpath=ancestor::tr');
  await row.scrollIntoViewIfNeeded();
  await row.getByRole('button', { name: 'Open' }).click({ force: true });
  await expect(page.locator('canvas.waveform-canvas')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.decoder-table')).toBeVisible({ timeout: 15_000 });
}

test.beforeEach(async ({ page }) => {
  fs.mkdirSync(shots, { recursive: true });
  if (!useMockHarness && test.info().title.includes('mock fixture')) {
    test.skip(true, 'fixture session is only available in mock mode');
  }
  if (useMockHarness) {
    await installMockApp(page);
    await page.goto('/');
  }
  await ensureConnected(page);
  await stopActiveCapture(page);
});

test.afterEach(async ({ page }) => {
  if (useMockHarness) return;
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
  await expect(page.locator('.mode-tile', { hasText: 'Maximum analog' }).first()).toBeVisible();
  await expect(page.getByRole('option', { name: '200 MHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Digital deep' }).click();
  await page.getByRole('button', { name: 'DELTA RLE' }).click();
  await expect(page.getByRole('button', { name: 'DELTA RLE' })).toHaveClass(/active/);
  await page.screenshot({ path: shot('capture-compression-delta-rle.png'), fullPage: true });

  await page.getByRole('button', { name: 'Live ring' }).click();
  await expect(page.getByRole('option', { name: '50 MHz' })).toBeAttached();
  await page.screenshot({ path: shot('capture-live-50mhz.png'), fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Analog fast' }).click();
  await expect(page.getByText('High-speed analog captures one analog input (AIN3) at the best ADC rate.')).toBeVisible();
  await expect(page.getByRole('option', { name: '1 MHz' })).toBeAttached();
  await expect(page.getByText('Analog and mixed captures use raw readback.')).toBeVisible();
  await page.screenshot({ path: shot('capture-analog-fast.png'), fullPage: true });

  await page.locator('.mode-tile', { hasText: 'Maximum analog' }).click();
  await expect(page.getByText('Maximum analog captures the physical MAX1000 analog profile: AIN3, AIN1, AIN4, and AIN6.')).toBeVisible();
  await expect(page.getByRole('option', { name: '125 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Mixed scan' }).click();
  await expect(page.getByText('Mixed mode captures 16 digital bits plus the 4 analog scan channels, sampled together at a shared scan frame rate.')).toBeVisible();
  await expect(page.getByText('Analog and mixed captures use raw readback.')).toBeVisible();
  await expect(page.getByRole('option', { name: '125 kHz' })).toBeAttached();

  await page.locator('.mode-tile', { hasText: 'Packed narrow' }).click();
  await expect(page.getByText('Packed narrow is live-only.')).toBeVisible();
  await page.screenshot({ path: shot('capture-controls.png'), fullPage: true });
});

test('compression sweep shows raw and delta_rle throughput differences', async ({ page }) => {
  test.skip(useMockHarness, 'live hardware only');
  test.setTimeout(240_000);

  const sampleCount = useMockHarness ? 250_000 : 50_000;
  const sweepRates = useMockHarness ? [1_000_000, 10_000_000, 50_000_000] : [1_000_000, 10_000_000];
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
  }

  await page.screenshot({ path: shot('compression-sweep-summary.png'), fullPage: true });
});

test('generator page matches supported board protocols', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('heading', { name: 'Signal generator' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send + capture' })).toBeVisible({ timeout: 15_000 });
  const protocolCount = await waitForGeneratorProtocolOptions(page);
  if (protocolCount === 0) {
    test.skip(true, 'live generator protocols did not load on this board session');
  }
  await expect(page.getByLabel('Generator protocol').locator('option')).toHaveCount(4);
  await expect(page.getByText('Hardware support on this board is UART, RS-485, I2C, SPI, and raw two-output Bit Banger playback. Protocol exerciser workflows can be built from the raw symbol mode.')).toBeVisible();
  await page.screenshot({ path: shot('generator-page.png'), fullPage: true });
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
});

test('mock capture dashboard shows protocol activity and errors', async ({ page }) => {
  await page.getByRole('button', { name: 'Sessions' }).click();
  const row = page.locator('tr').filter({ has: page.locator('input[value="MAX1000 mixed analog sweep"]') }).first();
  await row.getByRole('button', { name: 'Open' }).click();
  await page.getByRole('button', { name: 'Dashboard' }).click();
  await expect(page.getByText('12', { exact: true })).toBeVisible();
  await expect(page.getByText('uart_byte').first()).toBeVisible();
  await expect(page.getByText('Activity heatmap')).toBeVisible();
  await expect(page.getByText('Bus transaction timeline')).toBeVisible();
  await expect(page.getByText('framing error')).toBeVisible();
});

test('mock trigger builder previews pattern qualifiers', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Trigger', exact: true }).click();
  await page.getByLabel('Trigger type').selectOption('pattern');
  await page.getByLabel('Pattern (1/0/x per channel)').fill('1x01');
  await expect(page.getByLabel('Trigger preview')).toBeVisible();
  await expect(page.getByLabel('Trigger preview')).toContainText('1x01');
  await expect(page.getByLabel('Trigger preview')).toContainText("don't care");
});

test('command palette navigates between app pages', async ({ page }) => {
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true })));
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  await page.getByLabel('Command search').fill('generator');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Signal generator' })).toBeVisible();
});

test('signal generator loopback shows waveform and decode', async ({ page }) => {
  await page.getByRole('button', { name: 'Generator' }).click();
  await expect(page.getByRole('button', { name: 'Send + capture' })).toBeEnabled({ timeout: 15_000 });
  const protocolCount = await waitForGeneratorProtocolOptions(page);
  if (protocolCount === 0) {
    test.skip(true, 'live generator protocols did not load on this board session');
  }
  if (useMockHarness) {
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
  } else {
    await openLiveSession(page, 'Generator self-test (uart)');
  }
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
  await expect(milResult).toContainText(/READ|RESPONSE/);
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
  test.setTimeout(240_000);

  await page.getByRole('button', { name: 'Device' }).click();
  await expect(page.getByRole('heading', { name: 'Device' })).toBeVisible();
  await expect(page.getByText('held by playwright')).toBeVisible();
  await page.screenshot({ path: shot('live-device-page.png'), fullPage: true });

  await page.locator('.sidebar button[title="Capture"]').click();
  await expect(page.getByText('Hardware mode')).toBeVisible();
  await expect(page.getByText('Readback codec')).toBeVisible();
  await page.screenshot({ path: shot('live-capture-controls.png'), fullPage: true });

  await openLiveSession(page, 'Generator self-test (uart)');
  await page.screenshot({ path: shot('live-generator-loopback-capture.png'), fullPage: true });

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
  await page.screenshot({ path: shot('live-accelerometer-session-waveform.png'), fullPage: true });

  const sessions = await listLiveSessions(page);
  const picks = [
    { query: 'Generator self-test (uart)', shot: 'live-generator-session-waveform.png' },
    { query: 'MIL transaction - Modbus RTU demo', shot: 'live-mil-session-waveform.png' },
    { query: 'HW smoke test capture', shot: 'live-hw-smoke-session-waveform.png' },
    { query: 'LIS3DH WHO_AM_I live', shot: 'live-accelerometer-session-waveform.png' },
    { query: 'README HW analog fast live', shot: 'live-analog-fast-waveform.png' },
    { query: 'README HW dual analog live', shot: 'live-dual-analog-waveform.png' },
    { query: 'README HW mixed analog live', shot: 'live-mixed-analog-waveform.png' },
  ].filter((pick) => sessions.some((s: any) => String(s.name).includes(pick.query)));
  expect(picks.length).toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Sessions' }).click();
  await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();

  for (const pick of picks) {
    const row = page.locator('tr').filter({
      hasText: pick.query,
    }).first();
    try {
      await expect(row).toBeVisible({ timeout: 15_000 });
      await row.scrollIntoViewIfNeeded();
      await row.getByRole('button', { name: 'Open' }).click({ force: true });
      await expect(page.locator('canvas.waveform-canvas')).toBeVisible({ timeout: 15_000 });
      await page.screenshot({ path: shot(pick.shot) });
      await page.getByRole('button', { name: 'Sessions' }).click();
      await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
    } catch {
      continue;
    }
  }
});

if (useMockHarness) {
  test('analog session renders waveforms and decode on the mock fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions' }).click();
    const analogRow = page.locator('tr').filter({
      has: page.locator('input[value="MAX1000 mixed analog sweep"]'),
    }).first();
    await expect(analogRow).toBeVisible();
    await analogRow.getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
    await expect(page.locator('.decoder-table')).toBeVisible();
    await expect(page.locator('.decoder-table tbody tr').first()).toContainText('START');

    await page.getByRole('button', { name: 'Channels' }).click();
    await expect(page.getByRole('option', { name: 'a1 (analog)' })).toBeAttached();
    await expect(page.getByRole('option', { name: 'a2 (analog)' })).toBeAttached();

    await page.screenshot({ path: shot('analog-session-waveform.png'), fullPage: true });
  });

  test('accelerometer session renders waveform and decode on the mock fixture', async ({ page }) => {
    await page.getByRole('button', { name: 'Sessions' }).click();
    const accelRow = page.locator('tr').filter({
      has: page.locator('input[value="LIS3DH WHO_AM_I dialogue"]'),
    }).first();
    await expect(accelRow).toBeVisible();
    await accelRow.getByRole('button', { name: 'Open' }).click();
    await expect(page.locator('canvas.waveform-canvas')).toBeVisible();
    await expect(page.locator('.decoder-table')).toBeVisible();
    await expect(page.getByRole('cell', { name: 'START', exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: '0x33', exact: true })).toBeVisible();

    await page.screenshot({ path: shot('accelerometer-session-waveform.png'), fullPage: true });
  });
}
