import { expect, test } from '@playwright/test';
import path from 'node:path';

const screenshots = path.resolve(process.cwd(), 'test-results/screenshots');
const clientId = 'codex-hardware-features';

test.beforeEach(async ({ page }) => {
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

test('hardware capture controls expose the real pre-trigger path', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByRole('button', { name: 'Trigger', exact: true }).click();
  await page.getByLabel('Trigger type').selectOption('rising');
  await expect(page.getByText(/Trigger position:/)).toBeVisible();
  await page.getByRole('slider').fill('25');
  await expect(page.getByText(/pre-trigger .* samples/)).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, 'hardware-pretrigger-controls.png'), fullPage: true });
});

test('hardware queue captures a real MAX1000 session', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  await page.getByLabel('Capture name').fill('HW Playwright queued capture');
  await page.getByRole('button', { name: 'Queue capture job' }).click();
  await expect(page.getByText(/Headless job done/)).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText(/session ses_/)).toBeVisible();
  await page.screenshot({ path: path.join(screenshots, 'hardware-capture-job.png'), fullPage: true });
});

test('hardware accelerometer sequence trigger scopes the I2C decoder', async ({ page }) => {
  const result = await page.evaluate(async () => {
    const body = {
      decoder_instance: 'dec-accel',
      auto_scope: true,
      trigger: {
        type: 'sequence',
        sequence_steps: [{ type: 'start', value: 25 }, { type: 'byte', value: 15 }],
        window_s: 0.01,
        occurrence: 1,
        pre_trigger_samples: 0,
        position_pct: 0,
        execution: 'post_capture',
      },
    };
    const response = await fetch('/api/sessions/ses_454be01209/trigger-search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Client-Id': localStorage.getItem('msa_client_id') ?? '' },
      body: JSON.stringify(body),
    });
    return response.json();
  });
  expect(result.sample).toBe(1800);
  expect(result.event.type).toBe('start');
  expect(result.scopes).toEqual([{ decoder_id: 'dec-accel', start_sample: 1800, end_sample: 1800, event_count: 1 }]);
});

test('hardware capture controls screenshot matrix covers every advertised rate', async ({ page }) => {
  await page.locator('.sidebar button[title="Capture"]').click();
  const rateSelect = page.getByLabel('Sample rate');
  const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const screenshotRates = async (mode: string, acquisition: 'single' | 'live' = 'single') => {
    await page.locator('.mode-tile', { hasText: mode }).click();
    if (acquisition === 'live') await page.getByRole('button', { name: 'Live ring' }).click();
    const options = await rateSelect.locator('option').evaluateAll((items) => items.map((item) => ({
      value: (item as HTMLOptionElement).value,
      label: item.textContent?.trim() ?? '',
    })));
    for (const option of options) {
      await rateSelect.selectOption(option.value);
      await page.screenshot({
        path: path.join(screenshots, `hardware-matrix-${slug(mode)}-${acquisition}-${slug(option.label)}.png`),
        fullPage: true,
      });
    }
    return options.map((option) => option.label);
  };

  const matrix: { mode: string; acquisition?: 'single' | 'live' }[] = [
    { mode: 'Digital deep' },
    { mode: 'Digital deep', acquisition: 'live' },
    { mode: 'Packed narrow', acquisition: 'live' },
    { mode: 'Analog fast' },
    { mode: 'Maximum analog' },
    { mode: 'Mixed scan' },
  ];
  const coverage: Record<string, string[]> = {};
  for (const item of matrix) {
    coverage[`${item.mode} / ${item.acquisition ?? 'single'}`] = await screenshotRates(item.mode, item.acquisition ?? 'single');
  }
  expect(coverage['Digital deep / single']).toContain('200 MHz');
  expect(coverage['Digital deep / live']).toContain('50 MHz');
  expect(coverage['Packed narrow / live']).toContain('200 MHz');
  expect(coverage['Analog fast / single']).toContain('1 MHz');
  expect(coverage['Maximum analog / single']).toEqual(['125 kHz']);
  expect(coverage['Mixed scan / single']).toEqual(['125 kHz']);
});
