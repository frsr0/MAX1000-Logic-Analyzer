import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useApp } from '../state/appStore';

const DIGITAL_RATES = [10e3, 100e3, 500e3, 1e6, 2e6, 5e6, 10e6, 20e6, 50e6, 100e6, 200e6];
const DIGITAL_DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000, 500_000, 1_000_000];
const ANALOG_RATES = [100e3];
const ANALOG_DEPTHS = [1024, 10_000, 50_000, 100_000, 250_000];
const ALL_DIGITAL = Array.from({ length: 16 }, (_, i) => i);
const NO_DIGITAL: number[] = [];

type CaptureMode = 'single' | 'continuous' | 'rolling' | 'triggered' | 'analog' | 'mixed'
  | 'analog_continuous' | 'mixed_continuous';

const ANALOG_MODES: CaptureMode[] = ['analog', 'mixed', 'analog_continuous', 'mixed_continuous'];
const ANALOG_ONLY_MODES: CaptureMode[] = ['analog', 'analog_continuous'];

function formatRate(rate: number) {
  return rate >= 1e6 ? `${rate / 1e6} MHz` : `${rate / 1e3} kHz`;
}

export function CaptureControls() {
  const { status, captureSettings, setCaptureSettings, toast, controlMode } = useApp();
  const [scenarios, setScenarios] = useState<{ id: string; name: string }[]>([]);
  const [findings, setFindings] = useState<{ level: string; message: string }[]>([]);
  const [name, setName] = useState('');

  const connected = status?.device_connected ?? false;
  const capturing = status?.capture_state === 'capturing' || status?.capture_state === 'armed';
  const isMock = status?.device_kind === 'mock';
  const hasErrors = findings.some((f) => f.level === 'error');
  const analogMode = ANALOG_MODES.includes(captureSettings.mode as CaptureMode);
  const rateOptions = analogMode ? ANALOG_RATES : DIGITAL_RATES;
  const depthOptions = analogMode ? ANALOG_DEPTHS : DIGITAL_DEPTHS;

  useEffect(() => {
    if (isMock) api.mockScenarios().then((r) => setScenarios(r.scenarios)).catch(() => {});
    else setScenarios([]);
  }, [isMock, connected]);

  useEffect(() => {
    if (!connected) return;
    const t = setTimeout(() => {
      api.validateSettings(captureSettings)
        .then((r) => setFindings(r.findings))
        .catch(() => setFindings([]));
    }, 300);
    return () => clearTimeout(t);
  }, [captureSettings, connected]);

  useEffect(() => {
    const digitalSelectionOk = captureSettings.mode !== 'analog'
      || captureSettings.enabled_digital.length === 0;
    if (rateOptions.includes(captureSettings.sample_rate)
        && depthOptions.includes(captureSettings.num_samples)
        && captureSettings.analog_enabled === analogMode
        && digitalSelectionOk) {
      return;
    }
    setCaptureSettings({
      analog_enabled: analogMode,
      sample_rate: rateOptions.includes(captureSettings.sample_rate)
        ? captureSettings.sample_rate
        : rateOptions[rateOptions.length - 1],
      num_samples: depthOptions.includes(captureSettings.num_samples)
        ? captureSettings.num_samples
        : depthOptions[Math.min(depthOptions.length - 1, 1)],
      enabled_digital: captureSettings.mode === 'analog'
        ? NO_DIGITAL
        : captureSettings.enabled_digital?.length
          ? captureSettings.enabled_digital
          : ALL_DIGITAL,
    });
  }, [
    analogMode,
    captureSettings.analog_enabled,
    captureSettings.enabled_digital,
    captureSettings.mode,
    captureSettings.num_samples,
    captureSettings.sample_rate,
    depthOptions,
    rateOptions,
    setCaptureSettings,
  ]);

  const setMode = (mode: CaptureMode) => {
    const isAnalog = ANALOG_MODES.includes(mode);
    const isAnalogOnly = ANALOG_ONLY_MODES.includes(mode);
    const nextRates = isAnalog ? ANALOG_RATES : DIGITAL_RATES;
    const nextDepths = isAnalog ? ANALOG_DEPTHS : DIGITAL_DEPTHS;
    setCaptureSettings({
      mode,
      analog_enabled: isAnalog,
      enabled_digital: isAnalogOnly ? NO_DIGITAL : ALL_DIGITAL,
      sample_rate: nextRates.includes(captureSettings.sample_rate)
        ? captureSettings.sample_rate
        : nextRates[nextRates.length - 1],
      num_samples: nextDepths.includes(captureSettings.num_samples)
        ? captureSettings.num_samples
        : nextDepths[Math.min(nextDepths.length - 1, 1)],
    });
  };

  const start = async () => {
    try {
      await api.startCapture(captureSettings, name);
    } catch (e: any) {
      toast('error', e.message);
    }
  };
  const stop = async () => {
    try { await api.stopCapture(); } catch (e: any) { toast('error', e.message); }
  };

  const duration = captureSettings.num_samples / captureSettings.sample_rate;

  return (
    <div className="panel-body">
      <label className="field">
        <span>Capture name</span>
        <input value={name} placeholder="(auto)" onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Mode</span>
        <select value={captureSettings.mode}
          onChange={(e) => setMode(e.target.value as CaptureMode)}>
          <option value="single">Digital single</option>
          <option value="continuous">Digital continuous</option>
          <option value="rolling">Digital rolling</option>
          <option value="analog">Analog only (8 ADC)</option>
          <option value="mixed">Mixed digital + analog</option>
          <option value="analog_continuous">Analog continuous (8 ADC)</option>
          <option value="mixed_continuous">Mixed continuous</option>
        </select>
      </label>
      <label className="field">
        <span>Sample rate</span>
        <select value={captureSettings.sample_rate}
          onChange={(e) => setCaptureSettings({ sample_rate: Number(e.target.value) })}>
          {rateOptions.map((rate) => (
            <option key={rate} value={rate}>{formatRate(rate)}</option>
          ))}
          {!rateOptions.includes(captureSettings.sample_rate) && (
            <option value={captureSettings.sample_rate}>{formatRate(captureSettings.sample_rate)}</option>
          )}
        </select>
      </label>
      <label className="field">
        <span>Samples</span>
        <select value={captureSettings.num_samples}
          onChange={(e) => setCaptureSettings({ num_samples: Number(e.target.value) })}>
          {depthOptions.map((depth) => <option key={depth} value={depth}>{depth.toLocaleString()}</option>)}
        </select>
      </label>
      <div className="hint">duration ~= {duration >= 1 ? `${duration.toFixed(2)} s` : `${(duration * 1e3).toFixed(2)} ms`}</div>
      {(captureSettings.mode === 'single' || captureSettings.mode === 'analog' || captureSettings.mode === 'mixed') && (
        <label className="field">
          <span>Repeat N</span>
          <input type="number" min={1} max={100} value={captureSettings.repeat_count}
            onChange={(e) => setCaptureSettings({ repeat_count: Math.max(1, Number(e.target.value)) })} />
        </label>
      )}
      {isMock && (
        <label className="field">
          <span>Mock scenario</span>
          <select value={captureSettings.mock_scenario ?? 'demo_mixed'}
            onChange={(e) => setCaptureSettings({ mock_scenario: e.target.value })}>
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
      )}
      {findings.map((f, i) => (
        <div key={i} className={`finding ${f.level}`}>{f.message}</div>
      ))}
      <div className="button-row">
        {!capturing ? (
          <button className="primary big" disabled={!connected || !controlMode || hasErrors} onClick={start}>
            Capture
          </button>
        ) : (
          <button className="danger big" onClick={stop} disabled={!controlMode}>Stop</button>
        )}
      </div>
      {capturing && status && (
        <div className="progress">
          <div className="progress-bar" style={{
            width: `${(status.capture_progress.samples_read /
              Math.max(1, status.capture_progress.samples_total)) * 100}%`,
          }} />
          <span>{status.capture_progress.samples_read.toLocaleString()} / {status.capture_progress.samples_total.toLocaleString()} ({status.capture_progress.message})</span>
        </div>
      )}
      {!connected && <div className="hint">Connect a device on the Device page first.</div>}
      {!controlMode && <div className="finding warning">Read-only viewer mode: controls disabled.</div>}
    </div>
  );
}
