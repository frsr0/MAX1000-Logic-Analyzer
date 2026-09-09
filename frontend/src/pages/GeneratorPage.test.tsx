// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GeneratorPage } from './GeneratorPage';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

const protocols = ['uart', 'rs485', 'i2c', 'spi', 'swd', 'pattern', 'bitbang', 'counter', 'prbs', 'custom'];
const connected = (kind = 'hardware') => ({ device_connected: true, device_kind: kind });
const routes = [
  { protocol: 'uart', name: 'UART route', detail: 'TX on D3', features: [], available: true, physical: true, outputs: {} },
  { protocol: 'spi', name: '', detail: '', features: ['clock', 'data'], available: true, physical: true, outputs: {} },
  { protocol: 'custom', name: '', detail: '', features: [], available: true, physical: false, outputs: {} },
];

beforeEach(() => {
  localStorage.clear(); useApp.setState(useApp.getInitialState(), true);
  useApp.setState({ status: connected() as never, controlMode: true, toast: vi.fn(),
    openSession: vi.fn().mockResolvedValue(undefined), setPage: vi.fn() });
  vi.spyOn(api, 'generatorCapabilities').mockResolvedValue({ protocols, routes, status: {
    busy: false, detail: 'ready', actual_symbol_rate: 2500, below_floor: true, divider_width: 24,
  } } as never);
  vi.spyOn(api, 'bitbangPresets').mockResolvedValue({ presets: ['uart_8n1', 'clock'] } as never);
  vi.spyOn(api, 'generatorStatus').mockResolvedValue({ busy: false } as never);
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

async function renderReady(kind = 'hardware') {
  useApp.setState({ status: connected(kind) as never });
  const view = render(<GeneratorPage />);
  await screen.findByRole('option', { name: 'BITBANG' });
  return view;
}

function choose(protocol: string) {
  fireEvent.change(screen.getByLabelText('Generator protocol'), { target: { value: protocol } });
}

function fieldInputs(label: string) {
  return within(screen.getByText(label).closest('label')!).getAllByRole('spinbutton');
}

it('renders disconnected state and loads hardware capabilities, routes and status', async () => {
  useApp.setState({ status: null });
  const { rerender } = render(<GeneratorPage />);
  expect(screen.getByText('Connect a device first on the Device page.')).toBeTruthy();
  expect(api.generatorCapabilities).not.toHaveBeenCalled();
  useApp.setState({ status: connected() as never }); rerender(<GeneratorPage />);
  expect(await screen.findByText('10 supported')).toBeTruthy();
  expect(screen.getByText(/idle · ready · ~2.5 kS\/s · ⚠ below divider floor · 24-bit divider/)).toBeTruthy();
  const capabilityPanel = within(screen.getByTestId('generator-route-capabilities'));
  expect(capabilityPanel.getByText(/UART route/).parentElement?.textContent).toContain('TX on D3');
  expect(capabilityPanel.getByText('SPI').parentElement?.textContent).toContain('clock, data');
  expect(capabilityPanel.getByText('CUSTOM').parentElement?.textContent).toContain('basic route');
  expect(screen.getByLabelText('TX pin')).toHaveProperty('value', '3');
});

it('tolerates capability/catalog/poll failures and renders sparse status data', async () => {
  vi.useFakeTimers();
  vi.mocked(api.generatorCapabilities)
    .mockRejectedValueOnce(new Error('capabilities offline'))
    .mockResolvedValueOnce({ protocols: ['uart'], status: { busy: true } } as never);
  vi.mocked(api.bitbangPresets).mockRejectedValue(new Error('catalog offline'));
  vi.mocked(api.generatorStatus)
    .mockResolvedValueOnce({ busy: true, detail: '', actual_symbol_rate: 0,
      below_floor: false, divider_width: 16 } as never)
    .mockRejectedValueOnce(new Error('poll offline'));
  const { unmount } = render(<GeneratorPage />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('loading')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByText('BUSY')).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  unmount();

  render(<GeneratorPage />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole('option', { name: 'UART' })).toBeTruthy();
  expect(screen.queryByTestId('generator-route-capabilities')).toBeNull();
});

it('edits UART, RS-485, I2C, SPI, SWD, pattern and simple generator protocols', async () => {
  await renderReady();
  fireEvent.change(screen.getByLabelText(/Data text/), { target: { value: 'µA' } });
  fireEvent.change(screen.getByLabelText('Data hex'), { target: { value: 'aa-Z1' } });
  fireEvent.change(screen.getByLabelText('Baud'), { target: { value: '9600' } });
  fireEvent.change(screen.getByLabelText('TX pin'), { target: { value: '4' } });

  choose('rs485');
  fireEvent.change(screen.getByLabelText('B / + pin'), { target: { value: '5' } });
  fireEvent.change(screen.getByLabelText('A / - pin'), { target: { value: '6' } });
  fireEvent.change(screen.getByLabelText('Optional DE pin'), { target: { value: '7' } });
  fireEvent.change(screen.getByLabelText('Optional DE pin'), { target: { value: '' } });

  choose('i2c');
  fireEvent.change(screen.getByLabelText('Speed (Hz)'), { target: { value: '100000' } });
  fireEvent.change(screen.getByLabelText('Address (hex)'), { target: { value: 'zz' } });
  fireEvent.change(screen.getByLabelText('Register (hex)'), { target: { value: '2a' } });
  fireEvent.change(screen.getByLabelText('Register (hex)'), { target: { value: 'zz' } });
  const i2cPins = fieldInputs('SDA channel / SCL channel');
  fireEvent.change(i2cPins[0], { target: { value: '8' } }); fireEvent.change(i2cPins[1], { target: { value: '9' } });

  choose('spi');
  fireEvent.change(screen.getByLabelText('Clock rate (Hz)'), { target: { value: '2000000' } });
  const spiPins = fieldInputs('MOSI pin / SCLK pin');
  fireEvent.change(spiPins[0], { target: { value: '10' } }); fireEvent.change(spiPins[1], { target: { value: '11' } });
  const optional = fieldInputs('Optional CS pin / MISO input pin');
  fireEvent.change(optional[0], { target: { value: '12' } }); fireEvent.change(optional[0], { target: { value: '' } });
  fireEvent.change(optional[1], { target: { value: '13' } });
  const capturePins = fieldInputs('CS capture channel / MISO capture channel');
  fireEvent.change(capturePins[0], { target: { value: '14' } }); fireEvent.change(capturePins[0], { target: { value: '' } });
  fireEvent.change(fieldInputs('CS capture channel / MISO capture channel')[1], { target: { value: '12' } });
  expect(screen.getByText(/standalone Send is unsupported/)).toBeTruthy();

  choose('swd');
  const swdPins = fieldInputs('SWDIO pin / SWCLK pin');
  fireEvent.change(swdPins[0], { target: { value: '2' } }); fireEvent.change(swdPins[1], { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('SWD requests (JSON)'), { target: { value: 'invalid' } });
  fireEvent.change(screen.getByLabelText('SWD requests (JSON)'), { target: { value: '[{"read":false}]' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Send JTAG-to-SWD sequence' }));

  choose('pattern');
  fireEvent.change(screen.getByLabelText('Bit rate (bits/s)'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Output pin'), { target: { value: '6' } });
  expect(screen.getByText(/one bit per \? µs/)).toBeTruthy();
  choose('counter'); expect(screen.getByText(/16-bit counter/)).toBeTruthy();
  choose('prbs'); expect(screen.getByText(/pseudo-random bits/)).toBeTruthy();
  choose('custom'); expect(screen.queryByLabelText(/Data text/)).toBeNull();
  act(() => useApp.setState({ status: connected('mock') as never }));
  act(() => useApp.setState({ status: connected('hardware') as never }));
  choose('uart'); expect(screen.getByLabelText('TX pin')).toHaveProperty('value', '3');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Continuous' }));
});

it('configures every Bit Banger protocol template and edits faults, symbols and presets', async () => {
  await renderReady('mock');
  choose('rs485'); expect(screen.getByLabelText('B / + pin')).toHaveProperty('value', '0');
  act(() => useApp.setState({ status: connected('hardware') as never }));
  expect(screen.getByLabelText('B / + pin')).toHaveProperty('value', '3');
  fireEvent.change(screen.getByLabelText('B / + pin'), { target: { value: '5' } });
  act(() => useApp.setState({ status: connected('mock') as never }));
  act(() => useApp.setState({ status: connected('hardware') as never }));
  expect(screen.getByLabelText('B / + pin')).toHaveProperty('value', '5');
  act(() => useApp.setState({ status: connected('mock') as never }));
  choose('uart'); expect(screen.getByLabelText('TX pin')).toHaveProperty('value', '0');
  choose('spi'); expect(fieldInputs('MOSI pin / SCLK pin').map((input) => input.getAttribute('value'))).toEqual(['5', '4']);
  choose('swd'); expect(fieldInputs('SWDIO pin / SWCLK pin').map((input) => input.getAttribute('value'))).toEqual(['1', '0']);
  choose('bitbang');
  const template = screen.getByLabelText('Protocol template');

  fireEvent.change(template, { target: { value: 'rs485' } });
  for (const [label, value] of [['DE assert delay (µs)', '1'], ['DE release delay (µs)', '2'],
    ['Turnaround delay (µs)', '3'], ['Direction changes', '4']] as const) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }

  fireEvent.change(template, { target: { value: 'spi' } });
  fireEvent.change(screen.getByLabelText('Data hex'), { target: { value: 'ab-cd' } });
  fireEvent.change(screen.getByLabelText('SPI mode'), { target: { value: '11' } });
  const word = screen.getByText('Bit order / word size').closest('label')!;
  fireEvent.change(within(word).getByRole('combobox'), { target: { value: 'lsb' } });
  fireEvent.change(within(word).getByRole('spinbutton'), { target: { value: '16' } });
  fireEvent.change(screen.getByLabelText('Inter-word gap (symbols)'), { target: { value: '2' } });

  fireEvent.change(template, { target: { value: 'i2c' } });
  fireEvent.change(screen.getByLabelText('7-bit address (hex)'), { target: { value: 'zz' } });
  fireEvent.change(screen.getByLabelText('Register (hex)'), { target: { value: 'zz' } });
  fireEvent.change(screen.getByLabelText('Read length'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Repeated start' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'ACK writes' }));
  fireEvent.change(screen.getByLabelText('Bus recovery clocks'), { target: { value: '9' } });
  fireEvent.change(screen.getByLabelText('Clock stretch (µs)'), { target: { value: '5' } });

  fireEvent.change(template, { target: { value: 'onewire' } });
  fireEvent.change(screen.getByLabelText('Read slots'), { target: { value: '8' } });
  fireEvent.change(template, { target: { value: 'swd' } });
  fireEvent.change(screen.getByLabelText('SWD requests (JSON)'), { target: { value: 'bad' } });
  fireEvent.change(screen.getByLabelText('SWD requests (JSON)'), { target: { value: '[]' } });
  const reset = fieldInputs('Line reset / idle cycles');
  fireEvent.change(reset[0], { target: { value: '60' } }); fireEvent.change(reset[1], { target: { value: '9' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'JTAG-to-SWD transition' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Prepend IDCODE discovery' }));

  fireEvent.change(template, { target: { value: 'pwm' } });
  for (const [label, value] of [['Frequency (Hz)', '1100'], ['End frequency (Hz)', '2000'],
    ['Start phase (degrees)', '90']] as const) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const duty = fieldInputs('Duty / end duty (%)'); fireEvent.change(duty[0], { target: { value: '25' } });
  fireEvent.change(fieldInputs('Duty / end duty (%)')[1], { target: { value: '75' } });
  const sweep = fieldInputs('Sweep steps / cycles'); fireEvent.change(sweep[0], { target: { value: '3' } });
  fireEvent.change(fieldInputs('Sweep steps / cycles')[1], { target: { value: '9' } });

  fireEvent.change(screen.getByLabelText('Fault injection'), { target: { value: 'wrong_parity' } });
  fireEvent.change(screen.getByLabelText('Fault injection'), { target: { value: '' } });
  fireEvent.change(template, { target: { value: '' } });
  fireEvent.change(screen.getByLabelText('Symbol rate (symbols/s)'), { target: { value: '4800' } });
  fireEvent.change(screen.getByLabelText(/2-bit symbols/), { target: { value: '0, 1, 4, x, 3' } });
  fireEvent.change(screen.getByLabelText('Preset'), { target: { value: 'clock' } });
  fireEvent.change(screen.getByLabelText('Preset symbols'), { target: { value: '64' } });
  fireEvent.change(screen.getByLabelText('Preset'), { target: { value: 'uart_8n1' } });
  fireEvent.change(screen.getByLabelText('Preset'), { target: { value: '' } });
});

it('previews, imports and exports Bit Banger scripts with strict error handling', async () => {
  const preview = vi.spyOn(api, 'generatorPreview')
    .mockResolvedValueOnce({ count: 4, duration_s: 0.00001, output_frequency_hz: 2500,
      actual_symbol_rate: 2000, below_floor: true, tx_levels: [0, 1], clock_levels: [1, 0] } as never)
    .mockResolvedValueOnce({ count: 1, duration_s: 0, output_frequency_hz: 20,
      actual_symbol_rate: 0, below_floor: false, tx_levels: [], clock_levels: [] } as never)
    .mockResolvedValueOnce({ count: 0, duration_s: 0, output_frequency_hz: 0,
      actual_symbol_rate: 0, below_floor: false, tx_levels: [], clock_levels: [] } as never)
    .mockRejectedValueOnce(new Error('preview failed'));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:script'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  await renderReady(); choose('bitbang');
  fireEvent.click(screen.getByRole('button', { name: 'Preview waveform' }));
  expect(await screen.findByText(/TX ~2.5 kHz/)).toBeTruthy();
  expect(screen.getByText(/below the Bit_Engine/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Preview waveform' }));
  expect(await screen.findByText(/TX ~20 Hz/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Preview waveform' }));
  await waitFor(() => expect(preview).toHaveBeenCalledTimes(3));
  fireEvent.click(screen.getByRole('button', { name: 'Preview waveform' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'preview failed'));
  expect(preview).toHaveBeenCalledTimes(4);

  fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
  expect(URL.createObjectURL).toHaveBeenCalled(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:script');
  const fileInput = document.querySelector('input[type="file"]')!;
  const valid = { text: vi.fn().mockResolvedValue(JSON.stringify({ symbol_rate: 1200, symbols: [0, 1] })) };
  fireEvent.change(fileInput, { target: { files: [valid] } });
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'Bit Banger script imported'));
  fireEvent.change(screen.getByLabelText('Protocol template'), { target: { value: 'swd' } });
  expect(screen.getByLabelText('SWD requests (JSON)')).toHaveProperty('value', expect.stringContaining('"ap"'));
  const invalid = { text: vi.fn().mockResolvedValue('{}') };
  fireEvent.change(fileInput, { target: { files: [invalid] } });
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', expect.stringContaining('symbols or script')));
  const script = { text: vi.fn().mockResolvedValue(JSON.stringify({ script: [1, 2] })) };
  fireEvent.change(fileInput, { target: { files: [script] } });
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledTimes(3));
  const preset = { text: vi.fn().mockResolvedValue(JSON.stringify({ symbols: [1], preset: 'clock' })) };
  fireEvent.change(fileInput, { target: { files: [preset] } });
  await waitFor(() => expect(screen.getByLabelText('Preset symbols')).toHaveProperty('value', '32'));
  fireEvent.change(fileInput, { target: { files: [] } });
});

it('sends, streams and captures with bounded payloads and navigates results', async () => {
  const send = vi.spyOn(api, 'generatorSend')
    .mockResolvedValueOnce({ detail: 'sent' } as never)
    .mockResolvedValueOnce({ detail: 'streaming' } as never)
    .mockResolvedValueOnce({ session_id: 'loop', detail: null, passed: true, sent_hex: 'aa', decoded_hex: 'aa' } as never)
    .mockResolvedValueOnce({ passed: false, sent_hex: 'aa', decoded_hex: 'bb', detail: 'mismatch' } as never)
    .mockRejectedValueOnce(new Error('send failed'));
  vi.spyOn(api, 'generatorStop').mockRejectedValue(new Error('ignored'));
  await renderReady();
  fireEvent.change(screen.getByLabelText('Baud'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Send pattern' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'Pattern sent'));
  fireEvent.change(screen.getByLabelText('Expected hex for compare'), { target: { value: 'aa-zz' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stream continuously' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('success', expect.stringContaining('Live stream started')));
  fireEvent.click(screen.getByRole('button', { name: 'Send and capture' }));
  expect(await screen.findByText('PASS')).toBeTruthy();
  expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'Loopback captured');
  fireEvent.click(screen.getByRole('button', { name: 'Open loopback capture' }));
  await waitFor(() => expect(useApp.getState().openSession).toHaveBeenCalledWith('loop'));
  expect(useApp.getState().setPage).toHaveBeenCalledWith('capture');

  fireEvent.click(screen.getByRole('button', { name: 'Send and capture' }));
  expect(await screen.findByText('FAIL')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Send pattern' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'send failed'));
  fireEvent.click(screen.getByRole('button', { name: 'Stop output' }));
  expect(send).toHaveBeenCalledTimes(5);

  fireEvent.change(screen.getByLabelText('Data hex'), { target: { value: 'aa'.repeat(257) } });
  fireEvent.click(screen.getByRole('button', { name: 'Send pattern' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', expect.stringContaining('Generator FIFO holds 256 bytes')));
});

it('runs preview/capture sweeps, opens captures and handles sweep errors', async () => {
  const preview = vi.spyOn(api, 'generatorSweepPreview')
    .mockResolvedValueOnce({ passed: 1, count: 2, failed: 1, rows: [
      { protocol: 'uart', status: 'ok', session_id: 'sweep-session' },
      { protocol: 'uart', status: 'error', error: 'bad baud' },
    ] } as never)
    .mockResolvedValueOnce({ passed: 1, count: 1, failed: 0, rows: [] } as never)
    .mockRejectedValueOnce(new Error('preview sweep failed'));
  const capture = vi.spyOn(api, 'generatorSweepCapture')
    .mockResolvedValueOnce({ passed: 2, count: 2, failed: 0, rows: [] } as never)
    .mockResolvedValueOnce({ passed: 1, count: 1, failed: 0, rows: [] } as never)
    .mockRejectedValueOnce(new Error('capture sweep failed'));
  await renderReady();
  fireEvent.click(screen.getByRole('button', { name: 'Preview sweep' }));
  expect(await screen.findByText('1/2 variants valid')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Open capture' }));
  await waitFor(() => expect(useApp.getState().openSession).toHaveBeenCalledWith('sweep-session'));
  fireEvent.click(screen.getByRole('button', { name: 'Run sweep + capture' }));
  expect(await screen.findByText('2/2 variants valid')).toBeTruthy();
  expect(useApp.getState().toast).toHaveBeenCalledWith('success', 'Capture-backed sweep complete');
  act(() => useApp.setState({ status: connected('mock') as never }));
  choose('bitbang');
  fireEvent.click(screen.getByRole('button', { name: 'Preview sweep' }));
  await waitFor(() => expect(preview).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: 'Run sweep + capture' }));
  await waitFor(() => expect(capture).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole('button', { name: 'Preview sweep' }));
  fireEvent.click(screen.getByRole('button', { name: 'Run sweep + capture' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'capture sweep failed'));
  expect(preview).toHaveBeenCalledTimes(3); expect(capture).toHaveBeenCalledTimes(3);
});

it('runs generator self-test and enforces busy/read-only/protocol button gates', async () => {
  let finish!: (value: any) => void;
  const selfTest = vi.spyOn(api, 'generatorSelfTest')
    .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockRejectedValueOnce(new Error('self-test failed'));
  await renderReady();
  fireEvent.click(screen.getByText('Advanced diagnostics'));
  fireEvent.click(screen.getByRole('button', { name: 'Run generator self-test' }));
  expect(screen.getByRole('button', { name: 'Run generator self-test' }).hasAttribute('disabled')).toBe(true);
  await act(async () => finish({ passed: true, sent_hex: '01', decoded_hex: '01', detail: 'healthy' }));
  expect(screen.getByText('PASS')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Run generator self-test' }));
  await waitFor(() => expect(useApp.getState().toast).toHaveBeenCalledWith('error', 'self-test failed'));

  choose('spi');
  expect(screen.getByRole('button', { name: 'Send pattern' }).hasAttribute('disabled')).toBe(true);
  choose('pattern');
  expect(screen.getByRole('button', { name: 'Send and capture' }).hasAttribute('disabled')).toBe(true);
  act(() => useApp.setState({ controlMode: false }));
  expect(screen.getByRole('button', { name: 'Stop output' }).hasAttribute('disabled')).toBe(true);
  expect(selfTest).toHaveBeenCalledTimes(2);
});
