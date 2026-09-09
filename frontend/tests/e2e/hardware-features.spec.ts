import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { writeArtifactWithRetry } from '../../src/test/artifactWrite';

const screenshots = path.resolve(process.cwd(), 'test-results/screenshots');
const clientId = 'codex-hardware-features';
const runHardwareMatrix = process.env.PLAYWRIGHT_HARDWARE_MATRIX === '1';
/**
 * Screenshot with retry: Windows Defender briefly locks freshly-written PNGs
 * during rapid matrix runs ("UNKNOWN: unknown error, open ..."). Space writes
 * and retry once the scan releases the file.
 */
async function takeScreenshot(page: any, name: string, opts: { fullPage?: boolean } = {}) {
  await page.waitForTimeout(150);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await page.screenshot({ path: path.join(screenshots, name), ...opts });
      return;
    } catch (err: any) {
      const msg = String(err?.message ?? err);
      if (!/UNKNOWN/.test(msg)) throw err;
      await page.waitForTimeout(300 * (attempt + 1));
    }
  }
  await page.screenshot({ path: path.join(screenshots, name), ...opts });
}

async function takeElementScreenshot(page: any, selector: string, name: string) {
  await page.waitForTimeout(150);
  const element = page.locator(selector);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await element.screenshot({ path: path.join(screenshots, name) });
      return;
    } catch (err: any) {
      if (!/UNKNOWN/.test(String(err?.message ?? err))) throw err;
      await page.waitForTimeout(300 * (attempt + 1));
    }
  }
  await element.screenshot({ path: path.join(screenshots, name) });
}

type HardwareStatus = {
  device_connected?: boolean;
  device_kind?: string | null;
};

/**
 * Poll the backend /api/status until a MAX1000 is reported or the window
 * elapses.  The backend may still be booting when the suite starts, so a
 * short poll avoids skipping a healthy run on startup latency.  Uses
 * page.request (baseURL 127.0.0.1:4173 -> vite proxy -> backend) so no
 * prior navigation is needed.  Returns the first definitive status body, or
 * {} if the backend never answered.
 */
async function pollHardwareStatus(page: Page, timeoutMs = 15_000, intervalMs = 500): Promise<HardwareStatus> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const status: unknown = await page.request.get('/api/status').then((res) => res.json());
      if (
        typeof status === 'object' && status !== null
        && 'device_connected' in status && 'device_kind' in status
      ) {
        const deviceConnected = status.device_connected; // narrowed to unknown by `in`
        const deviceKind = status.device_kind; // narrowed to unknown by `in`
        return {
          device_connected: typeof deviceConnected === 'boolean' ? deviceConnected : false,
          device_kind: typeof deviceKind === 'string' ? deviceKind : null,
        };
      }
    } catch {
      // Backend not ready yet; keep polling.
    }
    await page.waitForTimeout(intervalMs);
  }
  return {};
}



