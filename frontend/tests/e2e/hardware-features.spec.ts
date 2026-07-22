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
