import { useState } from 'react';
import { api } from '../api/client';
import type { Mcp4725TestResult, Mcp4725WaveformConfig, Mcp4725WaveformResult } from '../api/types';
import { useApp } from '../state/appStore';
import { PIN_OPTIONS } from './milPins';

const ADC_OPTIONS = [
  { channel: 1 as const, label: 'AIN3 (J1/5)' },
  { channel: 2 as const, label: 'AIN1 (J1/3)' },
  { channel: 3 as const, label: 'AIN4 (J1/6)' },
  { channel: 4 as const, label: 'AIN6 (J1/8)' },
];

function pinLabel(pin: number): string {
  return PIN_OPTIONS.find((option) => option.pin === pin)?.label ?? `Pin ${pin}`;
}

function analogLabel(channel: number): string {
  return ADC_OPTIONS.find((option) => option.channel === channel)?.label ?? `ADC${channel}`;
}

function tracePath(values: number[]): string {
  if (!values.length) return '';
  return values.map((volts, index) =>
    `${index === 0 ? 'M' : 'L'} ${(index / Math.max(1, values.length - 1) * 720).toFixed(1)} ${(140 - volts / 3.3 * 120).toFixed(1)}`,
  ).join(' ');
}

export function MilMcp4725Card() {
  const { controlMode, toast, status, openSession, setPage } = useApp();
  const [address, setAddress] = useState(0x60);
  const [sdaPin, setSdaPin] = useState(5);
  const [sclPin, setSclPin] = useState(4);
  const [adcChannel, setAdcChannel] = useState<1 | 2 | 3 | 4>(3);
  const [code, setCode] = useState(2048);
  const [tolerance, setTolerance] = useState(0.2);
  const [waveform, setWaveform] = useState<Mcp4725WaveformConfig['waveform']>('sine');
  const [frequency, setFrequency] = useState(0.25);
  const [steps, setSteps] = useState(8);
  const [cycles, setCycles] = useState(2);
  const [minV, setMinV] = useState(0.2);
  const [maxV, setMaxV] = useState(3.1);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Mcp4725TestResult | null>(null);
  const [waveResult, setWaveResult] = useState<Mcp4725WaveformResult | null>(null);
  const routeValid = sdaPin !== sclPin;
  const toleranceValid = tolerance > 0 && tolerance <= 0.5;
  const waveformValid = routeValid && toleranceValid && minV >= 0 && maxV <= 3.3 && minV < maxV
    && frequency >= 0.25 && frequency <= 2 && Number.isInteger(steps) && steps >= 8 && steps <= 24
    && Number.isInteger(cycles) && cycles >= 1 && cycles <= 3
    && frequency * steps <= 20 && cycles / frequency <= 12;

  const run = async (codes: number[]) => {
    setBusy(true);
    setResult(null);
    try {
      const next = await api.milMcp4725Test({ address, sda_pin: sdaPin, scl_pin: sclPin, adc_channel: adcChannel, codes, tolerance_v: tolerance });
      setResult(next);
      toast(next.passed ? 'success' : 'warning',
        `${next.mock ? 'Mock scope' : 'MCP4725'} voltage check ${next.passed ? 'passed' : 'failed'}`);
    } catch (error: any) {
      toast('error', error.message);
    } finally {
      setBusy(false);
    }
  };

  const runWaveform = async () => {
    setBusy(true);
    setWaveResult(null);
    try {
      const next = await api.milMcp4725Waveform({
        address, sda_pin: sdaPin, scl_pin: sclPin, adc_channel: adcChannel, waveform,
        frequency_hz: frequency, steps_per_cycle: steps, cycles,
        min_v: minV, max_v: maxV, tolerance_v: tolerance,
      });
      setWaveResult(next);
      toast(next.passed ? 'success' : 'warning',
        `${next.mock ? 'Mock scope' : next.analog_input} waveform ${next.passed ? 'passed' : 'missed voltage, I²C ACK, or timing target'}`);
    } catch (error: any) {
      toast('error', error.message);
    } finally {
      setBusy(false);
    }
  };

  const openScope = async () => {
    try {
      await openSession(waveResult!.session_id);
      setPage('capture');
    } catch (error: any) {
      toast('error', error.message);
    }
  };

  return (
    <section className="card" aria-labelledby="mcp4725-title">
      <h3 id="mcp4725-title">MCP4725 DAC and signal generator</h3>
      <p className="hint">
        Connect DAC SDA to {pinLabel(sdaPin)}, SCL to {pinLabel(sclPin)}, VOUT to {analogLabel(adcChannel)},
        and the board to 3.3 V and common GND. The I²C lines need pull-ups to 3.3 V.
        Use a 3.3 V supply so VOUT stays within the analog input range.
      </p>
      <div className="mil-layout">
        <label className="field"><span>I²C address</span>
          <select value={address} onChange={(event) => setAddress(Number(event.target.value))}>
            {Array.from({ length: 8 }, (_, i) => 0x60 + i).map((value) => (
              <option key={value} value={value}>0x{value.toString(16)}</option>
            ))}
          </select>
        </label>
        <label className="field"><span>SDA output pin</span>
          <select value={sdaPin} onChange={(event) => setSdaPin(Number(event.target.value))}>
            {PIN_OPTIONS.map((option) => (
              <option key={option.pin} value={option.pin}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="field"><span>SCL output pin</span>
          <select value={sclPin} onChange={(event) => setSclPin(Number(event.target.value))}>
            {PIN_OPTIONS.map((option) => (
              <option key={option.pin} value={option.pin}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="field"><span>VOUT analog input</span>
          <select value={adcChannel} onChange={(event) => setAdcChannel(Number(event.target.value) as 1 | 2 | 3 | 4)}>
            {ADC_OPTIONS.map((option) => (
              <option key={option.channel} value={option.channel}>{option.label}</option>
            ))}
          </select>
        </label>
        <label className="field"><span>DAC code (0–4095)</span>
          <input type="number" min={0} max={4095} step={1} value={code}
            onChange={(event) => setCode(Number(event.target.value))} />
        </label>
        <label className="field"><span>Allowed voltage error (V)</span>
          <input type="number" min={0.01} max={0.5} step={0.01} value={tolerance}
            onChange={(event) => setTolerance(Number(event.target.value))} />
        </label>
      </div>
      {!routeValid && <div className="finding warning">SDA and SCL must use different pins.</div>}
      <div className="button-row wrap">
        <button disabled={busy || !controlMode || !routeValid || !Number.isInteger(code) || code < 0 || code > 4095 || !toleranceValid}
          onClick={() => run([code])}>Set code and measure</button>
        <button disabled={busy || !controlMode || !routeValid || !toleranceValid}
          onClick={() => run([0, 2048, 4095])}>Run three-point test</button>
      </div>
      {result && (
        <div className={`finding ${result.passed ? 'info' : 'warning'}`} role="status">
          <strong>{result.mock ? 'SIMULATED' : 'HARDWARE'} {result.passed ? 'PASS' : 'FAIL'}</strong> · 0x{result.address.toString(16)} · ±{result.tolerance_v.toFixed(2)} V
          <div className="table-scroll">
            <table className="data-table">
            <thead><tr><th>Code</th><th>Expected</th><th>Measured</th><th>Error</th><th>I²C ACKs</th><th>Result</th></tr></thead>
              <tbody>{result.steps.map((step) => (
                <tr key={step.code}>
                  <td>{step.code}</td><td>{step.expected_v.toFixed(3)} V</td>
                  <td>{step.measured_v.toFixed(3)} V</td><td>{step.error_v.toFixed(3)} V</td>
                  <td>{step.acknowledgements === null ? 'n/a' : step.acknowledgements.map((ack) => ack ? 'ACK' : 'NACK').join(' · ') || 'not captured'}</td>
                  <td>{step.passed ? 'PASS' : 'FAIL'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {!result.mock && !result.bus_passed && <div>
            No DAC ACK was seen on the selected bus. Check the device address and that SDA, SCL, and ground reach the module.
          </div>}
        </div>
      )}
      <h4>Waveform generator</h4>
      <p className="hint">
        Run 1–3 cycles of I²C DAC updates; the output holds the last code afterward. {status?.device_kind === 'mock'
          ? 'The connected mock scope will simulate VOUT and save an analog trace.'
          : `On hardware, ${analogLabel(adcChannel)} verifies each distinct level before the timed I²C run. The saved scope trace replays those measured levels at the actual write rate.`}
      </p>
      <div className="mil-layout">
        <label className="field"><span>Shape</span>
          <select value={waveform} onChange={(event) => setWaveform(event.target.value as Mcp4725WaveformConfig['waveform'])}>
            <option value="sine">Sine</option><option value="triangle">Triangle</option>
            <option value="sawtooth">Sawtooth</option><option value="square">Square</option>
          </select>
        </label>
        <label className="field"><span>Frequency (Hz)</span>
          <input type="number" min={0.25} max={2} step={0.05} value={frequency}
            onChange={(event) => setFrequency(Number(event.target.value))} />
        </label>
        <label className="field"><span>Steps per cycle</span>
          <input type="number" min={8} max={24} step={1} value={steps}
            onChange={(event) => setSteps(Number(event.target.value))} />
        </label>
        <label className="field"><span>Cycles</span>
          <input type="number" min={1} max={3} step={1} value={cycles}
            onChange={(event) => setCycles(Number(event.target.value))} />
        </label>
        <label className="field"><span>Minimum (V)</span>
          <input type="number" min={0} max={3.3} step={0.1} value={minV}
            onChange={(event) => setMinV(Number(event.target.value))} />
        </label>
        <label className="field"><span>Maximum (V)</span>
          <input type="number" min={0} max={3.3} step={0.1} value={maxV}
            onChange={(event) => setMaxV(Number(event.target.value))} />
        </label>
      </div>
      <button className="primary" disabled={busy || !controlMode || !waveformValid}
        onClick={runWaveform}>Run waveform and save trace</button>
      {waveResult && (
        <div className={`finding ${waveResult.passed ? 'info' : 'warning'}`} role="status">
          <strong>{waveResult.mock ? 'SIMULATED' : 'HARDWARE'} {waveResult.passed ? 'PASS' : 'FAIL'}</strong>
          {' · '}{waveResult.waveform} · requested {waveResult.requested_frequency_hz.toFixed(2)} Hz
          {' · '}measured {waveResult.actual_frequency_hz.toFixed(2)} Hz
          {' · '}VOUT {waveResult.measured_min_v.toFixed(2)}–{waveResult.measured_max_v.toFixed(2)} V
          {' · '}max error {waveResult.max_error_v.toFixed(3)} V
          {' · '}voltage {waveResult.voltage_passed ? 'pass' : 'fail'}
          {' · '}timing {waveResult.timing_passed ? 'pass' : 'fail'}
          {' · '}I²C ACK {waveResult.bus_passed ? 'pass' : 'fail'}
          <svg viewBox="0 0 720 160" role="img" aria-label="MCP4725 verified analog level trace"
            style={{ display: 'block', width: '100%', height: 180 }}>
            <line x1="0" y1="140" x2="720" y2="140" stroke="currentColor" opacity="0.3" />
            <line x1="0" y1="20" x2="720" y2="20" stroke="currentColor" opacity="0.3" />
            <path d={tracePath(waveResult.trace_v)} fill="none" stroke="#ffd54f" strokeWidth="2" />
          </svg>
          <small className="hint">{waveResult.trace_source === 'preflight_measured_levels'
            ? 'Scope trace: ADC measured each code before the timed run; levels are replayed at the measured I²C cadence.'
            : 'Scope trace: simulated DAC levels.'}</small>
          <button onClick={openScope}>Open scope trace</button>
        </div>
      )}
      <small className="hint">The test changes only the volatile DAC register. It does not write EEPROM.</small>
    </section>
  );
}
