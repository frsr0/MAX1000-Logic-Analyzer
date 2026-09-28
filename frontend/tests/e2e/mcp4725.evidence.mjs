// Historical before-fix evidence script. It expects the old timing failures
// and old UI labels; use mcp4725-fast.evidence.mjs against the current app.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '@playwright/test';

const base = 'http://127.0.0.1:8000';
const evidence = path.resolve(process.cwd(), '../docs/evidence/mcp4725-2026-09-28');
await fs.mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(30_000);
const records = [];
let card;
let clientId;

async function screenshot(name) {
  await card.screenshot({ path: path.join(evidence, `${name}.png`), animations: 'disabled' });
}

async function post(route, body) {
  const response = await page.request.post(`${base}${route}`, {
    headers: { 'X-Client-Id': clientId }, data: body,
  });
  assert.equal(response.status(), 200, `${route}: ${await response.text()}`);
  return response.json();
}

async function run(button, route, name, check) {
  const wait = page.waitForResponse((response) =>
    response.url() === `${base}${route}` && response.request().method() === 'POST');
  await card.getByRole('button', { name: button }).click();
  const response = await wait;
  const body = await response.json();
  assert.equal(response.status(), 200, `${name}: ${JSON.stringify(body)}`);
  check(body);
  await page.waitForTimeout(120);
  await screenshot(name);
  records.push({ name, request: response.request().postDataJSON(), response: body });
  console.log(name, body.passed, body.actual_frequency_hz ?? body.steps?.map((step) => step.measured_v));
  return body;
}

const testRoute = '/api/mil/mcp4725/test';
const waveformRoute = '/api/mil/mcp4725/waveform';
const select = (label, value) => card.getByLabel(label).selectOption(String(value));
const fill = (label, value) => card.getByLabel(label).fill(String(value));