test.beforeEach(async ({ page }) => {
  if (process.env.PLAYWRIGHT_USE_MOCK === '1') {
    test.skip(true, 'hardware-only suite is not part of the forced mock run');
    return;
  }
  if (test.info().title.includes('validates every advertised mode and rate') && !runHardwareMatrix) {
    test.skip(true, 'set PLAYWRIGHT_HARDWARE_MATRIX=1 to run the 37-capture physical hardware matrix');
  }
  // Hardware-presence guard: poll /api/status before force-acquiring the
  // control lock.  Without a MAX1000 attached the suite skips cleanly
  // instead of erroring on a failed connect; with hardware present the
  // acquire/connect flow below runs exactly as before.
  let status = await pollHardwareStatus(page);
  if (!status.device_connected || status.device_kind !== 'hardware') {
    // The backend connects lazily, so a disconnected status may simply mean
    // nothing has connected yet.  Repeat the force-acquire + /api/connect
    // the suite used unconditionally, then re-check; skip only if the device
    // is really absent (the connect 502s and status stays disconnected).
    const acquireResponse = await page.request.post('/api/control/acquire', {
      headers: { 'Content-Type': 'application/json', 'X-Client-Id': clientId },
      data: { name: 'codex-hardware', force: true },
    }).catch((error: unknown) => {
      if (runHardwareMatrix) throw error;
      return null;
    });
    const connectResponse = await page.request.post('/api/connect', {
      headers: { 'Content-Type': 'application/json', 'X-Client-Id': clientId },
      data: { device_id: 'hardware' },
    }).catch((error: unknown) => {
      if (runHardwareMatrix) throw error;
      return null;
    });
    if (runHardwareMatrix && acquireResponse && !acquireResponse.ok()) {
      throw new Error(`hardware control acquisition failed: ${acquireResponse.status()} ${await acquireResponse.text()}`);
    }
    if (runHardwareMatrix && connectResponse && !connectResponse.ok()) {
      throw new Error(`MAX1000 connection failed: ${connectResponse.status()} ${await connectResponse.text()}`);
    }
    status = await pollHardwareStatus(page, 5_000, 500);
  }
  if (!status.device_connected || status.device_kind !== 'hardware') {
    const reason = `no MAX1000 hardware attached (device_connected=${String(status.device_connected)}, device_kind=${status.device_kind ?? 'null'})`;
    if (runHardwareMatrix) throw new Error(reason);
    test.skip(true, `${reason}; skipping hardware suite`);
  }
  await page.addInitScript((id) => localStorage.setItem('msa_client_id', id), clientId);
  await page.goto('/');
  await page.evaluate(async () => {
    const headers = { 'Content-Type': 'application/json', 'X-Client-Id': localStorage.getItem('msa_client_id') ?? '' };
    await fetch('/api/control/acquire', { method: 'POST', headers, body: JSON.stringify({ name: 'codex-hardware', force: true }) });
    const status = await fetch('/api/status').then((res) => res.json());
    if (!status.device_connected || status.device_kind !== 'hardware') {
      const res = await fetch('/api/connect', { method: 'POST', headers, body: JSON.stringify({ device_id: 'hardware' }) });
      if (!res.ok) throw new Error(await res.text());
    }
  });
  await page.reload();
});

test.afterEach(async ({ page }) => {
  await page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
    };
    await fetch('/api/capture/stop', { method: 'POST', headers }).catch(() => {});
    await fetch('/api/generator/stop', { method: 'POST', headers }).catch(() => {});
  }).catch(() => {});
});

test('hardware capture controls expose the real pre-trigger path', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Trigger', exact: true }).click();
  await page.getByLabel('Start capture when').selectOption('rising');
  await expect(page.getByText(/Trigger position:/)).toBeVisible();

  // Drive the range input through React's onChange (native value setter +
  // input/change events) so position_pct lands on 25.
  const slider = page.locator('label.field', { hasText: 'Trigger position' }).getByRole('slider');
  await slider.evaluate((el) => {
    const input = el as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    setter?.call(input, '25');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await expect(page.getByText('Trigger position: 25 %')).toBeVisible();

  // TriggerPanel derives pre_trigger_samples = floor(num_samples * pct / 100)
  // and renders both halves: 'pre-trigger X samples / post-trigger Y'. At 25 %
  // the pre window is a real fraction of the capture (post ~= 3*pre within the
  // floor rounding), not the 0-sample placeholder the old regex accepted.
  const hint = page.locator('.hint', { hasText: 'pre-trigger' });
  await expect(hint).toBeVisible();
  const text = (await hint.textContent()) ?? '';
  const m = text.match(/pre-trigger ([\d,]+) samples \/ post-trigger ([\d,]+)/);
  expect(m, `pre/post-trigger hint rendered as: ${text}`).toBeTruthy();
  const pre = Number(m![1].replace(/,/g, ''));
  const post = Number(m![2].replace(/,/g, ''));
  expect(pre).toBeGreaterThan(0);
  expect(pre).toBeLessThan(post);
  expect(Math.abs(post - 3 * pre)).toBeLessThanOrEqual(3);

  await takeScreenshot(page, 'hardware-pretrigger-controls.png', { fullPage: true });
});

test('hardware queue captures a real MAX1000 session', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByLabel('Capture name').fill('HW Playwright queued capture');
  await page.getByRole('button', { name: 'Run in background' }).click();
  await expect(page.getByText(/Headless job done/)).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText(/session ses_/)).toBeVisible();
  await takeScreenshot(page, 'hardware-capture-job.png', { fullPage: true });
});

