// Run from frontend/: node tests/e2e/mcp4725-fast.evidence.mjs
// Requires the backend at 127.0.0.1:8000 and the wired MCP4725 on D4/D3.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const base = 'http://127.0.0.1:8000';
const evidenceOverride = process.env.MCP4725_EVIDENCE_DIR;
if (evidenceOverride && !path.isAbsolute(evidenceOverride)) {
  throw new Error('MCP4725_EVIDENCE_DIR must be an absolute path');
}
const evidence = evidenceOverride
  ? path.normalize(evidenceOverride)
  : path.resolve(process.cwd(), '../docs/evidence/mcp4725-fast-2026-09-28');
await fs.mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(30_000);
let clientId;
let card;
const records = [];

async function post(route, data) {
  const response = await page.request.post(`${base}${route}`, {
    headers: { 'X-Client-Id': clientId }, data,
  });
  assert.equal(response.status(), 200, `${route}: ${await response.text()}`);
  return response.json();
}

async function run(name, check) {
  const responseWait = page.waitForResponse((response) =>
    response.url() === `${base}/api/mil/mcp4725/waveform`
    && response.request().method() === 'POST', { timeout: 60_000 });
  await card.getByRole('button', { name: 'Run waveform and save trace' }).click();
  const response = await responseWait;
  const result = await response.json();
  assert.equal(response.status(), 200, `${name}: ${JSON.stringify(result)}`);
  check(result);
  await page.waitForTimeout(120);
  await card.screenshot({ path: path.join(evidence, `${name}.png`), animations: 'disabled' });
  records.push({ name, request: response.request().postDataJSON(), response: result });
  console.log(name, result.passed, result.actual_frequency_hz, result.bus_passed);
  return result;
}

const fill = (label, value) => card.getByLabel(label).fill(String(value));
const select = (label, value) => card.getByLabel(label).selectOption(String(value));

try {
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  clientId = await page.evaluate(() => localStorage.getItem('msa_client_id'));
  assert.ok(clientId);
  await post('/api/control/acquire', { name: 'MCP4725 fast evidence', force: true });
  await page.waitForFunction(async () => {
    const status = await (await fetch('/api/status')).json();
    return status.device_connected === true && status.device_kind === 'hardware';
  });
  // The app's startup auto-connect can briefly replace its hardware owner.
  // Wait through that reconnect before the first DAC request.
  await page.waitForTimeout(1500);
  const ready = await (await page.request.get(`${base}/api/status`)).json();
  assert.equal(ready.device_connected, true);
  await page.getByRole('button', { name: 'Hardware lab' }).click();
  card = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }) });
  await card.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }).waitFor();
  await fill('Cycles', 1);

  await fill('Frequency (Hz)', 0.5);
  await run('01-eight-steps-0_5hz', (result) => {
    assert.equal(result.passed, true);
    assert.equal(result.trace_source, 'preflight_measured_levels');
    assert.ok(Math.abs(result.actual_frequency_hz - 0.5) < 0.05);
  });

  await fill('Frequency (Hz)', 0.25);
  await fill('Steps per cycle', 24);
  await run('02-twentyfour-steps-0_25hz', (result) => {
    assert.equal(result.passed, true);
    assert.equal(result.trace_v.length, 24 * 8);
    assert.ok(Math.abs(result.actual_frequency_hz - 0.25) < 0.025);
  });

  await select('Shape', 'square');
  await fill('Steps per cycle', 8);
  await fill('Frequency (Hz)', 2);
  await run('03-eight-steps-2hz', (result) => {
    assert.equal(result.passed, true);
    assert.ok(Math.abs(result.actual_frequency_hz - 2) < 0.2);
  });

  await fill('Steps per cycle', 10);
  const maxRate = await run('04-twenty-writes-per-second', (result) => {
    assert.equal(result.passed, true);
    assert.ok(Math.abs(result.actual_frequency_hz - 2) < 0.2);
    assert.equal(result.voltage_passed, true);
    assert.equal(result.bus_passed, true);
  });
  await card.getByRole('button', { name: 'Open scope trace' }).click();
  await page.getByText('Loaded session', { exact: true }).waitFor();
  const captureText = await page.locator('.capture-page').innerText();
  assert.ok(captureText.includes('MCP4725 square (verified AIN4'));
  const sessionResponse = await page.request.get(`${base}/api/sessions/${maxRate.session_id}`);
  assert.equal(sessionResponse.status(), 200);
  const session = await sessionResponse.json();
  assert.ok(session.notes.includes('not a simultaneous analog capture'));
  assert.equal(session.generator.trace_source, 'preflight_measured_levels');
  await page.screenshot({ path: path.join(evidence, '05-verified-scope-trace.png'), fullPage: true, animations: 'disabled' });
  records.push({ name: 'verified-scope-trace', sessionId: maxRate.session_id,
    notes: session.notes, visibleText: captureText.slice(0, 300) });
  await page.getByRole('button', { name: 'Hardware lab' }).click();

  await fill('Frequency (Hz)', 0.25);
  await fill('Steps per cycle', 8);
  await select('I²C address', 0x61);
  await run('06-fast-path-wrong-address', (result) => {
    assert.equal(result.passed, false);
    assert.equal(result.bus_passed, false);
    assert.equal(result.timing_passed, true);
  });
  await select('I²C address', 0x60);

  const testWait = page.waitForResponse((response) =>
    response.url() === `${base}/api/mil/mcp4725/test`
    && response.request().method() === 'POST');
  await fill('DAC code (0–4095)', 2048);
  await card.getByRole('button', { name: 'Set code and measure' }).click();
  const restored = await (await testWait).json();
  assert.equal(restored.passed, true);
  await page.waitForTimeout(120);
  await card.screenshot({ path: path.join(evidence, '07-restored-midscale.png'), animations: 'disabled' });
  records.push({ name: 'restored-midscale', response: restored });

  await fs.writeFile(path.join(evidence, 'results.json'), JSON.stringify(records, null, 2) + '\n');
  console.log(`Saved ${records.length} records in ${evidence}`);
} finally {
  if (clientId) {
    await page.request.post(`${base}/api/control/release`, {
      headers: { 'X-Client-Id': clientId }, data: {},
    }).catch(() => {});
  }
  await browser.close();
}