try {
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  clientId = await page.evaluate(() => localStorage.getItem('msa_client_id'));
  assert.ok(clientId);
  await post('/api/control/acquire', { name: 'MCP4725 evidence run', force: true });
  await page.waitForFunction(async () => {
    const response = await fetch('/api/status');
    const status = await response.json();
    return status.device_connected === true && status.device_kind === 'hardware';
  });
  await page.getByRole('button', { name: 'Hardware lab' }).click();
  card = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }) });
  await card.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }).waitFor();

  const inventory = {
    addresses: await card.getByLabel('I²C address').locator('option').evaluateAll((options) => options.map((option) => option.value)),
    sdaPins: await card.getByLabel('SDA output pin').locator('option').evaluateAll((options) => options.map((option) => option.value)),
    sclPins: await card.getByLabel('SCL output pin').locator('option').evaluateAll((options) => options.map((option) => option.value)),
    analogInputs: await card.getByLabel('VOUT analog input').locator('option').evaluateAll((options) => options.map((option) => option.value)),
    shapes: await card.getByLabel('Shape').locator('option').evaluateAll((options) => options.map((option) => option.value)),
  };
  assert.equal(inventory.addresses.length, 8);
  assert.equal(inventory.sdaPins.length, 23);
  assert.deepEqual(inventory.sdaPins, inventory.sclPins);
  assert.equal(inventory.analogInputs.length, 4);
  assert.deepEqual(inventory.shapes, ['sine', 'triangle', 'sawtooth', 'square']);
  records.push({ name: 'selectable-options', inventory });
  await screenshot('01-hardware-default-controls');

  await run('Run three-point test', testRoute, '02-hardware-three-point', (result) => {
    assert.equal(result.mock, false);
    assert.equal(result.passed, true);
    assert.deepEqual(result.steps.map((step) => step.code), [0, 2048, 4095]);
    assert.ok(result.steps.every((step) => step.acknowledgements.every(Boolean)));
  });
  await fill('DAC code (0–4095)', 1024);
  await run('Set code and measure', testRoute, '03-hardware-single-code', (result) => {
    assert.equal(result.passed, true);
    assert.deepEqual(result.steps.map((step) => step.code), [1024]);
  });

  await select('I²C address', 0x61);
  await run('Set code and measure', testRoute, '04-hardware-wrong-address', (result) => {
    assert.equal(result.passed, false);
    assert.equal(result.bus_passed, false);
  });
  await select('I²C address', 0x60);
  await select('SCL output pin', 5);
  await run('Set code and measure', testRoute, '05-hardware-wrong-scl', (result) => {
    assert.equal(result.passed, false);
    assert.equal(result.bus_passed, false);
  });
  await select('SCL output pin', 3);

  // Clear the expected NACK result before photographing successful waveforms.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Hardware lab' }).click();
  card = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }) });

  await fill('Steps per cycle', 8);
  await fill('Cycles', 1);
  for (const [index, shape] of ['sine', 'triangle', 'sawtooth', 'square'].entries()) {
    await select('Shape', shape);
    await run('Run waveform and capture scope', waveformRoute,
      `0${index + 6}-hardware-${shape}`, (result) => {
        assert.equal(result.mock, false);
        assert.equal(result.passed, true);
        assert.equal(result.voltage_passed, true);
        assert.equal(result.timing_passed, true);
        assert.equal(result.bus_passed, true);
        assert.equal(result.trace_v.length, 8 * 8);
      });
  }

  await fill('Cycles', 2);
  await run('Run waveform and capture scope', waveformRoute,
    '09a-hardware-two-cycles', (result) => {
      assert.equal(result.passed, true);
      assert.equal(result.trace_v.length, 2 * 8 * 8);
    });
  await fill('Cycles', 1);
  await fill('Steps per cycle', 24);
  await select('Shape', 'sine');
  await run('Run waveform and capture scope', waveformRoute,
    '09b-hardware-24-steps', (result) => {
      assert.equal(result.voltage_passed, true);
      assert.equal(result.bus_passed, true);
      assert.equal(result.timing_passed, false);
      assert.equal(result.trace_v.length, 24 * 8);
    });
  await fill('Steps per cycle', 8);
  await select('Shape', 'square');

  await fill('Minimum (V)', 0.5);
  await fill('Maximum (V)', 2.5);
  const custom = await run('Run waveform and capture scope', waveformRoute,
    '10-hardware-custom-range', (result) => {
      assert.equal(result.passed, true);
      assert.ok(result.measured_min_v > 0.3 && result.measured_min_v < 0.7);
      assert.ok(result.measured_max_v > 2.3 && result.measured_max_v < 2.7);
    });
  await card.getByRole('button', { name: 'Open scope capture' }).click();
  await page.getByText('Loaded session', { exact: true }).waitFor();
  const captureText = await page.locator('.capture-page').innerText();
  assert.ok(captureText.includes('MCP4725 square'));
  assert.match(captureText, /\d+\.\d Hz/);
  assert.ok(!captureText.includes('0.0 kHz'));
  await page.screenshot({ path: path.join(evidence, '11-hardware-scope-session.png'), fullPage: true, animations: 'disabled' });
  records.push({ name: 'hardware-scope-session', sessionId: custom.session_id, visibleText: captureText.slice(0, 350) });
  await page.getByRole('button', { name: 'Hardware lab' }).click();

  await fill('Frequency (Hz)', 0.5);
  await run('Run waveform and capture scope', waveformRoute,
    '12-hardware-timing-limit', (result) => {
      assert.equal(result.voltage_passed, true);
      assert.equal(result.bus_passed, true);
      assert.equal(result.timing_passed, false);
      assert.equal(result.passed, false);
    });
  await fill('Frequency (Hz)', 0.25);

  // Mock mode verifies arbitrary selectable routes and inputs without moving wires.
  await post('/api/connect', { device_id: 'mock' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Hardware lab' }).click();
  card = page.locator('section.card').filter({ has: page.getByRole('heading', { name: 'MCP4725 DAC and signal generator' }) });
  await select('I²C address', 0x67);
  await select('SDA output pin', 22);
  await select('SCL output pin', 0);
  await select('VOUT analog input', 4);
  await fill('DAC code (0–4095)', 1234);
  await run('Set code and measure', testRoute, '13-mock-selectable-route', (result) => {
    assert.equal(result.mock, true);
    assert.equal(result.passed, true);
    assert.equal(result.sda_pin, 22);
    assert.equal(result.scl_pin, 0);
    assert.equal(result.adc_channel, 4);
    assert.equal(result.address, 0x67);
  });
  for (const channel of [1, 2, 3]) {
    await select('VOUT analog input', channel);
    await run('Set code and measure', testRoute, `13${channel}-mock-adc${channel}`, (result) => {
      assert.equal(result.mock, true);
      assert.equal(result.passed, true);
      assert.equal(result.adc_channel, channel);
    });
  }
  await select('VOUT analog input', 4);
  await select('Shape', 'triangle');
  await fill('Steps per cycle', 12);
  await fill('Cycles', 2);
  await fill('Minimum (V)', 0.4);
  await fill('Maximum (V)', 2.9);
  const mockWave = await run('Run waveform and capture scope', waveformRoute,
    '14-mock-custom-waveform', (result) => {
      assert.equal(result.mock, true);
      assert.equal(result.passed, true);
      assert.equal(result.trace_v.length, 12 * 2 * 8);
    });
  await card.getByRole('button', { name: 'Open scope capture' }).click();
  await page.getByText('Loaded session', { exact: true }).waitFor();
  const mockCaptureText = await page.locator('.capture-page').innerText();
  assert.ok(mockCaptureText.includes('MCP4725 triangle'));
  assert.match(mockCaptureText, /\d+\.\d Hz/);
  await page.screenshot({ path: path.join(evidence, '15-mock-scope-session.png'), fullPage: true, animations: 'disabled' });
  records.push({ name: 'mock-scope-session', sessionId: mockWave.session_id, visibleText: mockCaptureText.slice(0, 350) });
  await page.getByRole('button', { name: 'Hardware lab' }).click();

  await fill('Frequency (Hz)', 2);
  await fill('Steps per cycle', 8);
  await fill('Cycles', 1);
  await run('Run waveform and capture scope', waveformRoute,
    '15a-mock-max-frequency', (result) => {
      assert.equal(result.mock, true);
      assert.equal(result.passed, true);
      assert.equal(result.actual_frequency_hz, 2);
    });

  await select('SCL output pin', 22);
  await fill('Minimum (V)', 3);
  await fill('Maximum (V)', 2);
  assert.equal(await card.getByRole('button', { name: 'Set code and measure' }).isDisabled(), true);
  assert.equal(await card.getByRole('button', { name: 'Run waveform and capture scope' }).isDisabled(), true);
  await screenshot('16-invalid-controls-disabled');
  records.push({ name: 'invalid-controls-disabled', samePinsRejected: true, invertedRangeRejected: true });

  await post('/api/connect', { device_id: 'hardware' });
  await select('SDA output pin', 4);
  await select('SCL output pin', 3);
  await select('VOUT analog input', 3);
  await select('I²C address', 0x60);
  await fill('DAC code (0–4095)', 2048);
  await fill('Minimum (V)', 0.2);
  await fill('Maximum (V)', 3.1);
  await run('Set code and measure', testRoute, '17-hardware-restored-midscale', (result) => {
    assert.equal(result.mock, false);
    assert.equal(result.passed, true);
  });
  await fs.writeFile(path.join(evidence, 'results.json'), JSON.stringify(records, null, 2) + '\n');
  console.log(`Saved ${records.length} evidence records in ${evidence}`);
} finally {
  if (clientId) {
    await page.request.post(`${base}/api/control/release`, {
      headers: { 'X-Client-Id': clientId }, data: {},
    }).catch(() => {});
  }
  await browser.close();
}