type TriggerSearchResult = {
  sample: number | null;
  event: { type: string; start_sample: number; end_sample: number } | null;
  scopes: Array<{ decoder_id: string; start_sample: number; end_sample: number; event_count: number }>;
};

test('hardware accelerometer sequence trigger scopes the I2C decoder', async ({ page }) => {
  // Set up the condition instead of depending on a pre-recorded session id:
  // capture a live LIS3DH WHO_AM_I session (creates the dec-accel decoder).
  const { session_id: accelSessionId } = await page.evaluate(async () => {
    const res = await fetch('/api/diagnostics/live-accel-session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Client-Id': localStorage.getItem('msa_client_id') ?? '' },
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ session_id: string }>;
  });
  expect(accelSessionId).toMatch(/^ses_[0-9a-f]+$/);

  const result: TriggerSearchResult = await page.evaluate(async (sessionId) => {
    const body = {
      decoder_instance: 'dec-accel',
      auto_scope: true,
      trigger: {
        type: 'sequence',
        sequence_steps: [{ type: 'i2c_start' }, { type: 'i2c_byte', value: 15 }],
        window_s: 0.01,
        occurrence: 1,
        pre_trigger_samples: 0,
        position_pct: 0,
        execution: 'post_capture',
      },
    };
    const response = await fetch(`/api/sessions/${sessionId}/trigger-search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Client-Id': localStorage.getItem('msa_client_id') ?? '' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<TriggerSearchResult>;
  }, accelSessionId);

  // A fresh capture starts at an arbitrary phase, so the trigger sample is
  // not a fixed constant; assert the structural contract: the sequence
  // matched at a real sample, the event is an I2C START, and the decoder is
  // auto-scoped to exactly that sample.
  expect(result.sample).toEqual(expect.any(Number));
  expect(result.event?.type).toBe('i2c_start');
  expect(result.scopes).toEqual([{
    decoder_id: 'dec-accel',
    start_sample: result.event?.start_sample,
    end_sample: result.event?.end_sample,
    event_count: 1,
  }]);
});

test('hardware capture controls screenshot matrix covers every advertised rate', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  const rateSelect = page.getByLabel('Sample rate');
  const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const screenshotRates = async (mode: string, acquisition: 'single' | 'live' = 'single') => {
    await page.locator('.mode-tile', { hasText: mode }).click();
    if (acquisition === 'live') await page.getByRole('button', { name: 'Continuous view' }).click();
    await expect(page.locator('.side-panel')).toContainText('Capture source');
    const options = await rateSelect.locator('option').evaluateAll((items) => items.map((item) => ({
      value: (item as HTMLOptionElement).value,
      label: item.textContent?.trim() ?? '',
    })));
    for (const option of options) {
      await rateSelect.selectOption(option.value);
      // This matrix documents configuration coverage. Capture only the setup
      // panel so an unrelated previously loaded session cannot masquerade as
      // evidence for the selected mode/rate.
      await takeElementScreenshot(page, '.side-panel', `hardware-matrix-${slug(mode)}-${acquisition}-${slug(option.label)}.png`);
    }
    return options.map((option) => option.label);
  };

  const matrix: { mode: string; acquisition?: 'single' | 'live' }[] = [
    { mode: 'Digital capture' },
    { mode: 'Digital capture', acquisition: 'live' },
    { mode: 'High-speed single channel', acquisition: 'live' },
    { mode: 'Analog — one channel' },
    { mode: 'Analog — four channels' },
    { mode: 'Digital + analog' },
  ];
  const coverage: Record<string, string[]> = {};
  for (const item of matrix) {
    coverage[`${item.mode} / ${item.acquisition ?? 'single'}`] = await screenshotRates(item.mode, item.acquisition ?? 'single');
  }
  expect(coverage['Digital capture / single']).toContain('200 MHz');
  expect(coverage['Digital capture / live']).toContain('50 MHz');
  expect(coverage['High-speed single channel / live']).toContain('200 MHz');
  expect(coverage['Analog — one channel / single']).toContain('1 MHz');
  expect(coverage['Analog — four channels / single']).toEqual(['24 kHz']);
  expect(coverage['Digital + analog / single']).toEqual(['125 kHz']);
});

test('hardware capture matrix validates every advertised mode and rate before evidence screenshots', async ({ page }) => {
  test.setTimeout(900_000);

  type MatrixCase = {
    mode: string;
    acquisition: 'single' | 'live';
    apiMode: string;
    rates: number[];
    analog: boolean;
    digital: boolean;
  };

  const matrix: MatrixCase[] = [
    {
      mode: 'Digital capture', acquisition: 'single', apiMode: 'single',
      rates: [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 12.5e6, 14e6, 20e6, 50e6, 100e6, 200e6],
      analog: false, digital: true,
    },
    {
      mode: 'Digital capture', acquisition: 'live', apiMode: 'rolling',
      rates: [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 12.5e6, 14e6, 20e6, 50e6],
      analog: false, digital: true,
    },
    {
      mode: 'High-speed single channel', acquisition: 'live', apiMode: 'digital_narrow',
      rates: [200e6], analog: false, digital: true,
    },
    {
      mode: 'Analog — one channel', acquisition: 'single', apiMode: 'analog_fast',
      rates: [100e3, 200e3, 500e3, 1e6], analog: true, digital: false,
    },
    {
      mode: 'Analog — one channel', acquisition: 'live', apiMode: 'analog_continuous',
      rates: [100e3, 200e3, 500e3, 1e6], analog: true, digital: false,
    },
    {
      mode: 'Analog — four channels', acquisition: 'single', apiMode: 'analog_all',
      rates: [24e3], analog: true, digital: false,
    },
    {
      mode: 'Analog — four channels', acquisition: 'live', apiMode: 'analog_all_continuous',
      rates: [24e3], analog: true, digital: false,
    },
    {
      mode: 'Digital + analog', acquisition: 'single', apiMode: 'mixed',
      rates: [125e3], analog: true, digital: true,
    },
    {
      mode: 'Digital + analog', acquisition: 'live', apiMode: 'mixed_continuous',
      rates: [125e3], analog: true, digital: true,
    },
  ];

  const rateSelect = page.getByLabel('Sample rate');
  const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const evidence: Array<Record<string, unknown>> = [];
  const failures: Array<Record<string, unknown>> = [];

  const state = async () => page.evaluate(async () => fetch('/api/capture/state').then((res) => res.json()));
  const stop = async () => page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
    };
    const res = await fetch('/api/capture/stop', { method: 'POST', headers });
    return { ok: res.ok, body: await res.json().catch(() => ({})) };
  });
  const startJumperStimulus = async () => page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
    };
    const res = await fetch('/api/generator/send', {
      method: 'POST',
      headers,
      body: JSON.stringify({
        config: {
          protocol: 'uart',
          data_hex: '55aa55aa55aa55aa',
          baud: 1_000_000,
          tx_pin: 22,
          scl_pin: 25,
          continuous: true,
        },
        capture: false,
        live: true,
      }),
    });
    return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
  });
  const stopJumperStimulus = async () => page.evaluate(async () => {
    const headers = {
      'Content-Type': 'application/json',
      'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
    };
    const res = await fetch('/api/generator/stop', { method: 'POST', headers });
    return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
  });

  for (const item of matrix) {
    await page.locator('.mode-tile', { hasText: item.mode }).click();
    if (item.acquisition === 'live') {
      await page.getByRole('button', { name: 'Continuous view' }).click();
    } else {
      await page.getByRole('button', { name: 'One capture' }).click();
    }

    for (const rate of item.rates) {
      await rateSelect.selectOption(String(rate));
      await expect(rateSelect).toHaveValue(String(rate));

      const before = await state();
      const caseLabel = `${item.mode} ${item.acquisition} ${rate}`;
      let finished: Record<string, any> = {};
      let metadata: Record<string, any> = {};
      const settings = {
        sample_rate: rate,
        num_samples: item.apiMode === 'digital_narrow' ? 4096 : 1024,
        mode: item.apiMode,
        analog_enabled: item.analog,
        enabled_digital: item.digital
          ? (item.apiMode === 'digital_narrow' ? [0] : Array.from({ length: 16 }, (_, index) => index))
          : [],
        readback_compression: 'raw',
      };
      try {
        const jumperDriven = item.digital && item.apiMode !== 'digital_narrow';
        if (jumperDriven) {
          await stopJumperStimulus().catch(() => {});
          const stimulus = await startJumperStimulus();
          expect(stimulus.ok,
            `${caseLabel} pool-pin-22 jumper stimulus failed: ${JSON.stringify(stimulus.body)}`)
            .toBeTruthy();
        }
        const started = await page.evaluate(async (payload) => {
          const headers = {
            'Content-Type': 'application/json',
            'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
          };
          const res = await fetch('/api/capture/start', {
            method: 'POST', headers, body: JSON.stringify(payload),
          });
          return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
        }, { settings, name: `HW validated ${item.mode} ${item.acquisition} ${rate}` });
        expect(started.ok, JSON.stringify(started.body)).toBeTruthy();

        if (item.acquisition === 'single') {
          await expect.poll(async () => (await state()).state, { timeout: 60_000 })
            .toMatch(/^(done|error|cancelled)$/);
        } else {
          await expect.poll(async () => (await state()).last_session_id, { timeout: 60_000 })
            .not.toBe(before.last_session_id);
          await stop();
          await expect.poll(async () => (await state()).state, { timeout: 30_000 })
            .toMatch(/^(cancelled|done|error)$/);
        }

        finished = await state();
        // Live/continuous captures are deliberately stopped after the first
        // valid chunk.  The manager reports that normal stop as `cancelled`,
        // while single-shot captures finish as `done`.
        expect(finished.state, `${caseLabel} ended in ${finished.last_error || 'an unknown state'}`)
          .toMatch(/^(done|cancelled)$/);
        expect(finished.last_session_id, `${caseLabel} produced no session`).toBeTruthy();
        metadata = await page.evaluate(async (sessionId) => (
          fetch(`/api/sessions/${sessionId}/metadata`).then((res) => res.json())
        ), finished.last_session_id);
        expect(metadata.has_waveform, `${caseLabel} has no waveform`).toBeTruthy();
        expect(metadata.num_samples, `${caseLabel} returned no samples`).toBeGreaterThan(0);
        expect(metadata.sample_rate, `${caseLabel} has no effective rate`).toBeGreaterThan(0);
        expect(Math.abs(metadata.sample_rate - rate) / rate,
          `${caseLabel} effective rate was ${metadata.sample_rate}`).toBeLessThan(0.02);

        const channels = metadata.session?.channels ?? [];
        const digitalCount = channels.filter((channel: { type?: string }) => channel.type === 'digital').length;
        if (item.digital) expect(digitalCount, `${caseLabel} digital channels`).toBeGreaterThan(0);
        if (item.analog) expect(metadata.analog_channels.length, `${caseLabel} analog channels`).toBeGreaterThan(0);
        if (item.apiMode === 'mixed' || item.apiMode === 'mixed_continuous') {
          expect(metadata.analog_channels, `${caseLabel} physical mixed channels`).toEqual(['a1', 'a2']);
          expect(metadata.analog_channels, `${caseLabel} must reject unmapped analogue channels`)
            .not.toContain('a0');
          const physical = channels
            .filter((channel: { type?: string }) => channel.type === 'analog')
            .map((channel: { id?: string; adc_channel?: number; board_label?: string }) => ({
              id: channel.id, adc: channel.adc_channel, board: channel.board_label,
            }));
          expect(physical).toEqual([
            expect.objectContaining({ id: 'a1', adc: 1, board: 'AIN3' }),
            expect.objectContaining({ id: 'a2', adc: 2, board: 'AIN1' }),
          ]);
        }

        // Metadata can look perfect while the stored waveform is empty,
        // truncated, mis-keyed, or corrupt. Inspect the same raw payload the
        // renderer consumes and retain compact content statistics as evidence.
        const rawEnd = Math.min(Number(metadata.num_samples), 4096);
        const raw = await page.evaluate(async ({ sessionId, end }) => {
          const res = await fetch(`/api/sessions/${sessionId}/raw?start=0&end=${end}`);
          return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
        }, { sessionId: finished.last_session_id, end: rawEnd });
        expect(raw.ok, `${caseLabel} raw waveform endpoint returned ${raw.status}`).toBeTruthy();

        const content: Record<string, unknown> = {};
        if (item.digital) {
          const packed = raw.body.digital_packed;
          expect(Array.isArray(packed), `${caseLabel} digital payload is missing`).toBeTruthy();
          expect(packed.length, `${caseLabel} digital payload is empty`).toBeGreaterThan(0);
          expect(packed.length, `${caseLabel} digital payload is truncated`).toBe(rawEnd);
          expect(packed.every((value: unknown) => Number.isInteger(value)
            && Number(value) >= 0 && Number(value) <= 0xffff),
          `${caseLabel} digital payload contains invalid words`).toBeTruthy();
          const ch13Transitions = packed.slice(1).reduce((count: number, word: number, index: number) => (
            count + ((((word >> 13) & 1) !== ((packed[index] >> 13) & 1)) ? 1 : 0)
          ), 0);
          if (jumperDriven) {
            expect(ch13Transitions,
              `${caseLabel} CH13 did not observe the installed pool-pin-22 jumper`).toBeGreaterThan(0);
          }
          content.digital = {
            samples: packed.length,
            min: Math.min(...packed),
            max: Math.max(...packed),
            distinct_words: new Set(packed).size,
            ...(jumperDriven ? { ch13_transitions: ch13Transitions } : {}),
          };
        }
        if (item.analog) {
          const analogStats: Record<string, unknown> = {};
          for (const channel of metadata.analog_channels as string[]) {
            const values = raw.body[`analog_${channel}`];
            expect(Array.isArray(values), `${caseLabel} ${channel} payload is missing`).toBeTruthy();
            expect(values.length, `${caseLabel} ${channel} payload is empty`).toBeGreaterThan(0);
            expect(values.length, `${caseLabel} ${channel} payload is truncated`).toBe(rawEnd);
            expect(values.every((value: unknown) => Number.isFinite(Number(value))
              && Number(value) >= -0.05 && Number(value) <= 3.5),
            `${caseLabel} ${channel} contains invalid voltage samples`).toBeTruthy();
            analogStats[channel] = {
              samples: values.length,
              min_v: Math.min(...values),
              max_v: Math.max(...values),
              distinct_values: new Set(values).size,
            };
          }
          content.analog = analogStats;
        }

        evidence.push({
          status: 'passed',
          mode: item.mode,
          acquisition: item.acquisition,
          api_mode: item.apiMode,
          requested_rate_hz: rate,
          effective_rate_hz: metadata.sample_rate,
          samples: metadata.num_samples,
          digital_channels: digitalCount,
          analog_channels: metadata.analog_channels,
          waveform_content: content,
          session_id: finished.last_session_id,
        });
      } catch (error) {
        finished = finished.state ? finished : await state().catch(() => ({}));
        failures.push({
          status: 'failed', mode: item.mode, acquisition: item.acquisition,
          api_mode: item.apiMode, requested_rate_hz: rate,
          state: finished.state, error: finished.last_error || String(error),
          session_id: finished.last_session_id,
        });
      } finally {
        await stop().catch(() => {});
        if (finished.last_error) {
          await page.evaluate(async () => {
            const headers = {
              'Content-Type': 'application/json',
              'X-Client-Id': localStorage.getItem('msa_client_id') ?? '',
            };
            await fetch('/api/connect', {
              method: 'POST', headers, body: JSON.stringify({ device_id: 'hardware' }),
            });
          }).catch(() => {});
        }
        await takeScreenshot(page, `hardware-validated-matrix-${slug(item.mode)}-${item.acquisition}-${slug(String(rate))}.png`, { fullPage: true });
      }
    }
  }

  await writeArtifactWithRetry(
    path.join(screenshots, 'hardware-validated-matrix.json'),
    `${JSON.stringify({ generated_at: new Date().toISOString(), cases: [...evidence, ...failures], passed: evidence.length, failed: failures.length }, null, 2)}\n`,
    { write: (filePath, data) => fs.promises.writeFile(filePath, data).then(() => undefined) },
  );
  expect([...evidence, ...failures]).toHaveLength(37);
  const stopped = await stopJumperStimulus();
  expect(stopped.ok, `pool-pin-22 jumper stimulus did not stop: ${JSON.stringify(stopped.body)}`).toBeTruthy();
  expect(failures, JSON.stringify(failures, null, 2)).toHaveLength(0);
});
